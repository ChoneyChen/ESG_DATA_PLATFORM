"""Per-clause directory packages: index, digest integrity and coverage of the catalog."""
import json
import shutil
from pathlib import Path

import pytest

from esg_standard_packages.clause_package import ClausePackageCatalog, ClausePackageError

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ("esrs.2023-set1.e1-5", "2.1.2")


@pytest.fixture(scope="module")
def catalog() -> ClausePackageCatalog:
    return ClausePackageCatalog(ROOT)


def test_catalog_indexes_every_executable_package(catalog: ClausePackageCatalog):
    packages = catalog.list_packages()
    # Every non-retired release is indexed, so a promoted pilot version appears too.
    assert len(packages) >= 99
    assert all(row["clause_count"] == len(row["clauses"]) for row in packages)
    entry = catalog.package_entry(*PACKAGE)
    assert entry and entry["clause_count"] == len(entry["clauses"])
    assert all(row["path"].count("/") == 2 for row in entry["clauses"])
    versions = {row["package_version"] for row in packages if row["package_id"] == PACKAGE[0]}
    assert PACKAGE[1] in versions


def test_each_clause_directory_holds_five_parts(catalog: ClausePackageCatalog):
    parts = catalog.clause_parts(*PACKAGE, "E1-5_01")
    assert set(parts) == {"clause", "retrieval", "evidence", "acceptance", "provenance"}
    assert parts["clause"]["source_datapoint_id"] == "E1-5_01"
    assert parts["retrieval"]["positive_terms"]
    assert parts["evidence"]["requirements"]
    assert parts["provenance"]["package_source_digest"]


def test_profile_is_reconstructed_with_a_verified_digest(catalog: ClausePackageCatalog):
    profile = catalog.profile(*PACKAGE, "E1-5_01")
    assert profile.clause_id == "esrs.2023-set1.e1-5@2.1.2:E1-5_01"
    assert profile.output_scopes and profile.output_scopes[0].required
    assert profile.retrieval.positive_terms
    assert [row.field for row in profile.evidence]


def test_every_metric_of_a_package_has_a_clause_or_an_explicit_skip(catalog: ClausePackageCatalog):
    from esg_standard_packages.scope import load_registry
    registry, _, packages = load_registry(ROOT)
    package = packages["E1-5"]
    entry = catalog.package_entry(*PACKAGE)
    covered = {row["source_datapoint_id"] for row in entry["clauses"]}
    skipped = {row["source_datapoint_id"] for row in entry["skipped"]}
    expected = {metric.source_datapoint_id for metric in package.metrics}
    assert expected == covered | skipped
    assert not covered & skipped


def test_a_tampered_clause_fails_the_digest_check(tmp_path: Path):
    shutil.copytree(ROOT / "clauses", tmp_path / "clauses")
    target = tmp_path / "clauses/esrs.2023-set1.e1-5/2.1.2/E1-5_01/clause.json"
    body = json.loads(target.read_text(encoding="utf-8"))
    body["exclusions"] = ["一个不同的排除条件"]
    target.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    broken = ClausePackageCatalog(tmp_path)
    with pytest.raises(ClausePackageError, match="digest"):
        broken.profile(*PACKAGE, "E1-5_01")


def test_a_missing_part_is_an_error_not_an_empty_clause(tmp_path: Path):
    shutil.copytree(ROOT / "clauses", tmp_path / "clauses")
    (tmp_path / "clauses/esrs.2023-set1.e1-5/2.1.2/E1-5_01/evidence.json").unlink()
    broken = ClausePackageCatalog(tmp_path)
    with pytest.raises(ClausePackageError, match="missing"):
        broken.profile(*PACKAGE, "E1-5_01")


def test_unknown_package_or_clause_is_reported(catalog: ClausePackageCatalog):
    assert catalog.for_package("esrs.2023-set1.e1-5", "9.9.9") == {}
    with pytest.raises(ClausePackageError, match="no clause"):
        catalog.clause_parts(*PACKAGE, "E9-9_99")


def test_written_profiles_match_the_compiled_contract_digest(catalog: ClausePackageCatalog):
    """The clause package may not silently drift from the package it was compiled from."""
    from esg_standard_packages.clause_profile import profile_digest
    for source_id in ("E1-5_01", "E1-5_03", "E1-5_09"):
        profile = catalog.profile(*PACKAGE, source_id)
        assert profile_digest(profile) == profile.contract_digest
