"""Printed reference, time and unit spans precede quantity candidate typing."""

import pytest

from esg_contracts.evidence import TextEvidence
from esg_document.document.literals import numeric_rows, source_context_spans


def inventory(text):
    source = TextEvidence(object_id="actual-source", object_type="cell", page_index=0,
                          char_start=73, text=text)
    rows = numeric_rows(source)
    for row in rows:
        cell = row.cells[0]
        assert text[cell.char_start - 73:cell.char_end - 73] == cell.raw_text
    return [(row.cells[0].raw_text, row.cells[0].candidate_kind) for row in rows]


@pytest.mark.parametrize("text,value,kind", [
    (
        "Energy consumption Environmental Metrics and Targets, Greenhouse Gas Emissions, "
        "outside of the organization Scope 3 emissions, p.16",
        "16", "identifier",
    ),
    (
        "Energy use goal By 2025, the Company will reduce total energy consumption "
        "per unit of revenue by 10% compared to 2021.",
        "2025", "date",
    ),
    (
        "By 2025, the Company will reduce total energy consumption per unit revenue "
        "by 10% compared to 2021.",
        "2025", "date",
    ),
    (
        "To further reduce energy consumption and emissions in the production process, "
        "in May 2022, the Fengxian Base began to carry out equipment energy-saving "
        "technical transformation. ",
        "2022", "date",
    ),
    ("Total energy consumption per unit of revenue 9.70 MWh/RMB'0,000", "0,000", "identifier"),
])
def test_actual_full_batch_reference_period_and_unit_fragments(text, value, kind):
    assert (value, kind) in inventory(text)
    start = text.index(value)
    assert any(span.kind == kind and span.start <= start < start + len(value) <= span.end
               for span in source_context_spans(text))


def test_actual_broken_denominator_keeps_printed_bytes_and_actual_quantity():
    text = "Total energy consumption per unit of revenue 9.70 MWh/RMB'0,000"
    assert inventory(text) == [("9.70", "measurement"), ("0,000", "identifier")]
    assert "'0,000" in text and "10,000" not in text


@pytest.mark.parametrize("text,value", [
    ("Emissions were 75,647 tCO2e.", "75,647"),
    ("By 2025, reduce energy consumption by 10% compared to 2021.", "10%"),
    ("The amount was 2025 MWh.", "2025"),
    ("The output rose by 2025 MWh.", "2025"),
    ("The amount was compared with 2021 MWh.", "2021"),
    ("The measured amount was 10,000 MWh.", "10,000"),
    ("The observed range was 2020–2022 MWh.", "2020–2022"),
    ("The observed range was 100-200 kg.", "100-200"),
    ("The reported amount was 1.2e3 kg.", "1.2e3"),
])
def test_actual_quantities_remain_measurements_despite_date_or_scale_shapes(text, value):
    assert (value, "measurement") in inventory(text)


@pytest.mark.parametrize("text,value", [
    ("The amount was 2025 widgets.", "2025"),
    ("The output rose by 2025 widgets.", "2025"),
    ("The quantity before 2025 widgets was unknown.", "2025"),
    ("The counter after 2025 widgets stopped.", "2025"),
    ("The number of categories was 6.", "6"),
    ("The unknown range was 100-200 widgets.", "100-200"),
    ("The measured amount was 10,000 widgets.", "10,000"),
])
def test_unknown_units_and_genuine_year_like_counts_stay_eligible(text, value):
    assert (value, "unresolved") in inventory(text)


@pytest.mark.parametrize("text,value", [
    ("See p.16, pp. 16–18 and page 20.", "16–18"),
    ("The method follows ISO14064-1:2018.", "2018"),
    ("Categories 1 & 2 correspond to Scope 1, 2, and 3.", "3"),
    ("Source https://example.org/data/1101?year=2025 .", "1101"),
    ("Intensity was 9.70 MWh/RMB 10,000.", "10,000"),
    ("Intensity was 9.70 MWh/USD’0,000.", "0,000"),
    ("Unit: 10,000 kJ", "10,000"),
])
def test_source_context_types_are_not_reported_quantities(text, value):
    assert (value, "identifier") in inventory(text)


@pytest.mark.parametrize("text,value", [
    ("By 2025, the target will be reached.", "2025"),
    ("The change began in May 2022.", "2022"),
    ("The change began on 5 May 2022.", "5"),
    ("The period was January 1, 2024 to December 31, 2024.", "1, 2024"),
    ("The target applies in 2020–2025.", "2020–2025"),
])
def test_shared_month_names_and_period_parser_support_temporal_source_spans(text, value):
    assert (value, "date") in inventory(text)


def test_fraction_and_parenthesized_quantity_are_not_reference_lists():
    assert inventory("The measured fraction was (1/2).") == [("1", "unresolved"), ("2", "unresolved")]
    assert inventory("The measured amount was (2) MWh.") == [("2", "unresolved")]


def test_currency_denominator_scale_does_not_absorb_a_following_independent_amount():
    assert inventory("Intensity 9.70 MWh/RMB; the output was 10,000 widgets.") == [
        ("9.70", "measurement"), ("10,000", "unresolved")]
    assert inventory("Intensity 9.70 MWh/RMB, 10,000 widgets were counted.") == [
        ("9.70", "measurement"), ("10,000", "unresolved")]
    assert inventory("Intensity 9.70 MWh/RMB 10,000 widgets were counted.") == [
        ("9.70", "measurement"), ("10,000", "unresolved")]


def test_from_does_not_guess_a_period_for_an_unknown_quantity_range():
    assert inventory("The counter ranged from 2020 to 2025 widgets.") == [
        ("2020", "unresolved"), ("2025", "unresolved")]
    assert inventory("The ratio increased by 2025:1.") == [
        ("2025", "unresolved"), ("1", "unresolved")]


@pytest.mark.parametrize("text,expected", [
    ("Employee headcount increased from 2020 to 2025.", ["2020", "2025"]),
    ("The counter ranged from 2020 to 2025.", ["2020", "2025"]),
    ("The quantity fell from 2025 to 2020.", ["2025", "2020"]),
    ("The count increased by 2025.", ["2025"]),
])
def test_punctuation_does_not_prove_that_a_true_unitless_count_is_a_year(text, expected):
    assert inventory(text) == [(value, "unresolved") for value in expected]
