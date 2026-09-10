from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

import requests

from esg_v2.config import Settings
from esg_v2.contracts import OcrRunRequest
from esg_v2.ocr.client import PaddleOcrVlClient
from esg_v2.ocr.local_worker import EVENT_PREFIX
from esg_v2.ocr.loopback import loopback_proxy_bypass_environment, require_loopback_url
from esg_v2.ocr.resources import HttpOcrResourceReader, LocalOcrResourceReader, OcrResourceReader
from esg_v2.utils.sanitization import sanitize_remote_url


LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, Any]], None]
ConcreteProvider = Literal["local_paddleocr", "paddle_api"]

_LOCAL_RUNTIME_PROBE = r"""
import json
import sys
from pathlib import Path

import mlx_vlm
import paddle
import paddleocr
from paddleocr import PaddleOCRVL
from PIL import Image

payload = {
    "paddle": paddle.__version__,
    "paddleocr": getattr(paddleocr, "__version__", "unknown"),
    "mlx_vlm": getattr(mlx_vlm, "__version__", "unknown"),
}
model_root = Path(sys.argv[1])
if (model_root / "config.json").is_file() and any(model_root.glob("*.safetensors")):
    try:
        from mlx_vlm.utils import get_model_and_args, load_config, load_processor
        from safetensors import safe_open

        config = load_config(model_root)
        architecture, normalized_type = get_model_and_args(config, model_path=model_root)
        processor = load_processor(model_root, add_detokenizer=False)
        tensor_count = 0
        for weight_path in model_root.glob("*.safetensors"):
            with safe_open(str(weight_path), framework="np") as weights:
                tensor_count += len(list(weights.keys()))
        payload.update(
            {
                "mlx_model_type": normalized_type,
                "mlx_model_module": architecture.__name__,
                "mlx_processor": type(processor).__name__,
                "tensor_count": tensor_count,
            }
        )
    except Exception as exc:
        payload["model_probe_error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(payload))
"""


def _pipeline_model_cache_status(cache_root: Path) -> dict[str, bool]:
    model_names = ("PP-DocLayoutV3", "PP-LCNet_x1_0_doc_ori", "UVDoc")
    status = {name: False for name in model_names}
    if not cache_root.is_dir():
        return status
    for name in model_names:
        for model_dir in cache_root.rglob(name):
            if not model_dir.is_dir():
                continue
            has_config = any(
                (model_dir / file_name).is_file()
                for file_name in ("inference.json", "inference.yml", "config.json")
            )
            has_weights = any(
                path.is_file()
                and path.stat().st_size > 0
                and path.suffix.lower() in {".pdiparams", ".safetensors", ".onnx"}
                for path in model_dir.iterdir()
            )
            if has_config and has_weights:
                status[name] = True
                break
    return status


@dataclass(frozen=True)
class OcrProviderHealth:
    provider: str
    available: bool
    status: Literal["ready", "ready_with_downloads", "unavailable"]
    reason_codes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_manifest_dict(self) -> dict[str, Any]:
        payload = self.to_dict()
        details = dict(payload.get("details") or {})
        for key in ("runtime_python", "model_path", "pipeline_cache", "vlm_server_url"):
            details.pop(key, None)
        payload["details"] = details
        return payload


@dataclass(frozen=True)
class OcrProviderExecution:
    provider: ConcreteProvider
    job_id: str | None
    result_json_url: str | None
    jsonl_text: str
    submit_response: dict[str, Any]
    poll_events: list[dict[str, Any]]
    resource_reader: OcrResourceReader
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OcrProviderSelection:
    requested_provider: str
    execution: OcrProviderExecution
    attempts: list[dict[str, Any]]
    fallback_reason: str | None = None


class OcrProviderUnavailableError(RuntimeError):
    pass


