import json
import shutil

from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.runtime_compatibility import compatibility_snapshot
from tests.helpers import standard_dist_root


def test_catalog_has_light_original_id_core_and_explicit_validation_metadata():
    snapshot = StandardPackageCatalog(standard_dist_root()).snapshot()
    # Every module in the IG 3 catalog is compiled; none is left planned.
    assert len(snapshot["packages"]) == 99
    assert len(snapshot["planned"]) == 0
    assert snapshot["historical"]
    assert snapshot["diagnostics"]["validation_scope"] == "structural_only"
    package = next(p for p in snapshot["packages"] if p["disclosure_requirement"] == "E1-5")
    assert package["format_version"] == "2.1"
    assert package["status"] == "draft"
    assert package["real_extraction_validation"] == package["business_approval"] == "not_assessed"
    assert "E1-5_01" in package["core_source_datapoint_ids"]
    assert any(m["source_datapoint_id"] == "E1-5_01" for m in package["metric_summaries"])
    assert all("measurement_contract" not in m for m in package["metric_summaries"])


def test_invalid_package_does_not_hide_healthy_package_and_changed_files_invalidate_cache(tmp_path):
    original = standard_dist_root() / "esrs.2023-set1.e1-5/2.1.1/package.json"
    healthy = tmp_path / "esrs.2023-set1.e1-5/2.1.1/package.json"
    healthy.parent.mkdir(parents=True)
    shutil.copyfile(original, healthy)
    broken = tmp_path / "broken/2.1.1/package.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{}")
    catalog = StandardPackageCatalog(tmp_path)
    assert len(catalog.list()) == 1
    assert catalog.diagnostics()["state"] == "degraded"
    healthy.write_text("{}")
    assert catalog.diagnostics()["state"] == "unavailable"
    assert catalog.diagnostics()["invalid_package_count"] == 2
    shutil.copyfile(original, healthy)
    assert len(catalog.list()) == 1


def test_major_profile_readiness_preserves_every_id_with_explicit_pending(monkeypatch):
    import esg_targeted.runtime_compatibility as module
    monkeypatch.setattr(module.runtime_release, "snapshot", lambda: {"restart_required": False})
    payload = compatibility_snapshot(StandardPackageCatalog(standard_dist_root()))
    assert payload["readiness"]["ready"]
    assert payload["readiness"]["business_approval"] == "not_assessed"
    profile = payload["readiness"]["profile"]
    assert (profile["overall_count"], profile["ready_count"], profile["pending_count"]) == (55, 38, 17)
    assert profile["selected_output_count"] == 19
    assert len(profile["selection_snapshot"]["source_datapoint_ids"]) == 19


def test_catalog_cache_does_not_allow_callers_to_mutate_validation_truth():
    catalog = StandardPackageCatalog(standard_dist_root())
    records, _ = catalog.scan()
    records[0].metric_summaries.clear()
    second, _ = catalog.scan()
    assert second[0].metric_summaries


def test_standard_details_project_semantic_diagnostics_without_rewriting_history(monkeypatch):
    from esg_targeted.api import main
    catalog = StandardPackageCatalog(standard_dist_root())
    monkeypatch.setattr(main, 'standard_catalog', catalog)
    old = standard_dist_root() / 'esrs.2023-set1.e1-6/2.1.1/package.json'
    before = old.read_bytes()
    historical = main.get_standard('esrs.2023-set1.e1-6','2.1.1')
    current = main.get_standard('esrs.2023-set1.e1-6','2.1.2')
    assert len(historical['semantic_diagnostics']) == 35
    assert current['semantic_diagnostics'] == []
    assert old.read_bytes() == before
    assert 'positive_definition' not in historical['metrics'][0]['measurement_contract']
    assert current['metrics'][0]['measurement_contract']['positive_definition']
