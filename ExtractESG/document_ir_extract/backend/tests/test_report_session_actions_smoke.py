"""Only synthetic IR identities; never replay report facts or dispatch a task."""
from copy import deepcopy
import json

import pytest

from esg_v2.api.report_session_actions import report_evidence_actions


def write(root, identity, **updates):
    data = {"schema_version": "document-ir-v0.12", "run_id": identity,
        "document_id": "document-synthetic", "lineage_id": "lineage-synthetic", "ocr_run_id": "ocr-synthetic",
        "source": {"pdf_sha256": "a" * 64}, "can_build_evidence": True, **updates}
    path = root / identity
    path.mkdir(exist_ok=True)
    (path / "manifest.json").write_text(json.dumps(data))
    return data


def plan(**document):
    return {"plan_id": "synthetic-plan", "config": {"review_provider": "local_qwen", "allow_limited_evidence": False},
        "documents": [{"status": "failed", "source_sha256": "a" * 64, **document}]}


@pytest.mark.parametrize("identity", [None, "", "../ir-parent"])
def test_missing_parent_never_scans_or_adopts_root_revisions(tmp_path, identity):
    write(tmp_path, "ir-unrelated-root")
    value = plan(ir_run_id=identity)
    actions = [{"action": "resume", "endpoint": "/synthetic/resume"}]
    before = deepcopy(value)
    assert report_evidence_actions(value, None, actions, ir_root=tmp_path) == actions
    assert value == before


def test_real_direct_child_same_source_offers_one_transition(tmp_path):
    write(tmp_path, "ir-parent")
    write(tmp_path, "ir-child", parent_ir_run_id="ir-parent")
    write(tmp_path, "ir-unrelated-root")
    actions = report_evidence_actions(plan(ir_run_id="ir-parent"), None, [], ir_root=tmp_path, execution_status="failed")
    assert len(actions) == 1 and actions[0]["action"] == "evidence_transition"
    assert actions[0]["scope"] == {"old_ir_run_id": "ir-parent", "new_ir_run_id": "ir-child", "target_ids": []}
    assert actions[0]["calls_model"] is True


@pytest.mark.parametrize("changed", [{"parent_ir_run_id": None}, {"parent_ir_run_id": "ir-other"},
    {"source": {"pdf_sha256": "b" * 64}}, {"document_id": "different-document"},
    {"lineage_id": "different-lineage"}, {"run_id": "different-directory"}, {"can_build_evidence": "true"}])
def test_unrelated_or_unverified_candidate_cannot_be_suggested(tmp_path, changed):
    write(tmp_path, "ir-parent")
    write(tmp_path, "ir-child", **{"parent_ir_run_id": "ir-parent", **changed})
    actions = report_evidence_actions(plan(ir_run_id="ir-parent"), None, [], ir_root=tmp_path, execution_status="failed")
    assert actions == []


def test_active_execution_cannot_adopt_candidate_and_native_gap_repair_keeps_real_call_semantics(tmp_path):
    write(tmp_path, "ir-parent")
    write(tmp_path, "ir-child", parent_ir_run_id="ir-parent")
    value = plan(ir_run_id="ir-parent", native_session_id="native-synthetic",
        evidence_policy={"excluded_target_ids": ["table-synthetic"]})
    actions = report_evidence_actions(value, None, [], ir_root=tmp_path, execution_status="running")
    assert [a["action"] for a in actions] == ["local_repair"]
    terminal = report_evidence_actions(value, None, [], ir_root=tmp_path, execution_status="failed")
    assert next(a for a in terminal if a["action"] == "evidence_transition")["calls_model"] is True


def test_limited_candidate_requires_explicit_authorization(tmp_path):
    write(tmp_path, "ir-parent")
    write(tmp_path, "ir-child", parent_ir_run_id="ir-parent", can_build_evidence=False,
        can_build_limited_evidence=True, evidence_policy={"mode": "limited"})
    value = plan(ir_run_id="ir-parent")
    assert report_evidence_actions(value, None, [], ir_root=tmp_path) == []
    value["config"]["allow_limited_evidence"] = True
    assert report_evidence_actions(value, None, [], ir_root=tmp_path)[0]["action"] == "evidence_transition"
