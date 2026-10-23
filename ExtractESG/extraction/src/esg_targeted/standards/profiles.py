"""Product selection is a versioned contract, independent of package core."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from esg_standard_packages.scope import compile_profile


def dictionary_path(standard_root: Path) -> Path:
    """The platform dictionary is injected, not assumed to sit beside the repo."""
    override = os.getenv("ESG_PLATFORM_DICTIONARY")
    if override:
        return Path(override).expanduser().resolve()
    return standard_root.parents[1] / "PlatESG/catalog/metric-dictionary.v1.json"


def platform_major_profile(standard_root: Path, *, topics: list[str] | None):
    profile_path = standard_root / "profiles/esrs/2023-set1/platform-major.json"
    dictionary_path_value = dictionary_path(standard_root)
    dictionary = json.loads(dictionary_path_value.read_text(encoding="utf-8"))
    if topics is not None and not any(str(topic).strip() for topic in topics):
        raise ValueError("Topic filter must name at least one topic; an empty scope is not allowed")
    result = compile_profile(standard_root, profile_path, topics=topics)
    if topics is not None and not result["source_datapoint_ids"]:
        raise ValueError("Topic filter selected no platform-major clauses; no silent empty scope")
    ready = [m["source_datapoint_id"] for m in dictionary["metrics"] if m["readiness"] == "ready"]
    pending = [m["source_datapoint_id"] for m in dictionary["metrics"] if m["readiness"] == "planned"]
    # A compiled package is not a platform commitment. A platform-major scope
    # may only hold clauses the platform dictionary can accept; anything else
    # fails explicitly instead of producing facts the platform must reject.
    blocked = [i for i in result["source_datapoint_ids"] if i in set(pending)]
    if blocked:
        raise ValueError("Selected platform-major clauses are not ready on the platform; "
                         "no silent scope reduction: " + ", ".join(blocked))
    result.update(overall_source_datapoint_ids=[m["source_datapoint_id"] for m in dictionary["metrics"]],
                  ready_source_datapoint_ids=ready, pending_source_datapoint_ids=pending,
                  dictionary_version=dictionary["dictionary_version"])
    result["selection_snapshot"] = {
        "profile_id": result["profile_id"], "profile_version": result["profile_version"],
        "profile_digest": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        "dictionary_version": dictionary["dictionary_version"],
        "dictionary_digest": hashlib.sha256(dictionary_path_value.read_bytes()).hexdigest(),
        "source_datapoint_ids": result["source_datapoint_ids"],
        "package_digests": {r["package_id"]: r["source_digest"] for r in result["routes"]},
        "selection_kind": "platform_major",
    }
    return result


def validate_selection_snapshot(standard_root, selection, packages):
    if selection is None:
        return
    profile_path = standard_root / "profiles/esrs/2023-set1/platform-major.json"
    dictionary_file = dictionary_path(standard_root)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    dictionary = json.loads(dictionary_file.read_text(encoding="utf-8"))
    if selection.profile_id != profile["profile_id"] or selection.profile_version != profile["profile_version"]:
        raise ValueError("Unknown extraction profile version")
    if selection.profile_digest != hashlib.sha256(profile_path.read_bytes()).hexdigest():
        raise ValueError("Extraction profile content changed; preview the current profile again")
    if selection.dictionary_version != dictionary["dictionary_version"] or selection.dictionary_digest != hashlib.sha256(dictionary_file.read_bytes()).hexdigest():
        raise ValueError("Metric dictionary content changed; preview the current dictionary again")
    selected_original = []
    digests = {}
    for package, metric_ids in packages:
        by_id = {m.metric_id: m for m in package.metrics}
        selected_original.extend(by_id[mid].source_datapoint_id for mid in metric_ids)
        digests[package.manifest.package_id] = package.compilation.source_digest
    if len(set(selection.source_datapoint_ids)) != len(selection.source_datapoint_ids) or set(selected_original) != set(selection.source_datapoint_ids):
        raise ValueError("Selected output clauses differ from the frozen extraction profile")
    if not set(selected_original).issubset(profile["source_datapoint_ids"]):
        raise ValueError("A package-core clause is not automatically a platform-major clause")
    if digests != selection.package_digests:
        raise ValueError("Selected package content differs from the frozen extraction profile")
