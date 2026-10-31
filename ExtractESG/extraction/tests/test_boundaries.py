import json,shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from esg_clauses.repository import ClauseRepository
from esg_extraction.pipeline.state import RunStore
from esg_extraction.pipeline.api import create_app
from esg_contracts.facts import RunRequest
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_extraction.pipeline.decisions import NumericMatch,StatementMatch
from esg_extraction.pipeline.binding import numeric_fact,qualitative_fact
from esg_contracts.evidence import SearchIntent
from test_backbone import documents,repository,quantitative,ROOT

def test_only_one_worker_can_claim_a_run(tmp_path,repository):
    store=RunStore(tmp_path/'runs.db');request=RunRequest(ir_run_id='ir',selection='base16')
    state=store.create(request,repository.freeze(repository.select(selection='base16')),{'id':'document'})
    def claim(_):
        try:store.claim(state['run_id']);return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=8) as pool:assert sum(pool.map(claim,range(8)))==1

def test_submission_key_is_idempotent_and_input_bound(tmp_path,repository):
    store=RunStore(tmp_path/'runs.db');request=RunRequest(ir_run_id='ir',selection='base16');snap=repository.freeze(repository.select(selection='base16'))
    a=store.create(request,snap,{'id':'doc'},'key');b=store.create(request,snap,{'id':'doc'},'key')
    assert a['run_id']==b['run_id']
    with pytest.raises(ValueError):store.create(request,snap,{'id':'different'},'key')

def test_no_path_crosses_the_evidence_http_interface(documents,repository,tmp_path):
    store=RunStore(tmp_path/'runs.db');artifacts=FileArtifactPort(tmp_path/'out')
    app=create_app(lambda:(repository,store,artifacts,documents))
    with TestClient(app) as client:
        c=client.get('/api/clauses/E1-5_01').json();assert c['clause_key']=='E1-5_01'
        response=client.post('/api/runs/preview',json={'ir_run_id':'ir-example','clause_keys':['E1-5_01']})
        assert response.status_code==200
        payload=response.json();assert 'path' not in payload['document']
        snapshot=payload['targets'];snapshot['clauses']['E1-5_01']['definition']='tampered'
        response=client.post('/api/runs',json={'request':{'ir_run_id':'ir-example','clause_keys':['E1-5_01']},'target_snapshot':snapshot})
        assert response.status_code==409
        assert client.post('/api/jobs',json={'package_id':'legacy'}).status_code==404
        rejected=client.post('/api/runs',json={'request':{'ir_run_id':'ir-example','clause_keys':['E1-5_01'],'api_key':'never-echo-this-secret'}})
        assert rejected.status_code==422 and 'never-echo-this-secret' not in rejected.text

def test_reading_rejection_preserves_raw_value_as_a_different_mapping(documents,repository):
    c=repository.get('E3-4_01') # Water clause deliberately applied to an energy row.
    intent=SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='quantitative',positive_terms=['能源'])
    frame=documents.read('ir-example',documents.search('ir-example',intent)[0]);row=frame.rows[0]
    cell=next(v for v in row.cells if v.column_id=='c1')
    fact,mapping,_=numeric_fact(c,documents.document('ir-example'),frame,NumericMatch(selection="measurement",row_id=row.row_id,columns=['c1'],reason='test'),row,cell,'interpretation')
    assert fact.value.value_raw=='1,234.50'
    assert mapping.match=='different'
    assert 'rejected' not in fact.flags and 'unit_is_intensity' not in fact.flags
    from esg_contracts.facts import SourceFact
    extended=SourceFact.model_validate({**fact.model_dump(mode='json'),'future_source_note':{'method':'printed'}})
    assert extended.extensions['future_source_note']=={'method':'printed'}
    assert not mapping.scope_ids

def test_invented_identity_does_not_pollute_source_facts(documents,repository):
    c=quantitative(repository)
    intent=SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='quantitative',positive_terms=['能源'])
    frame=documents.read('ir-example',documents.search('ir-example',intent)[0]);row=frame.rows[0];cell=next(v for v in row.cells if v.column_id=='c1')
    match=NumericMatch(selection="measurement",row_id=row.row_id,columns=['c1'],reason='test',identity=[{'axis':'reporting_entity','value':'Invented Group','quote':'not printed','source_id':row.row_id}])
    fact,mapping,_=numeric_fact(c,documents.document('ir-example'),frame,match,row,cell,'interpretation')
    assert fact.identity.reporting_entity.state=='not_read'
    assert fact.identity.reporting_period.value=='2025'
    assert mapping.match=='match'  # Rejected optional identity does not reject the measurement.
    assert 'unbound_identity:reporting_entity' in fact.extensions['identity_attempts']

