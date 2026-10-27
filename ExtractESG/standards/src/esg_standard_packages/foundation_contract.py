"""Compile the foundation reading catalog from immutable standard contracts.

Display copy remains an explicit extraction adapter for the current model route.
Scientific meaning, unit and exclusions come from the same standard package as
review and publication. A frozen task can retain the older adapter text.
"""
from __future__ import annotations

import json
from pathlib import Path

from esg_standard_packages.scope import load_registry


DIMENSIONS = {
    "energy": "energy", "ghg-emissions": "ghg-emissions", "mass": "mass", "volume": "volume",
    "ratio": "ratio", "count": "count", "currency": "currency", "ghg-intensity": "ghg-intensity",
    "training-intensity": "training_intensity", "accident-rate": "ratio",
}


def reading_dimension(metric) -> str | None:
    contract = metric.measurement_contract
    value = metric.measurement_contract.unit_dimension
    if not value:
        if metric.measurement_kind in {"ratio", "rate", "percentage"}:
            return "ratio"
        if metric.official_data_type.strip().lower() in {"currency", "monetary"} or metric.value_family in {"currency", "monetary"}:
            return "currency"
        return None
    short = value.rsplit(".", 1)[-1]
    return DIMENSIONS.get(short, short)


def reading_definition(metric) -> str:
    contract = metric.measurement_contract
    distinctions = "; ".join(contract.distinguish_from)
    return (contract.positive_definition or metric.description) + (f" Not {distinctions}." if distinctions else "")


def foundation_contracts(standard_root: Path, *, profile: str | None = None) -> dict:
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
            contract = metric.measurement_contract
            if contract is None:
                continue
            result[metric.source_datapoint_id] = {
                "metric_id": metric.metric_id,
                "package_id": package.manifest.package_id,
                "package_version": package.manifest.package_version,
                "package_digest": package.compilation.source_digest,
                "description": metric.description,
                "positive_definition": contract.positive_definition,
                "distinguish_from": list(contract.distinguish_from),
                "exclusions": list(contract.exclusions),
                "unit_dimension": contract.unit_dimension,
                "canonical_unit_id": contract.canonical_unit_id,
                "reading_dimension": reading_dimension(metric),
                "reading_definition": reading_definition(metric),
                "measurement_contract": contract.model_dump(mode="json"),
            }
    return result
