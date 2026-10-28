"""Compile clause profiles for every executable package from immutable inputs.

One compiler serves both artifacts: the flat profile set used by the current
catalog API and the per-clause directory package introduced in phase 8.
"""
from __future__ import annotations

import json
from pathlib import Path

from .clause_profile import ClauseExtractionProfile, compile_clause_profile, profile_digest, validate_profile
from .retrieval_contract import clause_retrieval_terms
from .scientific_contract import UNIT_IDS
from .compiler import StandardPackageCompiler

MAX_TERMS = 8
COMPOSITE_UNITS = {
    "extractesg.core.unit.tco2e-per-monetary-unit": "tCO2e/currency",
    "extractesg.core.unit.currency-per-tonne-co2e": "currency/tCO2e",
    "extractesg.core.unit.cubic-metre-per-million-eur": "m3/currency",
}
IGNORED_FIXED = {"all", "unknown", "unspecified", "not_reported"}


def unit_from_data_type(official: str) -> str | None:
    """Some quantitative datapoints declare a type but no canonical unit."""
    text = (official or "").lower()
    if "percent" in text or "percentage" in text:
        return "ratio"
    if "area" in text:
        return "hectare"
    if "monetary" in text or "currency" in text:
        return "currency"
    if any(term in text for term in ("integer", "decimal", "table", "numerical", "number")):
        return "count"
    return None


def aggregation_for(metric, unit: str | None) -> str:
    text = f"{metric.labels.get('zh', '')} {metric.labels.get('en', '')} {metric.description}".lower()
    if any(term in text for term in ("占比", "比例", "share", "percentage", "ratio of")):
        return "share"
    if any(term in text for term in ("强度", "intensity", "per unit", "每单位")):
        return "intensity"
    if metric.measurement_kind in {"ratio", "rate", "percentage"} or unit == "ratio":
        return "intensity"
    return "total"


def _dictionary_metric(dictionary: dict | None, source_datapoint_id: str) -> dict:
    if not dictionary:
        return {}
    return next((item for item in dictionary.get("metrics", [])
                 if item.get("source_datapoint_id") == source_datapoint_id), {}) or {}


def fixed_axes_for(dictionary: dict | None, source_datapoint_id: str) -> tuple[dict[str, str], str | None]:
    metric = _dictionary_metric(dictionary, source_datapoint_id)
    axes = {key: value for key, value in (metric.get("fixed_dimensions") or {}).items()
            if value and value not in IGNORED_FIXED and key not in {"aggregation_role", "value_role"}}
    return axes, metric.get("value_role")


def canonical_unit_for(dictionary: dict | None, source_datapoint_id: str) -> str | None:
    return _dictionary_metric(dictionary, source_datapoint_id).get("canonical_unit")


def apply_override(profile: dict, override: dict) -> dict:
    """Apply an authored draft onto a compiled profile.

    Only declared, validated fields are copied; the digest is recomputed so the
    catalog never claims the original contract.
    """
    clause = override.get("clause") or {}
    updated = dict(profile)
    for field in ("positive_definition", "distinguish_from", "exclusions", "output_scopes", "publication"):
        if field in clause:
            updated[field] = clause[field]
    for part, keys in (("retrieval", ("positive_terms", "negative_terms", "structural_anchors")),
                       ("acceptance", ("minimum_reports", "samples"))):
        body = override.get(part) or {}
        for key in keys:
            if key in body:
                updated[part] = {**updated.get(part, {}), key: body[key]}
    requirements = (override.get("evidence") or {}).get("requirements")
    if requirements is not None:
        updated["evidence"] = requirements
    # The merged contract is a new contract: its digest must describe the edit,
    # not the package it came from.
    updated["contract_digest"] = "0" * 64
    updated["contract_digest"] = profile_digest(ClauseExtractionProfile.model_validate(updated))
    return updated


