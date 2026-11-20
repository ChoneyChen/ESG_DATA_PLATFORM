import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from esg_clauses.repository import ClauseRepository
from esg_clauses.scope import make_scope
from esg_data.hashing import digest
from esg_contracts.facts import QuantitativePayload,QualitativePayload,RunRequest,PipelineBudget
from esg_contracts.evidence import PDFPageText, SearchIntent
from esg_extraction.pipeline.engine import ExtractionBackbone, ExecutionYield, page_plan
from esg_extraction.pipeline.result_store import ResultStore
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_document.document.evidence_service import DocumentEvidenceService
ROOT=Path(__file__).resolve().parents[3]

@pytest.fixture
def repository():
    return ClauseRepository(ROOT/'ClauseESG/clauses')

@pytest.fixture
def documents(tmp_path):
    source=tmp_path/'ir';source.mkdir()
    def save(name,data):
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False))
    save('manifest.json',{'package_type':'document-ir-revision','package_schema_version':'document-ir-package-v1','schema_version':'document-ir-v0.12','run_id':'ir-example','document_id':'doc-example','ir_revision':1,
        'can_build_evidence':True,'document_label':'2025年度报告','source':{'pdf_sha256':'a'*64},'entrypoints':{'canonical_document':'doc.json','pages':'pages.json','tables':'tables.json','spreads':'spreads.json','logical_tables':'logical-tables.json','artifacts':'artifacts.json'}})
    save('spreads.json',{'spreads':[]});save('logical-tables.json',{'logical_tables':[]});save('artifacts.json',{'artifacts':[]})
    save('doc.json',{'metadata':{'document_id':'doc-example'},'sections':[]})
    save('pages.json',{'pages':[{'path':'page.json'}]});save('tables.json',{'tables':[{'path':'table.json'}]})
    save('page.json',{'page':{'page_index':0,'page_id':'page0','text':'2025年度报告'},'blocks':[{'block_id':'b1','block_type':'text','text':'我们已实施污染物排放管理制度，定期监测空气污染物排放。'}]})
    cells=[]
    for r,row in enumerate([['指标','2025','2024'],['综合能源消耗量','1,234.50','1,100'],['能源使用估计','约十三万吨','未披露']]):
        cells.extend({'cell_id':f't:r{r}c{c}','row_index':r,'col_index':c,'text':text,'row_span':1,'col_span':1} for c,text in enumerate(row))
    save('table.json',{'table_id':'t','page_index':0,'row_count':3,'column_count':3,'title':'能源消耗情况（MWh）','cells':cells})
    service = DocumentEvidenceService(lambda run: source)
    service.pages = lambda asset: [PDFPageText(page_index=0, text='能源消耗 管理制度')]
    service.image = lambda asset, index, thumbnail=False: b'page-image'
    return service

class Model:
    accepts_images = True

    def __init__(self):
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        items = []
        for key, card in kwargs["context"]["clauses"].items():
            if card["data_kind"] == "qualitative":
                items.append({"clause_key": key, "value": "我们已实施污染物排放管理制度。"})
            else:
                items.append({"clause_key": key, "unit": "MWh", "measurements": [
                    {"value": "1,234.50", "period": "2025"}, {"value": "1,100", "period": "2024"}
                ]})
                items.append({"clause_key": key, "value": "约十三万吨", "period": "2025"})
        return {"items": items}, {}

    def close(self):
        pass


def run_backbone(documents, model, artifacts, run_id, request, clauses, **kwargs):
    results = ResultStore(run_id, artifacts)
    original = documents.document(request.ir_run_id or "ir-example")
    document = original.model_copy(update={"source_sha256": "a" * 64})
    kwargs.setdefault("document", document)
    kwargs.setdefault("cancel_check", lambda: False)
    kwargs.setdefault("on_progress", lambda progress: None)
    for _ in range(100):
        try:
            return ExtractionBackbone(documents, model, artifacts, results).run(
                run_id, request, clauses, **kwargs
            )
        except ExecutionYield:
            pass
    raise AssertionError("Backbone did not finish within 100 saved execution slices")

def quantitative(repository,key='E1-5_01'):
    clause=repository.get(key)
    # A small test contract explicitly leaves optional scope identity unconstrained.
    return clause.model_copy(update={'reading':clause.reading.model_copy(update={'rules':[],'operator':'numeric_label'}),
        'output_scopes':[make_scope('本期',period='annual')]}).seal()


def test_selected_table_adds_matching_clause_without_adding_a_page(repository):
    total = quantitative(repository)
    total = total.model_copy(update={
        "exact_labels": ["首选指标"],
        "retrieval": total.retrieval.model_copy(update={"positive_terms": ["本条别名"]}),
    }).seal()
    other = quantitative(repository, "E3-4_11")
    other = other.model_copy(update={
        "exact_labels": ["同页指标"],
        "retrieval": other.retrieval.model_copy(update={"positive_terms": []}),
    }).seal()
    pages = [PDFPageText(page_index=index, text="首选指标 " + "章节说明" * 30) for index in range(11)]
    pages.append(PDFPageText(page_index=11, text=(
        "同页指标 本条别名 指标说明\n"
        "项目甲 100 110 120 130\n项目乙 200 210 220 230\n项目丙 300 310 320 330\n"
    )))
    clauses = {c.clause_key: c for c in (total, other)}
    jobs, _ = page_plan(pages, clauses, 100)
    assert set(dict(jobs)[11]) == set(clauses)
    assert 10 in dict(jobs)  # Adjacent to the other clause's only match.
    limited, _ = page_plan(pages, clauses, 8)
    assert total.clause_key not in dict(limited)[11]


