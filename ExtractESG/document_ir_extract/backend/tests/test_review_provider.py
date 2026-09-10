from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
    assert request.output_template["verdict"] == ["confirm", "propose_patch", "abstain"]
    assert "set_table_grid" in request.output_template["patches"][0]["operation"]
    assert request.messages[1]["content"][1]["image_url"]["url"] == "/tmp/page.png"
