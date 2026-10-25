"""Finished extraction reaches reviewable candidates without an operator click."""
import hashlib
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from esg_v2.api.console import console_router

HEADERS = {'Authorization': 'Bearer fixture-secret'}


def build(tmp_path, monkeypatch, *, plan_status='completed', bound=True, artifacts_at_start=False):
    root = tmp_path / 'output'
    bundle = root / 'tx-handoff'
    (bundle / 'input').mkdir(parents=True)
    manifest = {'source': {'pdf_sha256': 'a' * 64}, 'run_id': 'ir-fixed'}
    (bundle / 'manifest.json').write_text(json.dumps({'job_id': 'tx-handoff'}))
    (bundle / 'input/request.json').write_text(json.dumps({'ir_run_id': 'ir-fixed'}))
    (bundle / 'input/ir-manifest.json').write_text(json.dumps(manifest))
    digest = hashlib.sha256((bundle / 'manifest.json').read_bytes()).hexdigest()
    descriptor = {'job_id': 'tx-handoff', 'artifact_revision_id': 'original', 'bundle_relative_path': '.',
                  'manifest_sha256': digest, 'created_at': '2026-10-25T00:00:00Z',
                  'source_fact_count': 4, 'core_fact_count': 2, 'status': 'available'}
    asset = {'asset_id': 'pdf-fixed', 'sha256': 'a' * 64}
    report = {'report_id': 'report-fixed', 'revision': 2,
              'metadata': {'title': 'Fixed report', 'publisher_entity_ref': 'org:fixed',
                           'period_start': '2025-01-01', 'period_end': '2025-12-31', 'language': 'zh'},
              'asset_versions': [{'asset_id': 'pdf-fixed', 'source_sha256': 'a' * 64, 'version': 1}]}
    processing = SimpleNamespace(report=lambda _: report, reports=lambda: [report] if bound else [],
        _manifest=lambda *_: (manifest, 'digest'), integrity_check=lambda *_: None,
        assets=SimpleNamespace(get=lambda _: asset))
    plan = {'plan_id': 'plan-fixed', 'report_request': {'name': 'fixture'}, 'status': plan_status,
            'documents': [{'asset_id': 'pdf-fixed', 'source_sha256': 'a' * 64,
                           'ir_run_id': 'ir-fixed', 'native_session_id': 'native-fixed'}]}
    state = {'artifacts': [descriptor] if artifacts_at_start else []}
    session = {'jobs': [{'job_id': 'tx-handoff'}], 'execution_status': 'finished',
               'delivery_counts': {'available_bundle_count': 1}}
    targeted_calls = []

    def targeted(method, path, payload=None, **kwargs):
        targeted_calls.append((method, path, payload))
        state['artifacts'] = [descriptor]
        return {'session_id': 'native-fixed'}

    def session_detail(_):
        return {**session, 'delivery_artifacts': state['artifacts']}

    calls = []

    def rpc(method, url, **kwargs):
        calls.append((method, url, kwargs))
        class Response:
            status_code = 200
            def json(self):
                if url.endswith('/me'):
                    return {'operator_ref': 'server-actor', 'roles': ['review']}
                return {'receipt_id': 'canonical-receipt', 'status': 'imported', 'imported': 2,
                        'auto_review': {'status': 'completed', 'applied': 2, 'needs_human': 1,
                                        'verification_rule_version': 'machine-verification-v3'}}
        return Response()

    monkeypatch.setattr('esg_v2.api.console.requests.request', rpc)
    app = FastAPI()
    app.include_router(console_router(processing, SimpleNamespace(list=lambda: [plan]), session_detail,
        targeted, output_root=root))
    return TestClient(app), targeted_calls, calls, state


def test_terminal_session_materializes_and_delivers_automatically(tmp_path, monkeypatch):
    client, targeted_calls, calls, _ = build(tmp_path, monkeypatch)
    response = client.post('/api/console/report-sessions/plan-fixed/handoff', json={}, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'imported' and body['stage'] == 'review'
    assert body['report_id'] == 'report-fixed'
    assert targeted_calls == [('POST', '/api/report-sessions/native-fixed/materialize',
                               {'reason': '提取完成自动交付进入审核'})]
    deliveries = [call for call in calls if call[1].endswith('/deliveries')]
    assert len(deliveries) == 1
    assert deliveries[0][2]['json']['report_metadata']['organization_ref'] == 'org:fixed'
    assert body['auto_review'] == {'rule_version': 'machine-verification-v3', 'approved': 2,
                                   'needs_human': 1, 'failed': 0}


def test_existing_bundle_skips_materialization(tmp_path, monkeypatch):
    client, targeted_calls, calls, _ = build(tmp_path, monkeypatch, artifacts_at_start=True)
    body = client.post('/api/console/report-sessions/plan-fixed/handoff', json={}, headers=HEADERS).json()
    assert body['status'] == 'imported'
    assert targeted_calls == []
    assert any(call[1].endswith('/deliveries') for call in calls)


def test_running_extraction_waits_instead_of_delivering(tmp_path, monkeypatch):
    client, targeted_calls, calls, _ = build(tmp_path, monkeypatch, plan_status='active')
    body = client.post('/api/console/report-sessions/plan-fixed/handoff', json={}, headers=HEADERS).json()
    assert body['status'] == 'waiting' and body['stage'] == 'extraction'
    assert targeted_calls == [] and not any(call[1].endswith('/deliveries') for call in calls)


def test_unbound_pdf_asks_for_identity_without_delivering(tmp_path, monkeypatch):
    client, targeted_calls, calls, _ = build(tmp_path, monkeypatch, bound=False)
    body = client.post('/api/console/report-sessions/plan-fixed/handoff', json={}, headers=HEADERS).json()
    assert body['status'] == 'needs_identity' and body['stage'] == 'identity'
    assert not any(call[1].endswith('/deliveries') for call in calls)


def test_handoff_requires_operator_authorization(tmp_path, monkeypatch):
    client, _, _, _ = build(tmp_path, monkeypatch)
    assert client.post('/api/console/report-sessions/plan-fixed/handoff', json={}).status_code == 401