def test_continuation_and_column_overrides_in_one_page_read(documents, repository, tmp_path):
    clause = quantitative(repository, "E1-5_09")
    head = "Key Performance Indicators 2021 2022 Boundary"
    body = "能源占比 原披露表\n" + ("项目 268 169 138 102\n" * 6)
    documents.pages = lambda asset: [
        PDFPageText(page_index=0, text=head),
        PDFPageText(page_index=1, text=body),
    ]
    documents.image = lambda asset, index, thumbnail=False: f"{index}:{thumbnail}".encode()

    class ContinuationModel(Model):
        def complete(self, **kwargs):
            context = kwargs["context"]
            if context["page_index"] == 0:
                return {"items": []}, {}
            assert context["preceding_page"] == {"page_index": 0, "column_header": head}
            assert kwargs["images"] == (b"1:False", b"0:True")
            return {"items": [{
                "clause_key": clause.clause_key, "unit": "%", "relation": "match",
                "attributes": {"reporting_entity": "Company"},
                "measurements": [{"value": "18", "period": "2021"}, {
                    "value": "20", "period": "2022", "relation": "different",
                    "attributes": {"value_role": "target"},
                }],
            }]}, {}

    result = run_backbone(documents, ContinuationModel(), FileArtifactPort(tmp_path / "out"), "continuation",
        RunRequest(ir_run_id="ir-example", clause_keys=[clause.clause_key]), {clause.clause_key: clause})
    facts = {fact["value"]["value_raw"]: fact for fact in result["facts"]}
    relations = {mapping["source_fact_id"]: mapping["match"] for mapping in result["clause_mappings"]}
    assert facts["20"]["identity"]["reporting_entity"]["raw_text"] == "Company"
    assert facts["20"]["identity"]["value_role"]["raw_text"] == "target"
    assert relations[facts["18"]["source_fact_id"]] == "match"
    assert relations[facts["20"]["source_fact_id"]] == "different"
    assert all(fact["evidence"][0]["page_index"] == 1 for fact in facts.values())

def test_raw_contracts_are_open_for_metadata_but_keep_kinds_separate():
    assert QuantitativePayload(value_raw='约十三万吨',extensions={'new_axis':{'source':'note'}}).parse_status=='unparsed'
    assert QuantitativePayload(value_raw='未披露',unit_raw=None)
    assert QualitativePayload(statement_raw='尚未制定该政策',extensions={'new':True})
    with pytest.raises(ValidationError):QualitativePayload(statement_raw='政策',value_numeric='1')
    assert QuantitativePayload(value_raw='x',parse_status='parsed').parse_status=='unparsed'
    quarantined=QuantitativePayload(value_raw='x',value_numeric='NaN',new_annotation={'confidence':'unconfirmed'})
    assert quarantined.value_raw=='x' and quarantined.value_numeric is None
    assert quarantined.extensions['new_annotation']=={'confidence':'unconfirmed'}
    assert quarantined.extensions['_unparsed_metadata']['value_numeric']=='NaN'
    assert QualitativePayload(statement_raw='尚未制定',new_note='原文旁注').extensions['new_note']=='原文旁注'

def test_raw_table_rows_survive_numeric_parser(documents,repository):
    c=quantitative(repository)
    candidates=documents.search('ir-example',SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='quantitative',positive_terms=['能源']))
    frame=documents.read('ir-example',candidates[0])
    assert any(cell.raw_text=='约十三万吨' for row in frame.rows for cell in row.cells)

def test_both_kinds_share_one_backbone(documents,repository,tmp_path):
    model=Model();artifacts=FileArtifactPort(tmp_path/'artifacts')
    q=quantitative(repository);a=repository.get('E2-4_09')
    assert a.data_kind=='qualitative'
    a=a.model_copy(update={'retrieval':a.retrieval.model_copy(update={'positive_terms':['管理制度']}),'reading':a.reading.model_copy(update={'rules':[]}),
        'output_scopes':[make_scope('陈述')]}).seal()
    result=run_backbone(documents,model,artifacts,'test-run',RunRequest(ir_run_id='ir-example',clause_keys=[q.clause_key,a.clause_key]),{q.clause_key:q,a.clause_key:a})
    assert result['summary']['quantitative']>=2
    assert result['summary']['qualitative']==1
    values=[f['value'] for f in result['facts']]
    assert any(v.get('value_raw')=='1,234.50' and v['value_numeric']=='1234.50' for v in values)
    assert any(v.get('value_raw')=='约十三万吨' and v['parse_status']=='unparsed' for v in values)
    assert artifacts.read('test-run', 'results/facts.json') == result
    assert len(model.calls) == 1
    assert model.calls[0]["images"] == (b"page-image",)
    assert set(model.calls[0]["context"]["clauses"]) == {q.clause_key, a.clause_key}
    assert all(fact["evidence"][0]["object_type"] == "pdf_page" for fact in result["facts"])
    assert {mapping["match"] for mapping in result["clause_mappings"]} == {"uncertain"}
    periods = {fact["identity"]["reporting_period"]["raw_text"] for fact in result["facts"]
               if fact["identity"]["reporting_period"].get("raw_text")}
    assert periods == {"2024", "2025"}


