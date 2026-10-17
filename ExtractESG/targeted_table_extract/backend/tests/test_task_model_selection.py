"""Select actual adapters without loading weights or replaying report artifacts."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from esg_targeted.config import settings
from esg_targeted.contracts import ExtractionRequest, ReportExtractionRequest
from esg_targeted.models.budgeted import BudgetedModel
from esg_targeted.models.model_selection import model_selection, same_model_backend, freeze_report_role_models
from esg_targeted.models.qwen import QwenMlxModel
from esg_targeted.storage.job_store import JobStore
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.workflow import TargetedExtractionWorkflow
from tests.helpers import build_ir_fixture, standard_dist_root
from esg_targeted.report_session import ReportSessionService


def extraction(specialist="local_qwen", **kwargs):
    return ExtractionRequest.model_validate({"ir_run_id": "synthetic-ir", "package_id": "synthetic.e1-6", "package_version": "1.0.0", "metric_ids": ["dp07"],
        "roles": {"semantic_brain": {"provider": "local_qwen"}, "specialist": {"provider": specialist}}, **kwargs})


def workflow(tmp_path):
    runtime = replace(settings, project_root=tmp_path, output_root=tmp_path / "out", state_db=tmp_path / "jobs.sqlite",
        qwen_model_path=tmp_path / "Synthetic-Qwen-4bit", nuextract_model_path=tmp_path / "Synthetic-NuExtract-4bit")
    return TargetedExtractionWorkflow(runtime, JobStore(runtime.state_db), ArtifactStore(runtime.output_root))


def test_qwen_unified_builds_one_actual_qwen_adapter_and_never_nuextract(tmp_path, monkeypatch):
    import esg_targeted.workflow as module
    calls = []
    def qwen(path, **kwargs):
        calls.append(Path(path)); return QwenMlxModel(path, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError("NuExtract must never be constructed under Qwen-only selection")
    monkeypatch.setattr(module, "QwenMlxModel", qwen); monkeypatch.setattr(module, "NuExtractMlxModel", forbidden)
    engine = workflow(tmp_path); brain, specialist = engine._build_semantic_models(extraction())
    assert brain is specialist and isinstance(brain, QwenMlxModel) and brain.provider_name == "local_qwen"
    assert brain.capabilities.supports_vision and brain.capabilities.supports_structured_output
    assert len(calls) == 1 and brain._model is None and brain._processor is None


def test_mixed_plan_builds_each_selected_adapter_without_loading(tmp_path):
    engine = workflow(tmp_path); brain, specialist = engine._build_semantic_models(extraction("local_nuextract"))
    assert brain is not specialist and brain.provider_name == "local_qwen" and specialist.provider_name == "local_nuextract"
    assert brain._model is None and specialist._model is None


def test_roles_sharing_runtime_keep_separate_budget_roles_and_skip_inactive_close():
    runtime = SimpleNamespace(provider_name="local_qwen", close=lambda: None)
    brain = BudgetedModel(runtime, None, role="semantic_brain")
    specialist = BudgetedModel(runtime, None, role="specialist")
    assert brain is not specialist and same_model_backend(brain, specialist)
    assert brain._role == "semantic_brain" and specialist._role == "specialist"
    assert len({id(getattr(m, "_delegate", m)) for m in (brain, specialist)}) == 1


def test_selected_provider_cannot_silently_point_at_other_provider_weights(tmp_path):
    engine = workflow(tmp_path); request = extraction()
    request.roles.specialist.model = str(engine.settings.nuextract_model_path)
    with pytest.raises(ValueError, match="configured model"):
        engine._build_semantic_models(request)


def test_registered_model_name_and_absolute_path_resolve_same_identity(tmp_path):
    engine = workflow(tmp_path); first = extraction(); second = extraction()
    first.roles.specialist.model = engine.settings.qwen_model_path.name
    second.roles.specialist.model = str(engine.settings.qwen_model_path)
    assert model_selection(first, engine.settings, role="specialist").identity == model_selection(second, engine.settings, role="specialist").identity


def test_qwen_specialist_structured_task_uses_qwen_protocol_and_provider_without_loading(tmp_path):
    engine = workflow(tmp_path); model = engine._build_semantic_model(extraction(), role="specialist")
    captured = {}
    def fake_generate(**kwargs):
        captured.update(kwargs); return '{"value":"1"}', {"generation_tokens": 5}
    model._generate = fake_generate
    raw, info = model.generate_structured(context="synthetic source", instructions="read", template={"value": "string"}, image_path="synthetic.png")
    assert info["provider"] == "local_qwen" and captured["images"] == ["synthetic.png"] and model._model is None
    assert '"type": "object"' in captured["template"]


def report_request():
    return ReportExtractionRequest.model_validate({"input": {"kind": "document_ir", "ir_run_id": "ir-fixture-e2-4"},
        "standards": [{"package_id": "esrs.2023-set1.e2-4", "package_version": "2.0.0", "metric_ids": ["esrs.2023-set1.e2-4.dp02"]}],
        "model_plan": {}, "role_config": {"semantic_brain": {"provider": "local_qwen"}, "specialist": {"provider": "local_qwen"}}})


def test_new_task_plan_preview_create_and_idempotency_pin_actual_model_names(tmp_path):
    ir_root = tmp_path / "ir"; build_ir_fixture(ir_root)
    runtime = replace(settings, project_root=tmp_path, ir_roots=(ir_root,), standard_dist_root=standard_dist_root(),
        output_root=tmp_path / "output", state_db=tmp_path / "jobs.sqlite3", index_cache_root=tmp_path / "indexes",
        qwen_model_path=tmp_path / "Synthetic-Qwen-4bit", nuextract_model_path=tmp_path / "Synthetic-NuExtract-4bit")
    service = ReportSessionService(runtime); request = report_request()
    preview = service.preview(request)
    assert preview["roles"]["specialist"]["model"] == "Synthetic-Qwen-4bit"
    assert preview["model_task_assignments"]["specialist"]["provider"] == "local_qwen"
    created = service.create(request, idempotency_key="synthetic-only")
    again = service.create(request, idempotency_key="synthetic-only")
    assert created["session_id"] == again["session_id"]
    assert created["request"]["role_config"]["semantic_brain"]["model"] == "Synthetic-Qwen-4bit"
    assert created["jobs"][0]["request"]["roles"]["specialist"]["model"] == "Synthetic-Qwen-4bit"
    assert created["jobs"][0]["status"] == "queued"
    # Explicit registered names validate just like null -> configured defaults.
    explicit = freeze_report_role_models(request, runtime)
    assert service.create(explicit, idempotency_key="synthetic-only")["session_id"] == created["session_id"]


def test_legacy_report_request_freeze_is_noop(tmp_path):
    engine = workflow(tmp_path); request = report_request().model_copy(update={"model_plan": None})
    frozen = freeze_report_role_models(request, engine.settings)
    assert frozen is request and frozen.role_config.specialist.model is None
    assert "model_plan" not in frozen.model_dump(mode="json") and '"model_plan"' not in frozen.model_dump_json()


def test_result_summary_reports_selected_actual_model_mismatch_without_hiding_provider_match(tmp_path):
    from esg_targeted.standards.catalog import StandardPackageCatalog
    package = StandardPackageCatalog(standard_dist_root()).load("esrs.2023-set1.e2-4", "2.0.0")
    request = extraction()
    request.roles.specialist.model = "Synthetic-Qwen-4bit"
    summary = TargetedExtractionWorkflow._summary("synthetic-only", request, {}, package, [], {}, {}, [
        {"provider": "local_qwen", "model_id": "Wrong-registered-model", "role": "specialist"}])
    assert summary["model_execution"]["provider_match"] is True
    assert summary["model_execution"]["model_match"] is False
    actual = summary["model_execution"]["role_executions"][0]
    assert actual["expected_model"] == "Synthetic-Qwen-4bit" and actual["model_match"] is False
