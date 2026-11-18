"""Physical identifiers and dates do not become reported quantity candidates."""

import pytest

from esg_contracts.evidence import TextEvidence
from esg_document.document.literals import literal_kind, numeric_rows


def inventory(text):
    source = TextEvidence(object_id="source", page_index=0, char_start=37, text=text)
    rows = numeric_rows(source)
    for row in rows:
        cell = row.cells[0]
        assert text[cell.char_start - 37:cell.char_end - 37] == cell.raw_text
    return [(row.cells[0].raw_text, row.cells[0].candidate_kind) for row in rows]


@pytest.mark.parametrize("designation", ["ISO14064- 1:2018", "ISO 14064-1:2018", "ISO/IEC 27001:2022"])
def test_standard_designation_includes_the_version_after_a_colon(designation):
    values = inventory(f"Emissions are reported in line with the {designation} standard.")
    assert values and all(kind == "identifier" for _, kind in values)


def test_standard_version_does_not_hide_a_later_reported_amount():
    values = inventory("ISO14064- 1:2018 applies; the measured amount was 2018 MWh.")
    assert values == [("14064- 1", "identifier"), ("2018", "identifier"), ("2018", "measurement")]


def test_repeated_parenthesized_index_items_are_not_quantities():
    text = "(1) Total energy consumed, (2) percentage grid electricity and (3) percentage renewable"
    assert inventory(text) == [("1", "footnote"), ("2", "identifier"), ("3", "identifier")]


@pytest.mark.parametrize("text", ["Values were (2) MWh and (3) MWh.", "(2) MWh was consumed."])
def test_parenthesized_amounts_with_actual_units_remain_candidates(text):
    assert all(kind in {"measurement", "unresolved"} for _, kind in inventory(text))


def test_parenthesized_ranges_and_fractions_are_not_enumeration_markers():
    assert inventory("The observed ranges were (1-2) and (3-4).") == [
        ("1-2", "unresolved"), ("3-4", "unresolved")
    ]
    text = "The measured fraction was (1/2)."
    start = text.index("1/2")
    assert literal_kind(text, start, start + 3, None) == "unresolved"
    assert all(kind == "unresolved" for _, kind in inventory(text))


@pytest.mark.parametrize(
    "text,literal",
    [
        ("Emissions in 2022 relative to 2021.", "2021"),
        ("A comprehensive comparison with our 2021 GHG emissions follows.", "2021"),
        ("The comparison with the 2020 figures follows.", "2020"),
        ("Emissions were reported in 2020–2022.", "2020–2022"),
    ],
)
def test_explicit_temporal_and_comparison_years_are_dates(text, literal):
    assert (literal, "date") in inventory(text)


@pytest.mark.parametrize("text", ["The quantity reached 2021 widgets.", "The quantity was 2021.", "The count was 2,021."])
def test_year_shaped_quantities_without_temporal_context_remain_candidates(text):
    assert all(kind == "unresolved" for _, kind in inventory(text))


@pytest.mark.parametrize("text", ["The amount was 2021 MWh.", "The amount was compared with 2021 MWh.", "The observed range was 302-1 MWh.", "The observed range was 2020–2022 MWh."])
def test_real_unit_bearing_year_shaped_values_and_ranges_remain_measurements(text):
    assert all(kind == "measurement" for _, kind in inventory(text))
