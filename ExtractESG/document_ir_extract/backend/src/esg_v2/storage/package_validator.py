from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from esg_v2.storage.package_layout import require_run_id, resolve_package_path
from esg_v2.utils.hashing import sha256_file


@dataclass(frozen=True)
class PackageValidationResult:
    valid: bool
    package_type: str | None
    package_schema_version: str | None
    checked_file_count: int
    errors: tuple[str, ...]

    def require_valid(self) -> PackageValidationResult:
        if not self.valid:
            raise PackageValidationError("; ".join(self.errors))
        return self


class PackageValidationError(ValueError):
    pass


def validate_package(
    root: Path,
    *,
    expected_type: str,
    expected_schema: str,
    required_entrypoints: set[str],
) -> PackageValidationResult:
    errors: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return PackageValidationResult(False, None, None, 0, ("manifest.json is missing",))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return PackageValidationResult(False, None, None, 0, (f"manifest.json is invalid: {exc}",))

    package_type = manifest.get("package_type")
    package_schema = manifest.get("package_schema_version")
    if package_type != expected_type:
        errors.append(f"package_type must be {expected_type!r}, got {package_type!r}")
    if package_schema != expected_schema:
        errors.append(f"package_schema_version must be {expected_schema!r}, got {package_schema!r}")
    run_id = manifest.get("run_id")
    run_kind_by_type = {
        "ocr-run": "ocr",
        "document-ir-revision": "ir",
        "evidence-inventory": "evd",
        "targeted-recall-run": "trg",
    }
    run_kind = run_kind_by_type.get(expected_type)
    if run_kind is None:
        errors.append(f"package validator does not recognize package_type {expected_type!r}")
        run_kind = "ir"
    try:
        require_run_id(str(run_id), run_kind)
    except ValueError as exc:
        errors.append(str(exc))
    if run_id != root.name:
        errors.append(f"manifest.run_id {run_id!r} must equal package directory {root.name!r}")

    entrypoints = manifest.get("entrypoints")
    if not isinstance(entrypoints, dict):
        entrypoints = {}
        errors.append("manifest.entrypoints must be an object")
    missing_entrypoints = sorted(required_entrypoints - set(entrypoints))
    if missing_entrypoints:
        errors.append(f"required entrypoints are missing: {', '.join(missing_entrypoints)}")
    for key, value in entrypoints.items():
        if not _valid_relative_path(value):
            errors.append(f"entrypoint {key!r} is not a safe package-relative path: {value!r}")
            continue
        try:
            path = resolve_package_path(root, str(value))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file():
            errors.append(f"entrypoint {key!r} does not exist: {value}")

    integrity_relative = entrypoints.get("integrity")
    integrity_path = None
    if _valid_relative_path(integrity_relative):
        integrity_path = resolve_package_path(root, str(integrity_relative))
    if not integrity_path or not integrity_path.is_file():
        errors.append("integrity file is missing")
        return PackageValidationResult(False, package_type, package_schema, 0, tuple(errors))
    try:
        integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"integrity file is invalid: {exc}")
        return PackageValidationResult(False, package_type, package_schema, 0, tuple(errors))

    rows = integrity.get("files")
    if integrity.get("algorithm") != "sha256" or not isinstance(rows, list):
        errors.append("integrity file must declare sha256 and a files array")
        rows = []
    indexed_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("integrity file contains a non-object row")
            continue
        relative = row.get("path")
        if not _valid_relative_path(relative):
            errors.append(f"integrity row has an unsafe path: {relative!r}")
            continue
        if relative in indexed_paths:
            errors.append(f"integrity path is duplicated: {relative}")
            continue
        indexed_paths.add(str(relative))
        path = resolve_package_path(root, str(relative))
        if not path.is_file():
            errors.append(f"indexed file is missing: {relative}")
            continue
        actual_size = path.stat().st_size
        if row.get("size_bytes") != actual_size:
            errors.append(f"size mismatch for {relative}: expected {row.get('size_bytes')}, got {actual_size}")
        actual_hash = sha256_file(path)
        if row.get("sha256") != actual_hash:
            errors.append(f"sha256 mismatch for {relative}")

    actual_paths = {
        item.resolve().relative_to(root.resolve()).as_posix()
        for item in root.rglob("*")
        if item.is_file() and item.resolve() != integrity_path.resolve() and item.name != ".DS_Store"
    }
    unindexed = sorted(actual_paths - indexed_paths)
    stale = sorted(indexed_paths - actual_paths)
    if unindexed:
        errors.append(f"package contains unindexed files: {', '.join(unindexed[:20])}")
    if stale:
        errors.append(f"integrity index contains stale files: {', '.join(stale[:20])}")
    declared_count = integrity.get("file_count")
    if declared_count != len(rows):
        errors.append(f"integrity file_count must be {len(rows)}, got {declared_count!r}")

    return PackageValidationResult(
        valid=not errors,
        package_type=package_type if isinstance(package_type, str) else None,
        package_schema_version=package_schema if isinstance(package_schema, str) else None,
        checked_file_count=len(rows),
        errors=tuple(errors),
    )


def _valid_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)
