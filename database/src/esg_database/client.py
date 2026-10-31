"""One long-lived, serialized SQL adapter; bounded RPC, no per-request shell."""
from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import threading
from pathlib import Path


def module_release(profile="capture"):
    from esg_runtime.release import ModuleRelease
    root = Path(os.getenv("ESG_DATABASE_MODULE_ROOT", str(Path(__file__).resolve().parents[2])))
    python_root = Path(__file__).resolve().parent
    if profile == "products_client":
        patterns=("http_client.py","contracts.py","products_contracts.py")
    elif profile == "capture":
        patterns=(
            "runtime/capture_worker.mjs","runtime/rpc.mjs","runtime/connection.mjs","runtime/schema.mjs","runtime/canonical.mjs",
            "queries/capture*.mjs","queries/ingestion.mjs","migrations/0019*.sql","migrations/capture/**/*.sql")
    else:
        raise ValueError("Unknown database release profile")
    from esg_contracts import facts
    sdk = ModuleRelease(Path(facts.__file__).resolve().parents[1], "contracts-sdk", (
        "esg_contracts/**/*.py", "esg_data/**/*.py", "esg_reports/**/*.py"))
    if profile == "products_client":
        return ModuleRelease(python_root, "database-products-client", patterns, dependencies=(("contracts", sdk),))
    python = ModuleRelease(python_root, "database-sdk", ("*.py",))
    return ModuleRelease(root, "database-module", patterns, dependencies=(("contracts", sdk),("python", python)))


class DatabaseError(RuntimeError):
    def __init__(self, message: str, code: str = "database_error"):
        super().__init__(message)
        self.code = code


class DatabaseBridge:
    def __init__(self, environment: dict[str, str] | None = None):
        self.environment = {**os.environ, **(environment or {})}
        if self.environment.get("ESG_DATABASE_PROFILE","capture") != "capture":
            raise ValueError("Only the current capture adapter is available")
        self.environment["ESG_DATABASE_PROFILE"]="capture"
        for key in list(self.environment):
            if key.startswith("PLATESG_"):self.environment.pop(key)
        self.lock = threading.Lock()
        self.sequence = 0
        self.process = None
        self.selector = selectors.DefaultSelector()

    def start(self):
        node = self.environment.get("ESG_NODE") or shutil.which("node")
        if not node:
            candidate = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
            node = str(candidate) if candidate.exists() else None
        if not node:
            raise RuntimeError("Node.js 20+ is required by the PostgreSQL adapter")
        worker = "capture_worker.mjs"
        self.process = subprocess.Popen(
            [node, self.environment.get("ESG_DATABASE_WORKER", str(Path(self.environment.get("ESG_DATABASE_MODULE_ROOT", str(Path(__file__).resolve().parents[2]))) / "runtime" / worker))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
            text=True, encoding="utf-8", bufsize=1, env=self.environment,
        )
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.call("health", {}, timeout=90)
        return self

    def call(self, method: str, params: dict | None = None, *, timeout: float = 60):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise DatabaseError("Database adapter is unavailable", "adapter_unavailable")
            self.sequence += 1
            request_id = self.sequence
            self.process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params or {}}, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            if not self.selector.select(timeout):
                # A timed-out mutation has uncertain outcome. Never replay it automatically.
                self.process.kill()
                raise DatabaseError("Database operation timed out; inspect receipt before retrying", "adapter_timeout")
            line = self.process.stdout.readline()
            if not line:
                raise DatabaseError("Database adapter terminated", "adapter_unavailable")
            response = json.loads(line)
            if response.get("id") != request_id:
                raise DatabaseError("Database adapter response mismatch", "adapter_protocol_error")
            if "error" in response:
                raise DatabaseError(response["error"]["message"], response["error"].get("code", "database_error"))
            return response["result"]

    def close(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
            self.selector.close()