def test_reported_rate_multiplier_retains_printed_value_in_the_distinct_scope(documents, repository):
    from esg_extraction.pipeline.engine import _fact

    clause = repository.get("S1-14_05")
    document = documents.document("ir-example")
    row = {"value": "0.53", "unit": "per 200,000 Hours", "period": "2024", "relation": "match",
           "attributes": {"rate_multiplier": "200,000"}}
    fact, mapping = _fact(document, clause, 85, row, True)
    assert fact.value.value_raw == "0.53"
    assert fact.value.unit_raw == "per 200,000 Hours"
    assert fact.identity.dimensions["rate_multiplier"].raw_text == "200,000"
    assert fact.identity.dimensions["rate_multiplier"].value == "per_200000_hours"
    assert mapping.match == "different"
    difference, = mapping.scope_differences
    assert (difference.axis, difference.reported, difference.expected, difference.raw_text) == (
        "rate_multiplier", "per_200000_hours", "per_million_hours", "200,000",
    )


@pytest.mark.parametrize(("key", "expected"), [("E1-6_07", "scope_1"), ("E1-6_09", "scope_2"), ("E1-6_12", "total")])
def test_explicit_combined_ghg_label_keeps_the_value_under_a_distinct_scope(documents, repository, key, expected):
    from esg_clauses.reading import reported_combined_ghg_scope
    from esg_extraction.pipeline.engine import _fact

    clause = repository.get(key)
    label = "Scope 1 & 2 Emissions, Actual"
    fact, mapping = _fact(documents.document("ir-example"), clause, 17, {
        "value": "502", "label": label, "relation": "match", "attributes": {"ghg_scope": expected},
    }, True)
    assert fact.value.value_raw == "502"
    assert fact.identity.dimensions["ghg_scope"].value == "scope_1_2"
    assert fact.identity.dimensions["ghg_scope"].raw_text == label
    assert mapping.match == "different"
    assert mapping.scope_differences[0].expected == expected
    assert reported_combined_ghg_scope(clause, "Scope 1 emissions") is None
    assert reported_combined_ghg_scope(clause, "Scope 1, 2 and 3 emissions") is None


def test_declared_method_statement_resolves_uncertain_relation_without_using_dates(documents, repository):
    from esg_extraction.pipeline.engine import _fact

    clause = repository.get("E2-4_09")
    document = documents.document("ir-example")
    statement = "Furthermore, we have deployed an online monitoring system to analyze our total wastewater discharge."
    structure = {"topic": "wastewater discharge", "subject": "we", "action": "deployed an online monitoring system"}
    fact, mapping = _fact(document, clause, 33, {
        "value": statement, "relation": "uncertain", "structure": structure,
        "attributes": {"measurement_method": "online monitoring system"},
    }, True)
    assert fact.value.statement_raw == statement
    assert fact.value.structure.model_dump() == structure
    assert fact.identity.measurement_method.raw_text == "online monitoring system"
    assert fact.identity.reporting_period.raw_text is None
    assert mapping.match == "match"

    for other in (
        "We have not deployed an online monitoring system for wastewater.",
        "If we deployed an online monitoring system for wastewater, we would reassess staffing.",
        "Suppliers are screened for wastewater monitoring.",
    ):
        _, mapping = _fact(document, clause, 33, {"value": other, "relation": "uncertain"}, True)
        assert mapping.match == "uncertain"
    _, mapping = _fact(document, clause, 33, {"value": statement, "relation": "different"}, True)
    assert mapping.match == "different"


def test_annual_fields_select_the_applicable_printed_population_phrase(documents, repository):
    from esg_extraction.pipeline.engine import _fact

    clause = repository.get("S1-6_02")
    document = documents.document("ir-example")
    scope = "full-time contract employees in 2022 and 2023; the total number of employees in 2024"
    for year, value, gender, expected in (
        ("2023", "12,827", "Female", "full-time contract employees in 2022 and 2023"),
        ("2024", "10,597", "female", "the total number of employees in 2024"),
        ("2025", "2,000", "Women", scope),
    ):
        fact, _ = _fact(document, clause, 70, {
            "value": value, "period": year, "unit": "person", "attributes": {"population": scope, "gender": gender},
        }, True)
        assert fact.identity.dimensions["population"].raw_text == expected
        assert fact.identity.dimensions["gender"].value == "female"
        assert fact.identity.dimensions["gender"].raw_text == gender


def test_role_keeps_the_literal_separate_from_the_number_and_period(documents, repository):
    from esg_extraction.pipeline.engine import _fact

    clause = repository.get("E1-6_30")
    document = documents.document("ir-example")
    row = {"value": "0.13", "unit": "kWh per watt produced", "period": "2020", "relation": "match"}
    historical, mapping = _fact(document, clause, 42, row, True)
    assert historical.identity.value_role.state == "not_read" and mapping.match == "match"

    baseline, mapping = _fact(document, clause, 42, {
        **row, "value_role": {"code": "baseline", "raw_text": "2020 Baseline"},
        "relation_reason": "The blue reference bar is the 2020 baseline.",
    }, True)
    assert baseline.value.value_numeric == "0.13"
    assert baseline.identity.value_role.value == "baseline"
    assert baseline.identity.value_role.raw_text == "2020 Baseline"
    assert mapping.match == "different"
    assert mapping.scope_differences[0].expected == "actual"
    assert "blue reference bar" in mapping.reason

    ambiguous, mapping = _fact(document, clause, 42, {
        **row, "value_role": {"code": "baseline", "raw_text": "2020"},
    }, True)
    assert ambiguous.identity.value_role.state == "not_read" and mapping.match == "match"


