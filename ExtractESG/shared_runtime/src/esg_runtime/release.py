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
_CACHE: dict[tuple, dict] = {}

SERVICE_GROUPS = {
    "document-backend": ("document_backend", "document_contracts", "startup"),
    "extraction-backend": ("extraction_backend", "standard_contracts", "startup"),
    "workbench-frontend": ("frontend", "startup"),
    "publication-backend": (
        "publication_backend",
        "product_frontend",
        "publication_catalog",
    ),
}


def release_files(extract_root: Path) -> dict[str, list[Path]]:
    root = extract_root.resolve()
    shared = list((root / "contracts/src").rglob("*.py")) + list(
        (root / "shared_runtime/src").rglob("*.py")
    )
    shared += list((root.parent / "ClauseESG/src").rglob("*.py"))
    groups = {
        "document_backend": list((root / "document/src").rglob("*.py"))
        + list((root / "document/src").rglob("*.swift"))
        + list((root / "pipeline/src").rglob("*.py"))
        + list((root.parent / "ConsoleESG/backend/src").rglob("*.py")),
        "extraction_backend": list((root / "extraction/src").rglob("*.py")),
        "standard_contracts": shared,
        "document_contracts": shared,
        "publication_catalog": [
            root.parent / "database/catalog/metric-dictionary.v1.json"
        ]
        + list((root.parent / "database/catalog/import-contracts").rglob("*.json")),
        "catalog": list((root.parent / "ClauseESG/clauses").rglob("*.json"))
        + [root.parent / "database/catalog/metric-dictionary.v1.json"],
        "frontend": [
            p
            for p in (root.parent / "ConsoleESG/frontend/src").rglob("*")
            if p.suffix in {".tsx", ".ts", ".css"} and ".test." not in p.name
        ],
        "publication_backend": list((root.parent / "PlatESG/backend").glob("*.py"))
        + list((root.parent / "PlatESG/backend").glob("*.mjs"))
        + list((root.parent / "database/scripts").glob("*.mjs"))
        + list((root.parent / "database/migrations").glob("*.sql")),
        "product_frontend": [
            p
            for p in (root.parent / "PlatESG/frontend/src").rglob("*")
            if p.suffix in {".tsx", ".ts", ".css"} and ".test." not in p.name
        ],
        "startup": [
            root / "pyproject.toml",
            root / "scripts/run_service.sh",
            root / "scripts/start.sh",
            root / "scripts/serve_workbench_frontend.py",
        ],
    }
    return {
        name: sorted({p.resolve() for p in paths if p.is_file()})
        for name, paths in groups.items()
    }


def release_identity(extract_root: Path, service: str | None = None) -> dict:
    root = extract_root.resolve()
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
        (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        if path.is_file()
        else (str(path), None, None)
        for paths in groups.values()
        for path in paths
    )
    key = (str(root), service or "*", signature)
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
    identity = {
        "protocol_version": PROTOCOL_VERSION,
        "components": digests,
        "build_fingerprint": hashlib.sha256(
            json.dumps(digests, sort_keys=True).encode()
        ).hexdigest(),
        "contracts": {
            "clause": "clause-contract-1.2",
            "data": "extraction-data-2.0",
            "facts": "extraction-fact-bundle-2.2",
            "organization": "organized-extraction-1.1",
            "report_resource": "report-resource-1.0",
            "source_validation": "digest_and_bound_evidence",
        },
    }
    _CACHE.clear()
    _CACHE[key] = identity
    return json.loads(json.dumps(identity))


class ModuleRelease:
    """A deployable module fingerprints only its owned files, without a checkout."""
    def __init__(self, root: Path, service: str, patterns, dependencies=()):
        self.root, self.service, self.patterns = root.resolve(), service, patterns
        self.dependencies = dependencies
        self.started = self.identity()

    def identity(self):
        value = hashlib.sha256()
        for path in sorted({path for pattern in self.patterns for path in self.root.glob(pattern) if path.is_file()}):
            value.update(path.relative_to(self.root).as_posix().encode())
            value.update(b"\0")
            value.update(path.read_bytes())
        for name, dependency in self.dependencies:
            value.update(name.encode())
            value.update(dependency.identity()["build_fingerprint"].encode())
        fingerprint = value.hexdigest()
        return {"service": self.service, "protocol_version": PROTOCOL_VERSION,
            "build_fingerprint": fingerprint, "components": {self.service: fingerprint}}

    def snapshot(self):
        current = self.identity()
        changed = current["build_fingerprint"] != self.started["build_fingerprint"]
        return {**self.started, "current_disk_fingerprint": current["build_fingerprint"],
            "source_changed_since_start": changed, "restart_required": changed}


class RuntimeRelease:
    def __init__(self, extract_root: Path, service: str) -> None:
        self.root, self.service = extract_root.resolve(), service
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
