"""Boot-frozen release identity and read-only compatibility checks.

No Git identity, timestamp, environment secret, or mutable runtime artifact is
part of a release. A process retains its startup identity even if its files are
edited; clients can distinguish that situation from a healthy current process.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

PROTOCOL_VERSION = "workbench-compatibility-v1"
_CACHE: dict[tuple, dict] = {}

SERVICE_GROUPS = {
    "document-backend": ("document_backend", "document_contracts", "console_contracts"),
    "extraction-backend": ("extraction_backend", "standard_contracts"),
    "workbench-frontend": ("frontend", "startup"),
}


def package_files(names, suffixes=(".py",)):
    """Canonical names identify installed code independently of its directory."""
    result = {}
    for name in names:
        try:
            spec = importlib.util.find_spec(name)
        except (ModuleNotFoundError, ValueError):
            spec = None
        for location in (spec.submodule_search_locations or []) if spec else []:
            root = Path(location)
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in suffixes:
                    key = name + "/" + path.relative_to(root).as_posix()
                    result[key] = path.resolve()
    return sorted(result.items())


def package_digest(names):
    files = package_files(names)
    for name in names:
        if not any(key.startswith(name + "/") for key, _ in files):
            raise RuntimeError("Installed worker package is missing: " + name)
    value = hashlib.sha256()
    for key, path in files:
        value.update(key.encode())
        value.update(b"\0")
        value.update(path.read_bytes())
        value.update(b"\0")
    return value.hexdigest()


def release_files(extract_root=None):
    root = Path(extract_root).resolve() if extract_root else None
    shared = package_files(
        ("esg_contracts", "esg_data", "esg_reports", "esg_runtime", "esg_clauses")
    )
    client_files = {"http_client.py", "contracts.py", "products_contracts.py"}
    groups = {
        "document_backend": package_files(
            ("esg_document", "esg_pipeline", "esg_console"), (".py", ".swift")
        ),
        "extraction_backend": package_files(("esg_extraction",)),
        "standard_contracts": shared,
        "document_contracts": shared,
        "console_contracts": [
            (key, path)
            for key, path in package_files(("esg_database",))
            if path.name in client_files
        ],
        "frontend": [],
        "startup": [],
    }
    if root:
        workspace = root.parent
        frontend = [workspace / "database/contracts/index.d.ts"] + [
            p
            for p in (workspace / "ConsoleESG/frontend/src").rglob("*")
            if p.suffix in {".tsx", ".ts", ".css"} and ".test." not in p.name
        ]
        startup = [
            workspace / "ConsoleESG/scripts" / name
            for name in ("run_service.sh", "start.sh", "serve_workbench_frontend.py")
        ]
        for name, paths in (("frontend", frontend), ("startup", startup)):
            groups[name] = sorted(
                (path.relative_to(workspace).as_posix(), path.resolve())
                for path in paths
                if path.is_file()
            )
    return groups


def release_identity(extract_root: Path | None, service: str | None = None) -> dict:
    root = extract_root.resolve() if extract_root else None
    groups = release_files(root)
    # A release is scoped to what a service actually runs plus the shared
    # contracts it reads. An unrelated backend edit must not make the
    # publication database refuse writes until it is restarted too.
    if service:
        wanted = SERVICE_GROUPS.get(service)
        if not wanted:
            raise ValueError(f"未知服务身份：{service}")
        groups = {name: paths for name, paths in groups.items() if name in wanted}
    signature = tuple(
        (
            (str(path), path.stat().st_mtime_ns, path.stat().st_size)
            if path.is_file()
            else (str(path), None, None)
        )
        for paths in groups.values()
        for _, path in paths
    )
    cache_key = (str(root), service or "*", signature)
    if cache_key in _CACHE:
        return json.loads(json.dumps(_CACHE[cache_key]))
    digests = {}
    for group, paths in groups.items():
        digest = hashlib.sha256()
        for key, path in paths:
            digest.update(key.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes() if path.is_file() else b"<missing>")
            digest.update(b"\0")
        digests[group] = digest.hexdigest()
    identity = {
        "protocol_version": PROTOCOL_VERSION,
        "components": digests,
        "build_fingerprint": hashlib.sha256(
            json.dumps(digests, sort_keys=True).encode()
        ).hexdigest(),
        "contracts": {
            "clause": "clause-contract-1.3",
            "clause_navigation": "clause-catalog-4.0",
            "clause_descriptor": "clause-descriptor-1.0",
            "data": "extraction-data-2.0",
            "facts": "extraction-fact-bundle-2.2",
            "organization": "organized-extraction-1.1",
            "report_resource": "report-resource-1.0",
            "source_validation": "digest_and_bound_evidence",
            "export": "extraction-export-1.1",
        },
    }
    _CACHE.clear()
    _CACHE[cache_key] = identity
    return json.loads(json.dumps(identity))


class ModuleRelease:
    """A deployable module fingerprints only its owned files, without a checkout."""

    def __init__(self, root: Path, service: str, patterns, dependencies=()):
        self.root, self.service, self.patterns = root.resolve(), service, patterns
        self.dependencies = dependencies
        self.started = self.identity()

    def identity(self):
        value = hashlib.sha256()
        for path in sorted(
            {
                path
                for pattern in self.patterns
                for path in self.root.glob(pattern)
                if path.is_file()
            }
        ):
            value.update(path.relative_to(self.root).as_posix().encode())
            value.update(b"\0")
            value.update(path.read_bytes())
        for name, dependency in self.dependencies:
            value.update(name.encode())
            value.update(dependency.identity()["build_fingerprint"].encode())
        fingerprint = value.hexdigest()
        return {
            "service": self.service,
            "protocol_version": PROTOCOL_VERSION,
            "build_fingerprint": fingerprint,
            "components": {self.service: fingerprint},
        }

    def snapshot(self):
        current = self.identity()
        changed = current["build_fingerprint"] != self.started["build_fingerprint"]
        return {
            **self.started,
            "current_disk_fingerprint": current["build_fingerprint"],
            "source_changed_since_start": changed,
            "restart_required": changed,
        }


class RuntimeRelease:
    def __init__(self, extract_root: Path | None, service: str) -> None:
        self.root, self.service = (
            extract_root.resolve() if extract_root else None
        ), service
        self.started = release_identity(self.root, self.service)

    def snapshot(self) -> dict:
        current = release_identity(self.root, self.service)
        changed = current["build_fingerprint"] != self.started["build_fingerprint"]
        return {
            "service": self.service,
            **self.started,
            "current_disk_fingerprint": current["build_fingerprint"],
            "source_changed_since_start": changed,
            "restart_required": changed,
        }


def check_peer(
    payload: dict,
    expected: dict,
    service: str,
    groups=("standard_contracts", "catalog"),
) -> list[str]:
    """Verify another service runs the same shared contracts, not the same files.

    Each service is only restarted for its own code, so peers agree on the
    standards and catalog groups they exchange and on the protocol contract.
    """
    errors = []
    if payload.get("service") != service:
        errors.append("服务身份不匹配")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("缺少当前版本握手协议，请更新服务")
    components = (
        payload.get("components") if isinstance(payload.get("components"), dict) else {}
    )
    for group in groups:
        if components.get(group) != expected["components"].get(group):
            errors.append(f"服务共享合同（{group}）不一致，需要协调重启")
    if payload.get("source_changed_since_start") or payload.get("restart_required"):
        errors.append("服务启动后项目已更新，需要协调重启")
    if payload.get("contracts") != expected["contracts"]:
        errors.append("条款、数据或报告资源合同不一致")
    if not payload.get("readiness", {}).get("ready", False):
        errors.append(
            "业务未就绪："
            + "；".join(
                payload.get("readiness", {}).get("blockers", ["未返回就绪诊断"])
            )
        )
    return errors


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
        errors.append("条款、数据或报告资源合同不一致")
    if not payload.get("readiness", {}).get("ready", False):
        errors.append(
            "业务未就绪："
            + "；".join(
                payload.get("readiness", {}).get("blockers", ["未返回就绪诊断"])
            )
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="ESG 工作台版本握手（只读）")
    parser.add_argument(
        "--root",
        type=Path,
        default=__import__(
            "esg_runtime.layout", fromlist=["EXTRACT_ROOT"]
        ).EXTRACT_ROOT,
    )
    parser.add_argument("--url")
    parser.add_argument(
        "--service",
        choices=["document-backend", "extraction-backend", "workbench-frontend"],
    )
    args = parser.parse_args()
    expected = release_identity(args.root, args.service)
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
