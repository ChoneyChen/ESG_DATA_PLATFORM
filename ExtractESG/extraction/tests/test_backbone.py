import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from esg_clauses.repository import ClauseRepository
from esg_clauses.scope import make_scope
from esg_data.hashing import digest
from esg_targeted.pipeline_contracts import QuantitativePayload,QualitativePayload,RunRequest,PipelineBudget
from esg_targeted.evidence_contracts import SearchIntent
from esg_targeted.pipeline.engine import ExtractionBackbone
from esg_targeted.pipeline.adapters.artifacts import FileArtifactPort
from esg_v2.document.evidence_service import DocumentEvidenceService
ROOT=Path(__file__).resolve().parents[3]

@pytest.fixture
def repository():
    return ClauseRepository(ROOT/'ExtractESG/standard')

@pytest.fixture
def documents(tmp_path):
    source=tmp_path/'ir';source.mkdir()
    def save(name,data):
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False))
    save('manifest.json',{'schema_version':'document-ir-v0.12','run_id':'ir-example','document_id':'doc-example','ir_revision':1,
        'can_build_evidence':True,'document_label':'2025年度报告','entrypoints':{'canonical_document':'doc.json','pages':'pages.json','tables':'tables.json'}})
    save('doc.json',{'metadata':{'document_id':'doc-example'},'sections':[]})
    save('pages.json',{'pages':[{'path':'page.json'}]});save('tables.json',{'tables':[{'path':'table.json'}]})
    save('page.json',{'page':{'page_index':0,'page_id':'page0','text':'2025年度报告'},'blocks':[{'block_id':'b1','block_type':'text','text':'我们已实施污染物排放管理制度，定期监测空气污染物排放。'}]})
    cells=[]
    for r,row in enumerate([['指标','2025','2024'],['综合能源消耗量','1,234.50','1,100'],['能源使用估计','约十三万吨','未披露']]):
        cells.extend({'cell_id':f't:r{r}c{c}','row_index':r,'col_index':c,'text':text,'row_span':1,'col_span':1} for c,text in enumerate(row))
    save('table.json',{'table_id':'t','page_index':0,'row_count':3,'column_count':3,'title':'能源消耗情况（MWh）','cells':cells})
    return DocumentEvidenceService(lambda run:source)

class Model:
    def __init__(self):self.calls=[]
    def complete(self,**kwargs):
        self.calls.append(kwargs)
        frame=kwargs['context']['frame'];target=kwargs['context']['target']
        if target['data_kind']=='qualitative':
            spans=[t for t in frame['texts'] if '管理制度' in t['text']]
            return {'matches':[{'span_id':t['span_id'],'reason':'direct disclosure'} for t in spans]},{}
        return {'matches':[{'row_id':r['row_id'],'columns':['c1'],'reason':'source cell','identity':[{'bad':'optional metadata'}]}
                           for r in frame['rows'] if any(c['column_id']=='c1' for c in r['cells'])]},{}
    def close(self):pass

def quantitative(repository,key='E1-5_01'):
    clause=repository.get(key)
    # A small test contract explicitly leaves optional scope identity unconstrained.
    return clause.model_copy(update={'reading':clause.reading.model_copy(update={'rules':[],'operator':'numeric_label'}),
        'output_scopes':[make_scope('本期',period='annual')]}).seal()

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
    result=ExtractionBackbone(documents,model,artifacts).run('test-run',RunRequest(ir_run_id='ir-example',clause_keys=[q.clause_key,a.clause_key]),{q.clause_key:q,a.clause_key:a})
    assert result['summary']['quantitative']>=2
    assert result['summary']['qualitative']==1
    values=[f['value'] for f in result['facts']]
    assert any(v.get('value_raw')=='1,234.50' and v['value_numeric']=='1234.50' for v in values)
    assert any(v.get('value_raw')=='约十三万吨' and v['parse_status']=='unparsed' for v in values)
    assert result['facts'][0]['flags'] # Malformed optional identity does not discard the value.
    assert artifacts.read('test-run','results/facts.json')==result
    for call in model.calls:
        assert call['context']['target']['clause_digest']
        assert call['context']['frame']

def test_no_cross_clause_or_cross_run_model_cache(documents,repository,tmp_path):
    model=Model();engine=ExtractionBackbone(documents,model,FileArtifactPort(tmp_path/'out'))
    c=quantitative(repository);other=c.model_copy(update={'clause_key':'TEST_02'}).seal()
    req=RunRequest(ir_run_id='ir-example',clause_keys=[c.clause_key,other.clause_key])
    engine.run('one',req,{c.clause_key:c,other.clause_key:other});n=len(model.calls)
    assert n==2
    engine.run('two',req,{c.clause_key:c,other.clause_key:other})
    assert len(model.calls)==2*n

def test_budget_exhaustion_is_not_non_disclosure(documents,repository,tmp_path):
    c=quantitative(repository);other=c.model_copy(update={'clause_key':'TEST_02'}).seal()
    result=ExtractionBackbone(documents,Model(),FileArtifactPort(tmp_path/'out')).run('limited',RunRequest(ir_run_id='ir-example',clause_keys=[c.clause_key,other.clause_key],budget=PipelineBudget(max_model_calls=1)),{c.clause_key:c,other.clause_key:other})
    assert result['coverage'][1]['state']=='not_covered'
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
    from esg_v2.document.literals import numeric_rows
    from esg_targeted.evidence_contracts import TextEvidence
    text=TextEvidence(object_id='source',text='报告值为'+literal+'吨。',page_index=0)
    rows=numeric_rows(text)
    assert len(rows)==1 and rows[0].cells[0].raw_text==literal
    cell=rows[0].cells[0]
    assert text.text[cell.char_start:cell.char_end]==literal


def test_document_reader_rejects_retired_ir_versions(documents):
    root=documents.resolve('ir-example');path=root/'manifest.json';body=json.loads(path.read_text());body['schema_version']='document-ir-v0.8';path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='unsupported Document IR schema'):documents.document('ir-example')
