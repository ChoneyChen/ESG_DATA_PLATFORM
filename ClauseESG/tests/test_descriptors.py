import json
from pathlib import Path
import pytest
from esg_clauses.repository import ClauseRepository, ClauseRepositoryError
from esg_clauses.descriptor import ClauseDescriptor, ClauseRecord
from esg_clauses.reading import check_reading


@pytest.fixture
def current():
    return ClauseRepository(Path(__file__).resolve().parents[1] / "clauses")


def test_structure_has_no_third_value_kind_and_never_executes_members(current):
    record = current.get("E2-4_01")
    assert isinstance(record, ClauseDescriptor)
    assert "data_kind" not in record.model_dump()
    assert record.member_clause_keys == ["E2-4_02", "E2-4_03", "E2-4_04"]
    assert isinstance(ClauseRecord.model_validate(record.model_dump()).root, ClauseDescriptor)
    options = current.selection_options(["E2-4_01", "E2-4_02"])
    assert options["eligible"] == ["E2-4_02"] and not options["executes"]
    assert options["excluded"][0]["status"] == "structural"
    with pytest.raises(ClauseRepositoryError):
        current.select(["E2-4_01", "E2-4_02"])
    with pytest.raises(ClauseRepositoryError):
        current.freeze({record.clause_key: record})
    selected = current.select(options["eligible"])
    assert list(current.thaw(current.freeze(selected))) == ["E2-4_02"]


def test_energy_total_is_not_an_intensity_and_optional_unit_is_not_a_blocker(current):
    clause = current.get("E1-5_19")
    assert clause.readiness == "ready" and clause.value.unit_dimension == "energy"
    assert clause.value.canonical_unit is None
    assert check_reading(clause, "高气候影响行业能源消费总量", None, "MWh", "energy").ok
    assert check_reading(clause, "高气候影响行业能源消费总量", None, None, None).ok
    assert not check_reading(clause, "单位收入能源强度", None, "MWh/万元", "energy-intensity").ok


def test_content_dispositions_are_specific_and_do_not_claim_scientific_acceptance(current):
    path = Path(__file__).resolve().parents[2] / "docs/implementation/2026-10-31/clause-dispositions.json"
    rows = json.loads(path.read_text())["changes"]
    assert len(rows) == 37 and len({row["clause_key"] for row in rows}) == 37
    assert {kind: sum(row["decision"] == kind for row in rows) for kind in ("structure", "quantitative", "qualitative")} == {
        "structure": 13, "quantitative": 9, "qualitative": 15}
    for row in rows:
        clause = current.get(row["clause_key"])
        assert clause.clause_digest == row["after_digest"]
        assert clause.provenance["content_review"]["scientific_acceptance"] == "not_evaluated"
        if not isinstance(clause, ClauseDescriptor):
            assert clause.acceptance.samples == []
            assert clause.purposes.capture.period_policy == "all_reported"
            assert not any(field.required_for_confirmation for field in clause.organization.fields if field.role != "raw_value")


def test_old_frozen_value_shape_and_digest_are_still_readable(current):
    clause = current.get("E2-4_02")
    body = clause.model_dump(mode="json")
    body["schema_version"] = "clause-contract-1.1"
    body.pop("purposes")
    old = type(clause).model_validate(body).seal()
    restored = current.thaw(current.freeze({old.clause_key: old}))
    assert restored[old.clause_key].clause_digest == old.clause_digest
    assert "purposes" not in restored[old.clause_key].model_dump(mode="json")