def indexed_packages(standards_root: Path):
    """Every non-retired release, so a pilot version has clause packages too."""
    root = Path(standards_root)
    index = json.loads((root / "registry/releases.json").read_text(encoding="utf-8"))
    compiler = StandardPackageCompiler()
    for row in index.get("releases", []):
        if row.get("state") == "retired":
            continue
        path = root / row["path"]
        if not path.is_file():
            continue
        yield compiler.validate_compiled(path)


def build_profiles(standards_root: Path, dictionary: dict | None,
                   overrides: dict[tuple[str, str, str], dict] | None = None) -> list[dict]:
    terms = clause_retrieval_terms(standards_root)
    outputs = []
    for package in indexed_packages(standards_root):
        profiles, skipped = [], []
        for metric in package.metrics:
            contract = metric.measurement_contract
            if contract is None:
                skipped.append({"metric_id": metric.metric_id,
                                "source_datapoint_id": metric.source_datapoint_id,
                                "reason": "metric has no scientific contract"})
                continue
            source_id = metric.source_datapoint_id
            axes, role = fixed_axes_for(dictionary, source_id)
            value_type = ("statement" if role == "statement"
                          else "numeric" if metric.data_class == "quantitative" else "statement")
            positive = (terms.get(source_id) or {}).get("positive_terms") or []
            dimension = (contract.unit_dimension or "").rsplit(".", 1)[-1]
            unit = (canonical_unit_for(dictionary, source_id) or UNIT_IDS.get(contract.canonical_unit_id)
                    or COMPOSITE_UNITS.get(contract.canonical_unit_id or "")
                    or {"currency": "currency", "count": "count", "ratio": "ratio"}.get(dimension)
                    or unit_from_data_type(metric.official_data_type))
            if value_type == "numeric" and unit is None:
                if metric.measurement_kind in {"ratio", "rate", "percentage"} or metric.official_data_type.lower() in {"percentage", "ratio"}:
                    unit = "ratio"
                else:
                    skipped.append({"metric_id": metric.metric_id, "source_datapoint_id": source_id,
                                    "reason": f"numeric clause has no canonical unit mapping ({contract.canonical_unit_id})"})
                    continue
            try:
                profile = compile_clause_profile(
                    {
                        "metric_id": metric.metric_id, "source_datapoint_id": source_id,
                        "labels": metric.labels, "description": metric.description,
                        "canonical_unit": unit, "unit_dimension": contract.unit_dimension,
                        "allowed_value_origins": metric.allowed_value_origins,
                        "data_class": "quantitative" if value_type == "numeric" else "qualitative",
                        "measurement_contract": contract.model_dump(mode="json"),
                    },
                    package_id=package.manifest.package_id, package_version=package.manifest.package_version,
                    retrieval_terms=list(dict.fromkeys(term for term in positive if term.strip()))[:MAX_TERMS],
                    fixed_axes=axes, value_type=value_type, aggregation=aggregation_for(metric, unit))
                validate_profile(profile)
            except ValueError as error:
                skipped.append({"metric_id": metric.metric_id, "source_datapoint_id": source_id,
                                "reason": str(error)})
                continue
            body = profile.model_dump(mode="json")
            override = (overrides or {}).get((package.manifest.package_id, package.manifest.package_version, source_id))
            if override is not None:
                body = apply_override(body, override)
                profile = ClauseExtractionProfile.model_validate(body)
                validate_profile(profile)
                body = profile.model_dump(mode="json")
            profiles.append({"profile": body,
                             "paragraph": metric.paragraph, "source_refs": list(metric.source_refs),
                             "disclosure_requirement": metric.disclosure_requirement,
                             "obligation_class": str(getattr(metric.obligation_class, "value", metric.obligation_class)),
                             "disclosure_role": metric.disclosure_role})
        outputs.append({"package_id": package.manifest.package_id,
                        "package_version": package.manifest.package_version,
                        "source_digest": package.compilation.source_digest,
                        "standard": package.manifest.standard.model_dump(mode="json"),
                        "profiles": profiles, "skipped": skipped})
    return outputs


def load_dictionary(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
