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
    periods = {fact["identity"]["reporting_period"]["raw_text"] for fact in result["facts"]
               if fact["identity"]["reporting_period"].get("raw_text")}
    assert periods == {"2024", "2025"}

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
