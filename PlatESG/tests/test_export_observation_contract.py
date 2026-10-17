"""Exports share the public Observation DTO without changing frozen science."""
from copy import deepcopy

from fastapi.testclient import TestClient

from PlatESG.backend import main


def test_json_export_matches_http_observation_dto_and_freezes_paged_version(monkeypatch, tmp_path):
    version = 42
    raw = {
        'dataset_version': version, 'observation_id': 'synthetic-exact', 'entity_ref': 'synthetic-company',
        'metric_id': 'synthetic-total', 'source_datapoint_id': None, 'clause_kind': 'project_extension',
        'extension_id': 'synthetic-extension', 'related_source_datapoint_ids': ['E1-6_07', 'E1-6_09'],
        'value_origin': 'derived', 'definition_version': '1.0.0', 'period_start': '2024-01-01',
        'period_end': '2024-12-31', 'boundary_code': 'own_operations', 'value_role': 'actual',
        'dimensions': {'ghg_scope': 'scope_1_2'}, 'canonical_unit': 'tCO2e',
        'value_numeric': '9007199254740993.000001', 'comparison_eligibility': 'comparable',
        'comparison_reasons': [], 'publication_status': 'published',
        'created_at': '2024-12-31T23:59:59Z', 'published_at': '2024-12-31T23:59:59Z',
        'internal_debug': {'reviewer': 'private-reviewer'},
        'reported_properties': [{'field_key': 'production_site', 'label_zh': '生产场址',
            'value_raw': ['南部基地', '南部基地'], 'value_type': 'text_array', 'unit_raw': None,
            'evidence_scope': 'source', 'evidence': [{'page_number': 18, 'source_quote': '合成来源说明'}],
            'report_title': '软件测试合成报告', 'source_role': 'operand', 'source_label': '范围1排放',
            'source_field': 'private_selector'}],
    }
    interval = {**deepcopy(raw), 'observation_id': 'synthetic-interval', 'value_numeric': None,
                'lower_bound': '0.000000000000000001', 'upper_bound': '1.000000000000000001'}
    calls = []

    class FrozenBridge:
        def __init__(self, config):
            pass

        def call(self, method, params):
            assert method == 'observations'
            calls.append(deepcopy(params))
            second = params.get('cursor') == 'second-page'
            if second:
                assert params['dataset_version'] == version
            return {'dataset_version': version, 'dictionary_version': 'synthetic-dictionary',
                    'items': [deepcopy(interval if second else raw)],
                    'next_cursor': None if second else 'second-page', 'as_of': '2025-01-01T00:00:00Z'}

    monkeypatch.setattr(main, 'DatabaseBridge', FrozenBridge)
    app = main.create_app({'PLATESG_AUTO_PUBLISH': '0', 'ESG_EXTENSION_ROOT': str(tmp_path / 'extensions')})
    client = TestClient(app)  # No lifespan/database/background processor/model.
    first = client.get('/v1/observations').json()
    second = client.get('/v1/observations', params={'dataset_version': version, 'cursor': 'second-page'}).json()
    response = client.get('/v1/exports', params={'format': 'json'})
    assert response.status_code == 200, response.text
    exported = response.json()
    assert exported['items'] == first['items'] + second['items']
    assert exported['dataset_version'] == version
    assert exported['items'][0]['value_numeric'] == '9007199254740993.000001'
    assert exported['items'][1]['value_numeric'] is None
    assert exported['items'][1]['lower_bound'] == '0.000000000000000001'
    assert exported['items'][1]['upper_bound'] == '1.000000000000000001'
    assert exported['items'][0]['reported_properties'][0]['value_raw'] == ['南部基地', '南部基地']
    assert exported['items'][0]['reported_properties'][0]['source_label'] == '范围1排放'
    assert all('created_at' not in row and 'internal_debug' not in row for row in exported['items'])
    assert 'source_field' not in exported['items'][0]['reported_properties'][0]
    assert exported['items'][0]['entity_kind'] is None
    assert response.headers['content-disposition'] == f'attachment; filename="platesg-v{version}.json"'
    assert calls[-2]['dataset_version'] is None and calls[-1]['dataset_version'] == version
