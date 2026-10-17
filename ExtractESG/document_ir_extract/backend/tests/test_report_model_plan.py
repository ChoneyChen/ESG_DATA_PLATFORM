"""Synthetic model-task configuration checks; no local inference or live jobs."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from esg_targeted.contracts import ReportExtractionRequest
from esg_v2.control_plane.model_plan import report_processing_config, report_model_task_options
from esg_v2.control_plane.plans import PipelinePlanCoordinator
from esg_v2.control_plane.store import PipelineQueueStore


def request(**kwargs):
    return ReportExtractionRequest.model_validate({"input": {"kind": "pdf", "asset_id": "synthetic-asset"},
        "standards": [{"package_id": "synthetic.e1-6", "package_version": "1.0.0", "metric_ids": ["dp07"]}], **kwargs})


def test_legacy_processing_semantics_unchanged_without_task_plan():
    value = request()
    config = report_processing_config(value)
    assert value.model_plan is None
    assert config.ocr_provider == "local_paddleocr" and config.review_provider == "local_qwen"
    assert config.execute_required_reviews is True and config.execute_optional_reviews is False
    assert value.role_config.specialist.provider == "local_nuextract"


def test_explicit_qwen_task_plan_maps_actual_review_and_fast_basic_flags():
    value = request(model_plan={}, role_config={"semantic_brain": {"provider": "local_qwen"}, "specialist": {"provider": "local_qwen"}})
    config = report_processing_config(value)
    assert config.ocr_provider == "local_paddleocr" and config.ocr_model == "PaddleOCR-VL-1.6"
    assert config.review_provider == "local_qwen"
    assert config.execute_required_reviews is False and config.execute_optional_reviews is False
    assert value.role_config.specialist.provider == "local_qwen"


def test_selected_nuextract_visual_review_is_explicit_not_forced():
    value = request(model_plan={"visual_review_provider": "local_nuextract", "execute_visual_reviews": True, "execute_required_reviews": True})
    config = report_processing_config(value)
    assert config.review_provider == "local_nuextract" and config.execute_required_reviews and config.execute_optional_reviews


@pytest.mark.parametrize("provider", ["local_qwen", "local_nuextract"])
def test_unimplemented_ocr_adapter_is_not_accepted_as_operational_provider(provider):
    with pytest.raises(ValueError):
        request(model_plan={"ocr_provider": provider})


@pytest.mark.parametrize("plan", [{"ocr_provider": "paddle_api"}, {"visual_review_provider": "qiniu"}])
def test_cloud_task_assignment_requires_explicit_authorization(plan):
    with pytest.raises(ValueError, match="authorization"):
        request(model_plan=plan)


def test_authorized_cloud_tasks_map_existing_adapters_without_fallback():
    value = request(model_plan={"ocr_provider": "paddle_api", "visual_review_provider": "qiniu", "execute_visual_reviews": True},
        role_config={"cloud_authorization": {"allowed": True, "allow_ocr_api": True}})
    config = report_processing_config(value)
    assert config.ocr_provider == "paddle_api" and config.allow_cloud_ocr and config.review_provider == "qiniu"


def test_task_options_inspect_synthetic_files_only_and_distinguish_unimplemented_adapter(tmp_path):
    model_paths = []
    for name in ("Synthetic-Qwen-4bit", "Synthetic-NuExtract-4bit", "Synthetic-Paddle"):
        path = tmp_path / name; path.mkdir(); (path / "config.json").write_text(json.dumps({"model_type": "synthetic"})); (path / "weights.safetensors").write_bytes(b"synthetic-not-model")
        model_paths.append(path)
    options = report_model_task_options(SimpleNamespace(qwen_model_path=model_paths[0], nuextract_model_path=model_paths[1], local_paddleocr_model_path=model_paths[2]))
    assert options["calls_model"] is False
    qwen = options["tasks"]["specialist"]["providers"][0]
    assert qwen["provider"] == "local_qwen" and qwen["model"] == "Synthetic-Qwen-4bit" and qwen["installed"]
    assert qwen["runtime_state"] == "not_probed" and not qwen["quality_verified"]
    unsupported = [p for p in options["tasks"]["ocr"]["providers"] if not p["supported"]]
    assert len(unsupported) == 2 and all("适配器" in p["disabled_reason"] for p in unsupported)


def test_pipeline_plan_defaults_use_actual_processing_config_and_keep_roles_snapshot(tmp_path):
    store = PipelineQueueStore(tmp_path / "queue.sqlite")
    engine = PipelinePlanCoordinator(store, lambda *a: None, lambda *a: None, lambda *a: None)
    value = request(model_plan={"visual_review_provider": "local_nuextract", "execute_required_reviews": True},
        role_config={"semantic_brain": {"provider": "local_qwen"}, "specialist": {"provider": "local_qwen"}})
    config = report_processing_config(value).model_dump(mode="json")
    plan = engine.create_report(value, {"asset_id": "synthetic-asset", "file_name": "synthetic.pdf", "processing_config": config})
    assert plan["config"]["review_provider"] == "local_nuextract" and plan["config"]["execute_required_reviews"]
    assert plan["config"]["report_request"]["role_config"]["specialist"]["provider"] == "local_qwen"
    assert plan["config"]["report_request"]["model_plan"]["visual_review_provider"] == "local_nuextract"
    assert store.list() == []