class PaddleApiOcrProvider:
    name: ConcreteProvider = "paddle_api"

    def __init__(self, settings: Settings):
        self.settings = settings

    def health(self, *, request_token: str | None = None) -> OcrProviderHealth:
        configured = bool(request_token or self.settings.paddle_token)
        return OcrProviderHealth(
            provider=self.name,
            available=configured,
            status="ready" if configured else "unavailable",
            reason_codes=[] if configured else ["api_token_missing"],
            details={
                "job_url": sanitize_remote_url(self.settings.paddle_job_url),
                "environment_token_configured": bool(self.settings.paddle_token),
                "request_token_supported": True,
            },
        )

    def execute(
        self,
        request: OcrRunRequest,
        *,
        input_path: Path | None,
        expected_page_count: int = 0,
        progress_callback: ProgressFn | None = None,
        log: LogFn | None,
    ) -> OcrProviderExecution:
        token = request.token or self.settings.paddle_token
        if not token:
            raise OcrProviderUnavailableError(
                "PaddleOCR-VL API token is missing. Provide it in the UI or PADDLEOCR_VL_API_TOKEN."
            )
        client = PaddleOcrVlClient(
            job_url=self.settings.paddle_job_url,
            token=token,
            timeout_seconds=self.settings.request_timeout_seconds,
            trust_environment_proxy=self.settings.paddle_trust_environment_proxy,
        )
        _log(log, "Submitting PaddleOCR-VL API job")
        submit_response = client.submit(
            model=request.model,
            optional_payload=request.optional_payload,
            file_path=str(input_path) if input_path else None,
            file_url=request.file_url,
        )
        job_id = str(submit_response["data"]["jobId"])
        _log(log, f"API job submitted: {job_id}")
        poll_events: list[dict[str, Any]] = []
        result_json_url = ""
        while True:
            job_result = client.get_job(job_id)
            poll_events.append(job_result)
            data = job_result.get("data") or {}
            state = data.get("state")
            if state in {"pending", "running"}:
                extract_progress = data.get("extractProgress") or {}
                total = extract_progress.get("totalPages") or expected_page_count
                extracted = extract_progress.get("extractedPages") or 0
                _notify_progress(
                    progress_callback,
                    {
                        "provider": self.name,
                        "state": state,
                        "extractedPages": int(extracted),
                        "totalPages": int(total or 0),
                        "message": f"API OCR {state}: {extracted}/{total or '?'} pages",
                    },
                )
                _log(log, f"API OCR {state}: {extracted}/{total} pages" if total is not None else f"API OCR {state}")
                time.sleep(request.poll_interval_seconds)
                continue
            if state == "done":
                result_json_url = str(data["resultUrl"]["jsonUrl"])
                extracted = int((data.get("extractProgress") or {}).get("extractedPages") or expected_page_count)
                _notify_progress(
                    progress_callback,
                    {
                        "provider": self.name,
                        "state": "done",
                        "extractedPages": extracted,
                        "totalPages": int(expected_page_count or extracted),
                        "message": f"API OCR done: {extracted} pages",
                    },
                )
                _log(log, f"API OCR done: {extracted} pages")
                break
            if state == "failed":
                raise RuntimeError(str(data.get("errorMsg") or "PaddleOCR-VL API job failed"))
            raise RuntimeError(f"Unexpected PaddleOCR-VL API job state: {state}")
        return OcrProviderExecution(
            provider=self.name,
            job_id=job_id,
            result_json_url=result_json_url,
            jsonl_text=client.download_text(result_json_url),
            submit_response=submit_response,
            poll_events=poll_events,
            resource_reader=HttpOcrResourceReader(client),
            metadata={"transport": "ai-studio-job-api", "model": request.model},
        )


