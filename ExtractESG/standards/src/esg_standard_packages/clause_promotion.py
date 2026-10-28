"""Promote a validated clause draft into a new immutable package version.

The draft only carries concept-level edits (positive definition, confusable
concepts, exclusions). Those are exactly the fields the authoring source owns;
retrieval terms and scopes are compiled from them, so they are not copied.

Promotion never switches the active package by itself: it compiles a new version,
registers it in the release index so it can be selected for a pilot run, and
leaves the active module pin untouched unless ``activate`` is explicit.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .clause_drafts import ClauseDraftStore
from .compiler import StandardPackageCompiler
from .errors import PackageIntegrityError
from .io import load_json, pretty_json_bytes, sha256_bytes, canonical_json_bytes

CONCEPT_FIELDS = ("positive_definition", "distinguish_from", "exclusions")


class PromotionError(ValueError):
    """A draft cannot become a package version without a human decision."""


def _bump(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _module_entry(registry: dict, package_id: str) -> dict:
    entry = next((row for row in registry.get("modules", []) if row.get("package_id") == package_id), None)
    if entry is None:
        raise PromotionError(f"Standard registry has no module for {package_id}")
    return entry


def plan(store: ClauseDraftStore, draft_id: str, *, version: str | None = None) -> dict:
    draft = store.load(draft_id)
    manifest = draft["manifest"]
    if manifest.get("status") != "validated":
        raise PromotionError("Only a validated draft can be promoted")
    validation = store.validate_parts({name: draft[name] for name in
                                       ("clause", "retrieval", "evidence", "acceptance", "provenance")})
    if not validation["ok"]:
        raise PromotionError("Draft validation failed: " + "；".join(
            error["message"] for error in validation["errors"]))
    root = store.packages.root.parent
    registry = load_json(root / "registry/esrs/2023-set1/registry.json")
    base = manifest["base"]
    entry = _module_entry(registry, base["package_id"])
    current = entry["package_version"]
    target = version or _bump(current)
    release_dir = root / "releases" / base["package_id"] / target
    if (release_dir / "package.json").exists():
        raise PromotionError(f"Package version already exists: {base['package_id']}@{target}")
    clause = draft["clause"]
    base_clause = store.packages.clause_parts(base["package_id"], base["package_version"],
                                              base["source_datapoint_id"])["clause"]
    changes = {field: {"before": base_clause.get(field), "after": clause.get(field)}
               for field in CONCEPT_FIELDS if clause.get(field) != base_clause.get(field)}
    if not changes:
        raise PromotionError("Draft makes no change the package can carry")
    return {"draft_id": draft_id, "package_id": base["package_id"], "current_version": current,
            "new_version": target, "source_datapoint_id": base["source_datapoint_id"],
            "source_directory": entry.get("source_directory"), "changes": changes,
            "draft_digest": clause.get("contract_digest"),
            "release_path": f"releases/{base['package_id']}/{target}/package.json"}


def _patch_metrics(metrics: list[dict], source_datapoint_id: str, clause: dict) -> list[dict]:
    found = False
    for metric in metrics:
        if metric.get("source_datapoint_id") != source_datapoint_id:
            continue
        contract = metric.get("measurement_contract")
        if not isinstance(contract, dict):
            raise PromotionError("Target metric has no measurement contract to patch")
        for field in CONCEPT_FIELDS:
            if field in clause:
                contract[field] = clause[field]
        found = True
        break
    if not found:
        raise PromotionError(f"Authoring source has no metric {source_datapoint_id}")
    return metrics


def promote(store: ClauseDraftStore, draft_id: str, *, version: str | None = None,
            register: bool = True, activate: bool = False) -> dict:
    promotion = plan(store, draft_id, version=version)
    root = store.packages.root.parent
    source_dir = root / promotion["source_directory"]
    if not source_dir.is_dir():
        raise PromotionError(f"Authoring source is missing: {source_dir}")
    target_version = promotion["new_version"]
    target_dir = source_dir.parent / target_version
    if target_dir.exists():
        raise PromotionError(f"Authoring directory already exists: {target_dir}")

    shutil.copytree(source_dir, target_dir)
    manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["package_version"] = target_version
    (target_dir / "manifest.json").write_bytes(pretty_json_bytes(manifest))
    metrics = json.loads((target_dir / "metrics.json").read_text(encoding="utf-8"))
    draft = store.load(draft_id)
    metrics = _patch_metrics(metrics, promotion["source_datapoint_id"], draft["clause"])
    (target_dir / "metrics.json").write_bytes(pretty_json_bytes(metrics))

    core_version = manifest.get("core_schema_version", "1.2.0")
    core_path = root / "schemas" / "core" / core_version / "core.json"
    if not core_path.is_file():
        raise PromotionError(f"Core schema is missing: {core_path}")
    compiler = StandardPackageCompiler()
    compiled = compiler.compile(core_path=core_path, module_dir=target_dir)
    metric = next(item for item in compiled.metrics
                  if item.source_datapoint_id == promotion["source_datapoint_id"])
    for field in CONCEPT_FIELDS:
        expected = draft["clause"].get(field)
        actual = getattr(metric.measurement_contract, field, None)
        if expected is not None and actual != expected:
            raise PackageIntegrityError(f"Compiled metric does not carry the promoted {field}")
    if compiled.manifest.package_version != target_version:
        raise PackageIntegrityError("Compiled package version differs from the plan")

    release_path = root / promotion["release_path"]
    release_path.parent.mkdir(parents=True, exist_ok=True)
    body = pretty_json_bytes(compiled.model_dump(mode="json"))
    release_path.write_bytes(body)

    result = {**promotion, "source_digest": compiled.compilation.source_digest,
              "release_sha256": sha256_bytes(body),
              "metric_count": len(compiled.metrics), "registered": False, "activated": False,
              "authoring_directory": str(target_dir.relative_to(root))}
    if register:
        index_path = root / "registry/releases.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if any(row["package_id"] == promotion["package_id"] and row["package_version"] == target_version
               for row in index["releases"]):
            raise PackageIntegrityError("Release index already lists this version")
        index["releases"].append({
            "business_approval": "not_assessed",
            "core_schema_version": compiled.manifest.core_schema_version,
            "format_version": compiled.format_version,
            "package_id": promotion["package_id"],
            "package_version": target_version,
            "path": promotion["release_path"],
            "real_extraction_validation": "not_assessed",
            "sha256": result["release_sha256"],
            "source_digest": compiled.compilation.source_digest,
            "state": "compiled_draft",
        })
        index["releases"].sort(key=lambda row: (row["package_id"], tuple(
            int(part) for part in row["package_version"].split("."))))
        index_path.write_bytes(pretty_json_bytes(index))
        result["registered"] = True
    if activate:
        # Switching the active pin changes what every new task reads from now on.
        registry_path = root / "registry/esrs/2023-set1/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = _module_entry(registry, promotion["package_id"])
        entry.update({"package_version": target_version,
                      "runtime_path": promotion["release_path"],
                      "source_digest": compiled.compilation.source_digest,
                      "source_directory": result["authoring_directory"],
                      "state": "compiled_draft"})
        registry_path.write_bytes(pretty_json_bytes(registry))
        result["activated"] = True
    return result


def activate_release(store: ClauseDraftStore, package_id: str, version: str) -> dict:
    """Point the active module pin at an already registered release.

    This changes what every new task reads. It refuses a version that is not
    registered, retired, or whose release digest disagrees with the index.
    """
    root = store.packages.root.parent
    index = json.loads((root / "registry/releases.json").read_text(encoding="utf-8"))
    entry = next((row for row in index.get("releases", [])
                  if row["package_id"] == package_id and row["package_version"] == version), None)
    if entry is None:
        raise PromotionError(f"Release is not registered: {package_id}@{version}")
    if entry.get("state") == "retired":
        raise PromotionError(f"A retired release cannot become the active standard: {package_id}@{version}")
    release_path = root / entry["path"]
    if not release_path.is_file():
        raise PromotionError(f"Release file is missing: {entry['path']}")
    compiled = StandardPackageCompiler().validate_compiled(release_path)
    if compiled.compilation.source_digest != entry["source_digest"]:
        raise PromotionError("Release digest differs from the index")
    registry_path = root / "registry/esrs/2023-set1/registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    module = _module_entry(registry, package_id)
    previous = module["package_version"]
    module.update({"package_version": version, "runtime_path": entry["path"],
                   "source_digest": entry["source_digest"], "state": entry.get("state", "compiled_draft"),
                   "source_directory": (f"authoring/esrs/2023-set1/"
                                        f"{package_id.rsplit('.', 1)[-1]}/{version}")})
    registry_path.write_bytes(pretty_json_bytes(registry))
    return {"package_id": package_id, "from_version": previous, "to_version": version,
            "source_digest": entry["source_digest"], "changed": previous != version}
