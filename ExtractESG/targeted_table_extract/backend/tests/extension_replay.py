"""Generate synthetic extension Bundles using a supplied fixed release.

This is an offline contract fixture, not a report/model acceptance. It performs
no network calls and never creates approvals, registrations or published data.
A publication replay must persist/verify the supplied release in its own isolated
authoritative database before importing this Bundle.
"""
from dataclasses import replace
from pathlib import Path
import argparse
import json
import shutil

from esg_standard_packages.extensions import CompiledExtension, ExtensionSelection, validate_release
from esg_standard_packages.io import load_json
from esg_targeted.config import settings
from esg_targeted.contracts import ExtractionRequest, JobStatus, ModelRunResult, SemanticAssignment, SemanticDecision, SemanticFactGroup
from esg_targeted.ids import new_job_id
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.runtime_snapshot import RuntimeStandardView, freeze_runtime_snapshot
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.job_store import JobStore
from esg_targeted.workflow import TargetedExtractionWorkflow, CancelledError
from tests.helpers import build_ir_fixture, standard_dist_root


class SyntheticCombinedResponse:
    """Fixed offline response adapter for the known physical fixture only."""
    provider_name = 'synthetic'
    model_id = 'isolated_extension_replay_fixture'

    def decide(self, packet, **kwargs):
        elements = {e['element_code']:e['element_id'] for e in packet.elements}
        candidate = next(c for c in packet.candidates if c.candidate_type == 'quantity' and 'CO2' in c.raw_value)
        spans = {s.text:s for s in packet.spans if s.context_group_id == candidate.context_group_id}
        assignments = [SemanticAssignment(element_id=elements['value'],source_ref_id=candidate.candidate_id,
            evidence_span_ids=[candidate.span_id],value_raw=candidate.raw_value,source_mode='candidate'),
            SemanticAssignment(element_id=elements['unit'],source_ref_id=candidate.span_id,
            evidence_span_ids=[candidate.span_id],value_raw='tCO2e',source_mode='span')]
        for code, raw in [('reporting_period','2024年'),('reporting_boundary','中国境内运营'),('reporting_entity','Fixture Company')]:
            span = spans[raw]
            assignments.append(SemanticAssignment(element_id=elements[code],source_ref_id=span.span_id,
                evidence_span_ids=[span.span_id],value_raw=raw,source_mode='span'))
        decision = SemanticDecision(task_id=packet.task_id,status='found',uncertainty_code='none',
            selected_evidence_span_ids=list(dict.fromkeys(s for a in assignments for s in a.evidence_span_ids)),
            fact_groups=[SemanticFactGroup(group_ref_id=candidate.context_group_id,assignments=assignments)])
        return ModelRunResult(decision=decision,raw_output=decision.model_dump_json(),
            cleaned_output=decision.model_dump_json(),visual_used=False,image_count=0)

    def close(self): pass


