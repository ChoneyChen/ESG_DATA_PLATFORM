"""Readonly facade contract; isolated transport, no SQL writes or model calls."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import requests

from esg_v2.api.console import console_router
from esg_v2.api.console_delivery_identity import COUNT_KEYS


NATIVE = 'report-' + 'a' * 32
PLAN = 'plan-' + 'b' * 32
HEADERS = {'Authorization': 'Bearer isolated-read-token'}


def counts(source=None, selected=None, internal=None, published=None):
    return dict(zip(COUNT_KEYS, (source, selected, internal, published)))


def fixture(tmp_path, monkeypatch):
    root = tmp_path / 'output'
    bundle = root / 'tx-fixed'
    bundle.mkdir(parents=True)
    raw = json.dumps({'job_id': bundle.name}).encode()
    (bundle / 'manifest.json').write_bytes(raw)
    manifest = {'run_id': 'ir-fixed', 'document_id': 'doc-fixed', 'ir_revision': 2,
                'source': {'pdf_sha256': 'c' * 64}}
    plan = {'plan_id': PLAN, 'status': 'interrupted', 'report_request': {'name': 'fixed extraction'},
            'documents': [{'ir_run_id': 'ir-fixed', 'native_session_id': NATIVE}]}
    report = {'report_id': 'report-registered', 'revision': 7,
        'metadata': {'title': 'Registered report', 'publisher_entity_ref': 'group-registered',
                     'period_start': '2025-01-01', 'period_end': '2025-12-31'},
        'asset_versions': [{'asset_id': 'asset-fixed', 'source_sha256': 'c' * 64, 'version': 3}]}
    asset = {'asset_id': 'asset-fixed', 'sha256': 'c' * 64}
    reports, plans = [report], [plan]
    session = {'session_id': PLAN, 'native_session_id': NATIVE, 'job_ids': ['tx-fixed'],
        'execution_status': 'failed', 'native_execution_status': 'failed', 'result_status': 'needs_confirmation',
        'database_status': 'not_imported',
        'delivery_counts': {'saved_source_facts': 294},
        'jobs': [{'job_id': 'tx-fixed', 'status': 'failed', 'recorded_status': 'failed',
                  'request': {'report_session_id': NATIVE, 'package_id': 'esrs.e1-5'}}],
        'stages': [{'job_id': 'tx-fixed', 'counts': {'selected_metrics': 2, 'attempted_metrics': 1, 'not_executed_metrics': 1}}],
        'delivery_artifacts': [{'job_id': 'tx-fixed', 'status': 'available', 'artifact_revision_id': 'original',
            'bundle_relative_path': '.', 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'created_at': '2026-10-07T00:00:00Z', 'source_fact_count': 294, 'core_fact_count': 0}]}
    def registered_report(ref):
        match = next((r for r in reports if r['report_id'] == ref), None)
        if match is None:
            raise KeyError(ref)
        return match
    processing = SimpleNamespace(
        report=registered_report,
        reports=lambda: reports, _manifest=lambda *_: (manifest, 'fixed-sha'), integrity_check=lambda *_: None,
        assets=SimpleNamespace(get=lambda _: asset))
    calls = []
    result = {'schema_version': 'delivery-status-v1', 'status': 'source_archived',
        'checked_at': datetime.now(timezone.utc).isoformat(), 'native_session_id': NATIVE,
        'report_ref': report['report_id'], 'identity_basis': 'sql_batch_stage_match',
        'report_counts_scope': 'fixed_report_version', 'publication_dataset_version': 3,
        'reason_codes': [], 'reason_messages': [],
        'report_counts': counts(299, 0, 0, 0), 'session_counts': counts(294, 0, 0, 0),
        'current_revision_counts': counts(291, 0, 0, 0),
        'jobs': [{'job_id': 'tx-fixed', 'status': 'source_archived', 'identity_basis': 'sql_batch_stage_match',
                  'counts': counts(294, 0, 0, 0), 'reason_codes': [], 'artifacts': []}]}
    state = {'roles': ['read'], 'result': result, 'failure': None, 'status': 200}
    def rpc(method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert kwargs['headers'] == HEADERS
        assert url.endswith('/internal/v1/me') or url.endswith('/internal/v1/delivery-status/query')
        assert kwargs['allow_redirects'] is False
        if state['failure'] and (state.get('failure_stage') != 'query' or url.endswith('/delivery-status/query')):
            raise state['failure']
        return SimpleNamespace(status_code=state['status'], json=lambda:
            {'roles': state['roles']} if url.endswith('/me') else deepcopy(state['result']))
    monkeypatch.setattr('esg_v2.api.console.requests.request', rpc)
    app = FastAPI()
    app.include_router(console_router(processing, SimpleNamespace(list=lambda: plans),
        lambda _: deepcopy(session), None, output_root=root))
    return SimpleNamespace(client=TestClient(app), root=root, bundle=bundle, report=report, reports=reports,
        plan=plan, plans=plans, manifest=manifest, asset=asset, processing=processing, session=session, calls=calls, state=state,
        url=f'/api/console/report-sessions/{PLAN}/delivery-status')


def query_calls(f):
    return [c for c in f.calls if c[1].endswith('/delivery-status/query')]


def assert_unknown(response, reason=None, status='unknown'):
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == status and result['available'] is False
    for key in ('report_counts', 'session_counts', 'current_revision_counts'):
        assert result[key] == counts()
    if reason:
        assert reason in result['reason_codes']
    return result


def test_delivery_status_requires_read_credentials_and_never_exposes_token(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    assert f.client.get(f.url).status_code == 401
    assert f.client.get(f.url, headers={'Authorization': 'isolated-read-token'}).status_code == 401
    assert f.calls == []
    f.state['roles'] = ['review']
    assert f.client.get(f.url, headers=HEADERS).status_code == 403
    assert query_calls(f) == []


def test_reload_requeries_sql_preserves_scope_and_does_not_use_file_or_legacy_counts(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    first = f.client.get(f.url, headers=HEADERS)
    second = f.client.get(f.url, headers=HEADERS)
    assert first.status_code == second.status_code == 200
    value = second.json()
    assert value['status'] == 'source_archived'
    assert value['report_counts']['archived_source_count'] == 299
    assert value['session_counts']['archived_source_count'] == 294
    assert value['current_revision_counts']['archived_source_count'] == 291
    assert value['current_revision_counts']['selected_candidate_count'] == 0
    assert value['current_revision_counts']['published_observation_count'] == 0
    assert value['execution']['legacy_database_status'] == 'not_imported'
    assert value['execution']['database_status_authoritative'] is False
    assert value['execution']['status'] == 'failed'
    assert value['execution']['jobs'][0]['attempted_metrics'] == 1
    assert len(query_calls(f)) == 2
    payload = query_calls(f)[-1][2]['json']
    assert payload['native_session_id'] == NATIVE
    assert payload['fixed_source'] == {'document_id': 'doc-fixed', 'ir_run_id': 'ir-fixed', 'ir_revision': 2}
    assert payload['report_identity']['report_version_ref'] == 'report-registered:asset:3'
    assert payload['report_identity']['organization_ref'] == 'group-registered'
    assert payload['job_ids'] == ['tx-fixed']
    assert payload['artifacts'][0]['bundle_path'] == str(f.bundle.resolve())
    assert 'isolated-read-token' not in second.text


def test_native_alias_resolves_same_exact_scope(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    response = f.client.get(f.url.replace(PLAN, NATIVE), headers=HEADERS)
    assert response.status_code == 200 and response.json()['plan_id'] == PLAN
    assert query_calls(f)[0][2]['json']['native_session_id'] == NATIVE


def test_same_pdf_multiple_reports_requires_selection_and_never_picks_first(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    other = deepcopy(f.report)
    other['report_id'] = 'report-other-version'
    other['asset_versions'][0]['version'] = 9
    f.reports.append(other)
    result = assert_unknown(f.client.get(f.url, headers=HEADERS), 'report_selection_required')
    assert len(result['report_options']) == 2 and query_calls(f) == []
    f.state['result']['report_ref'] = other['report_id']
    selected = f.client.get(f.url, params={'report_id': other['report_id']}, headers=HEADERS)
    assert selected.status_code == 200 and selected.json()['status'] == 'source_archived'
    assert query_calls(f)[-1][2]['json']['report_identity']['report_version_ref'] == 'report-other-version:asset:9'


def test_same_report_duplicate_pdf_versions_stays_unknown_after_report_selection(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.report['asset_versions'].append({**f.report['asset_versions'][0], 'version': 4})
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'report_selection_required')
    assert_unknown(f.client.get(f.url, params={'report_id': f.report['report_id']}, headers=HEADERS), 'report_version_ambiguous')
    assert query_calls(f) == []


@pytest.mark.parametrize('field,value', [('ir_revision', None), ('ir_revision', True), ('document_id', None), ('run_id', 'other-ir')])
def test_fixed_ir_identity_is_not_inferred_or_coerced(tmp_path, monkeypatch, field, value):
    f = fixture(tmp_path, monkeypatch)
    f.manifest[field] = value
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'fixed_evidence_identity_mismatch', 'identity_mismatch')
    assert query_calls(f) == []


def test_explicit_other_pdf_report_and_source_change_are_rejected(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.report['asset_versions'][0]['source_sha256'] = 'd' * 64
    assert_unknown(f.client.get(f.url, params={'report_id': f.report['report_id']}, headers=HEADERS),
                   'fixed_evidence_identity_mismatch', 'identity_mismatch')
    f.report['asset_versions'][0]['source_sha256'] = 'c' * 64
    f.asset['sha256'] = 'd' * 64
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'fixed_evidence_identity_mismatch', 'identity_mismatch')
    assert query_calls(f) == []


def test_current_revision_is_exact_latest_and_tied_versions_are_unknown(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    revision = 'partial-' + 'e' * 32
    f.session['delivery_artifacts'].append({**f.session['delivery_artifacts'][0],
        'artifact_revision_id': revision, 'bundle_relative_path': f'terminal-revisions/{revision}',
        'created_at': '2026-10-08T00:00:00Z', 'manifest_sha256': 'e' * 64})
    assert f.client.get(f.url, headers=HEADERS).status_code == 200
    artifact = query_calls(f)[-1][2]['json']['artifacts'][0]
    assert artifact['artifact_revision_id'] == revision
    assert artifact['manifest_sha256'] == 'e' * 64
    assert artifact['bundle_path'] == str((f.bundle / 'terminal-revisions' / revision).resolve())
    f.session['delivery_artifacts'][0]['created_at'] = '2026-10-08T00:00:00Z'
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'artifact_revision_ambiguous')
    assert len(query_calls(f)) == 1


@pytest.mark.parametrize('change', ['relative_path', 'symlink', 'unknown_job', 'parent_session'])
def test_scope_and_paths_fail_closed_before_sql_projection(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    if change == 'relative_path':
        f.session['delivery_artifacts'][0]['bundle_relative_path'] = '../../outside'
    elif change == 'unknown_job':
        f.session['delivery_artifacts'][0]['job_id'] = 'tx-other'
    elif change == 'parent_session':
        f.session['jobs'][0]['request']['report_session_id'] = 'report-' + 'd' * 32
    else:
        moved = tmp_path / 'outside'
        f.bundle.rename(moved)
        f.bundle.symlink_to(moved, target_is_directory=True)
    result = assert_unknown(f.client.get(f.url, headers=HEADERS), status='identity_mismatch')
    assert query_calls(f) == [] and result['execution']['jobs'][0]['status'] == 'failed'


def test_no_frozen_artifact_passes_empty_descriptor_and_keeps_true_unexecuted_state(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.session['delivery_artifacts'] = []
    f.session['jobs'][0].update(status='interrupted', recorded_status='queued')
    f.session['stages'][0]['counts'] = {'selected_metrics': 9, 'attempted_metrics': 0, 'not_executed_metrics': 9}
    f.state['result']['status'] = 'no_artifact'
    f.state['result']['jobs'][0]['status'] = 'no_artifact'
    value = f.client.get(f.url, headers=HEADERS).json()
    assert query_calls(f)[0][2]['json']['artifacts'] == []
    job = value['execution']['jobs'][0]
    assert job['status'] == 'interrupted' and job['recorded_status'] == 'queued'
    assert (job['selected_metrics'], job['attempted_metrics'], job['not_executed_metrics']) == (9, 0, 9)
    assert job['artifact_state'] == 'not_saved'


def test_native_read_failure_is_unknown_without_calling_sql(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.session['native_unavailable'] = {'code': 'extraction_read_unavailable'}
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'native_session_unavailable')
    assert query_calls(f) == []


def test_ambiguous_native_plan_binding_is_not_selected_by_order(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    other = deepcopy(f.plan); other['plan_id'] = 'plan-' + 'f' * 32
    f.plans.append(other)
    assert_unknown(f.client.get(f.url.replace(PLAN, NATIVE), headers=HEADERS), 'session_binding_ambiguous')
    assert query_calls(f) == []


@pytest.mark.parametrize('status', [401, 403])
def test_platform_permission_rejections_remain_http_rejections(tmp_path, monkeypatch, status):
    f = fixture(tmp_path, monkeypatch)
    f.state['status'] = status
    assert f.client.get(f.url, headers=HEADERS).status_code == status
    assert query_calls(f) == []


def test_sql_read_timeout_is_unknown_null_counts_then_refresh_can_recover(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.state['failure'] = requests.Timeout('isolated transport timeout')
    f.state['failure_stage'] = 'query'
    unknown = assert_unknown(f.client.get(f.url, headers=HEADERS), 'publication_unavailable')
    assert unknown['execution']['status'] == 'failed'
    f.state['failure'] = None
    value = f.client.get(f.url, headers=HEADERS).json()
    assert value['status'] == 'source_archived' and value['current_revision_counts']['archived_source_count'] == 291


@pytest.mark.parametrize('field,value', [('report_ref', 'report-other'), ('native_session_id', 'report-' + 'f' * 32)])
def test_other_identity_sql_response_cannot_cover_current_scope(tmp_path, monkeypatch, field, value):
    f = fixture(tmp_path, monkeypatch)
    f.state['result'][field] = value
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'delivery_projection_identity_mismatch', 'identity_mismatch')


@pytest.mark.parametrize('field,value', [('archived_source_count', True), ('selected_candidate_count', -1)])
def test_malformed_sql_counts_are_unknown_not_coerced(tmp_path, monkeypatch, field, value):
    f = fixture(tmp_path, monkeypatch)
    f.state['result']['current_revision_counts'][field] = value
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'delivery_projection_invalid')


def test_missing_registered_report_returns_unknown_without_sql_query(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    assert_unknown(f.client.get(f.url, params={'report_id': 'report-absent'}, headers=HEADERS), 'report_identity_not_registered')
    f.reports.clear()
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'report_identity_not_registered')
    assert query_calls(f) == []


@pytest.mark.parametrize('legacy_field', ['absent', 'null'])
def test_legacy_original_artifact_and_unavailable_stage_counts_stay_distinct(tmp_path, monkeypatch, legacy_field):
    f = fixture(tmp_path, monkeypatch)
    if legacy_field == 'absent':
        del f.session['delivery_artifacts']
    else:
        f.session['delivery_artifacts'] = None
    (f.bundle / 'exports').mkdir()
    (f.bundle / 'exports/extraction-result.json').write_text('{}')
    f.session['stages'] = []
    value = f.client.get(f.url, headers=HEADERS).json()
    assert query_calls(f)[0][2]['json']['artifacts'][0]['artifact_revision_id'] == 'original'
    job = value['execution']['jobs'][0]
    assert job['attempted_metrics'] is None and job['not_executed_metrics'] is None
    assert value['current_revision_counts']['archived_source_count'] == 291


def test_upstream_partial_current_revision_does_not_become_extraction_success(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.state['result']['status'] = 'partial'
    f.state['result']['current_revision_counts'] = counts(0, 0, 0, 0)
    value = f.client.get(f.url, headers=HEADERS).json()
    assert value['status'] == 'partial' and value['execution']['status'] == 'failed'
    assert value['session_counts']['archived_source_count'] == 294
    assert value['publication_dataset_version'] == 3
    assert value['report_counts_scope'] == 'fixed_report_version'


def test_false_zero_or_other_child_scope_is_not_accepted_as_authoritative(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.state['result']['jobs'] = [{'job_id': 'tx-other', 'counts': counts(0, 0, 0, 0)}]
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'delivery_projection_invalid')


def test_platform_nonobject_response_is_unknown_not_server_error(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.state['result'] = []
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'delivery_projection_invalid')


@pytest.mark.parametrize('jobs', [[None], [3], ['job'], [[]]])
def test_malformed_sql_job_entries_are_unknown_without_attribute_error(tmp_path, monkeypatch, jobs):
    f = fixture(tmp_path, monkeypatch)
    f.state['result']['jobs'] = jobs
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'delivery_projection_invalid')


def test_missing_report_object_is_unknown_without_attribute_error(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.processing.report = lambda _: None
    assert_unknown(f.client.get(f.url, params={'report_id': 'report-absent'}, headers=HEADERS), 'report_identity_not_registered')
    assert query_calls(f) == []


@pytest.mark.parametrize('field,value', [('report_counts_scope', 'all_reports'), ('publication_dataset_version', True)])
def test_unverified_publication_scope_is_unknown(tmp_path, monkeypatch, field, value):
    f = fixture(tmp_path, monkeypatch)
    f.state['result'][field] = value
    assert_unknown(f.client.get(f.url, headers=HEADERS), 'delivery_projection_invalid')
