import io
import json
from hashlib import sha256
from zipfile import ZipFile, ZIP_DEFLATED
import pytest
from esg_contracts.facts import RunRequest
from esg_extraction.pipeline.state import RunStore
from esg_extraction.pipeline.exporting import seal_export, export_archive, json_bytes
from esg_extraction.pipeline.engine import ExtractionBackbone
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_database.ingestion import decode_archive
from test_backbone import repository, documents, Model, quantitative


def finished_export(tmp_path, documents, repository):
    clause=quantitative(repository)
    store=RunStore(tmp_path/'runs.sqlite');artifacts=FileArtifactPort(tmp_path/'artifacts')
    request=RunRequest(ir_run_id='ir-example',clause_keys=[clause.clause_key])
    run=store.create(request,repository.freeze({clause.clause_key:clause}),documents.document('ir-example').model_dump(mode='json'))
    ExtractionBackbone(documents,Model(),artifacts).run(run['run_id'],request,{clause.clause_key:clause})
    store.update(run['run_id'],status='completed')
    manifest=seal_export(run['run_id'],store,artifacts)
    return run['run_id'],store,artifacts,manifest


def test_sealed_export_survives_producer_independent_reception(tmp_path,documents,repository):
    run,store,artifacts,manifest=finished_export(tmp_path,documents,repository)
    assert seal_export(run,store,artifacts)==manifest
    payload=decode_archive(export_archive(run,manifest,artifacts))
    assert payload['manifest']['export_id']==manifest.export_id
    assert len(payload['facts'])==3 and not payload['rejected']
    assert any(row['fact']['identity']['reporting_period']['value']=='2024' and row['fact']['value']['value_raw']=='1,100' for row in payload['facts'])
    assert manifest.authority=='facts' and manifest.schema_version=='extraction-export-1.1'
    assert any(row['fact']['value']['value_raw']=='1,234.50' for row in payload['facts'])
    assert all(row['fact']['identity']['reporting_entity']['value'] is None for row in payload['facts'])
    assert payload['manifest']['publication_status']=='not_evaluated'


def test_mutated_sealed_member_is_not_silently_exported(tmp_path,documents,repository):
    run,_,artifacts,manifest=finished_export(tmp_path,documents,repository)
    artifacts.path(run,f'exports/{manifest.export_id}/facts.json').write_text('[]')
    with pytest.raises(ValueError,match='changed'):
        export_archive(run,manifest,artifacts)


def test_receiver_keeps_good_rows_when_another_fact_has_wrong_document(tmp_path,documents,repository):
    run,_,artifacts,manifest=finished_export(tmp_path,documents,repository)
    archive=export_archive(run,manifest,artifacts)
    with ZipFile(io.BytesIO(archive)) as zip:
        members={name:json.loads(zip.read(name)) for name in zip.namelist()}
    members['facts.json'][1]['document_id']='different-document'
    descriptor=members['manifest.json']
    for file in descriptor['files']:
        data=json_bytes(members[file['name']]);file['bytes']=len(data);file['sha256']=sha256(data).hexdigest()
    descriptor['export_id']='export-'+sha256(json_bytes([descriptor['schema_version'],run,*[file['sha256'] for file in descriptor['files']]])).hexdigest()
    stream=io.BytesIO()
    with ZipFile(stream,'w',compression=ZIP_DEFLATED) as zip:
        for name,body in members.items():zip.writestr(name,json_bytes(body))
    result=decode_archive(stream.getvalue())
    assert len(result['facts'])==2 and len(result['rejected'])==1


def test_unknown_delivery_checks_receipt_before_replaying_a_write(tmp_path, documents, repository, monkeypatch):
    import requests
    from esg_extraction.pipeline.delivery import deliver
    run, store, artifacts, manifest = finished_export(tmp_path, documents, repository)
    store.update(run, delivery={'export_id':manifest.export_id,'state':'prepared'})
    class Client:
        trust_env = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs):
            response = requests.Response();response.status_code=404;return response
        def post(self, *args, **kwargs): raise requests.Timeout('Response lost after submission')
    monkeypatch.setattr(requests, 'Session', Client)
    state=deliver(run,store,artifacts,'http://127.0.0.1:18280')
    assert state['delivery']['state']=='unknown'
    receipt={'schema_version':'extraction-receipt-1.0','export_id':manifest.export_id,
        'input_digest':sha256(json_bytes(manifest.model_dump(mode='json'))).hexdigest(),
        'receipt_id':'receipt','status':'accepted','accepted_facts':3,'rejected_facts':0,
        'dispositions':[],'publication_status':'not_evaluated','received_at':'2026-10-30T00:00:00Z'}
    class Recovered(Client):
        def get(self,*args,**kwargs):
            response=requests.Response();response.status_code=200;response._content=json.dumps(receipt).encode();return response
        def post(self,*args,**kwargs):raise AssertionError('Committed delivery must not be replayed')
    monkeypatch.setattr(requests,'Session',Recovered)
    assert deliver(run,store,artifacts,'http://127.0.0.1:18280')['delivery']['state']=='delivered'


def test_permanent_receiver_rejection_does_not_retry_forever(tmp_path, documents, repository, monkeypatch):
    import requests
    from esg_extraction.pipeline.delivery import deliver
    run,store,artifacts,manifest=finished_export(tmp_path,documents,repository)
    store.update(run,delivery={'export_id':manifest.export_id,'state':'prepared'})
    class Client:
        trust_env=False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,*args,**kwargs):
            response=requests.Response();response.status_code=404;return response
        def post(self,*args,**kwargs):
            response=requests.Response();response.status_code=422;return response
    monkeypatch.setattr(requests,'Session',Client)
    delivery=deliver(run,store,artifacts,'http://127.0.0.1:18280')['delivery']
    assert delivery['state']=='failed' and not delivery['retryable']


def repack(members):
    descriptor=members['manifest.json']
    for member in descriptor['files']:
        body=json_bytes(members[member['name']])
        member['bytes']=len(body);member['sha256']=sha256(body).hexdigest()
    descriptor['export_id']='export-'+sha256(json_bytes([descriptor['schema_version'],descriptor['run_id'],
        *[member['sha256'] for member in sorted(descriptor['files'],key=lambda row:row['role'])]])).hexdigest()
    output=io.BytesIO()
    with ZipFile(output,'w',compression=ZIP_DEFLATED) as archive:
        for name,body in members.items():archive.writestr(name,json_bytes(body))
    return output.getvalue()


def test_conflicting_annotation_view_is_rebuilt_without_losing_true_facts(tmp_path,documents,repository):
    run,_,artifacts,manifest=finished_export(tmp_path,documents,repository)
    with ZipFile(io.BytesIO(export_archive(run,manifest,artifacts))) as archive:
        members={name:json.loads(archive.read(name)) for name in archive.namelist()}
    members['properties.json'][0]['value']='2099'
    received=decode_archive(repack(members))
    assert len(received['facts'])==3 and not received['rejected']
    assert {'code':'annotation_projection_rebuilt','member':'properties','authority':'facts'} in received['diagnostics']
    assert not any(row.get('value')=='2099' for row in received['projections']['properties'])


def test_retired_export_contract_is_rejected(tmp_path,documents,repository):
    run,_,artifacts,manifest=finished_export(tmp_path,documents,repository)
    with ZipFile(io.BytesIO(export_archive(run,manifest,artifacts))) as archive:
        members={name:json.loads(archive.read(name)) for name in archive.namelist()}
    members['manifest.json']['schema_version']='extraction-export-1.0'
    members['manifest.json'].pop('authority')
    with pytest.raises(ValueError):
        decode_archive(repack(members))
