"""Synthetic permission service responses; no credential files or network."""
import pytest
from fastapi import HTTPException

from esg_v2.api.operator_auth import authorize_operator


def response(monkeypatch, principal, status=200):
    calls = []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def get(self, url, **kwargs):
            calls.append((self.trust_env, url, kwargs))
            class Result:
                status_code = status
                def json(self): return principal
            return Result()
    monkeypatch.setattr("esg_v2.api.operator_auth.requests.Session", Session)
    return calls


def test_admin_verified_server_side_without_proxy_or_redirect(monkeypatch):
    calls = response(monkeypatch, {"operator_ref": "synthetic-admin", "roles": ["admin"]})
    assert authorize_operator("Bearer synthetic-secret", "review")["operator_ref"] == "synthetic-admin"
    assert calls[0][0] is False and calls[0][2]["allow_redirects"] is False
    assert calls[0][1].endswith("/internal/v1/me")


@pytest.mark.parametrize("credential", [None, "", "Bearer ", "plain-secret"])
def test_missing_credential_rejected_without_network(monkeypatch, credential):
    calls = response(monkeypatch, {})
    with pytest.raises(HTTPException) as exc:
        authorize_operator(credential, "admin")
    assert exc.value.status_code == 401 and not calls


@pytest.mark.parametrize("principal", [{"operator_ref": "", "roles": ["admin"]},
    {"operator_ref": "synthetic", "roles": "admin"}, {"operator_ref": "synthetic", "roles": [True]}])
def test_malformed_principal_cannot_grant_permission(monkeypatch, principal):
    response(monkeypatch, principal)
    with pytest.raises(HTTPException) as exc: authorize_operator("Bearer synthetic", "admin")
    assert exc.value.status_code == 503


def test_read_only_operator_cannot_deploy(monkeypatch):
    response(monkeypatch, {"operator_ref": "synthetic-reader", "roles": ["read"]})
    with pytest.raises(HTTPException) as exc: authorize_operator("Bearer synthetic", "admin")
    assert exc.value.status_code == 403
