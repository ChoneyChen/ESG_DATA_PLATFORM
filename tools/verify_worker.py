#!/usr/bin/env python3
"""Execute the installed worker outside the checkout against an explicit empty document port."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from esg_clauses.repository import ClauseRepository
from esg_clauses.templates import new_clause
from esg_contracts.evidence import DocumentReference
from esg_contracts.facts import RunRequest
from esg_contracts.worker import EVENT_PREFIX
from esg_extraction.pipeline.runtime import engine_digest
from esg_extraction.pipeline.state import RunStore
from esg_runtime.execution_slot import ExecutionSlot

ROOT = Path(__file__).resolve().parents[1]


def main():
    document = DocumentReference(
        document_id="synthetic-sdk-check",
        ir_run_id="ir-sdk-check",
        ir_revision=1,
        content_digest="a" * 64,
    )

    class Port(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply(document.model_dump(mode="json"))

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.reply(
                {
                    "schema_version": "object-search-page-1.0",
                    "objects": [],
                    "total": 0,
                    "returned": 0,
                    "next_cursor": None,
                    "query_digest": "b" * 64,
                    "scope": {"kind": "explicit_empty_test_port"},
                }
            )

        def reply(self, body):
            value = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)

    with tempfile.TemporaryDirectory(prefix="esg-worker-verified-") as folder:
        work = Path(folder)
        clause = (
            new_clause("SDK_CHECK", "SDK", "quantitative", "Synthetic SDK smoke check")
            .model_copy(update={"readiness": "ready", "blocking_reasons": []})
            .seal()
        )
        path = work / "clauses/SDK/SDK_CHECK"
        path.mkdir(parents=True)
        (path / "clause.json").write_text(clause.model_dump_json())
        repo = ClauseRepository(work / "clauses")
        repo.reindex()
        store = RunStore(work / "control/extractions.sqlite3")
        state = store.create(
            RunRequest(ir_run_id=document.ir_run_id, clause_keys=[clause.clause_key]),
            repo.freeze(repo.select([clause.clause_key])),
            document.model_dump(mode="json"),
            engine_fingerprint=engine_digest(),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), Port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        slot = ExecutionSlot(work / "control/model-execution.lock").acquire()
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("ESG_", "PLATESG_", "PYTHONPATH"))
        }
        env.update(
            ESG_DATA_ROOT=str(work),
            ESG_CLAUSE_ROOT=str(work / "clauses"),
            ESG_DOCUMENT_BACKEND_URL=f"http://127.0.0.1:{server.server_address[1]}",
            ESG_EXECUTION_SLOT_PATH=str(slot.path),
            ESG_EXECUTION_SLOT_FD=str(slot.fd),
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
        )
        python = Path(
            os.getenv(
                "ESG_MODEL_PYTHON",
                str(Path.home() / "Desktop/model/.runtime/venv/bin/python"),
            )
        )
        try:
            result = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "esg_extraction.pipeline.worker",
                    state["run_id"],
                ],
                env=env,
                cwd=work,
                pass_fds=(slot.fd,),
                capture_output=True,
                text=True,
                timeout=45,
            )
            if result.returncode:
                raise RuntimeError(result.stderr[-3000:])
            events = [
                json.loads(line[len(EVENT_PREFIX) :])
                for line in result.stdout.splitlines()
                if line.startswith(EVENT_PREFIX)
            ]
            assert len([e for e in events if e.get("kind") == "result"]) == 1, events
            terminal = store.get(state["run_id"])
            assert terminal["status"] == "completed", terminal
            assert (
                terminal["summary"]["model_calls"] == 0
                and terminal["summary"]["source_facts"] == 0
            )
            output = {
                "scope": "Installed current worker, explicit synthetic empty document port, no actual measurement or model inference in this smoke check.",
                "cwd_outside_checkout": True,
                "source_pythonpath": False,
                "inherited_model_slot": True,
                "native_execution": terminal["status"],
                "facts": 0,
                "model_calls": 0,
                "sealed_export": bool(terminal.get("delivery", {}).get("export_id")),
                "source_single_clause_only": True,
                "named_selection_required": False,
                "live_data_touched": False,
                "engine_digest": engine_digest(),
            }
            (ROOT / "docs/verification/installed-worker.json").write_text(
                json.dumps(output, ensure_ascii=False, indent=2) + "\n"
            )
            print(json.dumps(output))
        finally:
            slot.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
