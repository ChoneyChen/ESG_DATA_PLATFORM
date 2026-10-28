"""Foundation metrics and groups compiled from the clause contract.

The extraction link contains no clause list and no clause semantics. Every
metric below is built from ``clauses/<package>/<version>/<id>/clause.json``:
identity, label, definition, data kind, unit dimension, reading rules,
relationships and exact labels all come from the clause's own contract.
"""
from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

SCOPE2_UNSPECIFIED = "SCOPE2_METHOD_UNSTATED"

# Generic measurement-method vocabulary: this is engine vocabulary, not a
# clause definition. It is shared by every pollutant-method clause.
METHOD_TERMS = (
    "监测", "检测", "在线", "测量", "测算", "估算", "计算", "核算", "排放因子", "物料衡算", "系数", "统计口径",
    "monitor", "measur", "estimat", "calculat", "emission factor", "mass balance", "methodolog",
)
METHOD_GROUP_KEYS = ("air", "water_pollutants")

# Reasons that mean "same subject, another level or category". These are
# engine reason codes, not clause content.
CHILD_REJECTIONS = frozenset({
    "label_not_renewable", "label_is_fossil_carrier", "label_not_fossil_energy",
    "label_not_top_management_gender", "label_not_training_hours", "label_not_board_gender",
    "label_not_work_related_accident", "label_not_materials_used", "label_not_waste",
    "label_is_waste_category", "label_not_turnover_rate", "label_not_corruption_conviction",
    "label_not_recycled_or_reused_water", "not_a_ratio", "unit_is_count",
    "label_not_top_management_level", "label_is_population_subset",
    "label_is_hazardous_materials_subset",
})
RELATED_MEASURE_REJECTIONS = frozenset({"label_not_top_management_level"})


@dataclass(frozen=True)
class FoundationMetric:
    code: str
    metric_id: str
    package_id: str
    group: str
    text_group: str
    kind: str  # quantity | percentage | narrative
    dimension: str | None
    label: str
    definition: str
    parent_code: str | None = None
    clause_key: str | None = None
    clause_digest: str | None = None
    data_kind: str = "quantitative"
    share_partner: str | None = None
    siblings: tuple[str, ...] = ()
    exact_labels: tuple[str, ...] = ()
    child_terms: tuple[str, ...] = ()
    child_excludes: tuple[str, ...] = ()
    retrieval_terms: tuple[str, ...] = ()
    reading: object | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class SourceGroup:
    key: str
    codes: tuple[str, ...]
    strong: tuple[str, ...]
    weak: tuple[str, ...]


@dataclass(frozen=True)
class ClauseRuntime:
    metrics: tuple[FoundationMetric, ...]
    by_code: dict[str, FoundationMetric]
    groups: tuple[SourceGroup, ...]
    group_by_key: dict[str, SourceGroup]
    profiles: dict[str, tuple[str, ...]]
    roots: Path


def standards_root() -> Path:
    from esg_runtime.layout import STANDARDS_ROOT
    return Path(STANDARDS_ROOT)


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _reading_dimension(profile) -> str | None:
    dimension = (profile.unit_dimension or "").rsplit(".", 1)[-1]
    if dimension:
        return dimension
    if profile.value_type == "statement":
        return None
    unit = (profile.canonical_unit or "").lower()
    if unit == "ratio":
        return "ratio"
    return None


def _kind(profile) -> str:
    if profile.data_kind == "qualitative":
        return "narrative"
    if (profile.canonical_unit or "") == "ratio" or (profile.unit_dimension or "").endswith("ratio"):
        return "percentage"
    return "quantity"


def _definition(profile) -> str:
    if profile.display_definition:
        return profile.display_definition
    text = profile.positive_definition or profile.description
    if profile.distinguish_from:
        text = f"{text} Not {'; '.join(profile.distinguish_from)}."
    return text


