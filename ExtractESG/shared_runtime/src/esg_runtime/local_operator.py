"""Let a request from this machine reach the operator check without a credential.

The workbench no longer asks the user to paste a credential in its top bar. A
request that carries none is forwarded to the publication service's identity
endpoint without one only when it came from loopback; that service alone
decides whether such a request is the local operator (PLATESG_LOCAL_OPERATOR=0
turns it off there). Any other request without a credential is refused before
anything is forwarded, and a supplied credential is checked as before.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_from_loopback: ContextVar[bool] = ContextVar("esg_request_from_loopback", default=False)


def install_loopback_marker(app) -> None:
    """Record, per request, whether the direct client is this machine."""
    @app.middleware("http")
    async def mark_loopback(request, call_next):
        host = (request.client.host if request.client else "") or ""
        token = _from_loopback.set(host in LOOPBACK_HOSTS)
        try:
            return await call_next(request)
        finally:
            _from_loopback.reset(token)


def operator_headers(authorization: str | None) -> dict[str, str] | None:
    """Headers for the identity request, or None when the caller must be refused."""
    if isinstance(authorization, str) and authorization.startswith("Bearer ") and authorization[7:].strip():
        return {"Authorization": authorization}
    if authorization is None and _from_loopback.get():
        return {}
    return None


# A background task acts later, outside any request. It keeps the caller's
# credential, or this stand-in for a credential-less loopback caller; the
# stand-in is never forwarded, and the publication service decides again.
LOCAL_OPERATOR = "local-operator"


def held_authorization(authorization: str | None) -> str | None:
    """What a background task may keep for a caller that was just authorized."""
    if authorization:
        return authorization
    return LOCAL_OPERATOR if _from_loopback.get() else None


@contextmanager
def acting_for(held: str | None):
    """Yield the credential to forward for a held authorization."""
    if held != LOCAL_OPERATOR:
        yield held
        return
    token = _from_loopback.set(True)
    try:
        yield None
    finally:
        _from_loopback.reset(token)