class SyntheticOperandResponse:
    """Fixed original-clause answers; no answer for a calculated extension."""
    provider_name = 'synthetic'
    model_id = 'isolated_operand_fixture'

    def __init__(self):
        self.calls = []

    def decide(self, packet, **kwargs):
        metric_id = packet.metric['metric_id']
        labels = {'esrs.2023-set1.e1-6.dp07':'范围1温室气体毛排放总量',
                  'esrs.2023-set1.e1-6.dp09':'范围2地点法温室气体毛排放总量'}
        if metric_id not in labels:
            raise AssertionError('This fixture can answer only the two original operands, never the extension')
        self.calls.append(metric_id)
        code = {e['element_code']:e['element_id'] for e in packet.elements}
        label = labels[metric_id]
        label_span = next(s for s in packet.spans if s.text == label)
        spans = {s.text:s for s in packet.spans if s.context_group_id == label_span.context_group_id}
        amount = next(c for c in packet.candidates if c.context_group_id == label_span.context_group_id
                      and c.candidate_type == 'quantity' and 'CO2' in c.raw_value)
        assignments = [SemanticAssignment(element_id=code['ghg_emissions'],source_ref_id=amount.candidate_id,
            evidence_span_ids=[amount.span_id],value_raw=amount.raw_value,source_mode='candidate'),
            SemanticAssignment(element_id=code['ghg_unit'],source_ref_id=amount.span_id,
            evidence_span_ids=[amount.span_id],value_raw='tCO2e',source_mode='span')]
        for field, raw in [('reporting_period','2024年'),('reporting_boundary','中国境内运营'),('source_row_label',label)]:
            span = spans[raw]
            assignments.append(SemanticAssignment(element_id=code[field],source_ref_id=span.span_id,
                evidence_span_ids=[span.span_id],value_raw=raw,source_mode='span'))
        decision = SemanticDecision(task_id=packet.task_id,status='found',uncertainty_code='none',
            selected_evidence_span_ids=list(dict.fromkeys(s for a in assignments for s in a.evidence_span_ids)),
            fact_groups=[SemanticFactGroup(group_ref_id=label_span.context_group_id,assignments=assignments)])
        return ModelRunResult(decision=decision,raw_output=decision.model_dump_json(),
            cleaned_output=decision.model_dump_json(),visual_used=False,image_count=0)

    def close(self): pass


def _prepare_replay(output_root, release):
    output_root = output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError('Use an empty isolated replay directory; existing artifacts will not be overwritten')
    parent = StandardPackageCatalog(standard_dist_root()).load(release.definition.parent_package_id,
                                                            release.definition.parent_package_version)
    validate_release(release, parent=parent)
    output_root.mkdir(parents=True, exist_ok=True)
    return output_root, parent


def _execute_replay(output_root, parent, selection, frozen, response, *,
                    workflow_factory=TargetedExtractionWorkflow, accepted_statuses=None):
    runtime = replace(settings,project_root=output_root,ir_roots=(output_root / 'ir',),standard_dist_root=standard_dist_root(),
        output_root=output_root / 'output',state_db=output_root / 'jobs.sqlite3',index_cache_root=output_root / 'indexes')
    artifacts, jobs = ArtifactStore(runtime.output_root), JobStore(runtime.state_db)
    request = ExtractionRequest(ir_run_id='ir-fixture-e2-4',package_id=parent.manifest.package_id,
        package_version=parent.manifest.package_version,metric_ids=[selection.extension_id],
        extension_selections=[selection],runtime_standard_digest=frozen.runtime_digest,
        semantic_search=False,extraction_mode='per_metric')
    job_id = new_job_id()
    jobs.create(job_id,request,str(artifacts.job_dir(job_id)))
    for name, body in [('request',request),('standard-package',parent),('runtime-standard-snapshot',frozen)]:
        artifacts.write_json(job_id,f'input/{name}.json',body.model_dump(mode='json'))
    workflow_factory(runtime,jobs,artifacts,semantic_model_factory=lambda:response).run(job_id)
    job = jobs.get(job_id)
    if job.status not in (accepted_statuses or {JobStatus.COMPLETED,JobStatus.PARTIAL}):
        raise AssertionError(job.error)
    retained=(job.summary or {}).get('delivery_artifact',{}).get('bundle_relative_path','.')
    return {'job_id':job_id,'bundle_path':str(artifacts.job_dir(job_id) / retained / 'exports/extraction-result.json'),
        'artifact_root':str(artifacts.job_dir(job_id)), 'job_status':job.status.value,
        'release_digest':frozen.extension_releases[0].release_digest,'runtime_digest':frozen.runtime_digest,
        'publication_authority':'must_verify_supplied_release_in_isolated_database'}


