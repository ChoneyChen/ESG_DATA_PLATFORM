"""Compile clause profiles for every executable package from immutable inputs.

One compiler serves both artifacts: the flat profile set used by the current
catalog API and the per-clause directory package introduced in phase 8.
"""
from __future__ import annotations

import json
from pathlib import Path

from .clause_profile import (ClauseExtractionProfile, ClauseRelationships, ReadingContract, ReadingRule,
                             compile_clause_profile, profile_digest, validate_profile)
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
AUTHORING_ROOT = Path("clause-authoring")
CLAUSE_LEXICON = Path("profiles/esrs/2023-set1/clause-lexicon.json")


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
    """Output-scope aggregation from the measurement contract, never from prose.

    A description that says "not an intensity" must not make an absolute amount
    an intensity. The contract's measurement kind, unit dimension and declared
    aggregation are the only inputs.
    """
    contract = metric.measurement_contract
    kind = (metric.measurement_kind or "").lower()
    family = (metric.value_family or "").lower()
    dimension = (contract.unit_dimension or "").rsplit(".", 1)[-1].lower() if contract else ""
    aggregation = contract.aggregation if contract else "none"
    if kind in {"intensity", "rate"} or "intensity" in dimension:
        return "intensity"
    if kind in {"percentage", "share"} or family in {"percentage", "share"} or aggregation == "ratio_recompute":
        return "share"
    if unit == "ratio" and kind not in {"count", "quantity"}:
        return "share"
    return "total"


def _reading_terms(terms: list[str], vocabulary: dict[str, list[str]]) -> list[str]:
    resolved: list[str] = []
    for term in terms or []:
        if isinstance(term, str) and term.startswith("@"):
            resolved.extend(vocabulary.get(term[1:], []))
        else:
            resolved.append(term)
    return list(dict.fromkeys(item for item in resolved if item))


def load_clause_lexicon(standards_root: Path) -> dict[str, list[str]]:
    path = Path(standards_root) / CLAUSE_LEXICON
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {key: [str(item) for item in value] for key, value in (payload.get("terms") or {}).items()}


def load_clause_authoring(standards_root: Path, source_datapoint_id: str) -> dict | None:
    """The clause's own authored reading declaration, one file per clause."""
    path = Path(standards_root) / AUTHORING_ROOT / f"{source_datapoint_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def compile_reading(authoring: dict, lexicon: dict[str, list[str]]) -> ReadingContract:
    vocabulary = {**lexicon, **(authoring.get("vocabulary") or {})}
    rules = []
    for raw in authoring.get("rules") or []:
        rule = dict(raw)
        for key in ("terms", "unless_terms", "if_terms", "allowed"):
            if key in rule:
                rule[key] = _reading_terms(rule[key], vocabulary)
        rules.append(ReadingRule(**rule))
    referenced = sorted(set(authoring.get("uses") or []) | {
        term[1:] for raw in authoring.get("rules") or []
        for key in ("terms", "unless_terms", "if_terms")
        for term in raw.get(key) or [] if isinstance(term, str) and term.startswith("@")})
    return ReadingContract(operator=authoring.get("operator") or "numeric_label",
                           rules=rules,
                           vocabulary={name: vocabulary[name] for name in referenced if name in vocabulary},
                           allow_regulatory_limit=bool(authoring.get("allow_regulatory_limit")))


def _dictionary_metric(dictionary: dict | None, source_datapoint_id: str) -> dict:
    if not dictionary:
        return {}
    return next((item for item in dictionary.get("metrics", [])
                 if item.get("source_datapoint_id") == source_datapoint_id), {}) or {}


def load_clause_fixed_axes(standards_root: Path) -> dict[str, dict]:
    """Fixed clause values declared next to the standard, not by the platform."""
    path = Path(standards_root) / "profiles/esrs/2023-set1/clause-fixed-axes.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {key: dict(value) for key, value in (payload.get("axes") or {}).items()}


def fixed_axes_for(dictionary: dict | None, source_datapoint_id: str,
                   clause_fixed_axes: dict[str, dict] | None = None) -> tuple[dict[str, str], str | None]:
    metric = _dictionary_metric(dictionary, source_datapoint_id)
    declared = (clause_fixed_axes or {}).get(source_datapoint_id)
    if declared is not None:
        axes = {key: value for key, value in declared.items()
                if value and value not in IGNORED_FIXED and key not in {"aggregation_role", "value_role"}}
        return axes, metric.get("value_role")
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
    for field in ("positive_definition", "distinguish_from", "exclusions", "output_scopes", "publication",
                  "data_kind", "group", "relationships", "exact_labels", "reading"):
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
    # The merged contract is a new contract: both digests must describe the
    # edit, not the package it came from.
    updated["clause_digest"] = "0" * 64
    updated["contract_digest"] = "0" * 64
    draft = ClauseExtractionProfile.model_validate(updated)
    from .clause_profile import clause_content_digest
    updated["clause_digest"] = clause_content_digest(draft)
    draft = ClauseExtractionProfile.model_validate(updated)
    updated["contract_digest"] = profile_digest(draft)
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
                   overrides: dict[tuple[str, str, str], dict] | None = None,
                   clause_fixed_axes: dict[str, dict] | None = None) -> list[dict]:
    terms = clause_retrieval_terms(standards_root)
    lexicon = load_clause_lexicon(standards_root)
    if clause_fixed_axes is None:
        clause_fixed_axes = load_clause_fixed_axes(standards_root)
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
            axes, role = fixed_axes_for(dictionary, source_id, clause_fixed_axes)
            authoring = load_clause_authoring(standards_root, source_id) or {}
            data_kind = authoring.get("data_kind")
            value_type = ("statement" if data_kind == "qualitative" else
                          "numeric" if data_kind == "quantitative" else
                          "statement" if role == "statement"
                          else "numeric" if metric.data_class == "quantitative" else "statement")
            reading = compile_reading(authoring, lexicon) if authoring else None
            if value_type == "statement" and reading is not None and reading.operator != "statement":
                # A qualitative package may reuse concept terms written for the
                # same family; unit and ratio rules do not apply to a statement.
                keep = {"require_any", "require_all", "forbid_any", "forbid_any_unless",
                        "forbid_if_all", "reject_component", "flag_review"}
                reading = ReadingContract(operator="statement",
                                          rules=[rule for rule in reading.rules if rule.rule in keep],
                                          vocabulary=reading.vocabulary,
                                          allow_regulatory_limit=reading.allow_regulatory_limit)
            relationships = ClauseRelationships(**(authoring.get("relationships") or {}))
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
                    fixed_axes=axes, value_type=value_type, aggregation=aggregation_for(metric, unit),
                    data_kind=data_kind, reading=reading,
                    display_label=authoring.get("display_label"),
                    display_definition=authoring.get("display_definition"),
                    group=authoring.get("group"),
                    text_group=authoring.get("text_group"),
                    group_terms=lexicon.get(f"group:{authoring.get('group')}:strong") or [],
                    group_unit_terms=lexicon.get(f"group:{authoring.get('group')}:weak") or [],
                    relationships=relationships,
                    exact_labels=authoring.get("exact_labels"))
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
