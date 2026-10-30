"""Workspace release contract, captured before the API starts serving."""

from esg_runtime.layout import EXTRACT_ROOT
from esg_runtime.release import RuntimeRelease, check_compatibility, check_peer

runtime_release = RuntimeRelease(EXTRACT_ROOT, "document-backend")


def compatibility_snapshot():
    payload = runtime_release.snapshot()
    payload["readiness"] = {
        "ready": not payload["restart_required"],
        "blockers": ["项目代码已更新，需协调重启服务"]
        if payload["restart_required"]
        else [],
        "scope": "document_backend",
    }
    return payload