def test_printed_percent_is_distinct_from_hours_per_employee(documents, repository):
    from esg_extraction.pipeline.engine import _fact, _unit_scope_difference

    document = documents.document("ir-example")
    clause = repository.get("S1-13_04")
    fact, mapping = _fact(document, clause, 47, {
        "value": "100%", "label": "Leadership-related training courses coverage", "relation": "match",
    }, True)
    assert fact.value.value_raw == "100%" and fact.value.unit_raw == "%"
    assert mapping.match == "different"
    difference, = mapping.scope_differences
    assert (difference.axis, difference.reported, difference.expected, difference.raw_text) == (
        "unit_dimension", "percent", "hour/person", "%",
    )
    for compatible in ("percent", "ratio", "person/person", "unknown"):
        assert _unit_scope_difference("%", compatible) is None


def test_printed_energy_intensity_cannot_match_water_volume(documents, repository):
    from esg_extraction.pipeline.engine import _fact

    fact, mapping = _fact(documents.document("ir-example"), repository.get("E3-4_01"), 68, {
        "value": "13.112", "unit": "kWh/m²", "relation": "match",
        "label": "Energy consumption per unit of finished solar glass products",
    }, True)
    assert fact.value.value_raw == "13.112"
    assert mapping.match == "different"
    difference, = mapping.scope_differences
    assert (difference.reported, difference.expected, difference.raw_text) == (
        "megajoule/square-metre", "cubic-metre", "kWh/m²",
    )
    fact, mapping = _fact(documents.document("ir-example"), repository.get("E1-3_04"), 16, {
        "value": "28.76", "unit": "million kWh", "relation": "match",
        "label": "Expected annual generation of PV green power",
    }, True)
    assert fact.value.value_raw == "28.76" and mapping.match == "different"
    assert mapping.scope_differences[0].expected == "tonne-co2e"


def test_forecast_preserves_approximation_split_from_value(documents, repository):
    from esg_extraction.pipeline.engine import _fact

    item = {"value": "16,000", "unit": "tonne", "relation": "match",
            "label": "Reduce CO2 emissions by about Yiwu Base Low-Carbon Park Project",
            "value_role": {"code": "forecast", "raw_text": "is expected to"}}
    source = "The project is expected to generate power a year, reduce\nCO2 emissions by about 16,000 tonne"
    document = documents.document("ir-example")
    clause = repository.get("E1-3_04")
    fact, _ = _fact(document, clause, 16, item, True, source)
    assert fact.identity.value_role.value == "forecast"
    assert fact.value.value_raw == "16,000" and fact.value.comparator == "approx"
    fact, _ = _fact(document, clause, 16, item, True, source.replace("about 16,000", "16,000"))
    assert fact.value.comparator == "exact"


def test_numeric_reader_keeps_zero_and_skips_missing_value_messages(documents, repository):
    from esg_extraction.pipeline.engine import _disclosure, _fact

    clause = repository.get("S1-13_04")
    document = documents.document("ir-example")
    assert _fact(document, clause, 47, {
        "value": "No data available for average training hours per person", "unit": "hours",
    }, True) is None
    fact, _ = _fact(document, clause, 71, {"value": "0", "unit": "hour"}, True)
    assert fact.value.value_raw == "0"
    row = {"value": "15", "label": "Not Disclosed", "attributes": {"gender": "Not Disclosed"},
           "disclosure": {"state": "not_disclosed", "statement_raw": "Not Disclosed"}}
    fact, _ = _fact(document, repository.get("S1-6_02"), 58, row, True)
    assert _disclosure(row, 58, True, "Not Disclosed 15") is None
    assert fact.identity.dimensions["gender"].value == "not_disclosed"


def test_explicit_disclosure_survives_a_window_and_coexists_with_values(documents, repository, tmp_path, monkeypatch):
    from esg_extraction.pipeline import engine

    clause = repository.get("S1-6_12")
    footnote = "Data newly disclosed in 2024, not disclosed in 2023"
    documents.pages = lambda asset: [
        PDFPageText(page_index=0, text=f"Employee turnover rate\nIndonesia N/A\n{footnote}."),
        PDFPageText(page_index=1, text="Employee turnover rate\nIndonesia 2.9 98.7 N/A"),
    ]
    monkeypatch.setattr(engine, "WINDOW_MAX_CALLS", 1)

    class TurnoverModel(Model):
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            index = kwargs["context"]["page_index"]
            values = [{"period": "2023", "disclosure": {"state": "not_disclosed", "statement_raw": footnote}}] if index == 0 else [
                {"period": "2025", "value": "2.9%", "relation": "match"},
                {"period": "2024", "value": "98.7%", "relation": "match"},
                {"period": "2022", "value": "N/A", "disclosure": {"state": "not_disclosed", "statement_raw": "N/A"}},
            ]
            return {"items": [{"clause_key": clause.clause_key, "label": "Indonesia", "measurements": values}]}, {}

    model = TurnoverModel()
    result = run_backbone(documents, model, FileArtifactPort(tmp_path / "out"), "turnover",
        RunRequest(ir_run_id="ir-example", clause_keys=[clause.clause_key]), {clause.clause_key: clause})
    assert len(model.calls) == 2
    assert model.calls[0]["context"]["disclosure_text"] == [footnote + "."]
    assert "disclosure_text" not in model.calls[1]["context"]
    assert {fact["value"]["value_raw"] for fact in result["facts"]} == {"2.9%", "98.7%"}
    coverage, = result["coverage"]
    assert coverage["state"] == "covered"
    assert coverage["disclosure_observations"] == [{
        "state": "not_disclosed", "statement_raw": footnote, "page_index": 0,
        "period_raw": "2023", "source_row_label": "Indonesia",
    }]