def test_statement_requires_a_unique_literal_source(documents,repository):
    c=repository.get('E2-4_09');intent=SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='qualitative',positive_terms=['管理制度'])
    frame=documents.read('ir-example',documents.search('ir-example',intent)[0]);span=frame.texts[0]
    with pytest.raises(ValueError):qualitative_fact(c,documents.document('ir-example'),frame,StatementMatch(span_id=span.span_id,quote='model paraphrase',reason='test'),'i')

def test_independent_clause_edit_does_not_mutate_neighbors(tmp_path,repository):
    for key in ['E1-5_01','E1-5_05']:
        c=repository.get(key);p=tmp_path/c.group_id/key;p.mkdir(parents=True);(p/'clause.json').write_text(c.model_dump_json())
    (tmp_path/'selections.json').write_text(json.dumps({'profiles':{'two':['E1-5_01','E1-5_05']}}))
    copy=ClauseRepository(tmp_path);copy.reindex();before=copy.get('E1-5_05').clause_digest;c=copy.get('E1-5_01')
    changed=copy.save(c.model_copy(update={'definition':c.definition+' Add one precise distinction.'}),expected_digest=c.clause_digest)
    assert changed.clause_digest!=c.clause_digest
    assert copy.get('E1-5_05').clause_digest==before
    with pytest.raises(ValueError):copy.save(c,expected_digest=c.clause_digest)

def test_module_dependencies_do_not_import_retired_routes():
    runtime=ROOT/'ExtractESG/extraction/src/esg_extraction'
    for path in runtime.rglob('*.py'):
        text=path.read_text()
        assert 'esg_standard_packages' not in text
        assert 'foundation16' not in text
        assert 'StandardPackageCatalog' not in text
        assert 'E1-5_01' not in text
    engine=(runtime/'pipeline/engine.py').read_text()
    assert 'from pathlib' not in engine and 'ClauseRepository' not in engine
    for path in (ROOT/'ExtractESG/document/src').rglob('*.py'):
        assert 'esg_clauses' not in path.read_text()

@pytest.mark.parametrize('run,name',[('../other','x.json'),('one','../../outside'),('one','/tmp/outside')])
def test_artifact_store_rejects_escape(tmp_path,run,name):
    with pytest.raises(ValueError):FileArtifactPort(tmp_path).write(run,name,{})


def test_changed_engine_cannot_execute_an_already_created_run(documents,repository,tmp_path,monkeypatch):
    from esg_extraction.pipeline import runtime
    store=RunStore(tmp_path/'runs.db');artifacts=FileArtifactPort(tmp_path/'out')
    request=RunRequest(ir_run_id='ir-example',clause_keys=['E1-5_01'])
    state=store.create(request,repository.freeze(repository.select(request.clause_keys)),documents.document('ir-example').model_dump(mode='json'),engine_fingerprint='0'*64)
    monkeypatch.setattr(runtime,'resources',lambda:(repository,store,artifacts,documents))
    class NeverCalled:
        def complete(self,**kwargs):raise AssertionError('Must fail before model call')
        def close(self):pass
    with pytest.raises(ValueError,match='code changed'):runtime.execute(state['run_id'],model=NeverCalled())
    assert store.get(state['run_id'])['status']=='failed'


def test_repository_concurrent_saves_have_one_winner(tmp_path,repository):
    c=repository.get('E1-5_01');directory=tmp_path/c.group_id/c.clause_key;directory.mkdir(parents=True)
    (directory/'clause.json').write_text(c.model_dump_json())
    (tmp_path/'selections.json').write_text(json.dumps({'profiles':{'one':[c.clause_key]}}))
    copy=ClauseRepository(tmp_path);copy.reindex()
    def save(index):
        try:
            return copy.save(c.model_copy(update={'definition':c.definition+str(index)}),expected_digest=c.clause_digest).clause_digest
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        winners=[value for value in pool.map(save,range(6)) if value]
    assert len(winners)==1 and copy.get(c.clause_key).clause_digest==winners[0]
    assert not list(tmp_path.rglob('*.tmp'))


