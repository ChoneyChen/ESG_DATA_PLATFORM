from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import types

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    BoundingBox,
    DocumentIrBuildRequest,
    DocumentIrRepairRequest,
    ReviewPlan,
    ReviewRetryRequest,
    ReviewScopeItem,
    VlmReviewTask,
)
from esg_v2.document.model_runner import ModelRunner
from esg_v2.document.review_response_adapter import ReviewResponseAdapter
from esg_v2.models.contracts import CloudChatRequest
from esg_v2.models.local_nuextract_adapter import LocalNuExtractAdapter
from esg_v2.models.local_nuextract_registry import LocalNuExtractRegistry
from esg_v2.models.local_qwen_registry import LocalQwenRegistry
from esg_v2.models.local_nuextract_adapter import LocalQwenAdapter
from esg_v2.models.local_nuextract_worker import _generic_schema
from esg_v2.models.review_provider import create_review_provider


def _settings(tmp_path: Path) -> Settings:
    runtime = tmp_path / "venv" / "bin" / "python"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("", encoding="utf-8")
    model = tmp_path / "model"
    model.mkdir()
    return replace(
        Settings(),
        document_ir_output_root=tmp_path / "ir",
        nuextract_runtime_python=runtime,
        nuextract_model_path=model,
    )


def _task() -> VlmReviewTask:
    return VlmReviewTask(
        task_id="review-p0001-0001",
        task_type="table_structure_review",
        target_type="table",
        target_id="table-p0001-0001",
        page_index=0,
        bbox=BoundingBox(x0=0, y0=0, x1=10, y1=10),
        scope=[
            ReviewScopeItem(
                target_type="table",
                target_id="table-p0001-0001",
                blocking=True,
            )
        ],
        prompt_intent="Check the visible table.",
        review_plan=ReviewPlan(
            question="Is the table grid complete?",
            current_risk="Missing cells",
            allowed_operations=["confirm", "set_table_grid"],
        ),
    )


def test_review_requests_accept_parallel_provider_without_changing_qiniu_default() -> None:
    assert DocumentIrBuildRequest(ocr_run_id="ocr-test").review_provider == "qiniu"
    assert DocumentIrRepairRequest(
        parent_ir_run_id="ir-test",
        target_ids=["table-1"],
        reason_code="repair",
        requested_by="tester",
        review_provider="local_nuextract",
    ).review_provider == "local_nuextract"
    assert ReviewRetryRequest(
        requested_by="tester",
        review_provider="local_nuextract",
    ).review_provider == "local_nuextract"


