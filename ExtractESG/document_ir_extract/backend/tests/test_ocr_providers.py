from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from esg_v2.config import Settings
from esg_v2.contracts import OcrRunRequest
from esg_v2.ocr.providers import (
    OcrProviderExecution,
    OcrProviderHealth,
    OcrProviderRouter,
    OcrProviderSelection,
    OcrProviderUnavailableError,
    LocalPaddleOcrProvider,
    _MlxServerManager,
    _pipeline_model_cache_status,
)
from esg_v2.ocr.local_worker import _page_payload
from esg_v2.ocr.loopback import loopback_proxy_bypass_environment, require_loopback_url
from esg_v2.ocr.resources import LocalOcrResourceReader
from esg_v2.workflows.ocr_workflow import OcrWorkflow


def _settings(tmp_path: Path, *, token: str | None = "api-token") -> Settings:
    runtime = tmp_path / "runtime" / "bin" / "python"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("", encoding="utf-8")
    return Settings(
        output_root=tmp_path / "ocr-output",
        document_ir_output_root=tmp_path / "ir-output",
        upload_root=tmp_path / "uploads",
        paddle_token=token,
        local_paddleocr_runtime_python=runtime,
        local_paddleocr_model_path=tmp_path / "model",
        local_paddleocr_pipeline_cache=tmp_path / "pipeline-cache",
    )


def _execution(tmp_path: Path, provider: str) -> OcrProviderExecution:
    resources = tmp_path / f"{provider}-resources"
    resources.mkdir()
    return OcrProviderExecution(
        provider=provider,  # type: ignore[arg-type]
        job_id=f"{provider}-job",
        result_json_url=None,
        jsonl_text=json.dumps({"result": {"layoutParsingResults": []}}) + "\n",
        submit_response={"provider": provider},
        poll_events=[],
        resource_reader=LocalOcrResourceReader(resources),
    )


def _install_fake_local_runtime(settings: Settings) -> None:
    settings.local_paddleocr_runtime_python.write_text("#!/bin/sh\n", encoding="utf-8")
    settings.local_paddleocr_runtime_python.chmod(0o755)
    (settings.local_paddleocr_runtime_python.parent / "mlx_vlm.server").write_text(
        "#!/bin/sh\n",
        encoding="utf-8",
    )
    settings.local_paddleocr_model_path.mkdir(parents=True)
    (settings.local_paddleocr_model_path / "config.json").write_text(
        json.dumps({"model_type": "paddleocr_vl"}),
        encoding="utf-8",
    )
    (settings.local_paddleocr_model_path / "model.safetensors").write_bytes(b"weights")