def test_literal_binding_preserves_whitespace_and_actual_cell_page(documents,repository):
    root=documents.resolve('ir-example');table=json.loads((root/'table.json').read_text())
    cell=next(c for c in table['cells'] if c['cell_id']=='t:r1c1')
    cell.update(text=' 1,234.50\n',page_index=1)
    (root/'table.json').write_text(json.dumps(table))
    c=quantitative(repository);intent=SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='quantitative',positive_terms=['能源'])
    frame=documents.read('ir-example',documents.search('ir-example',intent)[0]);row=next(r for r in frame.rows if r.row_id.endswith(':r1')) if any(r.row_id.endswith(':r1') for r in frame.rows) else frame.rows[0]
    value=next(v for v in row.cells if v.cell_id=='t:r1c1')
    fact,_,_=numeric_fact(c,documents.document('ir-example'),frame,NumericMatch(selection="measurement",row_id=row.row_id,columns=['c1'],reason='literal'),row,value,'test')
    assert fact.value.value_raw==' 1,234.50\n'
    assert fact.evidence[0].page_index==1 and fact.evidence[0].object_id=='t:r1c1'
    assert fact.identity.reporting_period.evidence[0].object_id=='t:r0c1'
    from esg_contracts.facts import SourceFact
    altered=fact.model_dump(mode='json');altered['value']['value_raw']='invented'
    with pytest.raises(ValueError,match='exact raw value'):SourceFact.model_validate(altered)


@pytest.mark.parametrize('header,quarter',[('2025 Q2',2),('2025年第四季度',4)])
def test_quarter_headers_are_not_annualized(documents,repository,header,quarter):
    root=documents.resolve('ir-example');path=root/'table.json';table=json.loads(path.read_text())
    next(c for c in table['cells'] if c['cell_id']=='t:r0c1')['text']=header
    path.write_text(json.dumps(table))
    c=quantitative(repository);intent=SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='quantitative',positive_terms=['能源'])
    frame=documents.read('ir-example',documents.search('ir-example',intent)[0]);row=frame.rows[0];cell=next(v for v in row.cells if v.column_id=='c1')
    fact,mapping,_=numeric_fact(c,documents.document('ir-example'),frame,NumericMatch(selection="measurement",row_id=row.row_id,columns=['c1'],reason='period'),row,cell,'test')
    assert fact.identity.reporting_period.value=={'kind':'quarter','year':2025,'quarter':quarter,'raw':header}
    assert not mapping.scope_ids


def test_text_character_offsets_bind_unnormalized_ir_text(documents,repository):
    root=documents.resolve('ir-example');path=root/'page.json';page=json.loads(path.read_text())
    raw='  我们已实施\n污染物排放管理制度，  定期监测。'
    page['blocks'][0]['text']=raw;path.write_text(json.dumps(page))
    c=repository.get('E2-4_09');intent=SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='qualitative',positive_terms=['管理制度'],source_priority=['text'])
    frame=documents.read('ir-example',documents.search('ir-example',intent)[0]);span=frame.texts[0]
    fact,_,_=qualitative_fact(c,documents.document('ir-example'),frame,StatementMatch(span_id=span.span_id,quote='污染物排放管理制度，  定期监测。',reason='literal'),'test')
    evidence=fact.evidence[0]
    assert raw[evidence.char_start:evidence.char_end]==fact.value.statement_raw


@pytest.mark.parametrize('raw,comparator',[('>3','greater_than'),('≥3','at_least'),('<3','less_than'),('≤3','at_most'),('小于3','less_than')])
def test_comparators_keep_open_and_closed_bounds_distinct(raw,comparator):
    from esg_extraction.pipeline.binding import _number
    parsed,actual,_=_number(raw)
    assert actual==comparator and (parsed['lower'] or parsed['upper'])==3


def test_clause_evidence_is_evaluated_without_dropping_raw_facts(documents,repository):
    c=quantitative(repository);intent=SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='quantitative',positive_terms=['能源'])
    frame=documents.read('ir-example',documents.search('ir-example',intent)[0]);row=frame.rows[0];cell=next(v for v in row.cells if v.column_id=='c1')
    fact,mapping,_=numeric_fact(c,documents.document('ir-example'),frame,NumericMatch(selection="measurement",row_id=row.row_id,columns=['c1'],reason='literal'),row,cell,'test')
    assert fact.value.value_raw=='1,234.50'
    assert 'clause_evidence_missing:unit:row' in mapping.flags  # This fixture has no physically bound unit.
    assert mapping.match=='match'  # Comparison proof is independent of reception.