def build_reported_extension_bundle(output_root: Path, release: CompiledExtension) -> dict:
    output_root, parent = _prepare_replay(output_root, release)
    selection = ExtensionSelection(extension_id=release.definition.extension_id,
        definition_version=release.definition.definition_version,release_digest=release.release_digest,
        selection_kind='custom_extension',value_origins=['reported'])
    frozen = freeze_runtime_snapshot(parent,[selection],[release])
    ir_root = output_root / 'ir'
    run = build_ir_fixture(ir_root,pollutant_rows=[('范围1和范围2温室气体排放合计','30tCO2e','2024年','中国境内运营')])
    table_path = run / 'canonical/tables/table-0001.json'
    table = load_json(table_path); table['caption'] = '温室气体排放（隔离合同 fixture）'
    table['cells'].append({'cell_id':'cell-fixture-entity','row_index':0,'col_index':4,'page_index':0,
        'text':'Fixture Company','column_header_path':['报告主体'],'row_header_path':['范围1和范围2温室气体排放合计'],
        'bbox':{'x0':400,'y0':100,'x1':500,'y1':130}})
    table_path.write_text(json.dumps(table,ensure_ascii=False))
    result = {**_execute_replay(output_root,parent,selection,frozen,SyntheticCombinedResponse()),
        'fixture_kind':'synthetic_offline_reported_extension',
        'expected_value':'30','expected_unit':'tCO2e','expected_origin':'reported',
        'unknown_axes':['scope_2_method','amount_basis','gwp_basis','consolidation_approach','accounting_standard']}
    (output_root / 'replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def build_internal_extension_bundle(output_root: Path, release: CompiledExtension, *, _workflow_factory=None) -> dict:
    """Materialize two original candidates, with no computed/publication claim."""
    output_root, parent = _prepare_replay(output_root, release)
    location_rules = [r for r in release.definition.derivation_rules if {o.source_metric_id for o in r.operands} ==
                      {'esrs.2023-set1.e1-6.dp07','esrs.2023-set1.e1-6.dp09'}]
    if len(location_rules) != 1:
        raise ValueError('This fixed fixture needs exactly one Scope1 + location Scope2 rule')
    selection = ExtensionSelection(extension_id=release.definition.extension_id,
        definition_version=release.definition.definition_version,release_digest=release.release_digest,
        selection_kind='custom_extension',value_origins=['derived'],derivation_rule_ids=[location_rules[0].rule_id])
    frozen = freeze_runtime_snapshot(parent,[selection],[release])
    build_ir_fixture(output_root / 'ir',pollutant_rows=[
        ('范围1温室气体毛排放总量','20tCO2e','2024年','中国境内运营'),
        ('范围2地点法温室气体毛排放总量','10tCO2e','2024年','中国境内运营')])
    response = SyntheticOperandResponse()
    run_options = {'workflow_factory':_workflow_factory,'accepted_statuses':{JobStatus.CANCELLED}} if _workflow_factory else {}
    result = {**_execute_replay(output_root,parent,selection,frozen,response,**run_options),
        'fixture_kind':'synthetic_offline_internal_operand_delivery',
        'selected_output_count':1,'original_output_count':0,'extension_output_count':1,
        'expected_internal_values':{'E1-6_07':'20','E1-6_09':'10'},'expected_unit':'tCO2e',
        'expected_operand_origin':'reported','expected_extension_state':'awaiting_reviewed_operands',
        'expected_extension_quantitative_count':0,'semantic_metric_calls':response.calls,
        'scientific_context':'unknown; candidate delivery only, cannot derive or publish without review',
        'unknown_axes':['reporting_entity','scope_2_method','amount_basis','gwp_basis',
                        'consolidation_approach','accounting_standard']}
    (output_root / 'replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def build_internal_extension_stage_replay(output_root: Path, release: CompiledExtension) -> dict:
    """Actual checkpoint cancellation and resume, with immutable replay proof."""
    class StopAfterFirstOperand(TargetedExtractionWorkflow):
        def _commit_metric_result(self, **kwargs):
            outcome = super()._commit_metric_result(**kwargs)
            if kwargs['metric'].metric_id == 'esrs.2023-set1.e1-6.dp07':
                raise CancelledError('Anonymous offline fixture: stop after first durable operand checkpoint')
            return outcome
    replay = build_internal_extension_bundle(output_root,release,_workflow_factory=StopAfterFirstOperand)
    output_root = output_root.resolve()
    stopped_root = output_root / 'interrupted-job'
    shutil.copytree(Path(replay['artifact_root']),stopped_root)
    stopped_bundle_path = stopped_root / Path(replay['bundle_path']).relative_to(Path(replay['artifact_root']))
    stopped = load_json(stopped_bundle_path)
    frozen_before = load_json(stopped_root / 'input/runtime-standard-snapshot.json')
    root = output_root
    runtime = replace(settings,project_root=root,ir_roots=(root / 'ir',),standard_dist_root=standard_dist_root(),
        output_root=root / 'output',state_db=root / 'jobs.sqlite3',index_cache_root=root / 'indexes')
    jobs, artifacts = JobStore(runtime.state_db), ArtifactStore(runtime.output_root)
    jobs.prepare_resume(replay['job_id'])
    resumed_response = SyntheticOperandResponse()
    committed_resume_stages = []
    class TraceResumeCommits(TargetedExtractionWorkflow):
        def _commit_metric_result(self, **kwargs):
            from esg_targeted.results.stages import collect_stage
            outcome = super()._commit_metric_result(**kwargs)
            stage = collect_stage(self.artifact_store,replay['job_id'],use_snapshot=False)
            committed_resume_stages.append({'metric_id':kwargs['metric'].metric_id,
                'selected_core_count':stage['summary']['counts']['core_facts'],
                'internal_core_count':stage['summary']['counts'].get('internal_core_facts',0)})
            return outcome
    TraceResumeCommits(runtime,jobs,artifacts,
        semantic_model_factory=lambda:resumed_response).run(replay['job_id'])
    resumed = jobs.get(replay['job_id'])
    if resumed.status not in {JobStatus.COMPLETED,JobStatus.PARTIAL}:
        raise AssertionError(resumed.error)
    completed_bundle_path = artifacts.job_dir(replay['job_id']) / 'exports/extraction-result.json'
    complete = load_json(completed_bundle_path)
    frozen_after = load_json(Path(replay['artifact_root']) / 'input/runtime-standard-snapshot.json')
    result = {**replay,'fixture_kind':'synthetic_offline_checkpoint_cancel_and_resume',
        'interrupted_bundle_path':str(stopped_bundle_path),'interrupted_artifact_root':str(stopped_root),
        'resumed_bundle_path':str(completed_bundle_path),'resumed_job_status':resumed.status.value,
        'first_stop_exception':'workflow.CancelledError','first_stop_job_status':'cancelled',
        'first_stop_selected_core_count':len(stopped['records']['quantitative_observations']),
        'first_stop_internal_core_count':len(stopped['internal_dependency_scope']['records']['quantitative_observations']),
        'first_stop_operand_states':{o['metric_id']:o['status'] for o in stopped['internal_dependency_scope']['outcomes']},
        'resume_semantic_metric_calls':resumed_response.calls,
        'resume_committed_stage_counts':committed_resume_stages,
        'resumed_selected_core_count':len(complete['records']['quantitative_observations']),
        'resumed_internal_core_count':len(complete['internal_dependency_scope']['records']['quantitative_observations']),
        'runtime_snapshot_unchanged':frozen_before == frozen_after,
        'fixture_disclaimer':'Anonymous fixed response adapter; no real model, production approval, registry or publication.'}
    (output_root / 'proof.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=Path,required=True,help='Full compiled release matching authoritative isolated DB')
    parser.add_argument('--output',type=Path,required=True,help='Empty isolated replay directory')
    parser.add_argument('--kind',choices=['reported','internal','internal-stage'],default='reported',help='Reported combined, internal candidates or checkpoint stop/resume')
    args = parser.parse_args()
    builder = {'reported':build_reported_extension_bundle,'internal':build_internal_extension_bundle,
               'internal-stage':build_internal_extension_stage_replay}[args.kind]
    print(json.dumps(builder(args.output,
        CompiledExtension.model_validate(load_json(args.release))),ensure_ascii=False,indent=2))
