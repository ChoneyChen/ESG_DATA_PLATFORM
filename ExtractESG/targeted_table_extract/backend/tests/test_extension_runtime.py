"""Optional extension execution, with synthetic approval and no real model calls."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from esg_standard_packages.extensions import (
    ExtensionProposal, ExtensionRegistry, ExtensionSelection, ReviewAuthority,
    compile_extension, decide_proposal, register_extension, write_extension_release,
)
from esg_standard_packages.errors import PackageIntegrityError
from esg_standard_packages.io import load_json
from esg_targeted.config import settings
from esg_targeted.contracts import ExtractionRequest, ReportExtractionRequest, JobStatus
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.runtime_snapshot import (
    RuntimeStandardView, execution_catalog_dict, freeze_runtime_snapshot,
    freeze_extraction_request, load_runtime_package, metric_binding_identity,
    validate_runtime_snapshot, deferred_extension_result,
)
from esg_targeted.results.exporter import ResultExporter
from esg_targeted.results.spec import RECORD_LAYOUT
from esg_targeted.results.validation import ResultContractValidator
from esg_targeted.results.stages import collect_stage
from esg_targeted.report_session import ReportSessionService
from esg_targeted.inspection import ResultInspectionService
from esg_targeted.workflow import TargetedExtractionWorkflow
from tests.helpers import build_ir_fixture, standard_dist_root

ROOT = standard_dist_root().parent


@pytest.fixture
def extension(tmp_path, monkeypatch):
    parent = StandardPackageCatalog(standard_dist_root()).load('esrs.2023-set1.e1-6', '2.1.1')
    proposal = ExtensionProposal.model_validate(load_json(ROOT / 'extensions/authoring/esrs/2023-set1/e1-6/scope1-scope2-total/1.0.0/proposal.json'))
    approval = decide_proposal(proposal, authority=ReviewAuthority(actor_ref='synthetic_runtime_reviewer',
        actor_kind='human', verified_by='server_authentication', capabilities=['approve_extension_definition']),
        decision='approved', reason='Isolated test fixture, not a production release')
    release = compile_extension(proposal, parent=parent, approval=approval)
    registry = register_extension(ExtensionRegistry(schema_version='project-extension-registry-1.0'),
        release, path='scope1-scope2-total/1.0.0/release.json', parent=parent)
    runtime_root = tmp_path / 'extensions'
    write_extension_release(runtime_root, registry.registrations[0], release, parent=parent)
    (runtime_root / 'registry.json').write_text(registry.model_dump_json())
    monkeypatch.setenv('ESG_EXTENSION_ROOT', str(runtime_root))
    return parent, release, runtime_root


def select(release, *, origins=('reported',), rules=()):
    return ExtensionSelection(extension_id=release.definition.extension_id,
        definition_version=release.definition.definition_version, release_digest=release.release_digest,
        selection_kind='custom_extension', value_origins=list(origins), derivation_rule_ids=list(rules))


def service(tmp_path):
    ir_root = tmp_path / 'ir'
    build_ir_fixture(ir_root)
    runtime = replace(settings, project_root=tmp_path, ir_roots=(ir_root,), standard_dist_root=standard_dist_root(),
        output_root=tmp_path / 'output', state_db=tmp_path / 'jobs.sqlite3', index_cache_root=tmp_path / 'indexes')
    return ReportSessionService(runtime)


def report(selection):
    return ReportExtractionRequest(input={'kind':'document_ir','ir_run_id':'ir-fixture-e2-4'},
        standards=[], extension_selections=[selection], semantic_search=False)


def test_overlay_preserves_original_snapshot_and_unknown_context(extension):
    parent, release, _ = extension
    before = parent.model_dump(mode='json')
    frozen = freeze_runtime_snapshot(parent, [select(release)], [release])
    view = RuntimeStandardView(parent, frozen)
    assert view.model_dump(mode='json') == before == parent.model_dump(mode='json')
    assert len(view.metrics) == len(parent.metrics) + 1
    metric = view.metrics[-1]
    assert metric.source_datapoint_id is None and metric.extension_id == release.definition.extension_id
    fields = {e.element_code:e for e in view.elements if e.metric_id == metric.metric_id}
    for axis in ('scope_2_method','amount_basis','gwp_basis','consolidation_approach','accounting_standard'):
        assert fields[axis].value_contract.fixed_value is None
        assert fields[axis].null_allowed
    assert execution_catalog_dict(view)['metrics'][-1]['source_datapoint_id'] is None
    binding = metric_binding_identity(view, metric.metric_id)
    assert binding['runtime_standard_digest'] != parent.compilation.source_digest
    assert binding['related_source_datapoint_ids'] == ['E1-6_07','E1-6_09','E1-6_10']
    assert metric.measurement_contract.semantic_contract_version == 'legacy-unpartitioned-v1'
    assert 'positive_definition' not in metric.measurement_contract.model_dump(mode='json')


def test_new_extension_revision_uses_partitioned_semantics_and_requires_new_parent():
    catalog = StandardPackageCatalog(standard_dist_root())
    parent = catalog.load('esrs.2023-set1.e1-6', '2.1.2')
    proposal = ExtensionProposal.model_validate(load_json(ROOT / 'extensions/authoring/esrs/2023-set1/e1-6/scope1-scope2-total/1.0.1/proposal.json'))
    approval = decide_proposal(proposal, authority=ReviewAuthority(actor_ref='synthetic_revision_reviewer',
        actor_kind='human', verified_by='server_authentication', capabilities=['approve_extension_definition']),
        decision='approved', reason='Isolated revision fixture')
    release = compile_extension(proposal, parent=parent, approval=approval)
    frozen = freeze_runtime_snapshot(parent, [select(release)], [release])
    view = RuntimeStandardView(parent, frozen)
    contract = view.metrics[-1].measurement_contract
    assert contract.semantic_contract_version == 'partitioned-semantics-v1'
    assert contract.positive_definition == proposal.definition.labels['zh']
    assert proposal.definition.description in contract.interpretation_guidance
    assert len(contract.distinguish_from) == 3
    assert contract.exclusions == proposal.definition.exclusions
    assert len(parent.metrics) == 35
    assert view.model_dump(mode='json') == parent.model_dump(mode='json')
    with pytest.raises(PackageIntegrityError, match='parent package'):
        compile_extension(proposal, parent=catalog.load('esrs.2023-set1.e1-6','2.1.1'), approval=approval)


def test_extension_only_preview_and_create_separate_output_from_operands(extension, tmp_path):
    parent, release, _ = extension
    selected = select(release, origins=('derived',), rules=(release.definition.derivation_rules[0].rule_id,))
    sessions = service(tmp_path)
    preview = sessions.preview(report(selected))
    assert (preview['output_count'], preview['original_output_count'], preview['extension_output_count']) == (1,0,1)
    assert preview['internal_operand_count'] == 2
    assert set(preview['internal_dependencies'][0]['metric_ids']) == {'esrs.2023-set1.e1-6.dp07','esrs.2023-set1.e1-6.dp09'}
    assert preview['selected_outputs'][0]['source_datapoint_id'] is None
    assert preview['formula_operands'][-1]['calculation_stage'] == 'reviewed_publication'
    created = sessions.create(report(selected))
    job = sessions.jobs.get(created['job_ids'][0])
    assert job.request.metric_ids == [selected.extension_id]
    assert job.request.package_id == parent.manifest.package_id
    root = sessions.artifacts.job_dir(job.job_id)
    assert load_json(root / 'input/standard-package.json') == parent.model_dump(mode='json')
    assert load_json(root / 'input/runtime-standard-snapshot.json')['runtime_digest'] == job.request.runtime_standard_digest


def test_reported_selection_does_not_activate_calculation_operands(extension, tmp_path):
    _, release, _ = extension
    preview = service(tmp_path).preview(report(select(release)))
    assert preview['internal_operand_count'] == 0
    assert preview['formula_operands'] == []


def test_base_only_report_never_enables_registered_extensions(extension, tmp_path):
    parent, _, _ = extension
    parsed = ReportExtractionRequest(input={'kind':'document_ir','ir_run_id':'ir-fixture-e2-4'},
        standards=[dict(package_id=parent.manifest.package_id, package_version=parent.manifest.package_version,
                        metric_ids=['esrs.2023-set1.e1-6.dp07'])])
    preview = service(tmp_path).preview(parsed)
    assert preview['extension_output_count'] == 0
    assert preview['runtime_standard_snapshots'] == {}


def test_frozen_restore_does_not_depend_on_mutable_registry(extension, tmp_path):
    parent, release, runtime_root = extension
    req = ExtractionRequest(ir_run_id='ir-fixture-e2-4', package_id=parent.manifest.package_id,
        package_version=parent.manifest.package_version, extension_selections=[select(release)])
    request, frozen = freeze_extraction_request(parent, req, ROOT)
    artifact_root = tmp_path / 'frozen-task'
    (artifact_root / 'input').mkdir(parents=True)
    (artifact_root / 'input/runtime-standard-snapshot.json').write_text(frozen.model_dump_json())
    (runtime_root / 'registry.json').unlink()
    restored = load_runtime_package(parent, artifact_root=artifact_root, request=request)
    assert restored.runtime_digest == frozen.runtime_digest
    with pytest.raises(PackageIntegrityError, match='digest'):
        load_runtime_package(parent, artifact_root=artifact_root,
            request=request.model_copy(update={'runtime_standard_digest':'f'*64}))
    tampered = frozen.model_dump(mode='json'); tampered['extension_releases'][0]['definition']['labels']['zh'] = 'tampered'
    with pytest.raises(PackageIntegrityError): validate_runtime_snapshot(tampered, parent)


def test_unapproved_selection_rejected_without_task_creation(extension, tmp_path):
    _, release, runtime_root = extension
    (runtime_root / 'registry.json').write_text(ExtensionRegistry(schema_version='project-extension-registry-1.0').model_dump_json())
    sessions = service(tmp_path)
    with pytest.raises(PackageIntegrityError, match='approved'):
        sessions.create(report(select(release)))
    assert sessions.jobs.list() == []


def test_empty_output_and_proposal_only_rejected():
    with pytest.raises(ValidationError):
        ReportExtractionRequest(input={'kind':'document_ir','ir_run_id':'none'}, standards=[])


def test_deferred_core_and_bundle_have_no_synthesized_value(extension, tmp_path):
    parent, release, _ = extension
    selection = select(release, origins=('derived',), rules=(release.definition.derivation_rules[0].rule_id,))
    view = RuntimeStandardView(parent, freeze_runtime_snapshot(parent,[selection],[release]))
    ir = SimpleNamespace(document_id='doc-synthetic', run_id='ir-synthetic', ir_revision=1)
    result = deferred_extension_result(view, view.metrics[-1], ir, 'synthetic-job')
    records = {key:getattr(result,key) for key in RECORD_LAYOUT}
    assert ResultContractValidator(parent.core).validate(records)['status'] == 'passed'
    assert not records['quantitative_observations']
    assert result.outcome.dependency_status == 'blocked' and result.outcome.attempts == 0
    summary = dict(job_id='synthetic-job',core_schema=parent.core.model_dump(mode='json'),document={'document_id':ir.document_id})
    ResultExporter().export(output_dir=tmp_path / 'bundle', records=records, summary=summary, package=view)
    bundle = load_json(tmp_path / 'bundle/exports/extraction-result.json')
    assert bundle['execution_metric_catalog'][-1]['source_datapoint_id'] is None
    assert bundle['runtime_standard_snapshot']['runtime_digest'] == view.runtime_digest
    assert bundle['extension_computations'][0]['state'] == 'awaiting_reviewed_operands'
    row = ResultExporter._machine_fill_rows(records, summary, view)[0]
    assert row['source_datapoint_id'] is None and row['extension_id'] == selection.extension_id


def test_actual_workflow_derived_only_uses_internal_operands_without_model(extension, tmp_path):
    _, release, runtime_root = extension
    selected = select(release, origins=('derived',), rules=(release.definition.derivation_rules[0].rule_id,))
    sessions = service(tmp_path)
    parent = sessions.standards.load(release.definition.parent_package_id, release.definition.parent_package_version)
    parsed, frozen = freeze_extraction_request(parent, ExtractionRequest(ir_run_id='ir-fixture-e2-4',
        package_id=parent.manifest.package_id, package_version=parent.manifest.package_version,
        extension_selections=[selected], semantic_fill=False, semantic_search=False), ROOT)
    job_id = 'extension-runtime-fixture'
    sessions.jobs.create(job_id,parsed,str(sessions.artifacts.job_dir(job_id)))
    sessions.artifacts.write_json(job_id,'input/request.json',parsed.model_dump(mode='json'))
    sessions.artifacts.write_json(job_id,'input/standard-package.json',parent.model_dump(mode='json'))
    sessions.artifacts.write_json(job_id,'input/runtime-standard-snapshot.json',frozen.model_dump(mode='json'))
    (runtime_root / 'registry.json').unlink()
    def forbidden_model(): raise AssertionError('No real or fake semantic model is needed for retrieval-only fixture')
    workflow = TargetedExtractionWorkflow(sessions.settings,sessions.jobs,sessions.artifacts,
        semantic_model_factory=forbidden_model)
    workflow.run(job_id)
    job = sessions.jobs.get(job_id)
    assert job.status in {JobStatus.COMPLETED,JobStatus.PARTIAL}, job.error
    stage = collect_stage(sessions.artifacts,job_id)
    assert stage['summary']['counts']['selected_metrics'] == 1
    assert [o['metric_id'] for o in stage['summary']['outcomes']] == [selected.extension_id]
    assert stage['summary']['outcomes'][0]['dependency_status'] == 'blocked'
    assert not stage['records']['quantitative_observations']
    internal = sessions.artifacts.read_json(job_id,'results/internal-dependencies.json')
    assert set(internal['metric_ids']) == {'esrs.2023-set1.e1-6.dp07','esrs.2023-set1.e1-6.dp09'}
    inspection = ResultInspectionService(sessions.artifacts).build(job)
    assert len(inspection.metrics) == 1
    assert inspection.metrics[0].source_datapoint_id is None
    assert inspection.metrics[0].status == 'awaiting_reviewed_operands'


def test_reported_combined_value_reaches_core_without_imputed_method(extension, tmp_path):
    from tests.extension_replay import build_reported_extension_bundle
    parent, release, _ = extension
    replay = build_reported_extension_bundle(tmp_path / 'reported-replay', release)
    bundle = load_json(Path(replay['bundle_path']))
    facts = bundle['records']['quantitative_observations']
    assert len(facts) == 1 and str(facts[0]['value_numeric']) == '30'
    assert facts[0]['value_origin'] == 'reported' and facts[0]['metric_id'] == release.definition.extension_id
    assert facts[0]['reporting_year'] == 2024 and facts[0]['reporting_boundary'] == '中国境内运营'
    frozen = bundle['runtime_standard_snapshot']
    view = RuntimeStandardView(parent, frozen)
    codes = {e.element_id:e.element_code for e in view.elements}
    dimensions = bundle['records']['dimension_values']
    assert not any(codes.get(d['element_id']) in set(replay['unknown_axes']) for d in dimensions)
    binding = bundle['metric_source_mappings']['core_bindings'][0]
    assert binding['package_digest'] == parent.compilation.source_digest
    assert binding['extension_id'] == release.definition.extension_id
    assert binding['runtime_standard_digest'] == frozen['runtime_digest']


from tests.test_report_api_submission import api_client


def test_approved_extension_catalog_and_native_submit_api(extension, api_client):
    parent, release, _ = extension
    client, api, calls = api_client
    response = client.get('/api/extensions')
    assert response.status_code == 200
    assert [r['release_digest'] for r in response.json()['items']] == [release.release_digest]
    body = report(select(release)).model_dump(mode='json')
    preview = client.post('/api/report-sessions/preview',json=body)
    assert preview.status_code == 200 and preview.json()['original_output_count'] == 0
    created = client.post('/api/report-sessions?enqueue=false',json=body)
    assert created.status_code == 202, created.text
    assert len(created.json()['job_ids']) == 1 and calls == []
    child = api.job_store.get(created.json()['job_ids'][0])
    assert child.request.package_id == parent.manifest.package_id
    assert child.request.metric_ids == [release.definition.extension_id]


def test_standalone_submit_freezes_full_runtime_snapshot(extension, api_client):
    parent, release, _ = extension
    client, api, calls = api_client
    body = ExtractionRequest(ir_run_id='ir-fixture-e2-4',package_id=parent.manifest.package_id,
        package_version=parent.manifest.package_version,extension_selections=[select(release)]).model_dump(mode='json')
    response = client.post('/api/jobs',json=body)
    assert response.status_code == 202, response.text
    job = api.job_store.get(response.json()['job_id'])
    frozen = api.artifact_store.read_json(job.job_id,'input/runtime-standard-snapshot.json')
    assert frozen['extension_releases'][0] == release.model_dump(mode='json')
    assert frozen['runtime_digest'] == job.request.runtime_standard_digest
    assert calls[0][:2] == ('targeted-extraction',job.job_id)


def test_reported_resume_reuses_fixed_release_and_checkpoint(extension, tmp_path):
    from tests.extension_replay import build_reported_extension_bundle
    from esg_targeted.storage.artifacts import ArtifactStore
    from esg_targeted.storage.job_store import JobStore
    _, release, registry_root = extension
    root = tmp_path / 'resume-replay'
    replay = build_reported_extension_bundle(root,release)
    runtime = replace(settings,project_root=root,ir_roots=(root / 'ir',),standard_dist_root=standard_dist_root(),
        output_root=root / 'output',state_db=root / 'jobs.sqlite3',index_cache_root=root / 'indexes')
    jobs, artifacts = JobStore(runtime.state_db), ArtifactStore(runtime.output_root)
    jobs.update(replay['job_id'],status=JobStatus.INTERRUPTED)
    jobs.prepare_resume(replay['job_id'])
    (registry_root / 'registry.json').unlink()
    class NoNewDecision:
        def decide(self,*args,**kwargs): raise AssertionError('Accepted frozen checkpoint should resume without inference')
        def close(self): pass
    TargetedExtractionWorkflow(runtime,jobs,artifacts,semantic_model_factory=NoNewDecision).run(replay['job_id'])
    record = jobs.get(replay['job_id'])
    assert record.status in {JobStatus.COMPLETED,JobStatus.PARTIAL}, record.error
    bundle = artifacts.read_json(replay['job_id'],'exports/extraction-result.json')
    assert len(bundle['records']['quantitative_observations']) == 1
    assert bundle['runtime_standard_snapshot']['runtime_digest'] == replay['runtime_digest']


def test_explicit_old_version_survives_new_registered_version(extension, tmp_path):
    parent, old_release, registry_root = extension
    old_selection = select(old_release)
    sessions = service(tmp_path)
    old_session = sessions.create(report(old_selection))
    old_job_id = old_session['job_ids'][0]
    proposal = ExtensionProposal.model_validate(load_json(ROOT / 'extensions/authoring/esrs/2023-set1/e1-6/scope1-scope2-total/1.0.0/proposal.json'))
    newer_raw = proposal.model_dump(mode='json')
    newer_raw.update(proposal_id=proposal.proposal_id+'-synthetic-version-1.1.0')
    newer_raw['definition']['definition_version'] = '1.1.0'
    newer_raw['definition']['labels']['zh'] += '（隔离新版本）'
    newer_proposal = ExtensionProposal.model_validate(newer_raw)
    approval = decide_proposal(newer_proposal,authority=ReviewAuthority(actor_ref='synthetic_version_reviewer',
        actor_kind='human',verified_by='server_authentication',capabilities=['approve_extension_definition']),
        decision='approved',reason='Synthetic independent new version fixture')
    new_release = compile_extension(newer_proposal,parent=parent,approval=approval)
    registry = ExtensionRegistry.model_validate(load_json(registry_root / 'registry.json'))
    registry = register_extension(registry,new_release,path='scope1-scope2-total/1.1.0/release.json',parent=parent)
    write_extension_release(registry_root,registry.registrations[-1],new_release,parent=parent)
    (registry_root / 'registry.json').write_text(registry.model_dump_json())
    old_preview = sessions.preview(report(old_selection))
    old_frozen = old_preview['runtime_standard_snapshots'][parent.manifest.package_id]
    assert old_frozen['extension_releases'][0]['release_digest'] == old_release.release_digest
    new_preview = sessions.preview(report(select(new_release)))
    assert new_preview['runtime_standard_snapshots'][parent.manifest.package_id]['extension_releases'][0]['release_digest'] == new_release.release_digest
    # Already-created projections use their full 1.0.0 snapshot even with both
    # versions currently registered; a later task can explicitly choose 1.1.0.
    restored = load_runtime_package(parent,artifact_root=sessions.artifacts.job_dir(old_job_id),
                                    request=sessions.jobs.get(old_job_id).request)
    assert restored.runtime_snapshot.extension_releases[0] == old_release
    sessions.jobs.update(old_job_id,status=JobStatus.INTERRUPTED)
    sessions._mutate(old_session['session_id'],lambda state:state.update(execution_status='interrupted'))
    resumed = sessions.resume(old_session['session_id'])
    assert resumed['job_ids'] == [old_job_id]
    assert sessions.jobs.get(old_job_id).request.extension_selections == [old_selection]
    wrong_digest = old_selection.model_copy(update={'release_digest':new_release.release_digest})
    with pytest.raises(PackageIntegrityError): sessions.preview(report(wrong_digest))


def test_internal_operands_have_independent_core_sources_and_audit_scope(extension, tmp_path):
    from tests.extension_replay import build_internal_extension_bundle
    from esg_targeted.storage.artifacts import ArtifactStore
    from esg_targeted.standards.runtime_snapshot import validate_internal_dependency_scope
    parent, release, _ = extension
    replay = build_internal_extension_bundle(tmp_path / 'internal-replay',release)
    assert replay['job_status'] == JobStatus.PARTIAL.value
    bundle = load_json(Path(replay['bundle_path']))
    assert not bundle['records']['quantitative_observations']
    frozen = bundle['runtime_standard_snapshot']
    scope = validate_internal_dependency_scope(bundle['internal_dependency_scope'],RuntimeStandardView(parent,frozen))
    assert scope.source_datapoint_ids == ['E1-6_07','E1-6_09']
    assert len(scope.records['quantitative_observations']) == len(scope.core_bindings) == len(scope.source_facts) == 2
    assert {str(f['value_numeric']) for f in scope.records['quantitative_observations']} == {'20','10'}
    assert all(f['value_origin'] == 'reported' for f in scope.records['quantitative_observations'])
    assert scope.evidence_references == scope.records['evidence_references'] and scope.evidence_references
    assert scope.required_by_extensions[0].release_digest == release.release_digest
    assert scope.required_by_extensions[0].internal_operand_metric_ids == scope.metric_ids
    assert scope.contract_validation['status'] == bundle['summary']['internal_dependency_contract_validation']['status'] == 'passed'
    assert set(replay['semantic_metric_calls']) == set(scope.metric_ids)
    assert release.definition.extension_id not in replay['semantic_metric_calls']
    stage = collect_stage(ArtifactStore(tmp_path / 'internal-replay/output'),replay['job_id'])
    assert stage['summary']['counts']['selected_metrics'] == stage['summary']['counts']['extension_output_count'] == 1
    assert stage['summary']['counts']['original_output_count'] == 0
    codes = {e.element_id:e.element_code for e in RuntimeStandardView(parent,frozen).elements}
    assert not any(codes.get(d['element_id']) in set(replay['unknown_axes']) for d in scope.records['dimension_values'])
    tampered = scope.model_dump(mode='json'); tampered['source_datapoint_ids'][0] = 'E1-6_10'
    with pytest.raises((PackageIntegrityError,ValidationError)): validate_internal_dependency_scope(tampered,RuntimeStandardView(parent,frozen))


def test_runtime_and_internal_scope_schemas_match_code():
    from esg_targeted.standards.runtime_snapshot import RuntimeStandardSnapshot, InternalDependencyScope
    assert load_json(ROOT / 'extensions/schemas/runtime-standard-snapshot.schema.json') == RuntimeStandardSnapshot.model_json_schema()
    assert load_json(ROOT / 'extensions/schemas/internal-dependency-scope.schema.json') == InternalDependencyScope.model_json_schema()


def test_first_operand_cancellation_exports_partial_internal_core_and_resumes_once(extension, tmp_path):
    from tests.extension_replay import build_internal_extension_stage_replay
    from esg_targeted.storage.artifacts import ArtifactStore
    parent, release, registry = extension
    replay = build_internal_extension_stage_replay(tmp_path / 'cancel-resume',release)
    stopped = load_json(Path(replay['interrupted_bundle_path']))
    assert stopped['summary']['execution_status'] == 'cancelled'
    assert stopped['summary']['counts']['original_output_count'] == 0
    assert stopped['summary']['counts']['extension_output_count'] == 1
    assert stopped['summary']['counts']['core_facts'] == 0
    assert stopped['summary']['counts']['internal_core_facts'] == 1
    scope = stopped['internal_dependency_scope']
    assert scope['source_datapoint_ids'] == ['E1-6_07','E1-6_09']
    assert len(scope['core_bindings']) == len(scope['source_facts']) == 1
    assert set(scope['records']) == set(RECORD_LAYOUT)
    assert scope['records']['quantitative_observations'][0]['value_origin'] == 'reported'
    assert str(scope['records']['quantitative_observations'][0]['value_numeric']) == '20'
    assert replay['first_stop_operand_states']['esrs.2023-set1.e1-6.dp07'] == 'found'
    assert replay['first_stop_operand_states']['esrs.2023-set1.e1-6.dp09'] == 'not_executed'
    assert replay['resume_semantic_metric_calls'] == ['esrs.2023-set1.e1-6.dp09']
    assert any(row['metric_id'].endswith('dp09') and row['internal_core_count'] == 2 and
               row['selected_core_count'] == 0 for row in replay['resume_committed_stage_counts'])
    assert replay['runtime_snapshot_unchanged']
    complete = load_json(Path(replay['resumed_bundle_path']))
    assert not complete['records']['quantitative_observations']
    assert len(complete['internal_dependency_scope']['records']['quantitative_observations']) == 2
    assert complete['internal_dependency_scope']['core_bindings'][0] == scope['core_bindings'][0]
    stopped_store = ArtifactStore(Path(replay['interrupted_artifact_root']).parent)
    stopped_job_id = Path(replay['interrupted_artifact_root']).name
    before = {str(p):p.read_bytes() for p in Path(replay['interrupted_artifact_root']).rglob('*') if p.is_file()}
    stage = collect_stage(stopped_store,stopped_job_id,use_snapshot=False)
    after = {str(p):p.read_bytes() for p in Path(replay['interrupted_artifact_root']).rglob('*') if p.is_file()}
    assert before == after
    assert stage['internal_dependency_scope']['core_bindings'] == scope['core_bindings']
    assert stage['summary']['counts']['internal_core_facts'] == 1


@pytest.mark.parametrize('failure',['retired_source','wrong_runtime_digest'])
def test_interrupted_internal_stage_quarantines_stale_or_foreign_checkpoint(extension, tmp_path, failure):
    from tests.extension_replay import build_internal_extension_stage_replay
    from esg_targeted.storage.artifacts import ArtifactStore
    _,release,_ = extension
    replay = build_internal_extension_stage_replay(tmp_path / 'invalidated-replay',release)
    root = Path(replay['interrupted_artifact_root'])
    store = ArtifactStore(root.parent)
    bundle = load_json(Path(replay['interrupted_bundle_path']))
    if failure == 'retired_source':
        store.write_json(root.name,'agent/source-invalidations.json',{
            'source_fact_ids':[bundle['internal_dependency_scope']['source_facts'][0]['source_fact_id']]})
    else:
        path = next(p for p in (root / 'checkpoints').glob('*.json') if load_json(p)['metric_id'].endswith('dp07'))
        payload = load_json(path); payload['runtime_standard_digest'] = 'f'*64
        path.write_text(json.dumps(payload))
    stage = collect_stage(store,root.name,use_snapshot=False)
    scope = stage['internal_dependency_scope']
    assert not scope['records']['quantitative_observations']
    assert not scope['core_bindings'] and not scope['source_facts']
    assert stage['summary']['counts']['internal_core_facts'] == 0
    assert scope['source_datapoint_ids'] == ['E1-6_07','E1-6_09']
    assert not stage['records']['quantitative_observations']


def test_report_request_frozen_expectation_does_not_authorize_unregistered_release(extension, tmp_path):
    _, release, registry_root = extension
    sessions = service(tmp_path)
    raw = report(select(release))
    frozen = sessions.preview(raw)['runtime_standard_snapshots']
    expected = raw.model_copy(update={'frozen_runtime_standard_snapshots':frozen})
    assert sessions.preview(expected)['runtime_standard_snapshots'] == frozen
    changed = deepcopy(frozen)
    next(iter(changed.values()))['extension_releases'][0]['definition']['labels']['zh'] = 'tampered expectation'
    with pytest.raises(ValueError,match='冻结'):
        sessions.preview(expected.model_copy(update={'frozen_runtime_standard_snapshots':changed}))
    (registry_root / 'registry.json').write_text(ExtensionRegistry(schema_version='project-extension-registry-1.0').model_dump_json())
    with pytest.raises(PackageIntegrityError,match='approved'):
        sessions.create(expected)
    assert sessions.jobs.list() == []
