"""Independent boundary checks: frozen scope, empty public API, write gates.

No demo seeding, model execution, OCR, or existing database is involved.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

REPOSITORY = Path(__file__).resolve().parents[2]
STANDARDS = REPOSITORY / 'ExtractESG/standard_packages'
for directory in [REPOSITORY, STANDARDS / 'src', REPOSITORY / 'ExtractESG/targeted_table_extract/backend/src']:
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from PlatESG.backend.main import create_app
from esg_targeted.contracts import ExtractionSelectionSnapshot, ReportExtractionRequest
from esg_targeted.standards.profiles import platform_major_profile, validate_selection_snapshot
from esg_standard_packages.scope import load_registry


@pytest.fixture(scope='module')
def frozen_scope():
    response = platform_major_profile(STANDARDS, topics=['E1', 'E2', 'E3'])
    selection = ExtractionSelectionSnapshot.model_validate(response['selection_snapshot'])
    _, _, packages = load_registry(STANDARDS)
    by_package = {p.manifest.package_id: p for p in packages.values()}
    chosen = [(by_package[s['package_id']], s['metric_ids']) for s in response['session_standards']]
    return response, selection, chosen


@pytest.mark.parametrize('topics', [None, [], ['E99'], ['E1', 'E99'], ['S2'], ['2']])
def test_unbuilt_or_unknown_or_empty_topic_scope_never_silently_reduces(topics):
    with pytest.raises(ValueError):
        platform_major_profile(STANDARDS, topics=topics)


def test_scope_hashes_bind_full_original_profile_dictionary_and_selected_packages(frozen_scope):
    response, selection, chosen = frozen_scope
    assert len(response['overall_source_datapoint_ids']) == 49
    assert len(selection.source_datapoint_ids) == response['output_count'] == 16
    assert len(response['pending_source_datapoint_ids']) == 33
    assert selection.profile_digest == hashlib.sha256((STANDARDS / 'profiles/esrs/2023-set1/platform-major.json').read_bytes()).hexdigest()
    assert selection.dictionary_digest == hashlib.sha256((REPOSITORY / 'PlatESG/catalog/metric-dictionary.v1.json').read_bytes()).hexdigest()
    validate_selection_snapshot(STANDARDS, selection, chosen)
    request = ReportExtractionRequest.model_validate({
        'input': {'kind': 'pdf', 'asset_id': 'independent-scope-fixture'},
        'standards': response['session_standards'],
        'selection_snapshot': selection.model_dump(mode='json'),
    })
    assert request.selection_snapshot == selection


@pytest.mark.parametrize('field', ['profile_digest', 'dictionary_digest'])
def test_hash_tampering_requires_a_new_preview(frozen_scope, field):
    _, selection, chosen = frozen_scope
    corrupted = selection.model_copy(update={field: '0' * 64})
    with pytest.raises(ValueError, match='changed'):
        validate_selection_snapshot(STANDARDS, corrupted, chosen)


def test_core_clause_cannot_join_major_under_an_old_snapshot(frozen_scope):
    _, selection, chosen = frozen_scope
    modified = [(p, list(ids)) for p, ids in chosen]
    package, outputs = modified[0]
    source = next(m for m in package.metrics if m.source_datapoint_id == 'E1-5_02')
    assert source.source_datapoint_id in package.manifest.core_source_datapoint_ids
    outputs.append(source.metric_id)
    with pytest.raises(ValueError, match='differ'):
        validate_selection_snapshot(STANDARDS, selection, modified)
    forged = selection.model_copy(update={'source_datapoint_ids': [*selection.source_datapoint_ids, source.source_datapoint_id]})
    with pytest.raises(ValueError, match='not automatically'):
        validate_selection_snapshot(STANDARDS, forged, modified)


def test_changed_package_or_duplicate_scope_is_rejected(frozen_scope):
    _, selection, chosen = frozen_scope
    digests = dict(selection.package_digests)
    digests[next(iter(digests))] = '0' * 64
    with pytest.raises(ValueError, match='package content'):
        validate_selection_snapshot(STANDARDS, selection.model_copy(update={'package_digests': digests}), chosen)
    duplicate = [*selection.source_datapoint_ids, selection.source_datapoint_ids[0]]
    with pytest.raises(ValueError, match='differ'):
        validate_selection_snapshot(STANDARDS, selection.model_copy(update={'source_datapoint_ids': duplicate}), chosen)
    with pytest.raises(ValidationError):
        ExtractionSelectionSnapshot.model_validate({**selection.model_dump(), 'dictionary_digest': 'not-a-digest'})


@pytest.fixture(scope='module')
def empty_platform(tmp_path_factory):
    directory = tmp_path_factory.mktemp('independent-empty-platform')
    app = create_app({'PLATESG_DATA_DIR': str(directory / 'database'),
                      'PLATESG_AUTO_PUBLISH': '0', 'PLATESG_AUTO_IMPORT': '0',
                      'PLATESG_IMPORT_ROOTS': str(directory),
                      'PLATESG_REVIEW_TOKEN': 'independent-boundary-token'})
    app.state.test_import_root = directory
    with TestClient(app) as client:
        yield client


def test_empty_platform_preserves_definitions_but_creates_no_facts(empty_platform):
    response = empty_platform.get('/v1/metrics')
    assert response.status_code == 200
    catalog = response.json()
    assert len(catalog['items']) == 49
    assert all('readiness' not in m and 'standard_binding' not in m for m in catalog['items'])
    observations = empty_platform.get('/v1/observations', params={'dataset_version': catalog['dataset_version']}).json()
    assert observations['items'] == []
    assert observations['next_cursor'] is None
    assert empty_platform.get('/v1/entities').json()['items'] == []
    coverage_response = empty_platform.get('/v1/coverage')
    assert coverage_response.status_code == 200, coverage_response.text
    assert coverage_response.json()['items'] == []
    snapshot = empty_platform.get('/internal/v1/extraction-profiles', headers={'Authorization':'Bearer independent-boundary-token'}).json()
    assert snapshot['output_count'] == 16
    assert len(snapshot['overall_source_datapoint_ids']) == 49


@pytest.mark.parametrize('endpoint', ['/v1/metrics', '/v1/entities', '/v1/observations', '/v1/coverage', '/v1/exports'])
def test_unknown_dataset_cannot_fall_back_to_current(empty_platform, endpoint):
    response = empty_platform.get(endpoint, params={'dataset_version': 999999})
    assert response.status_code == 404


def test_comparison_of_empty_dataset_explains_absence_without_zero(empty_platform):
    response = empty_platform.post('/v1/comparisons', json={'metric_id': 'ghg.scope1', 'entity_refs': ['company-a', 'company-b']})
    assert response.status_code == 200
    result = response.json()
    assert result['rows'] == []
    assert result['available_cohorts'] == []
    assert {row['entity_ref'] for row in result['excluded']} == {'company-a', 'company-b'}
    assert all(row['reasons'] == ['no_published_observation_in_dataset'] for row in result['excluded'])
    unknown = empty_platform.post('/v1/comparisons', json={'metric_id': 'invented.scope1', 'entity_refs': ['company-a']})
    assert unknown.status_code == 404


def test_internal_routes_require_auth_and_strict_identity(empty_platform):
    auth = {'Authorization': 'Bearer independent-boundary-token'}
    entity = {'entity_ref': 'company-a', 'name_zh': '独立合同测试公司', 'entity_kind': 'listed_company',
              'industry_code': 'test', 'country_code': 'CN'}
    assert empty_platform.post('/internal/v1/entities', json=entity).status_code == 401
    assert empty_platform.post('/internal/v1/entities', json=entity, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert empty_platform.post('/internal/v1/entities', json={**entity, 'unexpected': True}, headers=auth).status_code == 422
    assert empty_platform.post('/internal/v1/entities', json=entity, headers=auth).status_code == 200
    assert empty_platform.post('/internal/v1/entities', json={**entity, 'entity_kind': 'group'}, headers=auth).status_code == 409
    context = {'mapping_id': '00000000-0000-0000-0000-000000000001', 'entity_ref': 'company-missing',
               'period_start': '2024-01-01', 'period_end': '2024-12-31', 'boundary_code': 'own_operations',
               'operator_ref': 'test', 'reason': 'independent gate test'}
    response = empty_platform.post('/internal/v1/measurement-contexts', json=context, headers=auth)
    assert response.status_code == 422
    assert response.json()['code'] == 'entity_not_registered'


def test_import_requires_authentic_scope_and_retains_partial_idempotence(empty_platform, frozen_scope):
    """An empty valid projection cannot fabricate a value or become complete."""
    auth = {'Authorization': 'Bearer independent-boundary-token'}
    entity = {'entity_ref': 'import-contract-company', 'name_zh': '导入合同测试公司',
              'entity_kind': 'listed_company', 'industry_code': 'test', 'country_code': 'CN'}
    assert empty_platform.post('/internal/v1/entities', json=entity, headers=auth).status_code == 200
    response, selection, _ = frozen_scope
    package = response['session_standards'][0]
    directory = empty_platform.app.state.test_import_root / 'minimal-partial-projection'
    (directory / 'input').mkdir(parents=True)
    (directory / 'results').mkdir()
    compiled = json.loads((STANDARDS / 'dist' / package['package_id'] / package['package_version'] / 'package.json').read_text())
    manifest = {'bundle_layout_version': 'targeted-result-bundle-v1',
        'schema_version': 'targeted-extraction-result-v1', 'contract_validation': {'status': 'passed'},
        'job_id': 'independent-partial-no-values', 'execution_status': 'interrupted', 'is_partial': True,
        'document': {'document_id': 'fixture-document'}}
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    (directory / 'input/request.json').write_text(json.dumps({**package, 'metric_ids': [package['metric_ids'][0]]}))
    (directory / 'input/standard-package.json').write_text(json.dumps(compiled))
    for filename in ['reporting-tasks', 'quantitative-observations', 'qualitative-assertions', 'attribute-values', 'dimension-values', 'evidence-references']:
        (directory / 'results' / (filename + '.jsonl')).write_text('')
    body = {'bundle_path': str(directory), 'selection_snapshot': selection.model_dump(mode='json'),
            'report_metadata': {'upstream_system': 'independent-contract-test', 'organization_ref': entity['entity_ref'],
                               'report_ref': 'no-value-report', 'report_version_ref': 'no-value-version',
                               'source_artifact_ref': 'fixture://no-value-report', 'source_sha256': 'a' * 64,
                               'report_title': '独立空投影合同样本'}}
    for field in ['profile_digest', 'dictionary_digest']:
        invalid = {**body, 'selection_snapshot': {**body['selection_snapshot'], field: '0' * 64}}
        rejected = empty_platform.post('/internal/v1/import-batches', json=invalid, headers=auth)
        assert rejected.status_code == 422
        assert empty_platform.get('/internal/v1/candidates', headers=auth).json()['items'] == []
    valid = empty_platform.post('/internal/v1/import-batches', json=body, headers=auth)
    assert valid.status_code == 200, valid.text
    imported = valid.json()
    assert imported['imported'] == 0
    assert imported['result_state'] == 'partial'
    assert imported['platform_major_count'] == 49
    assert imported['executable_major_count'] == 16
    replay = empty_platform.post('/internal/v1/import-batches', json=body, headers=auth)
    assert replay.status_code == 200
    assert replay.json()['idempotent'] is True
    assert replay.json()['import_batch_id'] == imported['import_batch_id']
    assert empty_platform.get('/v1/observations').json()['items'] == []
    changed = {**body, 'selection_snapshot': {**body['selection_snapshot'], 'source_datapoint_ids': ['E1-5_01']}}
    conflicting = empty_platform.post('/internal/v1/import-batches', json=changed, headers=auth)
    assert conflicting.status_code == 409
    assert conflicting.json()['code'] == 'import_scope_conflict'
    assert empty_platform.get('/v1/observations').json()['items'] == []
    # The real extractor writes execution_status, not legacy status. A
    # successful finite task with no disclosed values remains value-free.
    complete = {**manifest, 'execution_status': 'completed',
                'is_partial': False, 'unfinished_metric_ids': [], 'pending_metric_ids': [],
                'outcomes': [{'metric_id': package['metric_ids'][0], 'status': 'not_found',
                              'guard_accepted': True, 'contract_validation_status': 'passed'}]}
    (directory / 'manifest.json').write_text(json.dumps(complete))
    completed = empty_platform.post('/internal/v1/import-batches', json=body, headers=auth)
    assert completed.status_code == 200, completed.text
    assert completed.json()['result_state'] == 'complete'
    assert completed.json()['import_batch_id'] == imported['import_batch_id'], 'A manifest phase change with the same job and Core must not fabricate a new fact batch'
    assert completed.json()['idempotent'] is True
    assert empty_platform.get('/v1/observations').json()['items'] == []
    completed_replay = empty_platform.post('/internal/v1/import-batches', json=body, headers=auth)
    assert completed_replay.status_code == 200
    assert completed_replay.json()['import_batch_id'] == imported['import_batch_id']
    assert completed_replay.json()['result_state'] == 'complete'
    (directory / 'manifest.json').write_text(json.dumps({**complete, 'is_partial': True}))
    contradictory = empty_platform.post('/internal/v1/import-batches', json=body, headers=auth)
    assert contradictory.status_code == 200, contradictory.text
    assert contradictory.json()['result_state'] == 'partial'
    assert contradictory.json()['import_batch_id'] == imported['import_batch_id']