def test_model_failure_keeps_partial_diagnostic_and_never_invents_a_fact(documents,repository,tmp_path):
    from esg_extraction.models.errors import ModelRunFailure
    from esg_extraction.pipeline.engine import ExtractionBackbone
    class Incomplete:
        def complete(self,**kwargs):
            raise ModelRunFailure('token budget',category='generation_incomplete',raw_output='{"matches":[',telemetry={'finish_reason':'length'})
    c=quantitative(repository);artifacts=FileArtifactPort(tmp_path/'out')
    result=ExtractionBackbone(documents,Incomplete(),artifacts).run('failure',RunRequest(ir_run_id='ir-example',clause_keys=[c.clause_key]),{c.clause_key:c})
    assert not result['facts'] and result['coverage'][0]['state']=='not_covered'
    diagnostic=artifacts.read('failure','diagnostics/model-calls.json')[0]
    assert diagnostic['failure_category']=='generation_incomplete'
    assert diagnostic['incomplete_output']=='{"matches":['


def test_ir_structural_cache_cannot_hide_an_evidence_file_edit(documents):
    before=documents.document('ir-example')
    path=documents.resolve('ir-example')/'page.json';body=json.loads(path.read_text());body['blocks'][0]['text']+='新证据';path.write_text(json.dumps(body))
    after=documents.document('ir-example')
    assert before.content_digest!=after.content_digest


def test_document_checkout_discovery_does_not_require_a_clause_catalog(tmp_path):
    from esg_runtime.layout import project_root
    (tmp_path/'ExtractESG').mkdir();(tmp_path/'PlatESG').mkdir()
    anchor=tmp_path/'ExtractESG'/'some'/'module.py'
    assert project_root(anchor)==tmp_path


def test_model_reference_schema_excludes_target_and_resource_metadata(documents,repository):
    from esg_extraction.pipeline.decisions import decision_schema
    c=quantitative(repository);intent=SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='quantitative',positive_terms=['能源'])
    frame=documents.read('ir-example',documents.search('ir-example',intent)[0])
    schema=decision_schema('quantitative',frame)
    ids=schema['$defs']['IdentityProposal']['properties']['source_id']['enum']
    assert 'target' not in ids and 'frame' not in ids and 'doc-example' not in ids
    assert 'c1' in ids and 'r1.c1' in ids
    assert 't:r0c1' not in ids and 't:r1c1' not in ids
    assert all(branch['properties']['row_id']['enum']==[r.row_id for r in frame.rows] for branch in schema['$defs']['NumericMatch']['anyOf'])


def test_mutable_report_labels_do_not_replace_the_frozen_reading_context(documents,repository,tmp_path,monkeypatch):
    from esg_extraction.pipeline import runtime
    from esg_extraction.pipeline.engine import ExtractionBackbone
    store=RunStore(tmp_path/'runs.db');artifacts=FileArtifactPort(tmp_path/'out')
    request=RunRequest(ir_run_id='ir-example',clause_keys=['E1-5_01'])
    frozen=documents.document('ir-example')
    run=store.create(request,repository.freeze(repository.select(request.clause_keys)),frozen.model_dump(mode='json'),engine_fingerprint=runtime.engine_digest())
    monkeypatch.setattr(runtime,'resources',lambda:(repository,store,artifacts,documents))
    monkeypatch.setattr(documents,'document',lambda key:frozen.model_copy(update={'label':'corrected label','reporting_year':2030}))
    received=[]
    def read(self,*args,document,**kwargs):
        received.append(document)
        return {'coverage':[],'organization':{'status':'organized'},'summary':{'selected_clauses':1,'processed_clauses':0,'source_facts':0,'quantitative':0,'qualitative':0,'model_calls':0,'elapsed_seconds':0,'assessment':{'execution_state':'completed'}}}
    monkeypatch.setattr(ExtractionBackbone,'run',read)
    class Model:
        def close(self):pass
    runtime.execute(run['run_id'],model=Model())
    assert received==[frozen]
    assert received[0].reporting_year!=2030


def test_physical_evidence_change_still_refuses_execution(documents,repository,tmp_path,monkeypatch):
    from esg_extraction.pipeline import runtime
    store=RunStore(tmp_path/'runs.db');artifacts=FileArtifactPort(tmp_path/'out')
    request=RunRequest(ir_run_id='ir-example',clause_keys=['E1-5_01']);frozen=documents.document('ir-example')
    run=store.create(request,repository.freeze(repository.select(request.clause_keys)),frozen.model_dump(mode='json'),engine_fingerprint=runtime.engine_digest())
    monkeypatch.setattr(runtime,'resources',lambda:(repository,store,artifacts,documents))
    monkeypatch.setattr(documents,'document',lambda key:frozen.model_copy(update={'content_digest':'f'*64}))
    class Model:
        def close(self):pass
    with pytest.raises(ValueError,match='Document evidence changed'):runtime.execute(run['run_id'],model=Model())
    assert store.get(run['run_id'])['status']=='failed'
