"""Compile actual route/DTO definitions into an isolated app, no backend boot.

AST extraction deliberately excludes main.py startup, ledgers, queue, workflow,
model configuration and real HTTP transports. These are API software smoke
checks, not live failure/report quality acceptance.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
import pytest

from esg_runtime.lifecycle import LifecycleBlocked
from esg_runtime.local_operator import install_loopback_marker, operator_headers
from esg_targeted.storage.intermediate_cleanup import IntermediateCleanupPlan


@pytest.fixture
def api():
    source = Path(__file__).parents[1] / 'src/esg_targeted/api/main.py'
    names = {'IntermediateExecuteRequest', 'IntermediateRestoreRequest', '_intermediate_operator',
        '_lifecycle_error', 'intermediate_plan', 'execute_intermediates', 'restore_intermediates'}
    tree = ast.parse(source.read_text())
    definitions = [item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.ClassDef)) and item.name in names]
    assert {item.name for item in definitions} == names
    state = {'principal': {'roles': ['admin'], 'operator_ref': 'synthetic-admin'}, 'calls': []}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return json.dumps(state['principal']).encode()
    def open_request(request, timeout):
        state['calls'].append(('auth', request.full_url))
        state['forwarded_headers'] = request.headers
        return Response()
    def operation(name, **kwargs):
        state['calls'].append((name, kwargs))
        return {'state': 'synthetic_only', 'calls_model': False}
    app = FastAPI()
    install_loopback_marker(app)
    scope = {'app': app, 'BaseModel': BaseModel, 'ConfigDict': ConfigDict, 'Field': Field,
        'operator_headers': operator_headers,
        'IntermediateCleanupPlan': IntermediateCleanupPlan, 'Header': Header, 'HTTPException': HTTPException,
        'LifecycleBlocked': LifecycleBlocked, 'json': json,
        'os': SimpleNamespace(getenv=lambda *_: 'http://isolated.invalid'),
        'Request': lambda url, headers: SimpleNamespace(full_url=url, headers=headers),
        'ProxyHandler': lambda *_: None, 'build_opener': lambda *_: SimpleNamespace(open=open_request),
        'HTTPError': type('HTTPError', (Exception,), {}), 'URLError': type('URLError', (Exception,), {}),
        'intermediate_cleanup': SimpleNamespace(execute=lambda sid, **kw: operation('execute', session_id=sid, **kw),
            restore=lambda sid, **kw: operation('restore', session_id=sid, **kw), plan=lambda sid: None)}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), scope)
    return SimpleNamespace(client=TestClient(app), state=state, scope=scope,
        endpoint='/api/report-sessions/report-synthetic/intermediates/execute',
        body={'plan_id': 'intermediate-plan-' + 'a' * 64, 'expected_revision': 2, 'reason': '合成API冒烟'})


def test_loopback_without_credential_lets_the_publication_service_decide(api):
    local = TestClient(api.scope['app'], client=('127.0.0.1', 50000))
    assert local.post(api.endpoint, json=api.body).status_code == 200
    # Nothing is invented: the identity request carries no credential, and
    # the publication service alone decides whether this is the local operator.
    assert api.state['forwarded_headers'] == {}
    assert api.state['calls'][-1][0] == 'execute'
    remote = TestClient(api.scope['app'], client=('192.0.2.10', 50000))
    api.state['calls'].clear()
    assert remote.post(api.endpoint, json=api.body).status_code == 401
    assert api.state['calls'] == []


@pytest.mark.parametrize('authorization', [None, 'Basic fake', 'Bearer ', 'fake'])
def test_missing_or_invalid_bearer_never_invokes_auth_or_operation(api, authorization):
    headers = {} if authorization is None else {'Authorization': authorization}
    assert api.client.post(api.endpoint, headers=headers, json=api.body).status_code == 401
    assert api.state['calls'] == []


@pytest.mark.parametrize('principal', [None, [], 1, {}, {'roles': ['admin'], 'operator_ref': {}},
    {'roles': ['admin'], 'operator_ref': 42}, {'roles': ['admin'], 'operator_ref': True},
    {'roles': ['admin'], 'operator_ref': '   '}, {'roles': [1], 'operator_ref': 'operator'},
    {'roles': 'admin', 'operator_ref': 'operator'}, {'roles': ['read'], 'operator_ref': 'operator'},
    {'roles': ['review'], 'operator_ref': 'operator'}])
def test_strict_admin_and_operator_principal_reject_invalid_shapes(api, principal):
    api.state['principal'] = principal
    response = api.client.post(api.endpoint, headers={'Authorization': 'Bearer synthetic-token'}, json=api.body)
    assert response.status_code == 403 and all(call[0] == 'auth' for call in api.state['calls'])


@pytest.mark.parametrize('field,value', [('expected_revision', True), ('expected_revision', '2'),
    ('expected_revision', 2.0), ('expected_revision', -1), ('reason', ' '), ('plan_id', 'invalid')])
def test_strict_request_rejects_invalid_revision_or_reason_before_operation(api, field, value):
    body = {**api.body, field: value}
    assert api.client.post(api.endpoint, headers={'Authorization': 'Bearer synthetic-token'}, json=body).status_code == 422
    assert api.state['calls'] == []


def test_actor_paths_scope_cannot_be_supplied_and_routes_have_no_purge(api):
    for field in ('operator_ref', 'job_ids', 'paths', 'source_artifacts', 'permanent_deletion'):
        response = api.client.post(api.endpoint, headers={'Authorization': 'Bearer synthetic-token'}, json={**api.body, field: 'forged'})
        assert response.status_code == 422
    response = api.client.post(api.endpoint, headers={'Authorization': 'Bearer synthetic-token'}, json=api.body)
    assert response.status_code == 200
    assert api.state['calls'][-1] == ('execute', {**api.body, 'session_id': 'report-synthetic', 'operator_ref': 'synthetic-admin'})
    assert all('purge' not in route.path for route in api.scope['app'].routes)


def test_plan_read_role_and_restore_strict_identity_use_actual_definition(api):
    api.state['principal'] = {'roles': ['read'], 'operator_ref': 'synthetic-reader'}
    assert api.scope['_intermediate_operator']('Bearer synthetic-token') == 'synthetic-reader'
    assert api.client.post(api.endpoint, headers={'Authorization': 'Bearer synthetic-token'}, json=api.body).status_code == 403
    api.state['principal'] = {'roles': ['admin'], 'operator_ref': 'synthetic-admin'}
    path = api.endpoint.replace('/execute', '/restore')
    body = {'cleanup_id': 'intermediate-' + 'b' * 64, 'expected_revision': 2, 'reason': '合成恢复'}
    assert api.client.post(path, headers={'Authorization': 'Bearer synthetic-token'}, json=body).status_code == 200
    assert api.state['calls'][-1][0] == 'restore'
    assert api.client.post(path, headers={'Authorization': 'Bearer synthetic-token'}, json={**body, 'cleanup_id': '../escape'}).status_code == 422
