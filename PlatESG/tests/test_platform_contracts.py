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
sys.path.insert(0,str(REPOSITORY))
from PlatESG.backend.main import create_app

















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
    assert len(catalog['items']) == 55
    assert all('readiness' not in m and 'standard_binding' not in m for m in catalog['items'])
    observations = empty_platform.get('/v1/observations', params={'dataset_version': catalog['dataset_version']}).json()
    assert observations['items'] == []
    assert observations['next_cursor'] is None
    assert empty_platform.get('/v1/entities').json()['items'] == []
    coverage_response = empty_platform.get('/v1/coverage')
    assert coverage_response.status_code == 200, coverage_response.text
    assert coverage_response.json()['items'] == []
    snapshot = empty_platform.get('/internal/v1/extraction-profiles', headers={'Authorization':'Bearer independent-boundary-token'}).json()
    assert snapshot['output_count'] == 19
    assert len(snapshot['overall_source_datapoint_ids']) == 55


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