def test_local_registry_declares_same_model_secondary_verification(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    adapter = LocalNuExtractAdapter(settings)
    adapter.probe = lambda: {"available": True}  # type: ignore[method-assign]
    registry = LocalNuExtractRegistry(settings, adapter)
    registry.refresh()

    assert registry.candidates("reviewer")[0].provider == "local_nuextract"
    assert registry.candidates("verifier", exclude_family="nuextract3") == []
    assert registry.verification_policy == "same_model_secondary_verification"


def test_local_adapter_normalizes_worker_result_to_cloud_contract(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    adapter = LocalNuExtractAdapter(settings)

    class FakeWorker:
        def request(self, payload, *, timeout):
            assert payload["request_id"] == "request-1"
            assert timeout == settings.nuextract_timeout_seconds
            return {
                "ok": True,
                "text": '{"verdict":"confirm"}<|im_end|>',
                "usage": {"completion_tokens": 12},
            }

    adapter.worker = FakeWorker()  # type: ignore[assignment]
    result = adapter.chat_completions(
        CloudChatRequest(
            request_id="request-1",
            model_id=LocalNuExtractRegistry.MODEL_ID,
            messages=[{"role": "user", "content": "review"}],
        )
    )

    assert result.provider == "local_nuextract"
    assert result.raw_response["choices"][0]["message"]["content"].startswith("{")
    assert result.usage["completion_tokens"] == 12


def test_model_runner_builds_same_contract_template_for_local_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    class Registry:
        verification_policy = "same_model_secondary_verification"

    runner = ModelRunner(
        settings,
        adapter=object(),  # type: ignore[arg-type]
        registry=Registry(),  # type: ignore[arg-type]
        response_adapter=ReviewResponseAdapter(),
        verification_policy="same_model_secondary_verification",
    )
    request = runner.build_request(
        _task(),
        LocalNuExtractRegistry.PROFILE,
        "reviewer",
        1,
        ["/tmp/page.png"],
        "visible context",
        output_tokens=3000,
    )

    assert request.metadata["provider"] == "local_nuextract"
    assert request.output_template is not None
    assert request.output_template["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert request.output_template["properties"]["verdict"]["enum"] == ["confirm", "propose_patch", "abstain"]
    assert request.metadata["schema_delivery"] == "prompt_schema_and_server_validation"
    branches = request.output_template["properties"]["patches"]["items"]["oneOf"]
    assert "set_table_grid" in {branch["properties"]["operation"]["const"] for branch in branches}
    assert request.messages[1]["content"][1]["image_url"]["url"] == "/tmp/page.png"


def test_qwen_review_requests_and_registry_keep_same_contract(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    bundle = create_review_provider(settings, "local_qwen")
    bundle.adapter.probe = lambda: {"available": True}
    status = bundle.registry.refresh()
    assert status["provider"] == "local_qwen"
    assert bundle.registry.candidates("reviewer")[0].model_id == LocalQwenRegistry.MODEL_ID
    assert bundle.verification_policy == "same_model_secondary_verification"
    assert bundle.independent_model_family is False
    assert DocumentIrBuildRequest(ocr_run_id="ocr-test", review_provider="local_qwen").review_provider == "local_qwen"


def test_qwen_adapter_has_own_path_timeout_and_preserves_truncation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    adapter = LocalQwenAdapter(settings)

    class FakeWorker:
        def request(self, payload, *, timeout):
            assert timeout == settings.qwen_timeout_seconds
            return {"ok": True, "text": "{}", "finish_reason": "length", "usage": {"completion_tokens": 100}}

    adapter.worker = FakeWorker()
    result = adapter.chat_completions(CloudChatRequest(request_id="offline", model_id=LocalQwenRegistry.MODEL_ID, messages=[]))
    assert result.provider == "local_qwen"
    assert adapter.model_path == settings.qwen_model_path
    assert result.raw_response["choices"][0]["finish_reason"] == "length"


def test_ir_dynamic_patch_template_uses_real_json_schema():
    schema = _generic_schema({"patches": [{"operation": ["set_table_grid", "confirm"], "proposed_value": "verbatim-json-value"}], "findings": ["verbatim-string"]})
    assert schema["properties"]["patches"]["items"]["properties"]["proposed_value"] == {}
    assert schema["properties"]["findings"]["type"] == "array"
    assert schema["properties"]["patches"]["items"]["properties"]["operation"]["enum"] == ["set_table_grid", "confirm"]


def test_switching_ir_local_model_releases_previous_worker(monkeypatch, tmp_path: Path):
    from esg_v2.models import local_nuextract_adapter as module

    first = module._WorkerProcess(tmp_path / "python", tmp_path / "worker", tmp_path / "model")
    second = module._WorkerProcess(tmp_path / "python", tmp_path / "worker", tmp_path / "qwen", "local_qwen")
    stopped = []
    first.stop = lambda: stopped.append("nuextract")
    second._request_once = lambda payload, timeout: {"ok": True}
    monkeypatch.setattr(module, "_WORKERS", {("first",): first, ("second",): second})
    assert second.request({}, timeout=1) == {"ok": True}
    assert stopped == ["nuextract"]


def test_ir_worker_cache_keeps_prompt_protocol_when_weight_path_is_shared(monkeypatch, tmp_path: Path):
    from dataclasses import replace
    from esg_v2.models import local_nuextract_adapter as module

    settings = _settings(tmp_path)
    settings = replace(settings, qwen_model_path=settings.nuextract_model_path,
                       qwen_runtime_python=settings.nuextract_runtime_python)
    monkeypatch.setattr(module, "_WORKERS", {})
    native = module._shared_worker(settings, "local_nuextract")
    generic = module._shared_worker(settings, "local_qwen")
    assert native is not generic
    assert native.provider == "local_nuextract"
    assert generic.provider == "local_qwen"


def test_ir_worker_owner_loss_ends_worker_without_waiting_for_stdin(monkeypatch):
    from esg_v2.models import local_nuextract_worker as module

    parents = iter([42, 1])
    exited, waits = [], []
    monkeypatch.setattr(module.os, "getppid", lambda: next(parents))
    monkeypatch.setattr(module.os, "_exit", lambda status: exited.append(status))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: waits.append(seconds))
    module._monitor_parent(42, interval=0.1)
    assert exited == [1]
    assert waits == [0.1]


def test_ir_qwen_worker_uses_chat_schema_without_native_template(monkeypatch, tmp_path):
    from esg_v2.models.local_nuextract_worker import NuExtractWorker

    captured = {}
    generate_module = types.ModuleType("mlx_vlm.generate")
    prompt_module = types.ModuleType("mlx_vlm.prompt_utils")

    def fake_prompt(*args, **kwargs):
        captured.update(kwargs)
        captured["prompt"] = args[2]
        return "prompt"

    prompt_module.apply_chat_template = fake_prompt
    generate_module.generate = lambda *args, **kwargs: types.SimpleNamespace(text="{}", generation_tokens=32, finish_reason="length")
    monkeypatch.setitem(sys.modules, "mlx_vlm", types.ModuleType("mlx_vlm"))
    monkeypatch.setitem(sys.modules, "mlx_vlm.generate", generate_module)
    monkeypatch.setitem(sys.modules, "mlx_vlm.prompt_utils", prompt_module)
    worker = NuExtractWorker(tmp_path, "local_qwen")
    worker.model = types.SimpleNamespace(config={})
    worker.processor = object()
    result = worker.generate({"messages": [{"role": "user", "content": "source"}], "output_template": {"verdict": ["confirm", "abstain"]}, "max_tokens": 32})
    assert "OUTPUT_SCHEMA=" in captured["prompt"]
    assert "template" not in captured
    assert captured["enable_thinking"] is False
    assert result["finish_reason"] == "length"
