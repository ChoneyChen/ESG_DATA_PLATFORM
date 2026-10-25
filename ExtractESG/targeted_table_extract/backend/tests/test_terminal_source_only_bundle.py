"""Terminal handoff contracts; tmp stores only, no model or production writes.

The optional real replay is enabled explicitly by ESG_TEST_TERMINAL_JOB. A
configured missing path fails. It tests retention, never real ESG correctness.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from esg_targeted.contracts import JobStatus
from esg_targeted.results.exporter import ResultExporter
from esg_targeted.results.stages import collect_stage, publish_stage_from_inputs
from esg_targeted.results.terminal_bundle import (materialize_terminal_bundle, read_terminal_revision,
    delivery_artifacts, terminal_snapshot)
from esg_targeted.results.validation import ResultContractError
from esg_targeted.storage.artifacts import ArtifactStore
from tests.test_report_repair_regressions import _stage_session
from tests.test_report_api_submission import api_client
from tests.test_report_session import request, make_service


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_only(tmp_path):
    service, session_id, job_id, metrics, _, facts, _ = _stage_session(tmp_path)
    root = service.artifacts.job_dir(job_id)
    for path in (root/'checkpoints').glob('*.json'):
        path.unlink()
    for path in (root/'disclosures/projections').glob('*.json'):
        path.unlink()
    fact = deepcopy(facts[0])
    fact.update(quality_status='needs_confirmation', identity_raw={}, identity_evidence={}, unit_raw=None,
        value_raw='001.2300', unverified_identity_raw={'unknown_note': '不填入规范身份'},
        semantic_reading={'reported_attributes': {'unknown_long_tail': ['原值A', '原值B']}})
    service.artifacts.write_json(job_id,'agent/sources/source-only.json', {'object_id':'neutral-only','facts':[fact],
        'rows':[],'complete':False,'gaps':[]})
    (root/'agent/sources/anonymous-table.json').unlink()
    service.jobs.update(job_id,status=JobStatus.FAILED,error='isolated budget exhausted')
    service._mutate(session_id,lambda state:state.update(execution_status='failed',owner_pid=None,owner_started=None))
    return service,session_id,job_id,metrics,fact


def test_terminal_source_only_revision_preserves_raw_payload_scope_and_old_artifacts(tmp_path):
    service,session_id,job_id,metrics,fact = _source_only(tmp_path)
    root=service.artifacts.job_dir(job_id)
    service.artifacts.write_json(job_id,'manifest.json',{'job_id':job_id,'original_failure_record':True})
    original={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file()}
    saved=service.materialize(session_id,reason='隔离预算终态保全',requested_by='isolated-reviewer')
    artifact=saved['materializations'][0]
    assert artifact['source_fact_count']==1 and artifact['core_fact_count']==0
    revision=root/artifact['bundle_relative_path']
    payload=json.loads((revision/'exports/extraction-result.json').read_text())
    assert payload['source_facts']['facts']==[fact]
    assert all(not rows for rows in payload['records'].values())
    assert payload['summary']['result_status']=='needs_confirmation'
    assert payload['summary']['processing_metric_ids']==[]
    assert json.loads((revision/'input/request.json').read_text())['metric_ids']==metrics
    assert json.loads((revision/'input/report-scope.json').read_text())==service._state(session_id)['request']
    assert payload['summary']['artifact_revision']['parent_manifest_sha256']==original['manifest.json']
    assert {name:sha(root/name) for name in original}==original
    second=service.materialize(session_id,reason='隔离幂等重试',requested_by='isolated-reviewer')['materializations'][0]
    assert second['artifact_revision_id']==artifact['artifact_revision_id'] and second['idempotent']
    detail=service.detail(session_id)
    assert detail['delivery_counts']['saved_source_facts']==1 and detail['delivery_counts']['saved_core_facts']==0
    assert {action['action'] for action in detail['available_actions']} >= {'materialize','deliver'}


def test_injected_export_failure_is_atomic_and_retryable(tmp_path,monkeypatch):
    service,session_id,job_id,_,_= _source_only(tmp_path)
    root=service.artifacts.job_dir(job_id)
    original={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file()}
    real=ResultExporter.export
    def fail(*args,**kwargs):
        real(*args,**kwargs)
        raise OSError('isolated crash before immutable revision commit')
    monkeypatch.setattr(ResultExporter,'export',fail)
    with pytest.raises(OSError,match='isolated crash'):
        service.materialize(session_id,reason='隔离故障注入',requested_by='isolated-reviewer')
    assert not list((root/'terminal-revisions').iterdir())
    assert {name:sha(root/name) for name in original}==original
    monkeypatch.setattr(ResultExporter,'export',real)
    assert service.materialize(session_id,reason='隔离重试',requested_by='isolated-reviewer')['materializations'][0]['status']=='available'


def test_cross_document_and_changed_revision_are_rejected_without_reinterpreting_sources(tmp_path):
    service,_,job_id,_,_= _source_only(tmp_path)
    root=service.artifacts.job_dir(job_id)
    snapshot=collect_stage(service.artifacts,job_id,use_snapshot=False)
    foreign=deepcopy(snapshot);foreign['source_facts']['facts'][0]['document_id']='another-report'
    with pytest.raises(ResultContractError,match='differs from current durable fragments'):
        materialize_terminal_bundle(root,job_id,'failed',snapshot=foreign)
    assert not (root/'terminal-revisions').exists()
    artifact=materialize_terminal_bundle(root,job_id,'failed',snapshot=snapshot)
    revision=root/artifact['bundle_relative_path']
    (revision/'results/source-facts.json').write_text('{}')
    with pytest.raises(ResultContractError,match='content changed'):
        read_terminal_revision(root,revision)
    assert not delivery_artifacts(root)['items'] and delivery_artifacts(root)['blocked']


def test_durable_byte_proof_and_source_quality_include_unique_internal_dependencies(tmp_path,monkeypatch):
    from esg_targeted.results.terminal_bundle import _validate_sources, _quality_counts
    service,session_id,job_id,_,fact= _source_only(tmp_path)
    root=service.artifacts.job_dir(job_id)
    snapshot=collect_stage(service.artifacts,job_id,use_snapshot=False)
    internal=deepcopy(fact);internal.update(source_fact_id='internal-extra',quality_status='unbound',physical_sources=[])
    snapshot['internal_dependency_scope']={'source_facts':[deepcopy(fact),internal]}
    unique=_validate_sources(snapshot,service.jobs.get(job_id).request)
    assert len(unique)==2 and unique['internal-extra']['quality_status']=='unbound'
    assert _quality_counts(unique)=={'needs_confirmation':1,'unbound':1}
    artifact=service.materialize(session_id,reason='原始片段SHA保全',requested_by='isolated-reviewer')['materializations'][0]
    revision=root/artifact['bundle_relative_path']
    proof=json.loads((revision/'artifact-revision.json').read_text())
    assert proof['frozen_master_request']['file_sha256']==sha(service.artifacts.report_session_path(session_id)/'request.json')
    assert proof['durable_fragment_inputs']['job:agent/sources/source-only.json']['sha256']==sha(root/'agent/sources/source-only.json')
    assert proof['durable_fragment_inputs']['job:agent/sources/source-only.json']['role']=='source_interpretation'
    # Mutating a consumed checkpoint after collection must abort the new revision.
    real=ResultExporter.export
    source=root/'agent/sources/source-only.json'
    before=source.read_bytes()
    def mutate(*args,**kwargs):
        result=real(*args,**kwargs);source.write_bytes(before+b' ');return result
    monkeypatch.setattr(ResultExporter,'export',mutate)
    # A different terminal reason produces a new content-addressed revision.
    with pytest.raises(ResultContractError,match='Durable fragments changed'):
        materialize_terminal_bundle(root,job_id,'failed','isolated next revision')
    source.write_bytes(before)
    assert len(delivery_artifacts(root)['items'])==1


def test_live_owner_running_scope_unknown_jobs_and_empty_candidate_scope_do_not_export(tmp_path,monkeypatch):
    service,session_id,job_id,_,_= _source_only(tmp_path)
    service._mutate(session_id,lambda state:state.update(execution_status='running'))
    with pytest.raises(ValueError,match='terminal'):
        service.materialize(session_id,reason='不能运行中保存',requested_by='isolated-reviewer')
    service._mutate(session_id,lambda state:state.update(execution_status='failed'))
    with pytest.raises(ValueError,match='original session jobs'):
        service.materialize(session_id,reason='范围不能越界',requested_by='isolated-reviewer',job_ids=['other'])
    monkeypatch.setattr(service.jobs,'has_live_worker',lambda *_:True)
    with pytest.raises(ValueError,match='worker'):
        service.materialize(session_id,reason='需生产者停止',requested_by='isolated-reviewer')
    assert not (service.artifacts.job_dir(job_id)/'terminal-revisions').exists()
    with pytest.raises(ResultContractError,match='terminal'):
        terminal_snapshot(collect_stage(service.artifacts,job_id),'running')


def test_future_report_budget_terminal_settles_unentered_children_without_false_completion(tmp_path):
    calls=[]
    class BudgetFailure:
        def __init__(self,runtime,jobs,artifacts):self.jobs=jobs
        def run(self,job_id):
            calls.append(job_id)
            self.jobs.update(job_id,status=JobStatus.FAILED,error='isolated report budget',
                summary={'failure_category':'budget','budget_boundary':'report'})
    service=make_service(tmp_path,BudgetFailure)
    report=service.create(request())
    with pytest.raises(Exception,match='Report runtime budget exhausted'):
        service.run(report['session_id'])
    assert len(calls)==1
    detail=service.detail(report['session_id'])
    assert detail['execution_status']=='failed'
    assert [job['stage'] for job in detail['jobs'][1:]]==['not_executed','not_executed']
    assert all(job['status']!='queued' for job in detail['jobs'])
    assert detail['counts']['processed_metrics']==detail['counts']['core_facts']==0
    assert detail['counts']['selected_metrics']==3 and detail['counts']['not_executed_metrics']==3
    assert not detail['delivery_artifacts']


def test_native_materialization_api_is_authorized_bounded_and_model_free(api_client,monkeypatch):
    client,api,queue_calls=api_client
    report=client.post('/api/report-sessions?enqueue=false',json=request().model_dump(mode='json')).json()
    url=f"/api/report-sessions/{report['session_id']}/materialize"
    assert client.post(url,json={'reason':'隔离保存'}).status_code==401
    from fastapi import HTTPException
    monkeypatch.setattr(api,'_materialization_operator',lambda *_:'isolated-authoritative-reviewer')
    assert client.post(url,json={'reason':'隔离保存'}).status_code==409
    client.post(f"/api/report-sessions/{report['session_id']}/cancel?terminal=true")
    saved=client.post(url,json={'reason':'隔离保存'},headers={'Authorization':'Bearer isolated-token'})
    assert saved.status_code==200 and all(item['status']=='no_retained_facts' for item in saved.json()['materializations'])
    assert not queue_calls and not saved.json()['approval_or_publication_performed']
    assert client.post(url,json={'reason':'隔离保存','requested_by':'forged'},headers={'Authorization':'Bearer isolated-token'}).status_code==422
    def deny(_):raise HTTPException(403,'isolated denied')
    monkeypatch.setattr(api,'_materialization_operator',deny)
    assert client.post(url,json={'reason':'隔离保存'},headers={'Authorization':'Bearer isolated-token'}).status_code==403


@pytest.mark.skipif(not os.environ.get('ESG_TEST_TERMINAL_JOB'),reason='explicit real terminal artifact not supplied')
def test_actual_v4_budget_fragments_replay_preserves_291_unconfirmed_sources_and_17_scope(tmp_path):
    original=Path(os.environ['ESG_TEST_TERMINAL_JOB']).resolve()
    assert original.is_dir(), 'Explicit real artifact must exist'
    root=tmp_path/'output'/original.name;root.mkdir(parents=True)
    copied={}
    for prefix in ('input','agent/sources','checkpoints','agent/mapping-fragments','disclosures/projections'):
        for path in (original/prefix).rglob('*'):
            if path.is_file():
                relative=path.relative_to(original);target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,target);copied[str(relative)]=sha(path)
    for relative in ('agent/state.json','agent/source-invalidations.json','progress/metric-attempts.json',
                     'progress/core-postprocessing.json','results/internal-dependencies.json','failure.json','manifest.json'):
        path=original/relative
        if path.is_file():
            target=root/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target);copied[relative]=sha(path)
    child=json.loads((root/'input/request.json').read_text())
    session=child['report_session_id']
    master=original.parent/'.report-sessions'/session/'request.json'
    target=root.parent/'.report-sessions'/session/'request.json';target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(master,target)
    invalidations=master.parent/'source-invalidations.json'
    if invalidations.is_file():shutil.copyfile(invalidations,target.parent/invalidations.name)
    snapshot=collect_stage(ArtifactStore(root.parent),root.name,use_snapshot=False)
    facts=snapshot['source_facts']['facts']
    assert len(facts)==291 and all(f['quality_status']=='needs_confirmation' and f['identity_raw']=={} for f in facts)
    artifact=materialize_terminal_bundle(root,root.name,'failed','BudgetExceeded: real terminal budget',snapshot=snapshot,
        reason='Actual v4 read-only artifact copied into isolated replay',requested_by='isolated-contract-test')
    revision=root/artifact['bundle_relative_path']
    bundle=json.loads((revision/'exports/extraction-result.json').read_text())
    assert bundle['source_facts']['facts']==facts and not bundle['metric_source_mappings']['core_bindings']
    assert not bundle['records']['quantitative_observations'] and not bundle['records']['qualitative_assertions']
    scope=json.loads((revision/'input/report-scope.json').read_text())
    assert len(scope['selection_snapshot']['source_datapoint_ids'])==16 and len(scope['extension_selections'])==1
    assert bundle['summary']['result_status']=='needs_confirmation' and artifact['source_fact_count']==291
    assert {relative:sha(original/relative) for relative in copied}==copied
    assert {relative:sha(root/relative) for relative in copied}==copied
    if output:=os.environ.get('ESG_TERMINAL_REPLAY_RECEIPT'):
        # Other pytest processes may retire tmp_path before the Node replay.
        # Preserve this isolated materialization in the external proof folder.
        persistent=Path(output).parent/'isolated-v4'/root.parent.name/root.name
        persistent.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(root,persistent,dirs_exist_ok=True)
        persistent_shared=persistent.parent/'.report-sessions'/session
        persistent_shared.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(master,persistent_shared/'request.json')
        if invalidations.is_file():shutil.copyfile(invalidations,persistent_shared/invalidations.name)
        frozen_revision=persistent/artifact['bundle_relative_path']
        proof={'mode':'actual_v4_isolated_copy_no_runtime_mutation','original_job_id':original.name,'source_count':291,
            'core_count':0,'quality_counts':artifact['quality_counts'],'full_scope':17,'bundle_path':str(frozen_revision),
            'test_tmp_bundle_path':str(revision),
            'original_sha256':copied,'master_request_sha256':sha(master),'artifact':artifact,
            'real_report_semantic_accuracy_accepted':False,'database_writes':0,'models_called':0}
        Path(output).parent.mkdir(parents=True,exist_ok=True);Path(output).write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')


def test_completed_projection_materializes_without_an_explicit_handoff(tmp_path):
    service, session_id, job_id, metrics, *_ = _stage_session(tmp_path)
    service.jobs.update(job_id, status=JobStatus.COMPLETED)
    service._mutate(session_id, lambda state: state.update(execution_status='finished',
        owner_pid=None, owner_started=None))
    service._finalize_terminal_projections(session_id)
    detail = service.detail(session_id)
    available = [item for item in detail['delivery_artifacts'] if item['status'] == 'available']
    assert available, detail['delivery_artifact_blockers']
    assert available[0]['source_fact_count'] >= 1
    assert detail['delivery_counts']['available_bundle_count'] >= 1
