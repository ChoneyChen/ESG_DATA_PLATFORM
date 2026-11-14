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


def test_retired_value_contract_is_rejected(current):
    clause = current.get("E2-4_02")
    body = clause.model_dump(mode="json")
    body["schema_version"] = "clause-contract-1.1"
    body.pop("purposes")
    with pytest.raises(ValueError):
        type(clause).model_validate(body)
