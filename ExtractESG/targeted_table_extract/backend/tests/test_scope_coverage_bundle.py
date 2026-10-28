"""Scope coverage is derived from the real profiles and the facts a run bound."""
from pathlib import Path
from types import SimpleNamespace

from esg_targeted.foundation.bundle import BundleWriter

EXTRACT = Path(__file__).resolve().parents[3]
STANDARDS = EXTRACT / "standards"
PACKAGE = ("esrs.2023-set1.e1-5", "2.1.2")


def writer() -> BundleWriter:
    settings = SimpleNamespace(project_root=EXTRACT / "targeted_table_extract",
                               standard_dist_root=STANDARDS / "releases",
                               output_root=EXTRACT / "targeted_extract_output",
                               index_cache_root=EXTRACT / ".local/indexes")
    return BundleWriter(settings)


def records(boundary: str | None, *, year: int = 2025) -> dict:
    return {
        "reporting_tasks": [{"task_id": "task-1", "metric_id": "esrs.2023-set1.e1-5.dp01"}],
        "quantitative_observations": [{"observation_id": "obs-1", "task_id": "task-1",
                                       "reporting_year": year, "reporting_boundary": boundary,
                                       "value_raw": "1", "unit_raw": "MWh"}],
    }


def test_group_fact_covers_the_declared_scope():
    service = writer()
    package = service.catalog.load(*PACKAGE)
    rows, gaps = service._scope_coverage(package, records("group"))
    assert rows and rows[0]["state"] == "covered" and rows[0]["metric_id"] == "esrs.2023-set1.e1-5.dp01"
    assert gaps == []


def test_subsidiary_only_fact_leaves_the_group_scope_uncovered():
    service = writer()
    package = service.catalog.load(*PACKAGE)
    rows, gaps = service._scope_coverage(package, records("subsidiary"))
    required = [row for row in rows if row["required"]]
    assert required and required[0]["state"] == "out_of_scope"
    assert gaps and gaps[0]["kind"] == "scope_not_covered" and gaps[0]["scope_state"] == "out_of_scope"


def test_no_facts_reports_a_missing_scope_rather_than_a_conflict():
    service = writer()
    package = service.catalog.load(*PACKAGE)
    rows, gaps = service._scope_coverage(package, {"reporting_tasks": [{"task_id": "task-1",
                                                                        "metric_id": "esrs.2023-set1.e1-5.dp01"}],
                                                   "quantitative_observations": []})
    assert rows[0]["state"] == "missing"
    assert gaps and "本次运行没有覆盖" in gaps[0]["reason"]


def test_unknown_period_is_unconfirmed_not_missing(tmp_path: Path):
    service = writer()
    package = service.catalog.load(*PACKAGE)
    body = records("group")
    body["quantitative_observations"][0].pop("reporting_year")
    rows, _ = service._scope_coverage(package, body)
    assert rows[0]["state"] == "unconfirmed"


def test_unknown_package_has_no_profiles_instead_of_fabricated_ones():
    from esg_standard_packages.clause_package import ClausePackageCatalog
    catalog = ClausePackageCatalog(STANDARDS)
    assert catalog.for_package("esrs.2023-set1.e1-5", "9.9.9") == {}
    assert catalog.for_package(PACKAGE[0], PACKAGE[1])
