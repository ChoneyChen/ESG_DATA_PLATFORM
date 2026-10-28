"""Promotion: a validated draft becomes a new immutable package version."""
import json
import shutil
from pathlib import Path

import pytest

from esg_standard_packages.clause_drafts import ClauseDraftStore
from esg_standard_packages.clause_promotion import PromotionError, plan, promote

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ("esrs.2023-set1.e1-5", "2.1.2")


@pytest.fixture
def standards(tmp_path: Path) -> Path:
    """A minimal standards root: core, one authored package, its clause and index."""
    root = tmp_path / "standards"
    core = ROOT / "schemas/core/1.2.0/core.json"
    (root / "schemas/core/1.2.0").mkdir(parents=True)
    shutil.copy2(core, root / "schemas/core/1.2.0/core.json")
    authored = ROOT / "authoring/esrs/2023-set1/e1-5/2.1.2"
    shutil.copytree(authored, root / "authoring/esrs/2023-set1/e1-5/2.1.2")
    shutil.copytree(ROOT / "clauses/esrs.2023-set1.e1-5/2.1.2",
                    root / "clauses/esrs.2023-set1.e1-5/2.1.2")
    catalog = json.loads((ROOT / "clauses/catalog.json").read_text(encoding="utf-8"))
    entry = next(row for row in catalog["packages"] if row["package_id"] == PACKAGE[0])
    entry = {**entry, "clauses": [row for row in entry["clauses"]
                                  if row["source_datapoint_id"] == "E1-5_01"]}
    (root / "clauses").mkdir(parents=True, exist_ok=True)
    (root / "clauses/catalog.json").write_text(json.dumps(
        {"schema_version": "clause-catalog-1.0", "packages": [entry]}, ensure_ascii=False), encoding="utf-8")
    (root / "registry/esrs/2023-set1").mkdir(parents=True, exist_ok=True)
    registry = json.loads((ROOT / "registry/esrs/2023-set1/registry.json").read_text(encoding="utf-8"))
    registry["modules"] = [row for row in registry["modules"] if row["package_id"] == PACKAGE[0]]
    (root / "registry/esrs/2023-set1/registry.json").write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    release = ROOT / "releases" / PACKAGE[0] / PACKAGE[1] / "package.json"
    index = json.loads((ROOT / "registry/releases.json").read_text(encoding="utf-8"))
    row = next(item for item in index["releases"]
               if item["package_id"] == PACKAGE[0] and item["package_version"] == PACKAGE[1])
    (root / "releases" / PACKAGE[0] / PACKAGE[1]).mkdir(parents=True)
    shutil.copy2(release, root / "releases" / PACKAGE[0] / PACKAGE[1] / "package.json")
    (root / "registry/releases.json").write_text(json.dumps({"releases": [row]}, ensure_ascii=False), encoding="utf-8")
    return root


def store_for(root: Path, tmp_path: Path) -> ClauseDraftStore:
    return ClauseDraftStore(root, drafts_root=tmp_path / "drafts")


def validated_draft(store: ClauseDraftStore) -> str:
    manifest = store.create(package_id=PACKAGE[0], package_version=PACKAGE[1],
                            source_datapoint_id="E1-5_01", author="reviewer", note="补充排除条件")
    store.save(manifest["draft_id"], {"exclusions": ["不含车辆燃料", "不含原料用能"]},
               author="reviewer", note="补充排除条件", expected_digest=manifest["digest"])
    return manifest["draft_id"]


def test_plan_bumps_the_version_and_lists_the_change(standards: Path, tmp_path: Path):
    store = store_for(standards, tmp_path)
    draft = validated_draft(store)
    preview = plan(store, draft)
    assert preview["new_version"] == "2.1.3"
    assert preview["source_datapoint_id"] == "E1-5_01"
    assert "exclusions" in preview["changes"]


def test_an_unvalidated_draft_is_refused(standards: Path, tmp_path: Path):
    store = store_for(standards, tmp_path)
    manifest = store.create(package_id=PACKAGE[0], package_version=PACKAGE[1],
                            source_datapoint_id="E1-5_01", author="reviewer", note="未保存")
    with pytest.raises(PromotionError, match="validated"):
        plan(store, manifest["draft_id"])


def test_promotion_compiles_a_new_version_without_activating_it(standards: Path, tmp_path: Path):
    store = store_for(standards, tmp_path)
    draft = validated_draft(store)
    result = promote(store, draft)
    assert result["new_version"] == "2.1.3" and result["registered"] is True
    assert result["activated"] is False
    release = standards / "releases" / PACKAGE[0] / "2.1.3" / "package.json"
    assert release.is_file()
    compiled = json.loads(release.read_text(encoding="utf-8"))
    metric = next(item for item in compiled["metrics"] if item["source_datapoint_id"] == "E1-5_01")
    assert metric["measurement_contract"]["exclusions"] == ["不含车辆燃料", "不含原料用能"]
    index = json.loads((standards / "registry/releases.json").read_text(encoding="utf-8"))
    assert any(row["package_version"] == "2.1.3" and row["state"] == "compiled_draft"
               for row in index["releases"])
    registry = json.loads((standards / "registry/esrs/2023-set1/registry.json").read_text(encoding="utf-8"))
    assert registry["modules"][0]["package_version"] == "2.1.2"


def test_promotion_refuses_to_overwrite_an_existing_version(standards: Path, tmp_path: Path):
    store = store_for(standards, tmp_path)
    draft = validated_draft(store)
    promote(store, draft)
    with pytest.raises(PromotionError, match="already exists"):
        promote(store, draft)


def test_a_draft_with_no_package_level_change_is_refused(standards: Path, tmp_path: Path):
    store = store_for(standards, tmp_path)
    manifest = store.create(package_id=PACKAGE[0], package_version=PACKAGE[1],
                            source_datapoint_id="E1-5_01", author="reviewer", note="只改检索词")
    store.save(manifest["draft_id"], {"retrieval": {"positive_terms": ["综合能耗"], "negative_terms": [],
                                                    "structural_anchors": []}},
               author="reviewer", note="只改检索词", expected_digest=manifest["digest"])
    with pytest.raises(PromotionError, match="no change"):
        plan(store, manifest["draft_id"])


def test_activation_is_an_explicit_decision(standards: Path, tmp_path: Path):
    store = store_for(standards, tmp_path)
    draft = validated_draft(store)
    result = promote(store, draft, activate=True)
    assert result["activated"] is True
    registry = json.loads((standards / "registry/esrs/2023-set1/registry.json").read_text(encoding="utf-8"))
    assert registry["modules"][0]["package_version"] == "2.1.3"
