from __future__ import annotations

import atexit
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from esg_v2.config import Settings
from esg_v2.models.contracts import CloudChatRequest, CloudChatResult
from esg_v2.models.local_nuextract_worker import RESULT_PREFIX
from esg_v2.models.provider_error import ModelProviderError


class LocalNuExtractError(ModelProviderError):
    pass


class _WorkerProcess:
    def __init__(self, runtime_python: Path, worker_script: Path, model_path: Path) -> None:
        self.runtime_python = runtime_python
        self.worker_script = worker_script
        self.model_path = model_path
        self.process: subprocess.Popen[str] | None = None
        self.output: queue.Queue[str] = queue.Queue()
        self.lock = threading.Lock()

    def request(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        with self.lock:
            for internal_attempt in range(2):
                try:
                    return self._request_once(payload, timeout=timeout)
                except (BrokenPipeError, EOFError, OSError, subprocess.SubprocessError) as exc:
                    self.stop()
                    if internal_attempt:
                        raise LocalNuExtractError(
                            f"Local NuExtract3 worker unavailable after restart: {exc}",
                            retryable=True,
                        ) from exc
            raise LocalNuExtractError("Local NuExtract3 worker failed without a response")

    def _request_once(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        process = self._ensure_started()
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + timeout
        expected_id = str(payload.get("request_id") or "")
        diagnostics: list[str] = []
        while time.monotonic() < deadline:
            if process.poll() is not None and self.output.empty():
                raise EOFError(
                    f"worker exited with code {process.returncode}; output={' | '.join(diagnostics[-5:])}"
                )
            try:
                line = self.output.get(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if not line.startswith(RESULT_PREFIX):
                diagnostics.append(line[:500])
                continue
            response = json.loads(line[len(RESULT_PREFIX):])
            if str(response.get("request_id") or "") != expected_id:
                diagnostics.append(f"ignored response for {response.get('request_id')}")
                continue
            return response
        self.stop()
        raise LocalNuExtractError(
            f"Local NuExtract3 worker timed out after {timeout:.0f}s",
            retryable=True,
        )

    def _ensure_started(self) -> subprocess.Popen[str]:
        if self.process is not None and self.process.poll() is None:
            return self.process
        if not self.runtime_python.exists():
            raise LocalNuExtractError(
                f"NuExtract runtime Python not found: {self.runtime_python}",
                retryable=False,
            )
        if not self.model_path.exists():
            raise LocalNuExtractError(
                f"NuExtract model path not found: {self.model_path}",
                retryable=False,
            )
        self.output = queue.Queue()
        self.process = subprocess.Popen(
            [
                str(self.runtime_python),
                str(self.worker_script),
                "--model",
                str(self.model_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read_output, args=(self.process,), daemon=True).start()
        return self.process

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self.output.put(line.rstrip("\n"))

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


_WORKERS: dict[tuple[str, str, str], _WorkerProcess] = {}
_WORKERS_LOCK = threading.Lock()


def _shared_worker(settings: Settings) -> _WorkerProcess:
    worker_script = Path(__file__).with_name("local_nuextract_worker.py").resolve()
    runtime_python = settings.nuextract_runtime_python.expanduser().absolute()
    model_path = settings.nuextract_model_path.expanduser().resolve()
    key = (
        str(runtime_python),
        str(worker_script),
        str(model_path),
    )
    with _WORKERS_LOCK:
        return _WORKERS.setdefault(
            key,
            _WorkerProcess(
                runtime_python,
                worker_script,
                model_path,
            ),
        )


def _shutdown_workers() -> None:
    with _WORKERS_LOCK:
        workers = list(_WORKERS.values())
        _WORKERS.clear()
    for worker in workers:
        worker.stop()


atexit.register(_shutdown_workers)


class LocalNuExtractAdapter:
    provider_name = "local_nuextract"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.worker = _shared_worker(settings)

    def chat_completions(self, request: CloudChatRequest) -> CloudChatResult:
        started = time.perf_counter()
        response = self.worker.request(
            {
                "request_id": request.request_id,
                "request": request.model_dump(mode="json"),
            },
            timeout=request.timeout_seconds or self.settings.nuextract_timeout_seconds,
        )
        if not response.get("ok"):
            raise LocalNuExtractError(
                f"Local NuExtract3 generation failed: {response.get('error') or 'unknown error'}",
                retryable=True,
            )
        text = str(response.get("text") or "")
        raw = {
            "id": request.request_id,
            "model": request.model_id,
            "provider": self.provider_name,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": response.get("usage") or {},
        }
        return CloudChatResult(
            request_id=request.request_id,
            provider=self.provider_name,
            model_id=request.model_id,
            raw_response=raw,
            usage=raw["usage"],
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def probe(self) -> dict[str, Any]:
        # Keep the venv symlink intact; resolving it bypasses pyvenv.cfg.
        runtime = self.settings.nuextract_runtime_python.expanduser().absolute()
        model = self.settings.nuextract_model_path.resolve()
        worker_script = Path(__file__).with_name("local_nuextract_worker.py").resolve()
        if not runtime.exists() or not model.exists():
            return {
                "available": False,
                "runtime_python": str(runtime),
                "runtime_exists": runtime.exists(),
                "model_path": str(model),
                "model_exists": model.exists(),
            }
        try:
            result = subprocess.run(
                [str(runtime), str(worker_script), "--model", str(model), "--probe"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            line = next(
                (item for item in reversed(result.stdout.splitlines()) if item.startswith(RESULT_PREFIX)),
                "",
            )
            payload = json.loads(line[len(RESULT_PREFIX):]) if line else {}
            return {
                "available": result.returncode == 0 and bool(payload.get("ok")),
                "runtime_python": str(runtime),
                **payload,
            }
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return {
                "available": False,
                "runtime_python": str(runtime),
                "model_path": str(model),
                "error": f"{type(exc).__name__}: {exc}",
            }