def test_annual_repair_reads_only_missing_printed_cells(documents, repository, tmp_path, monkeypatch):
    from esg_extraction.pipeline import engine

    clause = repository.get("E1-6_07")
    documents.pages = lambda asset: [PDFPageText(page_index=0, text=(
        "Key Performance Indicators Boundary 2024 2025 GRI CSRD SASB NSF ISSB\n"
        "Scope 1 GHG\nEmissions\n(Metric Tons CO2eq)\nGlobal\n(Operational\nControl)\n"
        "16,593 22,386 305-1 E1-6 11.2.1 S2 29\n"
        "Scope 2 GHG Market-based 919,451 753,843 305-2 E1-6"
    ))]
    monkeypatch.setattr(engine, "WINDOW_MAX_CALLS", 1)

    class LeadingYearModel(Model):
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            context = kwargs["context"]
            assert context["table_year_headers"] == [["2024", "2025"]]
            if len(self.calls) == 1:
                assert "missing_period_rows" not in context
                measurements = [{"value": "22,386", "period": "2025"}]
            else:
                assert context["missing_period_rows"] == [{
                    "clause_key": clause.clause_key, "label": "Scope 1 GHG Emissions",
                    "periods": ["2024"], "printed_cells": {"2024": "16,593"},
                }]
                measurements = [{"value": "16,593", "period": "2024"}, {"value": "1", "period": "2025"}]
            return {"items": [{"clause_key": clause.clause_key, "relation": "match", "unit": "tCO2e",
                               "label": "Scope 1 GHG Emissions", "measurements": measurements}]}, {}

    model = LeadingYearModel()
    result = run_backbone(documents, model, FileArtifactPort(tmp_path / "out"), "leading-year",
        RunRequest(ir_run_id="ir-example", clause_keys=[clause.clause_key]), {clause.clause_key: clause})
    assert len(model.calls) == 2
    assert {fact["value"]["value_raw"] for fact in result["facts"]} == {"16,593", "22,386"}
    assert result["coverage"][0]["inspected_objects"] == 1


def test_dense_annual_page_splits_only_its_wide_reading_group():
    from esg_extraction.pipeline.engine import _reading_groups

    wide = [f"key-{index}" for index in range(26)]
    readings = [[0, wide], [1, wide[:23]], [2, wide], [3, wide]]
    values = "\n".join(str(1000 + index) for index in range(114))
    annual = "Performance metrics 2022 2023 2024\n" + values
    page_text = {0: annual, 1: annual, 2: values, 3: "Environmental chapter 2022 2023 2024"}
    assert list(_reading_groups(readings, page_text, 6144)) == [[0, wide[:13]], [0, wide[13:]], *readings[1:]]
    assert list(_reading_groups(readings, page_text, 8192)) == readings


def test_clause_declared_annual_row_can_repair_an_empty_initial_answer(documents, repository, tmp_path):
    clause = repository.get("E1-6_11")
    documents.pages = lambda asset: [PDFPageText(page_index=0, text=(
        "Key Performance Indicators Boundary 2024 2025 GRI\n"
        "Scope 3 GHG\nEmissions\n(Metric Tons CO2eq)\n"
        "Supply Chain 4,401,699 3,981,422 305-3\n"
        "Scope 3 GHG Emissions Intensity 13 9"
    ))]

    class OmittedRowModel(Model):
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            expected = [{"clause_key": clause.clause_key, "label": "Scope 3 GHG Emissions",
                         "periods": ["2024", "2025"],
                         "printed_cells": {"2024": "4,401,699", "2025": "3,981,422"}}]
            assert kwargs["context"]["annual_rows"] == expected
            if len(self.calls) == 1:
                return {"items": []}, {}
            assert kwargs["context"]["missing_period_rows"] == expected
            return {"items": [{"clause_key": clause.clause_key, "relation": "match", "unit": "tCO2e",
                               "label": "Scope 3 GHG Emissions", "measurements": [
                                   {"value": "4,401,699", "period": "2024"},
                                   {"value": "3,981,422", "period": "2025"},
                               ]}]}, {}

    model = OmittedRowModel()
    result = run_backbone(documents, model, FileArtifactPort(tmp_path / "out"), "literal-row",
        RunRequest(ir_run_id="ir-example", clause_keys=[clause.clause_key]), {clause.clause_key: clause})
    assert len(model.calls) == 2
    assert {fact["value"]["value_raw"] for fact in result["facts"]} == {"4,401,699", "3,981,422"}

    narrative = "Scope 3 emissions, Scope 3 categories and other indirect GHG emissions are discussed here."
    candidate_pages = [PDFPageText(page_index=0, text=narrative),
                       PDFPageText(page_index=1, text=narrative),
                       documents.pages("unused")[0].model_copy(update={"page_index": 2})]
    selected, _ = page_plan(candidate_pages, {clause.clause_key: clause}, 2)
    assert selected[0][0] == 2


