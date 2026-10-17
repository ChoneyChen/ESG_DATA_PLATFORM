from dataclasses import replace

from esg_targeted.config import Settings
from esg_targeted.contracts import ReportExtractionRequest
from esg_targeted.report_session import ReportSessionService
from esg_targeted.standards.profiles import platform_major_profile
from tests.helpers import standard_dist_root


def service(tmp_path):
    settings = replace(Settings.load(), state_db=tmp_path / 'jobs.sqlite3', output_root=tmp_path / 'results',
                       index_cache_root=tmp_path / 'indexes', ir_roots=(tmp_path / 'ir',),
                       standard_dist_root=standard_dist_root())
    return ReportSessionService(settings)


def request(package_id, original_ids, catalog):
    package = catalog.load(package_id, '2.1.1')
    return ReportExtractionRequest(input={'kind': 'pdf', 'asset_id': 'fixture-pdf'}, standards=[{
        'package_id': package_id, 'package_version': '2.1.1',
        'metric_ids': [m.metric_id for m in package.metrics if m.source_datapoint_id in original_ids]}])


def test_major_preview_keeps_only_16_outputs_and_separates_pinned_context(tmp_path):
    pipeline = service(tmp_path)
    profile = platform_major_profile(standard_dist_root().parent, topics=['E1', 'E2', 'E3'])
    query = ReportExtractionRequest(input={'kind': 'pdf', 'asset_id': 'fixture-pdf'},
        standards=profile['session_standards'], selection_snapshot=profile['selection_snapshot'])
    plan = pipeline.preview(query)
    assert plan['output_count'] == 16
    assert [m['source_datapoint_id'] for m in plan['selected_outputs']] == profile['source_datapoint_ids']
    assert all(m['package_core'] for m in plan['selected_outputs'])
    assert all(ref['output_policy'] == 'context_only' for ref in plan['context_requirements'])
    referenced = {clause['source_datapoint_id'] for ref in plan['context_requirements'] for clause in ref['clauses']}
    assert {'BP-1_01', 'MDR-M_02', 'SBM-1_06'}.issubset(referenced)
    assert all(ref['source_digest'] for ref in plan['context_requirements'])
    assert not referenced.intersection(m['source_datapoint_id'] for m in plan['selected_outputs'])
    assert any('reporting_entity' in item['identity_axes'] for item in plan['identity_requirements'])
    assert plan['exclusions'] and plan['formula_operands']
    assert not pipeline.jobs.list(), 'preview must not create extraction jobs'


def test_formula_operands_are_internal_and_do_not_inflate_output_count(tmp_path):
    pipeline = service(tmp_path)
    plan = pipeline.preview(request('esrs.2023-set1.e1-6', ['E1-6_12'], pipeline.standards))
    assert plan['output_count'] == 1
    assert plan['selected_outputs'][0]['source_datapoint_id'] == 'E1-6_12'
    assert len(plan['internal_dependencies'][0]['metric_ids']) == 3
    rule = next(rule for rule in plan['formula_operands'] if rule['target']['source_datapoint_id'] == 'E1-6_12')
    assert {operand['source_clause']['source_datapoint_id'] for operand in rule['operands']} == {'E1-6_07', 'E1-6_09', 'E1-6_11'}
    assert all(operand['scheduled_as_internal_dependency'] for operand in rule['operands'])
    assert rule['reported_value_priority']


def test_unmapped_evidence_operands_do_not_get_invented_esrs_numbers(tmp_path):
    pipeline = service(tmp_path)
    plan = pipeline.preview(request('esrs.2023-set1.e2-4', ['E2-4_11'], pipeline.standards))
    assert plan['output_count'] == 1
    assert plan['internal_dependencies'][0]['metric_ids'] == []
    assert all(operand['source_clause'] is None and operand['description']
               for rule in plan['formula_operands'] for operand in rule['operands'])


def test_mdr_branch_is_not_activated_without_report_context(tmp_path):
    pipeline = service(tmp_path)
    plan = pipeline.preview(request('esrs.2023-set1.e1-2', ['E1-2_01'], pipeline.standards))
    assert plan['output_count'] == 1
    shared = [ref for ref in plan['context_requirements'] if 'activation_predicate' in ref]
    assert shared
    assert all(ref['activation_state'] == 'awaiting_report_context' for ref in shared)
    assert {ref['purpose'] for ref in shared} == {'adopted_policy', 'absent_policy'}
    assert all(ref['output_policy'] == 'context_only' for ref in shared)
