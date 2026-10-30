"""Offline contract/evidence regression; no provider, model or live IR is mutated."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import types

from jsonschema import Draft202012Validator
import pytest

from esg_document.document.chart_evidence import assess_chart_points
from esg_document.document.chart_spec_normalizer import ChartSpecNormalizer
from esg_document.document.contracts import (
    ArtifactRef, AtomicPatch, BlockIR, BoundingBox, ChartSpec, DocumentIR,
    DocumentIRMetadata, ReviewPlan, ReviewerResult, SourceTrace, VerifierResult,
    VlmReviewTask,
)
from esg_document.document.convergence_engine import ConvergenceEngine
from esg_document.document.operation_registry import OperationRegistry, PATCH_OPERATION_NAMES
from esg_document.document.patch_guard import DocumentPatchApplier, PatchGuard
from esg_document.document.review_orchestrator import AgentReviewOrchestrator
from esg_document.document.review_plan_compiler import ReviewPlanCompiler, ReviewPlanContractError
from esg_document.document.review_response_adapter import ReviewResponseAdapter
from esg_document.document.transaction_coordinator import TransactionCoordinator
from esg_document.models.local_nuextract_worker import NuExtractWorker, _generic_schema
from test_patch_guard import _table_document, _visual_document


BBOX = {"x0": 20, "y0": 700, "x1": 60, "y1": 730, "unit": "points"}
GRID = {"row_count": 1, "column_count": 1, "cells": [{"row_index": 0, "col_index": 0, "text": "77", "bbox": BBOX}]}
CHART = {"chart_type": "bar", "series": [{"name": "count", "points": [{"category": "A", "value": 77, "evidence_refs": ["block-1"]}]}], "visual_evidence_refs": ["page.png"]}
EXAMPLES = {
    "confirm": None,
    "confirm_spread": {"reading_direction": "left_to_right"},
    "reject_spread": {"reason": "Independent pages"},
    "link_horizontal_continuation": {"links": [{"source_id": "table-1", "target_id": "table-2"}]},
    "replace_block_text": "A 77", "replace_cell_text": "A 77", "correct_ocr_text": "A 77",
    "set_table_grid": GRID, "replace_table": GRID,
    "insert_table_row": {"insert_before_row_index": 0, "cells": [{"col_index": 0, "text": "A"}]},
    "set_bbox": BBOX, "set_printed_page_label": "88", "set_visual_type": "chart",
    "set_caption": "A 77", "link_continuation": {"continues_to_table_id": "table-2", "axis": "horizontal"},
    "merge_blocks": {"merge_block_ids": ["block-2"]},
    "split_block": {"parts": [{"text": "A"}, {"text": "77"}]},
    "add_quality_flags": ["needs_review"], "retire_table_candidate": {"disposition": "non_table_visual"},
    "add_visual_text_block": {"text": "A 77", "bbox": BBOX},
    "set_figure_legend_text": ["A"],
    "upsert_figure_structure": {"elements": [{"element_id": "node-1", "text": "A", "visual_role": "label", "bbox": BBOX}]},
    "upsert_chart_spec": CHART, "bind_block_to_figure": {"figure_id": "figure-1"},
    "replace_text": "A 77",
}


@pytest.mark.parametrize("operation", PATCH_OPERATION_NAMES)
def test_all_operations_share_valid_schema_and_canonical_codec(operation):
    assert set(EXAMPLES) == set(PATCH_OPERATION_NAMES)
    payload = OperationRegistry.resolve_payload(operation, deepcopy(EXAMPLES[operation]), legacy_aliases=False).value
    schema = OperationRegistry.payload_schema(operation)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)
    envelope = OperationRegistry.patch_schema([operation])
    Draft202012Validator.check_schema(envelope)
    Draft202012Validator(envelope).validate({"operation": operation, "target_type": sorted(OperationRegistry.get(operation).target_types)[0], "target_id": "target", "proposed_value": payload})
    assert OperationRegistry.prompt_fragment([operation])[0]["payload_contract"] == schema


@pytest.mark.parametrize("operation,value", [
    ("insert_table_row", {"row_index": 0, "insert_before_row_index": 1, "cells": [{"col_index": 0}]}),
    ("merge_blocks", {"block_ids": ["a"], "merge_block_ids": ["b"]}),
    ("split_block", {"segments": [{"text": "a"}, {"text": "b"}], "parts": [{"text": "c"}, {"text": "d"}]}),
    ("link_continuation", {"target_id": "a", "continues_to_table_id": "b"}),
    ("replace_block_text", {"text": "A", "arbitrary": "metadata"}),
    ("insert_table_row", {"insert_before_row_index": True, "cells": [{"col_index": 0}]}),
    ("insert_table_row", {"insert_before_row_index": "0", "cells": [{"col_index": 0}]}),
    ("add_visual_text_block", {"text": "A", "bbox": None}),
    ("insert_table_row", {"insert_before_row_index": 0, "cells": [{"col_index": 0, "col_span": True}]}),
    ("link_horizontal_continuation", {"links": [{"source_id": "a", "target_id": "b", "confidence": "0.9"}]}),
    ("upsert_figure_structure", {"elements": [{"element_id": "a", "text": "A", "visual_role": "label", "bbox": None}]}),
    ("set_bbox", {"x0": float("nan"), "y0": 0, "x1": 1, "y1": 1}),
])
def test_conflicting_or_untyped_operation_payloads_fail_closed(operation, value):
    with pytest.raises((ValueError, TypeError)):
        OperationRegistry.resolve_payload(operation, value)


def _patch(operation, target_id, target_type, value, refs=None):
    return AtomicPatch(patch_id="patch-1", source_task_id="task-1", operation=operation,
                       target_id=target_id, target_type=target_type, proposed_value=value,
                       evidence_refs=refs if refs is not None else ["page.png"])


def test_recorded_row_index_compatibility_applies_actual_missing_row_without_mutating_proposal():
    recorded = _recorded("review-000001")["patches"][0]
    document = _table_document()
    table = document.tables[0]
    table.table_id = recorded["target_id"]
    table.column_count = 5
    for cell in table.cells:
        cell.table_id = table.table_id
    table.row_count = 3
    patch = _patch("insert_table_row", table.table_id, "table", deepcopy(recorded["proposed_value"]))
    before = deepcopy(patch.proposed_value)
    guard = PatchGuard().evaluate(document, "task-1", [patch])
    assert guard.passed
    assert any("row_index->insert_before_row_index" in str(c.details) for c in guard.checks)
    DocumentPatchApplier().apply(document, [patch])
    assert table.row_count == 4
    assert len([c for c in table.cells if c.row_index == 3]) == 5
    assert patch.proposed_value == before
    assert "row_index" in patch.proposed_value


def test_merge_alias_writes_and_snapshots_both_entities_in_frozen_scope():
    document = _visual_document()
    document.blocks[0].text = "A 77"
    second = document.blocks[0].model_copy(deep=True, update={"block_id": "block-2", "order": 1, "text": "B 88"})
    document.blocks.append(second)
    patch = _patch("merge_blocks", "block-1", "block", {"block_ids": ["block-2"]})
    assert not PatchGuard().evaluate(document, "task-1", [patch], allowed_target_ids={"block-1"}).passed
    assert PatchGuard().evaluate(document, "task-1", [patch], allowed_target_ids={"block-1", "block-2"}).passed
    assert TransactionCoordinator.covered_targets(document, [patch]) == {"block-1", "block-2"}
    candidate = _candidate(document, [patch])
    assert candidate.before_snapshots["block-2"]["text"] == "B 88"
    assert candidate.after_snapshots["block-2"] is None
    assert "B 88" in candidate.after_snapshots["block-1"]["text"]
    assert len(document.blocks) == 2  # Candidate preview stays isolated.
    DocumentPatchApplier().apply(document, [patch])
    assert [b.block_id for b in document.blocks] == ["block-1"]


def test_continuation_alias_axis_snapshots_and_verifier_coverage_include_second_table():
    document = _table_document()
    second = document.tables[0].model_copy(deep=True, update={"table_id": "table-2", "page_index": 1, "page_indices": [1]})
    document.tables.append(second)
    link = _patch("link_continuation", "table-1", "table", {"target_id": "table-2", "axis": "vertical"})
    caption = _patch("set_caption", "table-2", "table", "Continuation")
    assert not PatchGuard().evaluate(document, "task-1", [link], allowed_target_ids={"table-1"}).passed
    assert PatchGuard().evaluate(document, "task-1", [link], allowed_target_ids={"table-1", "table-2"}).passed
    task = _task("table", "table-1")
    reviewer = _reviewer()
    transactions = TransactionCoordinator().plan(document, task, reviewer, [link, caption], {"table-1", "table-2"})
    assert len(transactions) == 1
    assert transactions[0].target_ids == ["table-1", "table-2"]
    candidate = _candidate(document, [link, caption])
    assert candidate.after_snapshots["table-2"]["continues_from_table_id"] == "table-1"
    assert candidate.after_snapshots["table-2"]["continuation_axis"] == "vertical"
    assert candidate.before_snapshots["table-2"]["continues_from_table_id"] is None
    DocumentPatchApplier().apply(document, [link, caption])
    assert second.continues_from_table_id == "table-1"
    assert second.caption == "Continuation"


def test_split_legacy_segments_applies_parts_and_does_not_rewrite_numbers():
    document = _visual_document()
    document.blocks[0].text = "A 77 B 88"
    patch = _patch("split_block", "block-1", "block", {"segments": [{"text": "A 77", "bbox": BBOX}, {"text": "B 88"}]})
    document.blocks[0].bbox = BoundingBox(x0=10, y0=680, x1=200, y1=760, unit="points")
    assert PatchGuard().evaluate(document, "task-1", [patch]).passed
    DocumentPatchApplier().apply(document, [patch])
    assert [b.text for b in document.blocks] == ["A 77", "B 88"]
    assert document.blocks[0].bbox == BoundingBox.model_validate(BBOX)
    wrong = _patch("split_block", "block-1", "block", {"parts": [{"text": "A 7"}, {"text": "7"}]})
    assert not PatchGuard().evaluate(document, "task-1", [wrong]).passed


def test_insert_row_contract_defaults_do_not_invent_header_role():
    document = _table_document()
    patch = _patch("insert_table_row", "table-1", "table", {"insert_before_row_index": 0, "cells": [{"col_index": 0, "text": "A"}]})
    DocumentPatchApplier().apply(document, [patch])
    assert next(c for c in document.tables[0].cells if c.row_index == 0).is_header is False


def test_text_object_is_canonical_text_instead_of_python_dict_repr_and_page_bbox_is_not_advertised():
    document = _visual_document()
    patch = _patch("replace_block_text", "block-1", "block", {"text": "77 confirmed"})
    assert PatchGuard().evaluate(document, "task-1", [patch]).passed
    DocumentPatchApplier().apply(document, [patch])
    assert document.blocks[0].text == "77 confirmed"
    assert "page" not in OperationRegistry.get("set_bbox").target_types
    assert not PatchGuard().evaluate(document, "task-1", [_patch("set_bbox", "page-1", "page", BBOX)]).passed


def _chart_patch(document, value=77, refs=None, category="A"):
    chart = deepcopy(CHART)
    chart["series"][0]["points"][0].update(value=value, evidence_refs=refs if refs is not None else ["block-1"], category=category)
    return _patch("upsert_chart_spec", document.figures[0].figure_id, "figure", chart)


@pytest.mark.parametrize("value,refs", [(31415, ["block-1"]), (77, ["missing"]), (77, ["other-page"]), (77, ["other-figure"])])
def test_wrong_value_unresolved_crosspage_or_undeclared_crosscrop_citation_is_blocked(value, refs):
    document = _visual_document()
    document.blocks.extend([
        document.blocks[0].model_copy(update={"block_id": "other-page", "page_index": 1}),
        document.blocks[0].model_copy(update={"block_id": "other-figure", "figure_id": "figure-2"}),
    ])
    patch = _chart_patch(document, value, refs)
    guard = PatchGuard().evaluate(document, "task-1", [patch])
    assert not guard.passed
    assert any(c.code.endswith("chart_point_references") and not c.passed for c in guard.checks)
    with pytest.raises(ValueError, match="unsupported"):
        DocumentPatchApplier().apply(document, [patch])
    assert document.figures[0].chart_spec is None


@pytest.mark.parametrize("text,refs", [("A 77", []), ("A 77", ["artifact-page"]), ("77", ["block-1"]), ("A 77 B 88", ["block-1"])])
def test_missing_image_only_numeric_only_or_multi_value_point_proof_is_explicitly_downgraded(text, refs):
    document = _visual_document()
    document.blocks[0].text = text
    patch = _chart_patch(document, refs=refs)
    patch.proposed_value["evidence_support"] = {"all_points_exactly_supported": True, "quality": "point_supported"}
    assert PatchGuard().evaluate(document, "task-1", [patch]).passed
    DocumentPatchApplier().apply(document, [patch])
    figure = document.figures[0]
    assert figure.visual_status == "reviewed"
    assert figure.chart_spec.evidence_support["quality"] == "context_only"
    assert figure.chart_spec.evidence_support["visual_pixel_values_verified"] is False
    assert "chart_points_require_evidence" in figure.quality_flags
    assert "agent_chart_spec_verified" not in figure.quality_flags


def test_exact_local_category_value_text_is_proven_without_claiming_pixel_or_unit_verification():
    document = _visual_document()
    document.blocks[0].text = "A 77"
    patch = _chart_patch(document)
    assert PatchGuard().evaluate(document, "task-1", [patch]).passed
    DocumentPatchApplier().apply(document, [patch])
    ledger = document.figures[0].chart_spec.evidence_support
    assert ledger["quality"] == "point_supported"
    assert ledger["points"][0]["supported_references"][0]["source_quote"] == "A 77"
    assert ledger["series_metadata_verified"] is False
    assert ledger["visual_pixel_values_verified"] is False
    assert "chart_points_text_supported" in document.figures[0].quality_flags
    assert "agent_chart_spec_verified" not in document.figures[0].quality_flags


def test_semantic_binding_cannot_override_outside_crop_geometry_or_ambiguous_category():
    document = _visual_document()
    document.blocks[0].text = "A 77"
    document.blocks[0].figure_id = "figure-1"
    document.blocks[0].bbox = BoundingBox(x0=300, y0=300, x1=330, y1=330, unit="points")
    patch = _chart_patch(document)
    assert PatchGuard().evaluate(document, "task-1", [patch]).passed
    DocumentPatchApplier().apply(document, [patch])
    assert document.figures[0].chart_spec.evidence_support["quality"] == "context_only"
    document.blocks[0].bbox = BoundingBox.model_validate(BBOX)
    document.blocks[0].text = "A B 77"
    patch.proposed_value["series"][0]["points"].append({"category": "B", "value": 77, "evidence_refs": []})
    ledger = assess_chart_points(document, document.figures[0], ChartSpec.model_validate(patch.proposed_value))
    assert ledger["points"][0]["status"] == "source_context_only"


def test_crosscrop_requires_explicit_frozen_composite_scope_and_normalizer_preserves_it():
    document = _visual_document()
    second = document.figures[0].model_copy(update={"figure_id": "figure-2", "crop_artifact_id": "crop-2"})
    document.figures.append(second)
    document.artifacts.append(ArtifactRef(artifact_id="crop-2", kind="region_crop", path="second.png", page_index=0))
    document.blocks[0].text = "A 77"
    document.blocks[0].figure_id = second.figure_id
    patch = _chart_patch(document)
    patch.proposed_value["source_figure_ids"] = ["figure-1", "figure-2"]
    patch.proposed_value["visual_evidence_refs"].append("second.png")
    assert not PatchGuard().evaluate(document, "task-1", [patch]).passed
    task = _task("figure", "figure-1")
    task.review_plan = ReviewPlan(question="Composite sources?", current_risk="", context_target_ids=["figure-2"])
    document.review_tasks.append(task)
    normalized = ChartSpecNormalizer.normalize(patch.proposed_value)
    assert normalized["source_figure_ids"] == ["figure-1", "figure-2"]
    patch.proposed_value = normalized
    assert PatchGuard().evaluate(document, "task-1", [patch]).passed
    DocumentPatchApplier().apply(document, [patch])
    assert document.figures[0].chart_spec.evidence_support["quality"] == "point_supported"


def test_chart_top_level_other_crop_or_page_image_is_not_a_valid_visual_source():
    document = _visual_document()
    document.artifacts.append(ArtifactRef(artifact_id="other-image", kind="page_image", path="other.png", page_index=1))
    patch = _chart_patch(document, refs=[])
    patch.proposed_value["visual_evidence_refs"].append("other.png")
    assert not PatchGuard().evaluate(document, "task-1", [patch]).passed
    patch.proposed_value["visual_evidence_refs"] = ["page.png"]
    assert PatchGuard().evaluate(document, "task-1", [patch]).passed


def _recorded(task_id):
    fixture = json.loads((Path(__file__).parent / "fixtures/real_ir_review_contracts.json").read_text())
    return deepcopy(next(r["response_payload"] for r in fixture["reviewer_responses"] if r["task_id"] == task_id))


def test_recorded_real_chart_point_refs_do_not_support_invented_pairing():
    payload = _recorded("review-000004")
    raw = payload["patches"][0]
    document = _visual_document()
    figure = document.figures[0]
    figure.figure_id = raw["target_id"]
    figure.page_index = 28
    block = document.blocks[0]
    block.block_id = "block-p0029-0006"
    block.page_index = 28
    block.text = "79个"
    document.artifacts[0].page_index = 28
    task = _task("figure", figure.figure_id)
    task.input_refs = ["page.png"]
    # The real source reference aliases are resolved to actual tracked artifacts;
    # this does not supply any missing point proof.
    for ref in raw["proposed_value"]["visual_evidence_refs"]:
        if ref.endswith((".png", ".jpg")):
            document.artifacts.append(ArtifactRef(artifact_id=ref, kind="page_image", path=ref, page_index=28))
    normalized = ReviewResponseAdapter().normalize_reviewer(document, payload, task, alias_normalizer=ReviewResponseAdapter.normalize_legacy_aliases)
    proposal = normalized["patches"][0]
    patch = _patch(proposal["operation"], proposal["target_id"], proposal["target_type"], proposal["proposed_value"], proposal["evidence_refs"])
    guard = PatchGuard().evaluate(document, task.task_id, [patch])
    assert not guard.passed
    ledger = next(c.details for c in guard.checks if c.code.endswith("chart_point_references"))
    assert "reference_does_not_support_value" in ledger["points"][0]["errors"]
    assert ledger["points"][1]["status"] == "source_context_only"


def test_recorded_real_empty_point_refs_remain_unverified():
    raw = _recorded("review-000008")["patches"][0]["proposed_value"]
    document = _visual_document()
    figure = document.figures[0]
    figure.figure_id = "figure-p0060-0001"
    figure.page_index = 59
    document.artifacts[0].page_index = 59
    raw["visual_evidence_refs"] = ["page.png"]
    patch = _patch("upsert_chart_spec", figure.figure_id, "figure", raw)
    assert PatchGuard().evaluate(document, "task-1", [patch]).passed
    DocumentPatchApplier().apply(document, [patch])
    assert figure.chart_spec.evidence_support["point_count"] == 3
    assert {p["status"] for p in figure.chart_spec.evidence_support["points"]} == {"source_context_only"}
    assert figure.chart_spec.series[1].unit is None


def _task(target_type, target_id):
    return VlmReviewTask(task_id="task-1", task_type="low_confidence_region_review", target_type=target_type,
                         target_id=target_id, page_index=0, prompt_intent="Scoped offline review")


def _reviewer():
    return ReviewerResult(reviewer_result_id="reviewer-1", task_id="task-1", model_id="fixed-offline", model_family="same", attempt=1, verdict="correct", confidence=.9)


def _candidate(document, patches):
    orchestrator = object.__new__(AgentReviewOrchestrator)
    orchestrator.applier = DocumentPatchApplier()
    orchestrator.transactions = TransactionCoordinator()
    return orchestrator._record_candidate(document, _task(patches[0].target_type, patches[0].target_id), patches, status="proposed")


def test_new_plan_freezes_operation_digest_and_old_v2_hash_remains_readable(monkeypatch):
    document = _visual_document()
    task = _task("figure", "figure-1")
    compiler = ReviewPlanCompiler()
    compiler.compile(document, task)
    assert task.review_plan.operation_contract_digest == OperationRegistry.contract_digest(task.review_plan.allowed_operations)
    frozen = task.model_dump_json()
    old = task.model_copy(deep=True)
    old.review_plan.compiler_version = "review-plan-v2"
    old.review_plan.operation_contract_version = "legacy-v1"
    old.review_plan.operation_contract_digest = ""
    old.review_plan.contract_hash = compiler._hash(old.review_plan)
    compiler.validate(document, old)
    monkeypatch.setattr(OperationRegistry, "contract_version", staticmethod(lambda: "future-contract"))
    with pytest.raises(ReviewPlanContractError, match="operation_contract_changed"):
        compiler.validate(document, task)
    assert task.model_dump_json() == frozen
    compiler.validate(document, old)


@pytest.mark.parametrize("independent", [False, True])
def test_final_decision_reports_actual_verifier_independence(independent):
    document = _visual_document()
    task = _task("figure", "figure-1")
    verifier = VerifierResult(verifier_result_id="verifier-1", task_id=task.task_id, model_id="offline",
                              model_family="same" if not independent else "other", reviewer_result_id="reviewer-1",
                              verdict="accept", confidence=.9, independent_model_family=independent)
    object.__new__(AgentReviewOrchestrator)._resolve_task(document, task, _reviewer(), verifier)
    reason = document.final_decisions[0].reason
    assert ("an independent model-family verifier" in reason) == independent
    assert ("same-model isolated secondary verification" in reason) == (not independent)


def test_explicit_schema_keeps_defs_discriminator_and_legacy_template_is_not_misidentified():
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
              "properties": {"patch": OperationRegistry.patch_schema(list(PATCH_OPERATION_NAMES))}}
    schema["$defs"] = schema["properties"]["patch"].pop("$defs")
    delivered = _generic_schema(schema)
    assert delivered == schema and delivered is not schema
    delivered["properties"]["patch"]["oneOf"].clear()
    assert schema["properties"]["patch"]["oneOf"]
    legacy = _generic_schema({"type": "verbatim-string", "properties": {"name": "verbatim-string"}})
    assert legacy["properties"]["type"]["type"] == ["string", "null"]


@pytest.mark.parametrize("change", [{"$schema": "wrong-draft"}, {"type": "array"}, {"properties": []}, {"properties": {"x": {"$ref": "#/$defs/missing"}}}])
def test_explicit_schema_errors_are_not_silently_converted_to_empty_shapes(change):
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {}}
    schema.update(change)
    with pytest.raises(ValueError):
        _generic_schema(schema)


@pytest.mark.parametrize("provider", ["local_qwen", "local_nuextract"])
def test_worker_receives_full_explicit_schema_and_reports_prompt_only_capability(monkeypatch, tmp_path, provider):
    captured = {}
    prompt_module = types.ModuleType("mlx_vlm.prompt_utils")
    generate_module = types.ModuleType("mlx_vlm.generate")
    def prompt(*args, **kwargs):
        captured["prompt"] = args[2]
        captured.update(kwargs)
        return "offline-prompt"
    prompt_module.apply_chat_template = prompt
    generate_module.generate = lambda *a, **k: types.SimpleNamespace(text='{"value":77}', generation_tokens=5, finish_reason="stop")
    monkeypatch.setitem(sys.modules, "mlx_vlm", types.ModuleType("mlx_vlm"))
    monkeypatch.setitem(sys.modules, "mlx_vlm.prompt_utils", prompt_module)
    monkeypatch.setitem(sys.modules, "mlx_vlm.generate", generate_module)
    worker = NuExtractWorker(tmp_path, provider)
    worker.model = types.SimpleNamespace(config={})
    worker.processor = object()
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {"value": {"type": "number"}}}
    result = worker.generate({"output_template": schema, "messages": [{"role": "user", "content": "offline"}]})
    assert schema == json.loads(captured["prompt"].split("OUTPUT_SCHEMA=", 1)[1].split("\nDOCUMENT_CONTEXT=", 1)[0])
    assert "template" not in captured
    assert result["schema_delivery"] == "prompt_schema_and_server_validation"
    assert result["token_level_schema_enforcement"] is False


def test_guard_failure_ownership_distinguishes_stale_system_contract_and_wrong_model_payload():
    document = _table_document()
    wrong = _patch("insert_table_row", "table-1", "table", {"insert_before_row_index": "0", "cells": [{"col_index": 0}]})
    classification = ConvergenceEngine().classify_guard_failure(document, PatchGuard().evaluate(document, "task-1", [wrong]))
    assert classification["failure_owner"] == "model"
    stale = _patch("set_caption", "table-1", "table", "Title")
    stale.before_value = "stale-source-value"
    classification = ConvergenceEngine().classify_guard_failure(document, PatchGuard().evaluate(document, "task-1", [stale]))
    assert classification["failure_owner"] == "system"