@pytest.mark.parametrize("label,method,category,expected_method,expected_category", [
    ("Scope 3 GHG Emissions (Metric Tons CO2eq)", "operational_control", None, None, "total"),
    ("Gross Scope 3 GHG Emissions", "Supplier-specific emission factors", None,
     "Supplier-specific emission factors", "total"),
    ("Scope 3 GHG Emissions – Business travel", "Distance-based method", "Business travel",
     "Distance-based method", "Business travel"),
    ("Scope 3 GHG Emissions Intensity", None, None, None, None),
])
def test_scope3_keeps_the_printed_boundary_and_recognizes_only_the_whole_scope_row(
    documents, repository, label, method, category, expected_method, expected_category,
):
    from esg_extraction.pipeline.engine import _fact

    attributes = {"reporting_boundary": "Supply Chain", "measurement_method": method,
                  "scope3_category": category}
    fact, _ = _fact(documents.document("ir-example"), repository.get("E1-6_11"), 83, {
        "label": label, "value": "4,401,699", "period": "2024", "unit": "Metric Tons CO2eq",
        "relation": "match", "attributes": attributes,
    }, True)
    assert fact.value.value_raw == "4,401,699"
    assert fact.value.unit_raw == "Metric Tons CO2eq"
    assert fact.identity.reporting_boundary.raw_text == "Supply Chain"
    assert fact.identity.measurement_method.value == expected_method
    dimension = fact.identity.dimensions.get("scope3_category")
    assert (dimension.value if dimension else None) == expected_category
    if expected_category == "total":
        assert dimension.raw_text == label


@pytest.mark.parametrize("key,label,value,unit,page,relation", [
    ("E1-5_01", "Energy Intensity", "171", "MWh/MW", 20, "different"),
    ("E2-4_02", "Exhaust emissions", "5,733,805.45", "10,000 m³", 125, "different"),
    ("E2-4_02", "Particulates", "176.63", "ton", 125, "match"),
    ("E2-4_03", "Wastewater Discharge (Tons)", "21,430,620", "Tons", 32, "different"),
    ("E2-4_03", "Chemical Oxygen Demand (COD)", "965,436", "Tons", 32, "match"),
    ("E2-5_02", "Total amount of hazardous waste generated", "2,132.52", "tons", 23, "different"),
    ("E2-5_02", "JA Solar Operation-wide GHG Emissions (Scope 1 + Scope 2)", "1,879,379", "tCO₂e", 14, "different"),
    ("E1-6_10", "total emission", "35,561,622", "tonnes of CO2e", 85, "different"),
    ("E1-6_10", "Indirect GHG emissions (market-based)", "35,454,451", "tCO2e", 84, "uncertain"),
    ("E1-6_10", "Indirect greenhouse gas emissions (Scope 2)", "3,220,788", "tCO2e", 70, "uncertain"),
    ("E1-6_10", "Scope 2 GHG Market-based", "919,451", "Metric Tons CO2eq", 84, "match"),
])
def test_confirmed_relationships_preserve_raw_disclosures(documents, repository, key, label, value, unit, page, relation):
    from esg_extraction.pipeline.engine import _fact

    fact, mapping = _fact(documents.document("ir-example"), repository.get(key), page - 1, {
        "label": label, "value": value, "unit": unit, "relation": "match",
        "attributes": {"ghg_scope": "scope_2", "measurement_method": "market_based"},
    }, True)
    assert fact.value.value_raw == value
    assert mapping.match == relation
    if label == "Scope 2 GHG Market-based":
        assert fact.identity.dimensions["ghg_scope"].value == "scope_2"
        assert fact.identity.measurement_method.value == "market_based"
    if label == "total emission":
        assert fact.identity.dimensions["ghg_scope"].value == "total"


def test_numbered_absence_requires_its_row_and_period_and_slash_follows_printed_legend():
    from esg_extraction.pipeline.engine import _disclosure, _related_disclosure_notes

    body = ("Environmental Performance 2024 2023 2022\n"
            "Share of renewable energy sources Note 5 1.52% 1.24% N/A\n"
            "Total energy consumption (MWh) 20,464,638 17,540,379 11,561,961")
    notes_page = "(5) Not disclosed in 2022\n(6) Reduction in air pollutants emissions"
    pages = {146: body, 148: notes_page}
    candidate = {"label": "Share of renewable energy sources", "period": "2022", "value": "N/A",
                 "disclosure": {"state": "not_disclosed", "statement_raw": "Not disclosed in 2022"}}
    observation = _disclosure(candidate, 146, True, body, _related_disclosure_notes(pages, 146))
    assert observation and observation.page_index == 148 and observation.period_raw == "2022"
    assert _disclosure(candidate, 148, True, notes_page) is None
    assert _disclosure({**candidate, "label": "Total energy consumption"}, 146, True, body,
                      _related_disclosure_notes(pages, 146)) is None
    assert _disclosure({**candidate, "period": "2024"}, 146, True, body,
                      _related_disclosure_notes(pages, 146)) is None

    slash = {"label": "Chemical Oxygen Demand (COD)", "period": "2019", "value": "/"}
    local = 'Chemical Oxygen Demand (COD) (Tons)\n330,036 /\nNote: 2. “/” indicates no statistical summarization'
    assert _disclosure(slash, 31, True, local).state == "not_available"
    assert _disclosure(slash, 31, True, local.split("\nNote:")[0]) is None