def load_clause_runtime(root: Path | None = None) -> ClauseRuntime:
    """Build the runtime catalogue from the clause packages, once per root."""
    from esg_standard_packages.clause_package import ClausePackageCatalog

    root = Path(root or standards_root())
    catalog = ClausePackageCatalog(root)
    registry = _json(root / "registry/esrs/2023-set1/registry.json", {"modules": []})
    selected = _json(root / "profiles/esrs/2023-set1/platform-major.json", {}).get("source_datapoint_ids") or []
    profile_payload = _json(root / "profiles/esrs/2023-set1/metric-profiles.json", {}).get("profiles") or {}
    quantitative = list(profile_payload.get("quantitative") or [])
    union = []
    for codes in profile_payload.values():
        for code in codes:
            if code not in union:
                union.append(code)
    # The foundation metric set is exactly the union of the authored selection
    # profiles; extra platform-major clauses stay addressable by code but are
    # not part of any foundation profile.
    metric_codes = list(dict.fromkeys([*quantitative, *union]))
    extra_codes = [code for code in selected if code not in metric_codes]
    modules = {original: module for module in registry.get("modules", []) if module.get("executable")
               for original in module.get("source_datapoint_ids", [])}
    profiles_by_code = {}

    def build(code):
        module = modules.get(code)
        if module is None:
            return None
        profile = catalog.profile(module["package_id"], module["package_version"], code)
        profiles_by_code[code] = profile
        relationships = profile.relationships
        return FoundationMetric(
            code=code,
            metric_id=profile.metric_id,
            package_id=profile.package_id,
            group=profile.group or "general",
            text_group=profile.text_group or profile.group or "general",
            kind=_kind(profile),
            dimension=_reading_dimension(profile),
            label=profile.display_label or profile.labels.get("zh") or code,
            definition=_definition(profile),
            parent_code=relationships.parent_clause,
            clause_key=profile.clause_key,
            clause_digest=profile.clause_digest,
            data_kind=profile.data_kind,
            share_partner=relationships.share_partner,
            siblings=tuple(relationships.siblings),
            exact_labels=tuple(profile.exact_labels),
            child_terms=tuple(relationships.child_terms),
            child_excludes=tuple(relationships.child_excludes),
            retrieval_terms=tuple(profile.retrieval.positive_terms),
            reading=profile.reading,
        )

    metrics = [metric for metric in (build(code) for code in metric_codes) if metric is not None]
    by_code = {item.code: item for item in metrics}
    for code in extra_codes:
        item = build(code)
        if item is not None:
            by_code[code] = item
    groups: list[SourceGroup] = []
    for key in sorted({item.group for item in metrics}):
        members = [item for item in metrics if item.group == key]
        strong: list[str] = []
        weak: list[str] = []
        for member in members:
            profile = profiles_by_code[member.code]
            strong.extend(profile.group_terms)
            weak.extend(profile.group_unit_terms)
        if not strong:
            for member in members:
                strong.extend(profiles_by_code[member.code].retrieval.positive_terms[:8])
        groups.append(SourceGroup(key, tuple(member.code for member in members),
                                  tuple(dict.fromkeys(strong)), tuple(dict.fromkeys(weak))))
    # The model may propose "Scope 2, method unstated". That pseudo-target uses
    # the Scope 2 clause's reading with the method check disabled; it is derived
    # from a clause, never authored in extraction code.
    if "E1-6_09" in by_code:
        import dataclasses
        base = by_code["E1-6_09"]
        rules = [rule.model_copy(update={"params": {**rule.params, "method": "any", "method_check": False}})
                 if rule.rule == "ghg_scope" else rule for rule in base.reading.rules]
        by_code["SCOPE2"] = dataclasses.replace(
            base, code="SCOPE2", reading=base.reading.model_copy(update={"rules": rules}))
    profiles = {}
    for name, codes in profile_payload.items():
        profiles[name] = tuple(code for code in codes if code in by_code)
    return ClauseRuntime(tuple(metrics), by_code, tuple(groups), {g.key: g for g in groups}, profiles, root)


_RUNTIME: ClauseRuntime | None = None
_RUNTIME_ROOT: Path | None = None


