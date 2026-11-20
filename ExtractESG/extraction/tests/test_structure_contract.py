"""Reported periods preserve their original boundaries and granularity."""

import pytest
from esg_data.periods import normalize_period, parse_period


@pytest.mark.parametrize('raw,kind', [('2023','annual'),('FY 2024','fiscal_year'),('2024 Q2','quarter'),
    ('2023年至2025年累计','range'),('2025年12月31日','instant'),('截至2025年12月31日累计','range'),
    ('2025年上半年','range'),('2023 › 2024','unknown'),('二〇二四年','annual'),('FY2023/24','fiscal_year')])
def test_periods_keep_grain_and_never_expand_ranges(raw,kind):
    period=parse_period(raw)
    assert period.kind==kind and period.raw==raw
    if raw=='2023年至2025年累计': assert period.start is None and period.end is None


def test_yearless_quarter_keeps_grain_without_inventing_a_year():
    from esg_data.periods import period_label
    period=parse_period('第一季度')
    assert period.kind=='quarter' and period.quarter==1 and period.year is None
    assert period_label(period.model_dump(mode='json'))=='第一季度'
    assert parse_period('本财年').year is None


def test_frozen_quarter_shorthand_can_still_be_read():
    assert normalize_period({'kind':'quarter','year':'2024','quarter':1}).quarter==1


def test_english_dates_keep_actual_boundaries_and_short_periods_are_not_annual():
    short=parse_period('1 January 2024 to 31 March 2024')
    assert short.kind=='range' and short.start.isoformat()=='2024-01-01' and short.end.isoformat()=='2024-03-31'
    annual=parse_period('January 1, 2024 to December 31, 2024')
    assert annual.kind=='annual' and annual.year==2024


@pytest.mark.parametrize("literal", ["01/01/2023-31/12/2023", "01/01/2023–12/31/2023"])
def test_explicit_slash_dates_keep_bounds_without_assuming_month_day_order(literal):
    from esg_data.periods import period_payload

    period = parse_period(literal)
    assert (period.kind, period.year) == ("annual", 2023)
    payload = period_payload(period)
    assert payload["raw"] == literal
    assert (payload["start"], payload["end"]) == ("2023-01-01", "2023-12-31")
    assert normalize_period(payload) == period
    assert parse_period("01/02/2023–02/04/2023").kind == "unknown"
