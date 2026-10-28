"""Product selection and frozen-source integrity at the extraction boundary."""
import importlib.util
import hashlib
import json
from pathlib import Path

import pytest
from esg_standard_packages.contracts import MeasurementContract

PLATFORM = Path(__file__).resolve().parents[1]
STANDARDS = PLATFORM.parent / 'ExtractESG' / 'standard_packages'
module_spec = importlib.util.spec_from_file_location('platform_dictionary_builder', PLATFORM / 'scripts/build-dictionary.py')
builder = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(builder)


@pytest.fixture(scope='module')
def artifacts():
    return builder.build_dictionary(dictionary_version='1.5.0', allow_contract_changes=True)


def test_generated_artifacts_preserve_public_semantics_and_compile_scientific_sources(artifacts):
    dictionary, profile = artifacts
    previous = json.loads((PLATFORM / 'catalog/metric-dictionary.v1.json').read_text())
    assert dictionary['dictionary_version'] == '1.5.0'
    assert [metric['source_datapoint_id'] for metric in dictionary['metrics']] == [metric['source_datapoint_id'] for metric in previous['metrics']]
    assert profile == json.loads((PLATFORM / 'catalog/extraction-profile.e1-e3.json').read_text()) | {'dictionary_version': '1.5.0'}
    for metric, old in zip(dictionary['metrics'], previous['metrics']):
        for key in ('metric_id', 'label_zh', 'value_type', 'canonical_unit', 'unit_dimension',
                    'required_dimensions', 'comparison_dimensions', 'fixed_dimensions', 'dimension_constraints',
                    'required_context_fields', 'typed_context_constraints', 'description', 'comparison_mode', 'value_role'):
            assert metric[key] == old[key], (metric['source_datapoint_id'], key)
        assert metric['scientific_contract_source']['measurement_contract'] == metric['source_measurement_contract']
        if metric['source_datapoint_id'] != 'E1-6_30':
            assert metric['aggregation'] == old['aggregation']
    intensity = next(metric for metric in dictionary['metrics'] if metric['source_datapoint_id'] == 'E1-6_30')
    assert intensity['aggregation'] == 'ratio_recompute'


def test_a_rebuild_without_the_flag_rejects_an_unapproved_aggregation_change():
    # The shipped dictionary already carries the approved change; the guard is
    # checked against the sealed previous version it was approved over.
    previous = json.loads((PLATFORM / 'catalog/history/metric-dictionary.1.4.5.json').read_text())
    with pytest.raises(ValueError, match='E1-6_30'):
        builder.build_dictionary(dictionary_version='1.5.0', previous_dictionary=previous)


def test_all_major_ids_preserved_in_original_order_with_explicit_pending(artifacts):
    dictionary, profile = artifacts
    selection = json.loads((STANDARDS / 'profiles/esrs/2023-set1/platform-major.json').read_text())
    ids = [m['source_datapoint_id'] for m in dictionary['metrics']]
    assert ids == selection['source_datapoint_ids']
    assert len(ids) == len(set(ids)) == 55
    assert len(profile['ready_source_datapoint_ids']) == 55
    assert len(profile['pending_source_datapoint_ids']) == 0
    assert profile['overall_source_datapoint_ids'] == ids
    # The executable E1–E3 scope holds 19 ready clauses out of the 55.
    assert len(profile['source_datapoint_ids']) == 19 and set(profile['source_datapoint_ids']) <= set(ids)
    assert profile['output_count'] == 19
    assert set(profile['excluded_by_explicit_filter']) == set(ids) - set(profile['source_datapoint_ids'])


def test_main_selection_does_not_activate_all_package_core(artifacts):
    dictionary, profile = artifacts
    registry = json.loads((STANDARDS / 'catalogs/esrs/2023-set1/registry.json').read_text())
    modules = {original: module for module in registry['modules'] for original in module['source_datapoint_ids']}
    for metric in dictionary['metrics']:
        assert metric['source_datapoint_id'] in modules[metric['source_datapoint_id']]['core_source_datapoint_ids']
    e1 = next(r for r in profile['routes'] if r['package_id'].endswith('.e1-5'))
    assert e1['source_datapoint_ids'] == ['E1-5_01', 'E1-5_02', 'E1-5_05', 'E1-5_09']
    assert 'esrs.2023-set1.e1-5.dp02' in e1['output_metric_ids']
    # The existing derived-ratio fallback may read internal operands, which
    # still cannot alter the selected outputs or their coverage denominator.
    assert set(e1['output_metric_ids']).isdisjoint(e1['internal_dependency_metric_ids'])
    assert len(e1['execution_metric_ids']) > len(e1['output_metric_ids'])


def test_published_bindings_pin_actual_runtime_and_planned_has_no_fake_version(artifacts):
    dictionary, _ = artifacts
    for metric in dictionary['metrics']:
        binding = metric['standard_binding']
        if metric['readiness'] == 'ready':
            package = json.loads((STANDARDS / 'dist' / binding['package_id'] / binding['package_version'] / 'package.json').read_text())
            assert binding['digest'] == package['compilation']['source_digest']
            source = next(m for m in package['metrics'] if m['metric_id'] == binding['metric_id'])
            assert source['source_datapoint_id'] == metric['source_datapoint_id']
            # The pinned raw release stays immutable; legacy omitted optional
            # fields receive the same typed defaults used by the dictionary builder.
            assert MeasurementContract.model_validate(source['measurement_contract']).model_dump(mode='json') == MeasurementContract.model_validate(metric['source_measurement_contract']).model_dump(mode='json')
            assert source['provenance']['row_sha256'] == metric['source_reference']['row_sha256']
        else:
            assert binding['package_id']
            assert binding['package_version'] is None
            assert binding['metric_id'] is None
            assert binding['digest'] is None
            assert metric['source_measurement_contract'] is None


