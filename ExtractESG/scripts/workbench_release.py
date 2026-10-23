"""Boot-frozen release identity and read-only compatibility checks.

No Git identity, timestamp, environment secret, or mutable runtime artifact is
part of a release. A process retains its startup identity even if its files are
edited; clients can distinguish that situation from a healthy current process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

PROTOCOL_VERSION = "workbench-compatibility-v1"
SUPPORTED_PACKAGE_FORMATS = ["1.0", "1.1", "2.0", "2.1"]
SUPPORTED_CORE_VERSIONS = ["1.0.0", "1.1.0", "1.2.0"]
_CACHE: dict[tuple[str, tuple], dict] = {}


def release_files(extract_root: Path) -> dict[str, list[Path]]:
    root = extract_root.resolve()
    standards = root / "standards" if (root / "standards").is_dir() else root / "standard_packages"
    groups = {
        "document_backend": list((root / "document_ir_extract/backend/src/esg_v2/api").rglob("*.py"))
            + [root / "document_ir_extract/backend/src/esg_v2/__init__.py"]
            + list((root / "document_processing/src").rglob("*.py"))
            + list((root / "control_plane/src").rglob("*.py"))
            + list((root / "contracts/src/esg_v2").rglob("*.py")),
        "targeted_backend": list((root / "targeted_table_extract/backend/src/esg_targeted/api").rglob("*.py"))
            + [root / "targeted_table_extract/backend/src/esg_targeted/__init__.py"]
            + list((root / "extraction/src").rglob("*.py"))
            + list((root / "contracts/src/esg_targeted").rglob("*.py")),
        "standard_contracts": list((root / "standards/src").rglob("*.py"))
            + list((root / "standards/schemas").rglob("*.json"))
            + list((root / "standards/extensions/schemas").rglob("*.json"))
            + list((root / "shared_runtime/src").rglob("*.py")),
        "frontend": [p for p in (root.parent / "ConsoleESG/src").rglob("*") if p.suffix in {".tsx",".ts",".css"}]
            + [root.parent / "ConsoleESG" / f for f in ("package.json","package-lock.json","vite.config.ts","tsconfig.json","index.html")],
        "publication_backend": list((root.parent / "PlatESG/backend").glob("*.py"))
            + list((root.parent / "PlatESG/backend").glob("*.mjs"))
            + list((root.parent / "PlatESG/backend/migrations").glob("*.sql"))
            + list((root.parent / "database/scripts").glob("*.mjs"))
            + list((root.parent / "database/scripts").glob("*.json"))
            + list((root.parent / "database/migrations").glob("*.sql")),
        "product_frontend": [p for p in (root.parent / "PlatESG/frontend/src").rglob("*") if p.suffix in {".tsx",".ts",".css"}]
            + [root.parent / "PlatESG/frontend" / f for f in ("package.json","package-lock.json","vite.config.ts","index.html")],
        "catalog": list((root / "standards/releases").rglob("package.json"))
            + list((root / "standards/catalog").rglob("*.json"))
            + list((root / "standards/registry").rglob("*.json"))
            + list((root / "standards/profiles").rglob("*.json"))
            + [root / "standards/extensions/registry.json"]
            + list((root / "standards/extensions/releases").rglob("*.json"))
            + [root.parent / "PlatESG/catalog/metric-dictionary.v1.json"],
        "startup": [root / "scripts/workbench_release.py", root / "scripts/start_local_workbenches.sh",root / "scripts/bootstrap_extension_runtime.py",
            root / "scripts/serve_workbench_frontend.py", root / "document_ir_extract/scripts/run_backend.sh",
            root / "document_ir_extract/scripts/run_frontend.sh", root / "targeted_table_extract/scripts/run_backend.sh",
            root.parent / "PlatESG/scripts/run.sh"],
    }
    if not (root / "document_processing/src").is_dir():
        groups["document_backend"] = list((root / "document_ir_extract/backend/src").rglob("*.py"))
    if not (root / "extraction/src").is_dir():
        groups["targeted_backend"] = list((root / "targeted_table_extract/backend/src").rglob("*.py"))
    if not (root / "standards").is_dir():
        groups["standard_contracts"] = list((standards / "src").rglob("*.py")) + list((standards / "schemas").rglob("*.json")) + list((root / "shared_runtime/src").rglob("*.py"))
        groups["catalog"] = list((standards / "dist").rglob("package.json")) + list((standards / "catalogs").rglob("*.json")) + list((standards / "profiles").rglob("*.json")) + [root.parent / "PlatESG/catalog/metric-dictionary.v1.json"]
    # Import-only historical contracts are executable authority as well. They
    # must participate in coordinated restarts without becoming active product
    # dictionaries. Runtime DB registrations remain mutable state, not source.
    groups["catalog"].extend((root.parent / "PlatESG/catalog/import-contracts").rglob("*.json"))
    groups["catalog"].extend((root.parent / "PlatESG/catalog/history").rglob("*.json"))
    registry_path = standards / "registry/esrs/2023-set1/registry.json"
    if not registry_path.is_file():
        registry_path = standards / "catalogs/esrs/2023-set1/registry.json"
    if registry_path.is_file():
        try:
            modules = json.loads(registry_path.read_text(encoding="utf-8"))["modules"]
            for module in modules:
                if module.get("executable") and module.get("source_directory"):
                    source = (standards / module["source_directory"]).resolve()
                    if root in source.parents:
                        groups["catalog"].extend(source.rglob("*.json"))
        except (ValueError, KeyError):
            pass  # Invalid registrations are diagnosed by catalog readiness.
    # Resolve aliases before hashing: a compatibility link is never another source.
    return {name: sorted({path.resolve() for path in paths}) for name, paths in groups.items()}


def release_identity(extract_root: Path) -> dict:
    root = extract_root.resolve()
    groups = release_files(root)
    signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) if path.is_file()
                      else (str(path), None, None) for paths in groups.values() for path in paths)
    key = (str(root), signature)
    if key in _CACHE:
        return json.loads(json.dumps(_CACHE[key]))
    digests = {}
    for group, paths in groups.items():
        digest = hashlib.sha256()
        for path in paths:
            # Paths are relative to the workspace, allowing equivalent checkouts
            # to advertise identical releases without leaking local paths.
            digest.update(str(path.relative_to(root.parent)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes() if path.is_file() else b"<missing>")
            digest.update(b"\0")
        digests[group] = digest.hexdigest()
    identity = {"protocol_version": PROTOCOL_VERSION, "components": digests,
                "build_fingerprint": hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest(),
                "contracts": {"standard_package_formats": SUPPORTED_PACKAGE_FORMATS,
                              "core_schema_versions": SUPPORTED_CORE_VERSIONS,
                              "extraction_profiles": ["platform_major"],
                              "standard_validation": "structural_only"}}
    _CACHE.clear()
    _CACHE[key] = identity
    return json.loads(json.dumps(identity))


class RuntimeRelease:
    def __init__(self, extract_root: Path, service: str) -> None:
        self.root, self.service = extract_root.resolve(), service
        self.started = release_identity(self.root)

    def snapshot(self) -> dict:
        current = release_identity(self.root)
        changed = current["build_fingerprint"] != self.started["build_fingerprint"]
        return {"service": self.service, **self.started,
                "current_disk_fingerprint": current["build_fingerprint"],
                "source_changed_since_start": changed,
                "restart_required": changed}


def check_compatibility(payload: dict, expected: dict, service: str) -> list[str]:
    errors = []
    if payload.get("service") != service:
        errors.append("服务身份不匹配")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("缺少当前版本握手协议，请更新服务")
    if payload.get("build_fingerprint") != expected["build_fingerprint"]:
        errors.append("运行构建与当前项目文件不一致，需要协调重启")
    if payload.get("source_changed_since_start") or payload.get("restart_required"):
        errors.append("服务启动后项目已更新，需要协调重启")
    if payload.get("contracts") != expected["contracts"]:
        errors.append("标准包、Core 或范围合同不一致")
    if not payload.get("readiness", {}).get("ready", False):
        errors.append("业务未就绪：" + "；".join(payload.get("readiness", {}).get("blockers", ["未返回就绪诊断"])))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="ESG 工作台版本握手（只读）")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--url")
    parser.add_argument("--service", choices=["document-backend", "targeted-backend", "workbench-frontend"])
    args = parser.parse_args()
    expected = release_identity(args.root)
    if not args.url:
        print(json.dumps(expected, ensure_ascii=False))
        return 0
    try:
        with build_opener(ProxyHandler({})).open(args.url, timeout=10) as response:
            payload = json.load(response)
        errors = check_compatibility(payload, expected, args.service)
    except (HTTPError, URLError, ValueError, TimeoutError, OSError) as exc:
        errors = [f"版本握手不可用：{type(exc).__name__}"]
    if errors:
        print("；".join(errors), file=sys.stderr)
        return 1
    print(f"{args.service}：版本一致、业务就绪")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
