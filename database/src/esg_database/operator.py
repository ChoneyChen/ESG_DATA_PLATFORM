"""Explicit local control, independent of Plat and stored credentials."""
import os
from fastapi import HTTPException
from esg_runtime.local_operator import require_local_origin,LOOPBACK_HOSTS


def require_operator(request,release):
    require_local_origin(request)
    if not request.client or request.client.host not in LOOPBACK_HOSTS or os.getenv("ESG_DATABASE_LOCAL_OPERATOR","1")!="1":
        raise HTTPException(403,"This change requires the local operator")
    if release.snapshot()["restart_required"]:
        raise HTTPException(503,"Restart the updated database service before changing state")
    return os.getenv("ESG_DATABASE_OPERATOR","local-operator")
