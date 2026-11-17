import pytest

from esg_document.document.report_period import detect_reporting_period


def page(*texts):
    return {'page': {'page_index': 5}, 'blocks': [
        {'block_id': f'block-{index}', 'text': text} for index, text in enumerate(texts)]}


@pytest.mark.parametrize('printed_range', [
    '1 January to 31 December 2021',
    'January 1 to December 31, 2021',
    '1 January 2021 to 31 December 2021',
    '1 July 2020 to 30 June 2021',
])
def test_explicit_range_uses_its_printed_end_year_and_keeps_source(printed_range):
    result = detect_reporting_period({'document_label': 'Report 2023'}, [page(
        f'The Report covers activities from {printed_range} (the “Reporting Period”). '
        'Some contents may extend to 2023.',
        'During the Reporting Period and the comparison against the performance in 2020.')])
    assert result.year == 2021
    assert result.basis == 'reporting_period_statement'
    assert result.evidence == printed_range
    assert result.source_id == 'block-0'


def test_comparison_year_does_not_become_a_reporting_period_statement():
    result = detect_reporting_period({'document_label': 'Report 2021'}, [page(
        'The Reporting Period and the comparison against the performance in 2020 '
        'are set out in the section headed ESG Performance in 2021.')])
    assert result.year == 2021
    assert result.basis == 'report_file_label'


def test_missing_year_in_the_date_range_preserves_unknown():
    result = detect_reporting_period({}, [page(
        'The reporting period is from 1 January to 31 December.')])
    assert result.year is None
    assert result.basis == 'unknown'


def test_comparative_date_range_does_not_override_the_declared_period():
    result=detect_reporting_period({},[page(
        "The reporting period is 2021, with comparative information covering "
        "1 January to 31 December 2020.")])
    assert result.year == 2021 and result.evidence == "reporting period is 2021"


def test_a_period_label_in_another_sentence_cannot_authorize_a_date_range():
    result=detect_reporting_period({"document_label":"Report 2021"},[page(
        "The reporting period is described in this report. "
        "Comparative information covers 1 January to 31 December 2020.")])
    assert result.year == 2021 and result.basis == "report_file_label"


def test_future_target_dates_do_not_override_the_actual_reporting_year():
    result=detect_reporting_period({},[page(
        "The reporting period is 2021, with targets covering "
        "1 January to 31 December 2030.")])
    assert result.year == 2021


@pytest.mark.parametrize("text,year", [('The Report is the sixth sustainability report of Xinyi Solar. Under the theme of “Action” and through three sections, namely “Responsible enterprise”, “Sustainable businesses” and “Friendly team”, the Report fully presented the action plan, effectiveness and achievement in relation to the ESG governance, sustainable development goals and climate action, sustainability of core businesses, talent building as well as the laws and regulations and internal rules and systems to be followed in the management system from 1 January to 31 December 2021 (the “Reporting Period” or “Year”). Some contents may be traced back to previous years or extended to 2023. The Report should be read in conjunction with the Company’s 2021 Annual Report and the “Corporate Governance Report ” included therein. The Report is published in Chinese and English. In case of any discrepancy between the two versions, the Chinese version shall prevail.', 2021), ('This report is the third annual ESG Report of Xinyi Solar with the theme to “start everything with “xin” and together break through the last mile to achieve the grid parity ”. The report is centered on “xin”, and the sustainable development work and performance in the three major areas of economy, environment and society in the period of 1 January to 31 December 2018 are fully presented from the five aspects of “Trustworthy (Cheng‘xin’) operation”, “Innovative (Chuang‘xin’) model”, “Fortunate (‘Xin’fu) team”, “Bonding (‘Xin’xi) around the world” and “Believe (‘Xin’yu) in the future”. Some of the works might be traced back to the past years or extend to 2019 and 2020. In this report, the terms “$”, “$‘000”, “$ million” and “$100 million” represent “HK$”, “HK$‘000”, “HK$ million” and “HK$100 million” respectively. During the reporting period, the Company has complied with the disclosure requirements of the “comply or explain” provisions as set out in the ESG Reporting Guide.', 2018)])
def test_report_presentation_and_post_range_period_label_keep_the_actual_statement(text,year):
    result=detect_reporting_period({},[page(text)])
    assert result.year == year and result.basis == "reporting_period_statement"


def test_annual_report_covering_the_printed_range_is_an_explicit_declaration():
    result=detect_reporting_period({},[page(
        "This is an annual report covering the period from 1 January 2024 to 31 December 2024. "
        "Unless otherwise stated, the data in the Report covers this period.")])
    assert result.year == 2024 and result.basis == "reporting_period_statement"
    assert result.evidence == "1 January 2024 to 31 December 2024"
