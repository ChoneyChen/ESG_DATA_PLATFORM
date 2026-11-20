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
from esg_contracts.evidence import SearchIntent
from test_backbone import documents,repository,quantitative,ROOT,run_backbone

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

@pytest.mark.parametrize("provider,model_id", [("deepseek", "deepseek-v4-pro"), ("dashscope", "qwen3.8-omni-flash")])
def test_cloud_provider_is_frozen_and_its_credential_is_write_only(documents, repository, tmp_path, monkeypatch, provider, model_id):
    import stat
    from esg_extraction.pipeline import runtime
    from esg_extraction.pipeline.adapters import dashscope_model, deepseek_model
    from esg_extraction.pipeline.adapters.model_credentials import ModelCredentialStore
    from test_backbone import Model

    store = RunStore(tmp_path / "runs.db")
    artifacts = FileArtifactPort(tmp_path / "out")
    resources = lambda: (repository, store, artifacts, documents)
    app = create_app(resources)
    operator = TestClient(app, client=("127.0.0.1", 50000), headers={"Origin": "http://127.0.0.1:18081"})
    public = TestClient(app, client=("192.0.2.1", 50000))
    write_endpoint = f"/api/model-providers/{provider}/credential"
    sample = "fixture-key-do-not-echo"
    cloud = {"provider": provider, "model": model_id}
    raw = {"ir_run_id": "ir-example", "clause_keys": ["E1-5_01"]}
    request = {**raw, "model_selection": cloud}
    if provider == "dashscope":
        request["document_model_selection"] = cloud

    assert "model_selection" not in RunRequest.model_validate(raw).model_dump()
    assert operator.post("/api/runs", json={"request": request}).status_code == 409
    assert public.put(write_endpoint, json={"api_key": sample}).status_code == 403
    wrong_origin = operator.put(write_endpoint, json={"api_key": sample}, headers={"Origin": "https://example.net"})
    assert wrong_origin.status_code == 403
    invalid = operator.put(write_endpoint, json={"api_key": [], sample: True})
    assert invalid.status_code == 422 and sample not in invalid.text
    response = operator.put(write_endpoint, json={"api_key": sample})
    assert response.status_code == 200 and sample not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert operator.get("/api/model-providers").json() == response.json()
    assert operator.get(write_endpoint).status_code == 405
    credential = ModelCredentialStore(store, provider)
    assert stat.S_IMODE(credential.path.stat().st_mode) == 0o600
    assert credential.read() == sample
    other_provider = "dashscope" if provider == "deepseek" else "deepseek"
    other_model = "qwen3.8-omni-flash" if provider == "deepseek" else "deepseek-flash"
    assert not ModelCredentialStore(store, other_provider).configured()
    assert operator.post("/api/runs/preview", json={**raw, "model_selection": {**cloud, "model": other_model}}).status_code == 422
    unconfigured_document = {**request, "document_model_selection": {"provider": other_provider, "model": other_model}}
    assert operator.post("/api/runs/preview", json=unconfigured_document).status_code == 409

    preview = operator.post("/api/runs/preview", json=request).json()
    assert preview["model_selection"] == cloud
    if provider == "dashscope":
        assert preview["document_model_selection"] == cloud
    created = operator.post("/api/runs", json={"request": request})
    assert created.status_code == 201 and sample not in created.text
    run = created.json()["run_id"]
    assert operator.post(f"/api/runs/{run}/cancel").json()["status"] == "cancelled"
    resumed = operator.post(f"/api/runs/{run}/resume", json={}).json()
    assert resumed["request"]["model_selection"] == cloud

    models = []

    class VisionModel(Model):
        def complete(self, **kwargs):
            if "page_indices" in kwargs["context"] and "clauses" not in kwargs["context"]:
                self.calls.append(kwargs)
                return {"page_indices": kwargs["context"]["page_indices"]}, {}
            return super().complete(**kwargs)

    def fake_model(model_id, key):
        assert model_id == cloud["model"] and key == sample
        model = VisionModel()
        models.append(model)
        return model

    monkeypatch.setattr(runtime, "resources", resources)
    if provider == "deepseek":
        monkeypatch.setattr(deepseek_model, "DeepSeekModel", fake_model)
    else:
        monkeypatch.setattr(dashscope_model, "DashScopeModel", fake_model)
    for _ in range(10):
        result = runtime.execute(run)
        if result["status"] == "completed":
            break
    assert result["status"] == "completed" and any(model.calls for model in models)
    assert artifacts.read(run, "input/engine.json")["model_selection"] == cloud
    assert sample.encode() not in store.path.read_bytes()


def test_candidate_diagnostics_keep_existing_row_span_and_literal_records(tmp_path, repository, documents):
    store = RunStore(tmp_path / 'runs.db')
    artifacts = FileArtifactPort(tmp_path / 'out')
    keys = ['E1-5_01', 'E2-4_09']
    run = store.create(RunRequest(ir_run_id='ir-example', clause_keys=keys),
                       repository.freeze(repository.select(keys)),
                       documents.document('ir-example').model_dump(mode='json'))
    run_id = run['run_id']
    path = 'diagnostics/candidates.json'
    rows = [
        {'clause_key': keys[0], 'object_id': 'table', 'row_id': 'r1',
         'selection': 'different_target', 'reason': 'clause_reading_rule'},
        {'clause_key': keys[1], 'object_id': 'block', 'span_id': 'span-1',
         'selection': 'different_target', 'reason': 'clause_reading_rule'},
        {'clause_key': keys[0], 'object_id': 'table', 'cell_id': 'r2-c2',
         'value_raw': '305-7', 'candidate_kind': 'identifier',
         'selection': 'nonmeasurement', 'reason': 'physical_literal_type'},
    ]
    artifacts.write(run_id, path, rows)
    app = create_app(lambda: (repository, store, artifacts, documents))
    response = TestClient(app).get(f'/api/runs/{run_id}/candidates')
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == len(rows)
    for actual, written in zip(payload, rows):
        assert all(actual[key] == value for key, value in written.items())
    assert [row['candidate_kind'] for row in payload] == [None, None, 'identifier']
    assert artifacts.read(run_id, path) == rows


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