def test_legend_repair_fills_only_missing_marked_columns(documents, repository, tmp_path):
    key = "E2-4_03"
    clause = repository.get(key)
    label = "Chemical Oxygen Demand (COD)"
    legend = "“/” indicates no statistical summarization"
    documents.pages = lambda asset: [PDFPageText(page_index=0, text=(
        f"Pollutant Type 2020 2019\r\n{label} (Tons)\r\n330,036\r\n/\r\n2021\r\n0\r\nNote: {legend}"
    ))]

    class MarkerModel(Model):
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                cells = [{"value": "330,036", "period": "2020", "relation": "match"}]
            else:
                rows = kwargs["context"]["missing_period_rows"]
                assert rows == [{"clause_key": key, "label": label, "periods": ["2019", "2021"],
                                 "printed_cells": {}, "slash_legend": legend}]
                cells = [{"value": "/", "period": "2019", "relation": "match"},
                         {"value": "0", "period": "2021", "relation": "match"},
                         {"value": "123", "period": "2021", "relation": "match"}]
            return {"items": [{"clause_key": key, "label": label, "unit": "Tons",
                               "measurements": cells}]}, {}

    model = MarkerModel()
    result = run_backbone(documents, model, FileArtifactPort(tmp_path / "out"), "marked-year",
        RunRequest(ir_run_id="ir-example", clause_keys=[key]), {key: clause})
    assert len(model.calls) == 2
    assert {fact["value"]["value_raw"] for fact in result["facts"]} == {"0", "330,036"}
    assert result["coverage"][0]["disclosure_observations"] == [{
        "state": "not_available", "statement_raw": legend, "page_index": 0,
        "period_raw": "2019", "source_row_label": label,
    }]


def test_missing_declaration_requires_printed_words():
    from esg_extraction.pipeline.engine import _disclosure

    def report(statement, page):
        return _disclosure({"disclosure": {"state": "not_disclosed", "statement_raw": statement}}, 0, True, page)

    comment = "The page does not disclose the percentage of employee turnover."
    assert report(comment, "Female employees 2,380 N/A") is None
    assert report(comment, "") is None
    assert report("Indonesia", "Indonesia not disclosed in 2023") is None
    assert report("Data not disclosed in 2023", "").state == "not_disclosed"
    assert report("Not disclosed in 2023", "Indonesia N/A\nNote 3: NOT DISCLOSED IN\n2023.").state == "not_disclosed"


def test_model_status_code_alone_does_not_create_a_value_or_disclosure(documents, repository):
    from esg_extraction.pipeline.engine import _disclosure, _fact

    clause = repository.get("E3-4_01")
    item = {"value": "not_disclosed", "label": "Total water consumption", "period": "2025"}
    assert _fact(documents.document("ir-example"), clause, 42, item, True) is None
    assert _disclosure(item, 42, True, "Total water consumption") is None


def test_local_model_accepts_padding_in_structural_keys_without_changing_source_text(tmp_path):
    from esg_extraction.pipeline.adapters.local_model import LocalQwenModel

    model = LocalQwenModel(tmp_path)
    model.generate_structured = lambda **_: (
        '{"items ": [{"value ": "33.00", "unit": "hour", "unit ": "hours",'
        '"attributes ": {"population ": "  All employees  "}}]}', {},
    )
    result, _ = model.complete(instructions="", context={}, schema={}, max_tokens=64, cancel_check=lambda: False)
    assert result == {"items": [{"value": "33.00", "unit": "hour",
                                "attributes": {"population": "  All employees  "}}]}


def test_truncated_local_qualitative_read_keeps_the_completed_item(documents, repository, tmp_path):
    from esg_extraction.models.errors import ModelRunFailure
    from esg_extraction.pipeline.adapters.local_model import LocalQwenModel

    clause = repository.get("E2-4_09")
    statement = "We deployed an online monitoring system for wastewater discharge."
    model = LocalQwenModel(tmp_path)
    artifacts = FileArtifactPort(tmp_path / "results")

    def generate(**kwargs):
        fields = kwargs["template"]["properties"]["items"]["items"]["properties"]
        assert "unit" not in fields and "measurements" not in fields and "value_role" not in fields
        assert "structure" in fields and "structure.topic" in kwargs["instructions"]
        assert "Read all printed periods" not in kwargs["instructions"]
        complete = json.dumps({"clause_key ": clause.clause_key, "value ": statement,
            "label ": "Water pollution", "relation ": "different"})
        raise ModelRunFailure("Token limit", category="generation_incomplete",
            raw_output='{" items ":[' + complete + ',{"value":"unfinished')

    model.generate_structured = generate
    result = run_backbone(documents, model, artifacts, "truncated", RunRequest(
        ir_run_id="ir-example", clause_keys=[clause.clause_key]), {clause.clause_key: clause})
    assert [fact["value"]["statement_raw"] for fact in result["facts"]] == [statement]
    assert result["clause_mappings"][0]["reason"] == "Read under the printed label ‘Water pollution’"
    assert result["coverage"][0]["state"] == "failed"
    call = artifacts.read("truncated", "diagnostics/calls/1.json")
    assert call["recoverable_items"] == 1 and call["telemetry"]["completion_chars"] > len(statement)


