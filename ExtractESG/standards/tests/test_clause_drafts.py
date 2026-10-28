"""Authored clause drafts: create, validate, save, discard — never touching the package."""
from pathlib import Path

import pytest

from esg_standard_packages.clause_drafts import ClauseDraftError, ClauseDraftStore

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ("esrs.2023-set1.e1-5", "2.1.2")


@pytest.fixture
def store(tmp_path: Path) -> ClauseDraftStore:
    return ClauseDraftStore(ROOT, drafts_root=tmp_path / "drafts")


def test_create_copies_the_clause_and_validates_it(store: ClauseDraftStore):
    manifest = store.create(package_id=PACKAGE[0], package_version=PACKAGE[1], source_datapoint_id="E1-5_01", author="reviewer", note="改写检索词")
    assert manifest["status"] == "draft" and manifest["draft_id"].startswith("draft-")
    assert manifest["validation"]["ok"] is True
    loaded = store.load(manifest["draft_id"])
    assert loaded["clause"]["source_datapoint_id"] == "E1-5_01"
    assert loaded["retrieval"]["positive_terms"]
    assert [row["draft_id"] for row in store.list()] == [manifest["draft_id"]]


def test_saving_edits_recomputes_the_digest_and_marks_it_validated(store: ClauseDraftStore):
    manifest = store.create(package_id=PACKAGE[0], package_version=PACKAGE[1], source_datapoint_id="E1-5_01", author="reviewer", note="改写检索词")
    saved = store.save(manifest["draft_id"], {"retrieval": {"positive_terms": ["综合能耗", "能源消耗总量"],
                                                           "negative_terms": ["能源节约量"], "structural_anchors": []}},
                       author="reviewer", note="补充正向词", expected_digest=manifest["digest"])
    assert saved["status"] == "validated"
    assert saved["digest"] != manifest["digest"]
    loaded = store.load(manifest["draft_id"])
    assert loaded["clause"]["contract_digest"] == saved["digest"]
    assert loaded["retrieval"]["positive_terms"] == ["综合能耗", "能源消耗总量"]


def test_an_invalid_scope_is_refused_and_nothing_is_written(store: ClauseDraftStore):
    manifest = store.create(package_id=PACKAGE[0], package_version=PACKAGE[1], source_datapoint_id="E1-5_01", author="reviewer", note="尝试自由文本口径")
    with pytest.raises(Exception, match="entity"):
        store.save(manifest["draft_id"], {"output_scopes": [{"scope_id": "scope-x", "label_zh": "x",
                                                             "period": "annual", "entity": "某子公司",
                                                             "value_role": "actual", "aggregation": "total",
                                                             "axes": {}, "required": True}]},
                   author="reviewer", note="x", expected_digest=manifest["digest"])
    assert store.load(manifest["draft_id"])["manifest"]["status"] == "draft"


def test_a_stale_digest_blocks_the_save(store: ClauseDraftStore):
    manifest = store.create(package_id=PACKAGE[0], package_version=PACKAGE[1], source_datapoint_id="E1-5_01", author="reviewer", note="并发编辑")
    with pytest.raises(ClauseDraftError, match="changed since"):
        store.save(manifest["draft_id"], {"exclusions": ["另一个排除条件"]},
                   author="reviewer", note="x", expected_digest="0" * 64)


def test_discard_removes_the_directory(store: ClauseDraftStore):
    manifest = store.create(package_id=PACKAGE[0], package_version=PACKAGE[1], source_datapoint_id="E1-5_01", author="reviewer", note="丢弃")
    store.discard(manifest["draft_id"])
    assert store.list() == []
    with pytest.raises(ClauseDraftError, match="does not exist"):
        store.load(manifest["draft_id"])


def test_unknown_clause_cannot_start_a_draft(store: ClauseDraftStore):
    with pytest.raises(ValueError):
        store.create(package_id=PACKAGE[0], package_version=PACKAGE[1], source_datapoint_id="E9-9_99", author="reviewer", note="x")


def test_a_draft_without_author_or_note_is_refused(store: ClauseDraftStore):
    with pytest.raises(ClauseDraftError, match="author"):
        store.create(package_id=PACKAGE[0], package_version=PACKAGE[1], source_datapoint_id="E1-5_01", author=" ", note="x")


def test_a_validated_draft_changes_the_compiled_profile(store: ClauseDraftStore):
    """The draft overlay is what makes an edit reach the catalog, without touching the package."""
    from esg_standard_packages.clause_build import apply_override
    from esg_standard_packages.clause_profile import ClauseExtractionProfile, profile_digest
    manifest = store.create(package_id=PACKAGE[0], package_version=PACKAGE[1],
                            source_datapoint_id="E1-5_01", author="reviewer", note="补充排除条件")
    store.save(manifest["draft_id"], {"exclusions": ["不含车辆燃料", "不含原料用能"]},
               author="reviewer", note="补充排除条件", expected_digest=manifest["digest"])
    parts = store.load(manifest["draft_id"])
    base_profile = store.packages.profile(*PACKAGE, "E1-5_01").model_dump(mode="json")
    merged = ClauseExtractionProfile.model_validate(apply_override(base_profile, parts))
    assert merged.exclusions == ["不含车辆燃料", "不含原料用能"]
    assert merged.contract_digest == profile_digest(merged)
    assert merged.contract_digest != base_profile["contract_digest"]
