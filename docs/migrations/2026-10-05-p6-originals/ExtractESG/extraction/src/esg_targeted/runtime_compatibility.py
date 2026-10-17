"""Workspace release and structural readiness; this is not business approval."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

EXTRACT_ROOT = Path(__file__).resolve().parents[4]
_spec = spec_from_file_location("esg_workbench_release", EXTRACT_ROOT / "scripts/workbench_release.py")
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
runtime_release = _module.RuntimeRelease(EXTRACT_ROOT, "targeted-backend")


class RuntimeCompatibilityError(ValueError):
    def __init__(self, blockers):
        self.detail = {"code": "runtime_incompatible", "layer": "compatibility",
            "message": "运行版本或标准合同未就绪，未创建任务", "blockers": blockers,
            "retryable": True, "artifacts_retained": True, "next_action": "inspect_compatibility"}
        super().__init__(self.detail["message"] + "：" + "；".join(blockers))


def require_runtime_ready(catalog):
    payload = compatibility_snapshot(catalog)
    if not payload["readiness"]["ready"]:
        raise RuntimeCompatibilityError(payload["readiness"]["blockers"])
    return payload


def compatibility_snapshot(catalog):
    from esg_targeted.standards.profiles import platform_major_profile
    payload = runtime_release.snapshot()
    diagnostics = catalog.diagnostics()
    blockers = []
    if payload["restart_required"]:
        blockers.append("项目代码已更新，需协调重启服务")
    if not diagnostics["valid_package_count"]:
        blockers.append("没有可读取的标准包")
    if diagnostics["invalid_package_count"]:
        blockers.append(f"{diagnostics['invalid_package_count']} 个标准包未通过结构校验")
    profile = None
    try:
        profile = platform_major_profile(catalog.dist_root.parent, topics=["E1", "E2", "E3"])
    except (ValueError, OSError, KeyError) as exc:
        blockers.append(f"平台主要条款配置不可执行：{exc}")
    payload["readiness"] = {"ready": not blockers, "blockers": blockers,
        "scope": "structural_contracts", "business_approval": "not_assessed",
        "catalog": diagnostics,
        "profile": None if profile is None else {"profile_id": profile["profile_id"],
            "profile_version": profile["profile_version"],
            "selection_snapshot": profile["selection_snapshot"],
            "overall_count": len(profile["overall_source_datapoint_ids"]),
            "ready_count": len(profile["ready_source_datapoint_ids"]),
            "pending_count": len(profile["pending_source_datapoint_ids"]),
            "selected_output_count": profile["output_count"]}}
    return payload
