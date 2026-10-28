"""Compile retrieval intent from immutable standard packages for both routes."""
from __future__ import annotations

from pathlib import Path

import json

from esg_standard_packages.scope import load_registry


def clause_retrieval_terms(standard_root: Path, *, profile: str | None = None) -> dict:
    registry, _, packages = load_registry(standard_root)
    selected = None
    if profile:
        selected = set(json.loads((standard_root / "profiles/esrs/2023-set1/platform-major.json").read_text())["source_datapoint_ids"])
    result = {}
    for module in registry["modules"]:
        if not module["executable"]:
            continue
        package = packages[module["disclosure_requirement"]]
        for metric in package.metrics:
            if selected is not None and metric.source_datapoint_id not in selected:
                continue
            positive = []
            # Sorted: set iteration order differs between processes, which would
            # make the clause catalog non-reproducible.
            for concept_id in sorted({cid for group in metric.subject_concept_groups for cid in group}):
                concept = next((item for item in package.concepts if item.concept_id == concept_id), None)
                if concept:
                    positive.extend(concept.labels.values())
                    positive.extend(alias for aliases in concept.aliases.values() for alias in aliases)
            contract = metric.measurement_contract
            result[metric.source_datapoint_id] = {
                "package_id": package.manifest.package_id,
                "package_version": package.manifest.package_version,
                "package_digest": package.compilation.source_digest,
                "metric_id": metric.metric_id,
                "labels": [value for value in metric.labels.values() if value],
                "positive_terms": list(dict.fromkeys([*metric.labels.values(), *positive, metric.description])),
                "distinguish_from": list(contract.distinguish_from) if contract else [],
                "exclusions": list(contract.exclusions) if contract else [],
                "reading_dimension": reading_dimension(metric),
            }
    return result


def reading_dimension(metric) -> str | None:
    dimension = metric.measurement_contract.unit_dimension if metric.measurement_contract else None
    if not dimension:
        if metric.measurement_kind in {"ratio", "rate", "percentage"}:
            return "ratio"
        if metric.official_data_type.strip().lower() in {"currency", "monetary"} or metric.value_family in {"currency", "monetary"}:
            return "currency"
        return None
    return dimension.rsplit(".", 1)[-1].replace("-", "_")