class _MlxServerManager:
    _lock = threading.Lock()
    _process: subprocess.Popen[str] | None = None
    _log_handle: Any = None

    @classmethod
    def is_running(cls, server_url: str) -> bool:
        require_loopback_url(server_url)
        try:
            with requests.Session() as session:
                # Requests can inherit macOS system proxies even when the shell has
                # no proxy variables. A managed loopback model must never leave the
                # host, including during its readiness probe.
                session.trust_env = False
                response = session.get(
                    server_url.rstrip("/") + "/v1/models",
                    timeout=1.5,
                )
                return response.status_code < 400
        except requests.RequestException:
            return False

    @classmethod
    def ensure_running(cls, settings: Settings, log: LogFn | None) -> None:
        if cls.is_running(settings.local_paddleocr_vlm_server_url):
            cls._verify_openai_transport(settings)
            return
        if not settings.local_paddleocr_autostart_vlm_server:
            raise OcrProviderUnavailableError(
                f"MLX-VLM server is not running at {settings.local_paddleocr_vlm_server_url}."
            )
        parsed = urlparse(settings.local_paddleocr_vlm_server_url)
        host = require_loopback_url(settings.local_paddleocr_vlm_server_url)
        port = parsed.port or 8111
        executable = settings.local_paddleocr_runtime_python.parent / "mlx_vlm.server"
        if not executable.is_file():
            raise OcrProviderUnavailableError(f"mlx_vlm.server executable not found: {executable}")
        with cls._lock:
            if cls.is_running(settings.local_paddleocr_vlm_server_url):
                cls._verify_openai_transport(settings)
                return
            if cls._process and cls._process.poll() is None:
                process = cls._process
            else:
                log_dir = settings.upload_root.parent / "runtime"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / "paddleocr-mlx-vlm-server.log"
                cls._log_handle = log_path.open("a", encoding="utf-8")
                env = loopback_proxy_bypass_environment(
                    os.environ,
                    settings.local_paddleocr_vlm_server_url,
                )
                env.setdefault("HF_HUB_OFFLINE", "1")
                env.setdefault("TRANSFORMERS_OFFLINE", "1")
                process = subprocess.Popen(
                    [str(executable), "--host", host, "--port", str(port), "--log-level", "WARNING"],
                    stdout=cls._log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )
                cls._process = process
                _log(log, f"Starting managed MLX-VLM server on {host}:{port}")
        deadline = time.monotonic() + settings.local_paddleocr_server_start_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"MLX-VLM server exited during startup with code {process.returncode}")
            if cls.is_running(settings.local_paddleocr_vlm_server_url):
                try:
                    cls._verify_openai_transport(settings)
                except Exception:
                    cls.shutdown()
                    raise
                _log(log, "Managed MLX-VLM server is ready")
                return
            time.sleep(0.5)
        cls.shutdown()
        raise TimeoutError("Timed out waiting for the local MLX-VLM server to become ready")

    @classmethod
    def _verify_openai_transport(cls, settings: Settings) -> None:
        probe_script = (
            "import sys;"
            "from openai import OpenAI;"
            "client=OpenAI(base_url=sys.argv[1],api_key='local',max_retries=0,timeout=5);"
            "client.models.list();"
            "client.close();"
            "print('ok')"
        )
        environment = loopback_proxy_bypass_environment(
            os.environ,
            settings.local_paddleocr_vlm_server_url,
        )
        try:
            probe = subprocess.run(
                [
                    str(settings.local_paddleocr_runtime_python),
                    "-c",
                    probe_script,
                    settings.local_paddleocr_vlm_server_url,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OcrProviderUnavailableError(
                f"MLX-VLM OpenAI transport preflight failed: {type(exc).__name__}: {exc}"
            ) from exc
        if probe.returncode != 0 or probe.stdout.strip().splitlines()[-1:] != ["ok"]:
            error = (probe.stderr or probe.stdout or "unknown transport error").strip()[-1200:]
            raise OcrProviderUnavailableError(
                f"MLX-VLM OpenAI transport preflight failed: {error}"
            )

    @classmethod
    def shutdown(cls) -> None:
        with cls._lock:
            process = cls._process
            cls._process = None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            if cls._log_handle:
                cls._log_handle.close()
                cls._log_handle = None


class LocalPaddleOcrProvider:
    name: ConcreteProvider = "local_paddleocr"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._health_cache: tuple[float, OcrProviderHealth] | None = None
        self._health_lock = threading.Lock()

    def health(self) -> OcrProviderHealth:
        with self._health_lock:
            if self._health_cache and time.monotonic() - self._health_cache[0] < 10:
                return self._health_cache[1]
            result = self._probe_health()
            self._health_cache = (time.monotonic(), result)
            return result

    def _probe_health(self) -> OcrProviderHealth:
        runtime = self.settings.local_paddleocr_runtime_python
        model_root = self.settings.local_paddleocr_model_path
        reason_codes: list[str] = []
        runtime_versions: dict[str, Any] = {}
        if not runtime.is_file():
            reason_codes.append("runtime_python_missing")
        else:
            try:
                probe_environment = os.environ.copy()
                probe_environment.setdefault("HF_HUB_OFFLINE", "1")
                probe_environment.setdefault("TRANSFORMERS_OFFLINE", "1")
                probe = subprocess.run(
                    [
                        str(runtime),
                        "-c",
                        _LOCAL_RUNTIME_PROBE,
                        str(model_root),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=probe_environment,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                reason_codes.append("runtime_import_failed")
                runtime_versions["error"] = str(exc)
            else:
                if probe.returncode != 0:
                    reason_codes.append("runtime_import_failed")
                    runtime_versions["error"] = (probe.stderr or probe.stdout).strip()[-1000:]
                else:
                    try:
                        runtime_versions = json.loads(probe.stdout.strip().splitlines()[-1])
                    except (json.JSONDecodeError, IndexError):
                        reason_codes.append("runtime_probe_invalid")

        server_executable = runtime.parent / "mlx_vlm.server"
        if self.settings.local_paddleocr_vlm_backend == "mlx-vlm-server" and not server_executable.is_file():
            reason_codes.append("vlm_server_executable_missing")

        config_exists = (model_root / "config.json").is_file()
        weight_files = [
            path
            for path in model_root.glob("*.safetensors")
            if path.is_file() and path.stat().st_size > 0
        ]
        if not model_root.is_dir() or not config_exists or not weight_files:
            reason_codes.append("model_weights_missing")
        elif runtime_versions.get("model_probe_error"):
            reason_codes.append("mlx_model_probe_failed")
        elif runtime_versions.get("mlx_model_type") != "paddleocr_vl":
            reason_codes.append("mlx_model_unsupported")
        elif not runtime_versions.get("mlx_processor") or not runtime_versions.get("tensor_count"):
            reason_codes.append("model_weights_unreadable")

        pipeline_cache = self.settings.local_paddleocr_pipeline_cache
        cached_pipeline_files = (
            sum(1 for path in pipeline_cache.rglob("*") if path.is_file())
            if pipeline_cache.is_dir()
            else 0
        )
        pipeline_models = _pipeline_model_cache_status(pipeline_cache)
        required_pipeline_models = {"PP-DocLayoutV3": pipeline_models["PP-DocLayoutV3"]}
        optional_pipeline_models = {
            "PP-LCNet_x1_0_doc_ori": pipeline_models["PP-LCNet_x1_0_doc_ori"],
            "UVDoc": pipeline_models["UVDoc"],
        }
        available = not reason_codes
        status: Literal["ready", "ready_with_downloads", "unavailable"]
        if not available:
            status = "unavailable"
        elif all(required_pipeline_models.values()):
            status = "ready"
        else:
            status = "ready_with_downloads"
        return OcrProviderHealth(
            provider=self.name,
            available=available,
            status=status,
            reason_codes=reason_codes,
            details={
                "runtime_python": str(runtime),
                "runtime_versions": runtime_versions,
                "vlm_server_executable_present": server_executable.is_file(),
                "model_path": str(model_root),
                "model_config_present": config_exists,
                "model_weight_files": len(weight_files),
                "model_size_bytes": sum(path.stat().st_size for path in weight_files),
                "mlx_model_compatible": runtime_versions.get("mlx_model_type") == "paddleocr_vl",
                "processor_ready": bool(runtime_versions.get("mlx_processor")),
                "model_tensor_count": int(runtime_versions.get("tensor_count") or 0),
                "pipeline_cache": str(pipeline_cache),
                "pipeline_cache_files": cached_pipeline_files,
                "required_pipeline_models": required_pipeline_models,
                "optional_pipeline_models": optional_pipeline_models,
                "vlm_backend": self.settings.local_paddleocr_vlm_backend,
                "vlm_server_url": self.settings.local_paddleocr_vlm_server_url,
                "vlm_server_running": _MlxServerManager.is_running(self.settings.local_paddleocr_vlm_server_url),
                "loopback_proxy_bypass": True,
            },
        )

    def execute(
        self,
        request: OcrRunRequest,
        *,
        input_path: Path | None,
        provider_root: Path,
        run_id: str,
        expected_page_count: int = 0,
        progress_callback: ProgressFn | None = None,
        log: LogFn | None,
    ) -> OcrProviderExecution:
        if input_path is None:
            raise OcrProviderUnavailableError("Local PaddleOCR-VL requires a locally available PDF file.")
        health = self.health()
        if not health.available:
            raise OcrProviderUnavailableError(
                "Local PaddleOCR-VL is not ready: " + ", ".join(health.reason_codes)
            )
        if self.settings.local_paddleocr_vlm_backend == "mlx-vlm-server":
            _MlxServerManager.ensure_running(self.settings, log)

        local_root = provider_root / "local-resources"
        local_root.mkdir(parents=True, exist_ok=True)
        result_path = local_root / "result.jsonl"
        config = {
            "run_id": run_id,
            "input_path": str(input_path),
            "asset_root": str(local_root),
            "result_path": str(result_path),
            "expected_page_count": int(expected_page_count),
            "heartbeat_seconds": self.settings.local_paddleocr_heartbeat_seconds,
            "options": request.optional_payload.model_dump(),
            "runtime": {
                "pipeline_version": self.settings.local_paddleocr_pipeline_version,
                "pipeline_cache": str(self.settings.local_paddleocr_pipeline_cache),
                "model_path": str(self.settings.local_paddleocr_model_path),
                "vlm_backend": self.settings.local_paddleocr_vlm_backend,
                "vlm_server_url": self.settings.local_paddleocr_vlm_server_url,
            },
        }
        with tempfile.TemporaryDirectory(prefix="esg-local-ocr-") as temp_dir:
            config_path = Path(temp_dir) / "request.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            command = [
                str(self.settings.local_paddleocr_runtime_python),
                str(Path(__file__).with_name("local_worker.py")),
                "--config",
                str(config_path),
            ]
            _log(log, "Starting local PaddleOCR-VL worker")
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=os.environ.copy(),
            )
            lines: queue.Queue[str | None] = queue.Queue()

            def read_stdout() -> None:
                assert process.stdout is not None
                for line in process.stdout:
                    lines.put(line)
                lines.put(None)

            threading.Thread(target=read_stdout, daemon=True).start()
            poll_events: list[dict[str, Any]] = []
            deadline = time.monotonic() + self.settings.local_paddleocr_job_timeout_seconds
            last_progress_at = time.monotonic()
            extracted_pages = 0
            stdout_closed = False
            while not stdout_closed or process.poll() is None:
                if time.monotonic() > deadline:
                    _terminate_subprocess(process)
                    raise TimeoutError("Local PaddleOCR-VL job exceeded its configured timeout")
                stalled_for = time.monotonic() - last_progress_at
                if stalled_for > self.settings.local_paddleocr_stall_timeout_seconds:
                    _terminate_subprocess(process)
                    raise TimeoutError(
                        "Local PaddleOCR-VL made no completed-page progress for "
                        f"{stalled_for:.0f}s at {extracted_pages}/{expected_page_count or '?'} pages"
                    )
                try:
                    line = lines.get(timeout=0.5)
                except queue.Empty:
                    continue
                if line is None:
                    stdout_closed = True
                    continue
                text = line.strip()
                if not text:
                    continue
                if text.startswith(EVENT_PREFIX):
                    event = json.loads(text.removeprefix(EVENT_PREFIX))
                    poll_events.append({"provider": self.name, "data": event})
                    current_pages = int(event.get("extractedPages") or 0)
                    if current_pages > extracted_pages:
                        extracted_pages = current_pages
                        last_progress_at = time.monotonic()
                    progress_event = {
                        "provider": self.name,
                        **event,
                        "extractedPages": current_pages,
                        "totalPages": int(event.get("totalPages") or expected_page_count or 0),
                    }
                    if event.get("state") == "running" and current_pages:
                        progress_event.setdefault(
                            "message",
                            f"Local OCR running: {current_pages}/{expected_page_count or '?'} pages",
                        )
                        _log(log, str(progress_event["message"]))
                    elif event.get("state") in {"initializing", "done"}:
                        _log(log, str(event.get("message") or f"Local OCR {event.get('state')}"))
                    _notify_progress(progress_callback, progress_event)
                elif "pytorch was not found" in text.lower():
                    _log(log, "Local OCR runtime: MLX backend active; PyTorch is not required")
                elif "warning" not in text.lower():
                    _log(log, f"Local OCR: {text[-500:]}")
            return_code = process.wait()
            if return_code != 0:
                failure = next(
                    (
                        str((event.get("data") or {}).get("error"))
                        for event in reversed(poll_events)
                        if (event.get("data") or {}).get("state") == "failed"
                    ),
                    f"worker exited with code {return_code}",
                )
                raise RuntimeError(f"Local PaddleOCR-VL failed: {failure}")
        if not result_path.is_file() or not result_path.stat().st_size:
            raise RuntimeError("Local PaddleOCR-VL completed without a result JSONL")
        return OcrProviderExecution(
            provider=self.name,
            job_id=f"local-{run_id}",
            result_json_url=None,
            jsonl_text=result_path.read_text(encoding="utf-8"),
            submit_response={
                "provider": self.name,
                "data": {"jobId": f"local-{run_id}", "state": "accepted"},
                "runtime": {
                    "pipeline_version": self.settings.local_paddleocr_pipeline_version,
                    "vlm_backend": self.settings.local_paddleocr_vlm_backend,
                },
            },
            poll_events=poll_events,
            resource_reader=LocalOcrResourceReader(local_root),
            metadata={
                "transport": "isolated-local-worker",
                "model": self.settings.local_paddleocr_model_path.name,
                "pipeline_version": self.settings.local_paddleocr_pipeline_version,
                "vlm_backend": self.settings.local_paddleocr_vlm_backend,
                "loopback_proxy_bypass": True,
                "health_at_start": health.to_manifest_dict(),
            },
        )


class OcrProviderRouter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.local = LocalPaddleOcrProvider(settings)
        self.api = PaddleApiOcrProvider(settings)

    def status(self) -> dict[str, Any]:
        return {
            "default_provider": self.settings.default_ocr_provider,
            "providers": {
                self.local.name: self.local.health().to_dict(),
                self.api.name: self.api.health().to_dict(),
            },
        }

    def execute(
        self,
        request: OcrRunRequest,
        *,
        input_path: Path | None,
        provider_root: Path,
        run_id: str,
        expected_page_count: int = 0,
        progress_callback: ProgressFn | None = None,
        log: LogFn | None,
    ) -> OcrProviderSelection:
        requested = request.ocr_provider
        attempts: list[dict[str, Any]] = []
        if requested == "paddle_api":
            execution = self.api.execute(
                request,
                input_path=input_path,
                expected_page_count=expected_page_count,
                progress_callback=progress_callback,
                log=log,
            )
            attempts.append({"provider": self.api.name, "outcome": "selected"})
            return OcrProviderSelection(requested, execution, attempts)
        if requested == "local_paddleocr":
            execution = self.local.execute(
                request,
                input_path=input_path,
                provider_root=provider_root,
                run_id=run_id,
                expected_page_count=expected_page_count,
                progress_callback=progress_callback,
                log=log,
            )
            attempts.append({"provider": self.local.name, "outcome": "selected"})
            return OcrProviderSelection(requested, execution, attempts)
        if requested != "local_first":
            raise ValueError(f"Unsupported OCR provider mode: {requested}")

        fallback_reason: str | None = None
        local_health = self.local.health()
        if input_path is None:
            fallback_reason = "local_source_unavailable"
            attempts.append({"provider": self.local.name, "outcome": "unavailable", "reason": fallback_reason})
        elif not local_health.available:
            fallback_reason = ",".join(local_health.reason_codes) or "local_provider_unavailable"
            attempts.append(
                {
                    "provider": self.local.name,
                    "outcome": "unavailable",
                    "reason": fallback_reason,
                    "health": local_health.to_manifest_dict(),
                }
            )
        else:
            try:
                execution = self.local.execute(
                    request,
                    input_path=input_path,
                    provider_root=provider_root,
                    run_id=run_id,
                    expected_page_count=expected_page_count,
                    progress_callback=progress_callback,
                    log=log,
                )
                attempts.append({"provider": self.local.name, "outcome": "selected"})
                return OcrProviderSelection(requested, execution, attempts)
            except Exception as exc:
                fallback_reason = f"local_execution_failed:{type(exc).__name__}"
                attempts.append(
                    {
                        "provider": self.local.name,
                        "outcome": "failed",
                        "reason": fallback_reason,
                        "error": _portable_error(str(exc), self.settings),
                    }
                )
                if not request.allow_api_fallback:
                    raise

        if not request.allow_api_fallback:
            raise OcrProviderUnavailableError(
                f"Local-first OCR could not use the local provider ({fallback_reason}); API fallback is disabled."
            )
        _log(log, f"Local OCR unavailable; falling back to PaddleOCR-VL API ({fallback_reason})")
        try:
            execution = self.api.execute(
                request,
                input_path=input_path,
                expected_page_count=expected_page_count,
                progress_callback=progress_callback,
                log=log,
            )
        except OcrProviderUnavailableError as exc:
            raise OcrProviderUnavailableError(
                f"Local OCR unavailable ({fallback_reason}) and API fallback is not configured: {exc}"
            ) from exc
        attempts.append({"provider": self.api.name, "outcome": "selected", "fallback_from": self.local.name})
        return OcrProviderSelection(requested, execution, attempts, fallback_reason)


def _notify_progress(callback: ProgressFn | None, event: dict[str, Any]) -> None:
    if callback:
        callback(event)


def _terminate_subprocess(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _log(log: LogFn | None, message: str) -> None:
    if log:
        log(message)


def _portable_error(message: str, settings: Settings) -> str:
    replacements = {
        str(Path.home()): "~",
        str(settings.local_paddleocr_runtime_python): "<local-runtime-python>",
        str(settings.local_paddleocr_model_path): "<local-model-path>",
        str(settings.local_paddleocr_pipeline_cache): "<local-pipeline-cache>",
    }
    value = message
    for source, replacement in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if source:
            value = value.replace(source, replacement)
    return value[-2000:]


atexit.register(_MlxServerManager.shutdown)
