"""Structural counterexamples beyond one report's year-column layout."""
import pytest
from esg_contracts.evidence import DocumentReference, EvidenceFrame, EvidenceRow, EvidenceCell, TextEvidence, ObjectReference
from esg_contracts.facts import RunRequest
from esg_data.periods import parse_period, normalize_period
from esg_clauses.scope import make_scope
from esg_extraction.pipeline.structure import period_columns, column_structure
from esg_extraction.pipeline.binding import numeric_fact, context_from_claims
from esg_extraction.pipeline.decisions import NumericMatch
from esg_extraction.pipeline.engine import ExtractionBackbone
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from test_backbone import repository, quantitative


def frame(headers, values=None):
    sources = {key: [TextEvidence(object_id="h-"+key, object_type="cell", text=text, page_index=0)] for key, text in headers.items()}
    unit = TextEvidence(object_id="u", object_type="cell", text="吨", page_index=0)
    label = TextEvidence(object_id="label", object_type="cell", text="实际排放量", page_index=0)
    row = EvidenceRow(row_id="r", label=label.text, row_role="data", unit_raw="吨", unit_sources=[unit], label_sources=[label],
        cells=[EvidenceCell(cell_id="v-"+key, column_id=key, raw_text=(values or {}).get(key, str(index+1))) for index,key in enumerate(headers)])
    return EvidenceFrame(object_id="t", kind="table", page_index=0, digest="frame", rows=[row],
        columns={key:[text] for key,text in headers.items()}, column_sources=sources)


def document(year=2025):
    return DocumentReference(document_id="doc", ir_run_id="ir", ir_revision=1, content_digest="frozen", reporting_year=year)


@pytest.mark.parametrize("primary_year", [2025, 2030, None])
def test_all_years_are_retained_even_if_model_only_names_one(primary_year, repository, tmp_path):
    evidence = frame({"a":"2023", "b":"2024", "c":"2025"})
    class Docs:
        def document(self, _): return document(primary_year)
        def search(self, *_): return [ObjectReference(object_id="t",kind="table",page_index=0,score=1)]
        def read(self, *_): return evidence
    class Reader:
        def complete(self, **kwargs):
            assert kwargs["context"]["physical_structure"]["period_policy"] == "all_reported"
            return {"matches":[{"row_id":"r","columns":["c"],"selection":"measurement","match":"match","reason":"real row"}]}, {}
    clause=quantitative(repository)
    result=ExtractionBackbone(Docs(),Reader(),FileArtifactPort(tmp_path)).run("run",RunRequest(ir_run_id="ir",clause_keys=[clause.clause_key]),{clause.clause_key:clause})
    assert {(f['identity']['reporting_period']['value'],f['value']['value_raw']) for f in result['facts']} == {('2023','1'),('2024','2'),('2025','3')}
    assert len(result['facts'])==3 and result['summary']['model_calls']==1
    assert all(mapping['match']=='match' for mapping in result['clause_mappings'])


def test_same_year_entity_and_unit_columns_do_not_share_identity(repository):
    evidence=frame({'a':'2024 Group A (kg)','b':'2024 Group B (t)'})
    clause=quantitative(repository)
    match=NumericMatch(row_id='r',columns=['a','b'],selection='measurement',reason='two entities',column_identity={
        key:[{'axis':'reporting_entity','value':name,'quote':name,'source_id':'h-'+key}]
        for key,name in [('a','Group A'),('b','Group B')]})
    bound=[numeric_fact(clause,document(),evidence,match,evidence.rows[0],cell,'interpretation')[0] for cell in evidence.rows[0].cells]
    assert [(f.identity.reporting_period.value,f.identity.reporting_entity.value,f.value.unit_raw) for f in bound]==[
        ('2024','Group A','kg'),('2024','Group B','t')]
    assert all(c.requires_identity_reading for c in column_structure(evidence).columns)


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


def test_missing_period_does_not_inherit_a_report_title_year():
    evidence=frame({'a':'数值'})
    context,_=context_from_claims(evidence,[],row=evidence.rows[0],column_id='a',document=document())
    assert context.reporting_period.state=='not_read'
    assert not period_columns(evidence,document())


def test_historical_annual_value_covers_annual_grain(repository):
    evidence=frame({'a':'2023'})
    clause=quantitative(repository).model_copy(update={'output_scopes':[make_scope('任何年度',period='annual')]}).seal()
    _,mapping,_=numeric_fact(clause,document(),evidence,NumericMatch(row_id='r',columns=['a'],selection='measurement',reason='historical actual'),evidence.rows[0],evidence.rows[0].cells[0],'i')
    assert mapping.scope_ids and mapping.match=='match'


def test_frozen_quarter_shorthand_can_still_be_read():
    assert normalize_period({'kind':'quarter','year':'2024','quarter':1}).quarter==1


def test_english_dates_keep_actual_boundaries_and_short_periods_are_not_annual():
    short=parse_period('1 January 2024 to 31 March 2024')
    assert short.kind=='range' and short.start.isoformat()=='2024-01-01' and short.end.isoformat()=='2024-03-31'
    annual=parse_period('January 1, 2024 to December 31, 2024')
    assert annual.kind=='annual' and annual.year==2024