def runtime_catalog(root: Path | None = None, *, reload: bool = False) -> ClauseRuntime:
    global _RUNTIME, _RUNTIME_ROOT
    resolved = Path(root) if root is not None else standards_root()
    if reload or _RUNTIME is None or _RUNTIME_ROOT != resolved:
        _RUNTIME = load_clause_runtime(resolved)
        _RUNTIME_ROOT = resolved
    return _RUNTIME


class _MetricMap(Mapping[str, FoundationMetric]):
    def __getitem__(self, key: str) -> FoundationMetric:
        return runtime_catalog().by_code[key]

    def __iter__(self) -> Iterator[str]:
        return iter(runtime_catalog().by_code)

    def __len__(self) -> int:
        return len(runtime_catalog().by_code)


class _MetricSeq(Sequence[FoundationMetric]):
    def __getitem__(self, index):
        return runtime_catalog().metrics[index]

    def __len__(self) -> int:
        return len(runtime_catalog().metrics)


class _GroupSeq(Sequence[SourceGroup]):
    def __getitem__(self, index):
        return runtime_catalog().groups[index]

    def __len__(self) -> int:
        return len(runtime_catalog().groups)


class _GroupMap(Mapping[str, SourceGroup]):
    def __getitem__(self, key: str) -> SourceGroup:
        return runtime_catalog().group_by_key[key]

    def __iter__(self):
        return iter(runtime_catalog().group_by_key)

    def __len__(self) -> int:
        return len(runtime_catalog().group_by_key)


class _ProfileMap(Mapping[str, tuple[str, ...]]):
    def __getitem__(self, key: str) -> tuple[str, ...]:
        return runtime_catalog().profiles[key]

    def __iter__(self):
        return iter(runtime_catalog().profiles)

    def __len__(self) -> int:
        return len(runtime_catalog().profiles)


BY_CODE: Mapping[str, FoundationMetric] = _MetricMap()
METRICS: Sequence[FoundationMetric] = _MetricSeq()
GROUPS: Sequence[SourceGroup] = _GroupSeq()
GROUP_BY_KEY: Mapping[str, SourceGroup] = _GroupMap()
PROFILES: Mapping[str, tuple[str, ...]] = _ProfileMap()
class _GroupTerms(Mapping[str, tuple[str, ...]]):
    def __getitem__(self, key):
        for metric in runtime_catalog().metrics:
            if metric.group == key and metric.child_terms:
                return metric.child_terms
        return ()

    def __iter__(self):
        return iter(sorted({metric.group for metric in runtime_catalog().metrics}))

    def __len__(self):
        return len(list(iter(self)))


class _ChildExcludes(Mapping[str, tuple[str, ...]]):
    def __getitem__(self, key):
        metric = runtime_catalog().by_code.get(key)
        return metric.child_excludes if metric else ()

    def __iter__(self):
        return iter({metric.code for metric in runtime_catalog().metrics if metric.child_excludes})

    def __len__(self):
        return len(list(iter(self)))


GROUP_COMPONENT_TERMS = _GroupTerms()
METRIC_CHILD_EXCLUDES = _ChildExcludes()


# Compatibility constants for consumers that name a selection set directly.
BASE16 = tuple(runtime_catalog().profiles.get("base16", ()))
EXTENDED = tuple(code for code in runtime_catalog().profiles.get("priority32", ()) if code not in BASE16)
QUANTITATIVE = tuple(runtime_catalog().profiles.get("quantitative", ()))


def metrics_for(profile: str) -> tuple[FoundationMetric, ...]:
    """The metrics of one set; an unknown set is an error, never a silent 16."""
    codes = runtime_catalog().profiles.get(profile)
    if codes is None:
        raise ValueError(f"Unknown metric profile: {profile}")
    return tuple(runtime_catalog().by_code[code] for code in codes)


def validate_contract_catalog() -> None:
    """Every foundation metric must come from a clause contract with a reading."""
    for metric in runtime_catalog().metrics:
        if not metric.clause_key or not metric.clause_digest:
            raise ValueError(f"Foundation metric lacks a clause contract: {metric.code}")
        if metric.reading is None:
            raise ValueError(f"Foundation metric lacks reading rules: {metric.code}")
