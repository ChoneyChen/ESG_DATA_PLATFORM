from esg_contracts.evidence import ObjectSearchPage
"""Scientific counterexamples: provenance alone cannot make a digit a quantity."""
import pytest
from esg_contracts.evidence import TextEvidence, EvidenceFrame, EvidenceCell, EvidenceRow
from esg_contracts.facts import RunRequest, ReportedField
from esg_extraction.pipeline.engine import ExtractionBackbone
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_document.document.literals import numeric_rows
from test_backbone import repository, documents, Model, quantitative, run_backbone
from test_organization import fact, mapping, reported


@pytest.mark.parametrize('text,raw,kind', [
    ('公司使用六氟化硫（SF6）气体设置报警装置。', '6', 'formula'),
    ('2由于子公司改进，氮氧化物排放量下降。', '2', 'footnote'),
    ('共有2家工厂实施监测。', '2', 'unresolved'),
    ('报告期排放0吨。', '0', 'measurement'),
    ('报告值为1-2吨。', '1-2', 'measurement'),
    ('2025年排放20吨。', '2025', 'date'),
])
def test_literal_typing_keeps_true_unitless_counts(text, raw, kind):
    rows = numeric_rows(TextEvidence(object_id='p', text=text, page_index=0))
    assert next(c for r in rows for c in r.cells if c.raw_text == raw).candidate_kind == kind


@pytest.mark.parametrize("unit,family,scale", [
    ("10,000 tce", "energy_tce", "10000"),
    ("10,000 kJ", "energy", "0.002777777780000"),
    ("10,000 m³", "volume", "10000"),
    ("Megaliters", "volume", "1000"),
])
def test_printed_scale_is_part_of_unit(unit, family, scale):
    from decimal import Decimal
    from esg_data.units import is_unit_expression, unit_dimension
    from esg_data.quantities import unit_scale
    assert is_unit_expression(unit)
    assert unit_scale(unit) == (family, Decimal(scale))
    assert unit_dimension(unit) is not None


@pytest.mark.parametrize("raw,unit,expected_unit,comparator,point,lower,upper", [
    ("66 million kWh", "kWh", "million kWh", "exact", "66", None, None),
    ("between 1.2 and 2.35 MW", None, "MW", "range", None, "1.2", "2.35"),
    ("5% to 10%", None, "%", "range", None, "5", "10"),
    ("more than 17,000 people", None, "people", "greater_than", "17000", "17000", None),
    ("approximately 5.19 tonne", None, "tonne", "approx", "5.19", None, None),
    ("1.23 × 10⁶", None, None, "exact", "1230000", None, None),
    ("50+ million tCO2e", None, "million tCO2e", "at_least", "50", "50", None),
])
def test_parse_complete_printed_expressions(raw, unit, expected_unit, comparator, point, lower, upper):
    from decimal import Decimal
    from esg_data.literals import parse_printed_measure

    quantity, printed_unit = parse_printed_measure(raw, unit)
    assert printed_unit == expected_unit
    assert quantity["comparator"] == comparator
    assert tuple(quantity[field] for field in ("value", "lower", "upper")) == tuple(
        Decimal(field) if field is not None else None for field in (point, lower, upper)
    )
    assert parse_printed_measure("2 projects reused 111,800 tonnes")[0] is None


@pytest.mark.parametrize("unit,expected,multiplier", [
    ("thousands m³", "cubic-metre", "1000"),
    ("thousand cubic meters (m³)", "cubic-metre", "1000"),
    ("100 million kWh", "megajoule", "360000000"),
    ("metric tons (t)", "tonne", "1"),
    ("kt", "tonne", "1000"),
    ("million liters", "cubic-metre", "1000"),
    ("kWh", "megajoule", "3.6"),
    ("GJ", "megajoule", "1000"),
    ("tCO₂e", "tonne-co2e", "1"),
    ("Thousand metric tons CO2-eq", "tonne-co2e", "1000"),
    ("tCO₂", "tonne-co2", "1"),
    ("10,000 tce 万吨标准煤", "tonne-coal-equivalent", "10000"),
    ("hour/person", "hour/person", "1"),
    ("tCO2e/MWp", "tonne-co2e/watt-peak", "0.000001"),
    ("m3/RMB 10,000", "cubic-metre/cny", "0.0001"),
    ("mWh", "megajoule", "0.0000036"),
    ("ML", "cubic-metre", "1000"),
    ("mL", "cubic-metre", "0.000001"),
])
def test_standard_unit_scales_only_identified_units(unit, expected, multiplier):
    from decimal import Decimal
    from esg_data.quantities import standard_unit

    assert standard_unit(unit) == (expected, Decimal(multiplier))
    assert standard_unit("tons per person") is None
    assert standard_unit("tCO₂e / revenue") is None
    assert standard_unit("MT CO₂e/MW") is None
    assert standard_unit("metric tons (kg)") is None


def test_parenthesized_scale_is_not_a_source_measurement():
    from esg_document.document.literals import numeric_rows
    from esg_contracts.evidence import TextEvidence
    source = TextEvidence(object_id="caption", page_index=71,
        text="Total Water Consumption(10,000 m³) Ultra-Pure Water Usage (10,000 m³)")
    assert all(cell.candidate_kind == "identifier" for row in numeric_rows(source) for cell in row.cells)
    actual = TextEvidence(object_id="statement",page_index=71,
        text="Total water consumption was 10,000 m³.")
    assert numeric_rows(actual)[0].cells[0].candidate_kind == "measurement"


def test_abbreviation_and_quoted_endings_keep_complete_source_sentences():
    from esg_data.text_search import sentence_ranges
    source = 'We follow U.S. standards and consult Dr. Lee. "We monitor emissions." We keep results.'
    parts = [source[start:end] for start,end in sentence_ranges(source)]
    assert parts == ['We follow U.S. standards and consult Dr. Lee. ', '"We monitor emissions." ', 'We keep results.']
    assert ''.join(parts) == source


def test_reference_column_survives_raw_table_fallback():
    from esg_document.document.evidence_service import reference_columns
    assert reference_columns({'c0':['Measure'], 'c1':['GRI'], 'c2':['NSF 457'], 'c3':['2024']}) == {'c1','c2'}


def test_superscript_notes_do_not_hide_measurement_words_or_change_unit_powers():
    from esg_data.literals import strip_text_footnotes
    from esg_document.document.search_ranking import phrase_score
    assert strip_text_footnotes("Recycled Water³") == "Recycled Water"
    assert strip_text_footnotes("水循环量³") == "水循环量"
    assert strip_text_footnotes("m³ and CO₂ and CO²") == "m³ and CO₂ and CO²"
    assert phrase_score(["recycled water"], "Recycled Water³") > 0
    assert phrase_score(["recycled water"], "Recycled Water3") == 0


def test_newline_separates_figure_labels_from_printed_quantities():
    from esg_contracts.evidence import TextEvidence
    from esg_document.document.literals import numeric_rows
    source=TextEvidence(object_id='figure',page_index=20,text='Total\n4.477 百萬立方米')
    row=numeric_rows(source)[0]
    assert row.cells[0].candidate_kind=='measurement'
    assert row.cells[0].raw_text=='4.477' and row.unit_raw=='百萬立方米'
    assert row.unit_sources[0].text==source.text
