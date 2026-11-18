"""Distinct measurements in one sentence need a value-specific scope label."""
from pathlib import Path
import pytest
from esg_clauses.repository import ClauseRepository
from esg_clauses.reading import check_reading


@pytest.fixture
def clauses():
    return ClauseRepository(Path(__file__).resolve().parents[1] / "clauses")


CANADIAN_SENTENCE = (
    "In 2022, our total scope 1 direct GHG emissions were 75,647 tCO ₂ e, "
    "or tons of CO ₂ equivalent, and scope 2 indirect GHG emissions were "
    "1,213,638 tCO ₂ e."
)


@pytest.mark.parametrize("key", ["E1-6_07", "E1-6_09"])
def test_parallel_scope_measurements_require_review(clauses, key):
    result = check_reading(clauses.get(key), CANADIAN_SENTENCE, None,
        "tCO2e", "ghg-emissions")
    assert result.ok and result.status == "review"
    assert "ghg_scope_ambiguous" in result.flags


@pytest.mark.parametrize("label,matched", [
    ("our total scope 1 direct GHG emissions were 75,647 tCO ₂ e", "E1-6_07"),
    ("scope 2 indirect GHG emissions were 1,213,638 tCO ₂ e", "E1-6_09"),
])
def test_literal_measurement_subclauses_preserve_their_own_scope(clauses, label, matched):
    for key in ("E1-6_07", "E1-6_09"):
        result = check_reading(clauses.get(key), label, None, "tCO2e", "ghg-emissions")
        assert result.ok == (key == matched)
        assert "ghg_scope_ambiguous" not in result.flags
        if result.ok:
            assert result.status is None


@pytest.mark.parametrize("key,label", [
    ("E1-6_07", "Direct GHG emissions"),
    ("E1-6_09", "Indirect GHG emissions"),
    ("E1-6_11", "Other indirect GHG emissions"),
    ("E1-6_11", "Indirect emissions (Scope 3)"),
    ("E1-6_11", "(Scope 3) Indirect emissions"),
    ("E1-6_11", "Other indirect GHG emissions (Scope 3)"),
    ("E1-6_11", "Indirect emissions（Scope 3）"),
])
def test_overlapping_vocabulary_aliases_do_not_create_another_scope(clauses, key, label):
    result = check_reading(clauses.get(key), label, None, "tCO2e", "ghg-emissions")
    assert result.ok
    assert "ghg_scope_ambiguous" not in result.flags


@pytest.mark.parametrize("key", ["E1-6_07", "E1-6_09"])
def test_explicit_parenthetical_scope_does_not_match_another_scope(clauses, key):
    result = check_reading(clauses.get(key), "Indirect emissions (Scope 3)",
        None, "tCO2e", "ghg-emissions")
    assert not result.ok


@pytest.mark.parametrize("key", ["E1-6_07", "E1-6_09"])
def test_parentheses_keep_independent_scope_measurements_distinct(clauses, key):
    result = check_reading(clauses.get(key),
        "Direct emissions (Scope 1) were 100 tCO2e and "
        "Indirect emissions (Scope 2) were 200 tCO2e",
        None, "tCO2e", "ghg-emissions")
    assert result.ok and result.status == "review"
    assert "ghg_scope_ambiguous" in result.flags


@pytest.mark.parametrize("key", ["E1-6_09", "E1-6_11"])
def test_scope_two_and_three_in_one_measurement_label_require_review(clauses, key):
    result = check_reading(clauses.get(key),
        "Scope 2 emissions were 100 tCO2e while Scope 3 emissions were 200 tCO2e",
        None, "tCO2e", "ghg-emissions")
    assert result.ok and "ghg_scope_ambiguous" in result.flags
    assert result.status == "review"


@pytest.mark.parametrize("key", ["E1-6_07", "E1-6_09", "E1-6_11"])
def test_combined_scope_rejection_is_preserved(clauses, key):
    result = check_reading(clauses.get(key), "Scope 1+2+3 GHG emissions",
        None, "tCO2e", "ghg-emissions")
    assert not result.ok and result.status == "rejected"


@pytest.mark.parametrize("key", ["E1-6_09", "E1-6_10"])
def test_parallel_scope_two_methods_keep_the_existing_review_reason(clauses, key):
    result = check_reading(clauses.get(key),
        "Scope 2 emissions, calculated using the location-based and market-based methods, "
        "were 1,788,759 tCO2e and 1,576,842 tCO2e, respectively.",
        None, "tCO2e", "ghg-emissions")
    assert result.ok and result.status == "review"
    assert "scope2_method_ambiguous" in result.flags
    assert "ghg_scope_ambiguous" not in result.flags
