"""Public CSV preserves released identities and exact values across pagination."""
from copy import deepcopy
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from PlatESG.backend import main


@pytest.mark.parametrize('requested_version', [None, 17])
def test_csv_keeps_extension_and_original_identity_exact_decimal_and_fixed_version(monkeypatch, tmp_path, requested_version):
    version = requested_version if requested_version is not None else 42
    common = {'dataset_version': version, 'entity_ref': 'fixture-company', 'entity_name': '=HYPERLINK("fixture")',
              'is_demo': False, 'definition_version': '1.0.0', 'period_start': '2024-01-01',
              'period_end': '2024-12-31', 'boundary_code': 'own_operations', 'measurement_method': None,
              'value_role': 'actual', 'canonical_unit': 'tCO2e', 'dimensions': {'scope_2_method': 'location_based'},
              'lower_bound': None, 'upper_bound': None, 'statement_text': '@fixture statement',
              'comparison_eligibility': 'comparable', 'comparison_reasons': [], 'publication_status': 'published'}
    extension = {**common, 'observation_id': 'fixture-derived', 'metric_id': 'project.scope1-scope2-total',
                 'source_datapoint_id': None, 'clause_kind': 'project_extension',
                 'extension_id': 'project.extension.scope1-scope2-total',
                 'related_source_datapoint_ids': ['E1-6_07', 'E1-6_09', 'E1-6_10'], 'value_origin': 'derived',
                 'value_numeric': '9007199254740993.000001'}
    original = {**common, 'observation_id': 'fixture-original', 'metric_id': 'ghg.scope1',
                'source_datapoint_id': 'E1-6_07', 'clause_kind': 'original_datapoint', 'extension_id': None,
                'related_source_datapoint_ids': [], 'value_origin': 'reported', 'value_numeric': '-0.000001'}
    # Validate the supplied public DTOs independently of the CSV field list.
    main.Observation.model_validate(extension)
    main.Observation.model_validate(original)
    calls = []

    class FrozenExportBridge:
        def __init__(self, config):
            pass

        def call(self, method, params):
            assert method == 'observations'
            calls.append(deepcopy(params))
            if not params.get('cursor'):
                assert params['dataset_version'] == requested_version
                return {'dataset_version': version, 'dictionary_version': '1.1.0',
                        'items': [deepcopy(extension)], 'next_cursor': 'fixed-second-page'}
            # A newer live release must not replace the first resolved dataset.
            assert params['dataset_version'] == version
            assert params['cursor'] == 'fixed-second-page'
            return {'dataset_version': version, 'dictionary_version': '1.1.0',
                    'items': [deepcopy(original)], 'next_cursor': None}

    monkeypatch.setattr(main, 'DatabaseBridge', FrozenExportBridge)
    app = main.create_app({'PLATESG_AUTO_PUBLISH': '0', 'ESG_EXTENSION_ROOT': str(tmp_path / 'extensions')})
    client = TestClient(app)  # No lifespan, database, background worker or model is started.
    params = {'format': 'csv'}
    if requested_version is not None:
        params['dataset_version'] = requested_version
    response = client.get('/v1/exports', params=params)
    assert response.status_code == 200, response.text
    assert response.headers['content-disposition'] == f'attachment; filename="platesg-v{version}.csv"'
    reader = csv.DictReader(io.StringIO(response.text.removeprefix('\ufeff')))
    rows = list(reader)
    assert len(calls) == len(rows) == 2
    assert rows[0]['clause_kind'] == 'project_extension'
    assert rows[0]['extension_id'] == extension['extension_id']
    assert rows[0]['source_datapoint_id'] == ''
    assert json.loads(rows[0]['related_source_datapoint_ids']) == extension['related_source_datapoint_ids']
    assert rows[0]['value_origin'] == 'derived'
    assert rows[0]['value_numeric'] == '9007199254740993.000001'
    assert rows[1]['source_datapoint_id'] == 'E1-6_07'
    assert rows[1]['clause_kind'] == 'original_datapoint'
    assert rows[1]['extension_id'] == ''
    assert json.loads(rows[1]['related_source_datapoint_ids']) == []
    assert rows[1]['value_origin'] == 'reported'
    assert rows[1]['value_numeric'] == '-0.000001'
    assert all(row['dataset_version'] == str(version) for row in rows)
    assert all(row['entity_name'].startswith("'=") and row['statement_text'].startswith("'@") for row in rows)
    assert all(json.loads(row['dimensions']) == common['dimensions'] for row in rows)