def test_truncated_multiple_targets_keep_completed_rows_at_call_limit(documents, repository, tmp_path):
    from esg_extraction.models.errors import ModelRunFailure

    clause = quantitative(repository)
    other = clause.model_copy(update={"clause_key": "TEST_02"}).seal()
    clauses = {c.clause_key: c for c in (clause, other)}

    class TruncatedPage(Model):
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            item = json.dumps({"clause_key": clause.clause_key, "value": "12", "unit": "MWh"})
            raise ModelRunFailure("Token limit", category="generation_incomplete",
                                  cleaned_output=f'{{"items":[{item},{{"clause_key":"TEST_02"')

    model = TruncatedPage()
    result = run_backbone(documents, model, FileArtifactPort(tmp_path / "result"), "last-call",
        RunRequest(ir_run_id="ir-example", clause_keys=list(clauses), budget=PipelineBudget(max_model_calls=1)), clauses)
    assert len(model.calls) == 1
    assert [fact["value"]["value_raw"] for fact in result["facts"]] == ["12"]
    assert {item["state"] for item in result["coverage"]} == {"failed"}


def test_no_cross_clause_or_cross_run_model_cache(documents,repository,tmp_path):
    model=Model();artifacts=FileArtifactPort(tmp_path/'out')
    c=quantitative(repository)
    c=c.model_copy(update={'purposes':c.purposes.model_copy(update={'enrichment':c.purposes.enrichment.model_copy(update={'identity_axes':[]})})}).seal()
    other=c.model_copy(update={'clause_key':'TEST_02'}).seal()
    req=RunRequest(ir_run_id='ir-example',clause_keys=[c.clause_key,other.clause_key])
    run_backbone(documents,model,artifacts,'one',req,{c.clause_key:c,other.clause_key:other});n=len(model.calls)
    assert n == 1
    run_backbone(documents,model,artifacts,'two',req,{c.clause_key:c,other.clause_key:other})
    assert len(model.calls)==2*n


def test_fact_identity_tracks_record_content_without_changing_page_anchor(documents, repository):
    from esg_extraction.pipeline.engine import _fact

    document = documents.document("ir-example")
    clause = quantitative(repository)
    item = {"value": "7.0", "period": "2024", "unit": "MWh"}
    visual, _ = _fact(document, clause, 0, item, True)
    text, _ = _fact(document, clause, 0, item, False)
    repeated, _ = _fact(document, clause, 0, item, True)
    assert visual.source_anchor_id == text.source_anchor_id
    assert visual.source_fact_id != text.source_fact_id
    assert visual == repeated

def test_budget_exhaustion_is_not_non_disclosure(documents,repository,tmp_path):
    c=quantitative(repository);other=c.model_copy(update={'clause_key':'TEST_02'}).seal()
    documents.pages = lambda asset: [PDFPageText(page_index=index, text="能源消耗") for index in range(2)]
    result=run_backbone(documents,Model(),FileArtifactPort(tmp_path/'out'),'limited' ,RunRequest(ir_run_id='ir-example',clause_keys=[c.clause_key,other.clause_key],budget=PipelineBudget(max_model_calls=1)),{c.clause_key:c,other.clause_key:other})
    assert 'execution_budget_exhausted' in result['coverage'][1]['reasons']

def test_repository_is_content_checked_and_selections_are_only_keys(repository):
    rows=repository.catalog()['clauses']
    assert len(rows)==1126
    for row in rows:repository.get(row['clause_key']).verify()
    selected=repository.select(selection='priority32');assert len(selected)==32
    assert set(selected)==set(repository.selections()['priority32'])
    frozen=repository.freeze(selected)
    assert ClauseRepository.thaw(frozen)==selected
    key=next(iter(frozen['clauses']));frozen['clauses'][key]['definition']='tampered'
    with pytest.raises(ValueError):ClauseRepository.thaw(frozen)


@pytest.mark.parametrize('literal',['>3','小于3','1-2','1.2×10⁴','10‰'])
def test_narrative_inventory_preserves_modifiers_as_one_literal(literal):
    from esg_document.document.literals import numeric_rows
    from esg_contracts.evidence import TextEvidence
    text=TextEvidence(object_id='source',text='报告值为'+literal+'吨。',page_index=0)
    rows=numeric_rows(text)
    assert len(rows)==1 and rows[0].cells[0].raw_text==literal
    cell=rows[0].cells[0]
    assert text.text[cell.char_start:cell.char_end]==literal


def test_document_reader_rejects_retired_ir_versions(documents):
    root=documents.resolve('ir-example');path=root/'manifest.json';body=json.loads(path.read_text());body['schema_version']='document-ir-v0.8';path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='unsupported Document IR schema'):documents.document('ir-example')
