"""Local request identity; no credentials or dependency on the publication service."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_from_loopback: ContextVar[bool] = ContextVar(
    "esg_request_from_loopback", default=False
)


def require_local_origin(request) -> None:
    """Browser writes must originate from the local workbench; CLI has no Origin."""
    from urllib.parse import urlsplit
    from fastapi import HTTPException

    origin = request.headers.get("Origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            valid = (
                parsed.scheme in {"http", "https"} and parsed.hostname in LOOPBACK_HOSTS
            )
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(403, "只允许本机工作台提交修改")


def install_loopback_marker(app) -> None:
    """Record, per request, whether the direct client is this machine."""

    @app.middleware("http")
    async def mark_loopback(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            from fastapi import HTTPException
            from fastapi.responses import JSONResponse
            try:
                require_local_origin(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        host = (request.client.host if request.client else "") or ""
        token = _from_loopback.set(host in LOOPBACK_HOSTS)
        try:
            return await call_next(request)
        finally:
            _from_loopback.reset(token)


def operator_headers(authorization: str | None) -> dict[str, str] | None:
    """Only a direct, credential-free loopback request can act locally."""
    if authorization is None and _from_loopback.get():
        return {}
    return None


# A background task retains only this non-secret local capability marker.
LOCAL_OPERATOR = "local-operator"


def held_authorization(authorization: str | None) -> str | None:
    """No caller-supplied credential is retained by background jobs."""
    return LOCAL_OPERATOR if operator_headers(authorization) == {} else None


@contextmanager
def acting_for(held: str | None):
    """Restore a previously granted local identity for a background operation."""
    if held != LOCAL_OPERATOR:
        yield None
        return
    token = _from_loopback.set(True)
    try:
        yield None
    finally:
        _from_loopback.reset(token)
