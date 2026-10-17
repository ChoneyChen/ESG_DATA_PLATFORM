"""Workspace release contract, captured before the API starts serving."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

EXTRACT_ROOT = Path(__file__).resolve().parents[4]
_spec = spec_from_file_location("esg_workbench_release", EXTRACT_ROOT / "scripts/workbench_release.py")
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
runtime_release = _module.RuntimeRelease(EXTRACT_ROOT, "document-backend")
check_compatibility = _module.check_compatibility


def compatibility_snapshot():
    payload = runtime_release.snapshot()
    payload["readiness"] = {"ready": not payload["restart_required"],
        "blockers": ["项目代码已更新，需协调重启服务"] if payload["restart_required"] else [],
        "scope": "document_backend"}
    return payload