def test_pipeline_cache_status_tracks_required_and_optional_models(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    (cache / "official_models" / "PP-DocLayoutV3").mkdir(parents=True)
    (cache / "official_models" / "PP-DocLayoutV3" / "inference.json").write_text("{}")

    assert _pipeline_model_cache_status(cache)["PP-DocLayoutV3"] is False

    (cache / "official_models" / "PP-DocLayoutV3" / "inference.pdiparams").write_bytes(b"weights")

    status = _pipeline_model_cache_status(cache)

    assert status == {
        "PP-DocLayoutV3": True,
        "PP-LCNet_x1_0_doc_ori": False,
        "UVDoc": False,
    }


def test_local_health_requires_static_mlx_model_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _install_fake_local_runtime(settings)

    def fake_probe(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "paddle": "3.2.1",
                    "paddleocr": "3.7.0",
                    "mlx_vlm": "0.6.17",
                    "mlx_model_type": "paddleocr_vl",
                    "mlx_processor": "PaddleOCRVLProcessor",
                    "tensor_count": 620,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("esg_v2.ocr.providers.subprocess.run", fake_probe)
    monkeypatch.setattr("esg_v2.ocr.providers._MlxServerManager.is_running", lambda url: False)

    health = LocalPaddleOcrProvider(settings).health()

    assert health.available is True
    assert health.status == "ready_with_downloads"
    assert health.details["mlx_model_compatible"] is True
    assert health.details["processor_ready"] is True
    assert health.details["model_tensor_count"] == 620
    assert health.details["required_pipeline_models"] == {"PP-DocLayoutV3": False}

    layout_cache = settings.local_paddleocr_pipeline_cache / "official_models" / "PP-DocLayoutV3"
    layout_cache.mkdir(parents=True)
    (layout_cache / "inference.json").write_text("{}", encoding="utf-8")
    (layout_cache / "inference.pdiparams").write_bytes(b"weights")
    assert LocalPaddleOcrProvider(settings).health().status == "ready"


def test_local_health_rejects_failed_model_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _install_fake_local_runtime(settings)
    monkeypatch.setattr(
        "esg_v2.ocr.providers.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "paddle": "3.2.1",
                    "paddleocr": "3.7.0",
                    "mlx_vlm": "0.6.17",
                    "model_probe_error": "SafetensorError: incomplete metadata",
                }
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr("esg_v2.ocr.providers._MlxServerManager.is_running", lambda url: False)

    health = LocalPaddleOcrProvider(settings).health()

    assert health.available is False
    assert health.status == "unavailable"
    assert health.reason_codes == ["mlx_model_probe_failed"]


def test_local_resource_reader_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    root.mkdir()
    (root / "inside.bin").write_bytes(b"inside")
    reader = LocalOcrResourceReader(root)

    assert reader.read_bytes("local-resource://inside.bin") == b"inside"
    with pytest.raises(ValueError, match="escapes provider root"):
        reader.read_bytes("local-resource://../outside.bin")


def test_loopback_environment_bypasses_macos_system_proxy_without_removing_it() -> None:
    environment = {
        "HTTP_PROXY": "http://127.0.0.1:7897",
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "NO_PROXY": "example.test",
    }

    result = loopback_proxy_bypass_environment(environment, "http://127.0.0.1:8111/")

    assert result["HTTP_PROXY"] == environment["HTTP_PROXY"]
    assert result["HTTPS_PROXY"] == environment["HTTPS_PROXY"]
    assert result["NO_PROXY"] == result["no_proxy"]
    assert set(result["NO_PROXY"].split(",")) == {
        "example.test",
        "127.0.0.1",
        "localhost",
        "::1",
    }


def test_local_model_server_rejects_non_loopback_url() -> None:
    with pytest.raises(ValueError, match="loopback host"):
        require_loopback_url("https://models.example.test/v1")


def test_local_model_server_health_probe_ignores_environment_proxy(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

    class FakeSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url: str, *, timeout: float):
            calls["trust_env"] = self.trust_env
            calls["url"] = url
            calls["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr("esg_v2.ocr.providers.requests.Session", FakeSession)

    assert _MlxServerManager.is_running("http://127.0.0.1:8111/") is True
    assert calls == {
        "trust_env": False,
        "url": "http://127.0.0.1:8111/v1/models",
        "timeout": 1.5,
    }


def test_local_worker_adapts_native_result_to_api_compatible_page_envelope(tmp_path: Path) -> None:
    class FakeItem:
        json = {
            "res": {
                "input_path": "/private/report.pdf",
                "page_index": 0,
                "width": 100,
                "height": 200,
                "parsing_res_list": [{"block_label": "text", "block_content": "hello"}],
            }
        }
        img = {"layout_det_res": Image.new("RGB", (10, 10), "white")}

        @staticmethod
        def _to_markdown(**kwargs):
            return {
                "markdown_texts": "![chart](imgs/chart.png)\n\nhello",
                "markdown_images": {"imgs/chart.png": Image.new("RGB", (5, 5), "black")},
            }

    payload = _page_payload(FakeItem(), page_index=0, asset_root=tmp_path)

    assert payload["prunedResult"]["width"] == 100
    assert "input_path" not in payload["prunedResult"]
    assert "page_index" not in payload["prunedResult"]
    assert payload["markdown"]["images"]["imgs/chart.png"].startswith("local-resource://")
    assert payload["outputImages"]["layout_det_res"].startswith("local-resource://")
    assert (tmp_path / "markdown" / "page-0001" / "image-0001-chart.png").is_file()
    assert (tmp_path / "layouts" / "layout_det_res-0001.jpg").is_file()


def test_local_first_selects_local_without_touching_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router = OcrProviderRouter(_settings(tmp_path))
    monkeypatch.setattr(
        router.local,
        "health",
        lambda: OcrProviderHealth("local_paddleocr", True, "ready"),
    )
    monkeypatch.setattr(router.local, "execute", lambda *args, **kwargs: _execution(tmp_path, "local_paddleocr"))
    monkeypatch.setattr(
        router.api,
        "execute",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API must not be called")),
    )

    selected = router.execute(
        OcrRunRequest(file_path="report.pdf"),
        input_path=tmp_path / "report.pdf",
        provider_root=tmp_path / "provider",
        run_id="ocr-20260828T000000Z-000000000001",
        log=None,
    )

    assert selected.execution.provider == "local_paddleocr"
    assert selected.fallback_reason is None
    assert selected.attempts == [{"provider": "local_paddleocr", "outcome": "selected"}]


def test_local_first_falls_back_to_api_when_weights_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = OcrProviderRouter(_settings(tmp_path))
    monkeypatch.setattr(
        router.local,
        "health",
        lambda: OcrProviderHealth(
            "local_paddleocr",
            False,
            "unavailable",
            ["model_weights_missing"],
        ),
    )
    monkeypatch.setattr(router.api, "execute", lambda *args, **kwargs: _execution(tmp_path, "paddle_api"))

    selected = router.execute(
        OcrRunRequest(file_path="report.pdf"),
        input_path=tmp_path / "report.pdf",
        provider_root=tmp_path / "provider",
        run_id="ocr-20260828T000000Z-000000000002",
        log=None,
    )

    assert selected.execution.provider == "paddle_api"
    assert selected.fallback_reason == "model_weights_missing"
    assert [item["outcome"] for item in selected.attempts] == ["unavailable", "selected"]


def test_local_first_without_api_fallback_fails_before_api_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = OcrProviderRouter(_settings(tmp_path))
    monkeypatch.setattr(
        router.local,
        "health",
        lambda: OcrProviderHealth(
            "local_paddleocr",
            False,
            "unavailable",
            ["model_weights_missing"],
        ),
    )
    monkeypatch.setattr(
        router.api,
        "execute",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API must not be called")),
    )

    with pytest.raises(OcrProviderUnavailableError, match="API fallback is disabled"):
        router.execute(
            OcrRunRequest(file_path="report.pdf", allow_api_fallback=False),
            input_path=tmp_path / "report.pdf",
            provider_root=tmp_path / "provider",
            run_id="ocr-20260828T000000Z-000000000003",
            log=None,
        )


def test_ocr_workflow_writes_local_result_through_the_existing_package_contract(tmp_path: Path) -> None:
    source = tmp_path / "report.pdf"
    pdf = PdfWriter()
    pdf.add_blank_page(width=595, height=842)
    with source.open("wb") as handle:
        pdf.write(handle)
    resources = tmp_path / "resources"
    resources.mkdir()
    page = {
        "prunedResult": {
            "width": 595,
            "height": 842,
            "model_settings": {},
            "parsing_res_list": [],
        },
        "markdown": {"text": "local page", "images": {}},
        "outputImages": {},
    }
    execution = OcrProviderExecution(
        provider="local_paddleocr",
        job_id="local-job",
        result_json_url=None,
        jsonl_text=json.dumps({"result": {"layoutParsingResults": [page]}}) + "\n",
        submit_response={"provider": "local_paddleocr", "data": {"jobId": "local-job"}},
        poll_events=[{"provider": "local_paddleocr", "data": {"state": "done"}}],
        resource_reader=LocalOcrResourceReader(resources),
        metadata={"transport": "isolated-local-worker", "model": "PaddleOCR-VL-1.6"},
    )

    routed: dict[str, object] = {}

    class FakeRouter:
        @staticmethod
        def execute(*args, **kwargs):
            routed.update(kwargs)
            kwargs["progress_callback"](
                {
                    "state": "running",
                    "extractedPages": 1,
                    "totalPages": kwargs["expected_page_count"],
                }
            )
            return OcrProviderSelection(
                requested_provider="local_first",
                execution=execution,
                attempts=[{"provider": "local_paddleocr", "outcome": "selected"}],
            )

    settings = _settings(tmp_path)
    progress_events: list[dict[str, object]] = []
    result = OcrWorkflow(settings, provider_router=FakeRouter()).run(
        OcrRunRequest(file_path=str(source)),
        run_id="ocr-20260828T000000Z-000000000004",
        progress=progress_events.append,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert result.provider == "local_paddleocr"
    assert result.result_json_url is None
    assert result.page_count == 1
    assert routed["expected_page_count"] == 1
    assert progress_events == [{"state": "running", "extractedPages": 1, "totalPages": 1}]
    assert manifest["ocr_provider"] == "local_paddleocr"
    assert manifest["provider_route"]["selected"] == "local_paddleocr"
    assert manifest["entrypoints"]["provider_result"] == "provider/result.jsonl"
    assert str(tmp_path) not in result.manifest_path.read_text(encoding="utf-8")
    assert (result.output_dir / "content" / "pages" / "page-0001.md").read_text() == "local page"