def test_method_and_scope_are_explicit_constraints_not_implicit_defaults(artifacts):
    dictionary, _ = artifacts
    by_id = {m['source_datapoint_id']: m for m in dictionary['metrics']}
    for source, scope, method in [('E1-6_07', 'scope_1', None), ('E1-6_09', 'scope_2', 'location_based'), ('E1-6_10', 'scope_2', 'market_based'), ('E1-6_11', 'scope_3', None)]:
        metric = by_id[source]
        assert metric['dimension_constraints']['ghg_scope']['allowed_values'] == [scope]
        assert 'measurement_method' not in metric['required_dimensions']
        if method:
            # A method the clause itself fixes is part of the comparison; a
            # scope that declares none does not require one to compare.
            assert 'measurement_method' in metric['comparison_dimensions']
            assert metric['required_context_fields'] == ['measurement_method']
            assert metric['typed_context_constraints']['measurement_method']['allowed_values'] == [method]
        else:
            assert 'measurement_method' not in metric['comparison_dimensions']
    assert by_id['E3-4_01']['dimension_constraints']['water_measure']['allowed_values'] == ['consumption']
    assert by_id['E3-4_11']['dimension_constraints']['water_measure']['allowed_values'] == ['withdrawal']
    assert by_id['E2-4_02']['dimension_constraints']['pollution_medium']['allowed_values'] == ['air']
    assert by_id['E2-4_03']['dimension_constraints']['pollution_medium']['allowed_values'] == ['water']


def test_canonical_units_and_non_numeric_polarity(artifacts):
    dictionary, _ = artifacts
    by_id = {m['source_datapoint_id']: m for m in dictionary['metrics']}
    for source in ['E1-5_01', 'E1-5_05']:
        assert by_id[source]['canonical_unit'] == 'MWh'
    for source in ['E1-6_07', 'E1-6_09', 'E1-6_10', 'E1-6_11']:
        assert by_id[source]['canonical_unit'] == 'tCO2e'
    for source in ['E3-4_01', 'E3-4_02', 'E3-4_03', 'E3-4_11', 'E3-4_12']:
        assert by_id[source]['canonical_unit'] == 'm3'
    assert by_id['E1-5_09']['canonical_unit'] == 'ratio'
    assert by_id['E1-5_09']['value_constraints'] == {'minimum': '0', 'maximum': '1'}
    assert by_id['E2-4_09']['value_type'] == 'statement'
    assert by_id['E2-4_09']['value_role'] == 'context'
    assert by_id['E2-4_09']['canonical_unit'] is None
    assert by_id['E2-4_09']['comparison_mode'] == 'structured_display'
    assert by_id['G1-1_03']['statement_polarity'] == 'negative_absence'
    assert by_id['S1-14_05']['canonical_unit'] == 'rate'
    assert by_id['G1-4_02']['canonical_unit'] is None


def test_raw_source_identity_and_legal_facets_preserved(artifacts):
    dictionary, _ = artifacts
    catalog = json.loads((STANDARDS / 'catalogs/esrs/2023-set1/ig3-original.json').read_text())
    rows = {r['id']: r for r in catalog['records'] if r['record_kind'] == '独立编号条目'}
    assert dictionary['source_sha256'] == catalog['source_sha256']
    for metric in dictionary['metrics']:
        row, source = rows[metric['source_datapoint_id']], metric['source_reference']
        assert metric['original_name'] == row['name']
        assert metric['original_data_type'] == row['data_type']
        assert source['source_sheet'] == row['source_sheet']
        assert source['source_row'] == row['source_row']
        assert source['may'] == row['may']
        assert source['conditional_or_alternative'] == row['conditional_or_alternative']
        assert source['row_sha256'] == hashlib.sha256(builder.canonical_json_bytes(row['raw_values'])).hexdigest()
    assert rows['E3-4_11']['may'] == 'V'
    assert rows['E3-4_12']['may'] == 'V'


def test_major_scope_maps_every_selected_metric_back_to_its_original_id():
    """The selector contract, without a browser: every metric the console may
    pre-select maps back to the same original ID, in the profile's own order.
    The old workbench enforced this in JavaScript; the console now relies on
    the same server contract."""
    import sys
    sys.path.insert(0, str(STANDARDS.parent / 'targeted_table_extract/backend/src'))
    from esg_targeted.standards.profiles import platform_major_profile
    profile = platform_major_profile(STANDARDS, topics=['E1', 'E2', 'E3'])
    dictionary = json.loads((PLATFORM / 'catalog/metric-dictionary.v1.json').read_text())
    by_metric = {m['standard_binding']['metric_id']: m['source_datapoint_id']
                 for m in dictionary['metrics'] if m.get('standard_binding')}
    originals = [by_metric[metric_id] for standard in profile['session_standards']
                 for metric_id in standard['metric_ids'] if metric_id in by_metric]
    assert originals == profile['source_datapoint_ids']
    assert profile['output_count'] == len(originals) == 19
    assert len(profile['overall_source_datapoint_ids']) == 55
    assert len(profile['pending_source_datapoint_ids']) == 0
