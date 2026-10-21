"""Declarative concept specs for datapoints beyond the hand-written guards.

The sixteen foundation datapoints carry bespoke guards because their edge
cases were learned one by one. A standard package holds hundreds of other
datapoints; adding each one as another branch would make the extractor grow
with the clause list instead of with the problem. A spec is data: a label
must name the concept, must not name an excluded one, must carry a unit of
the right dimension, and a total concept must not be satisfied by a part.

`guards.check` consults these specs only for codes that have no hand-written
branch, so the sixteen keep their learned behaviour while a new clause can be
extracted by declaring it here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def _rx(*terms: str) -> re.Pattern:
    return re.compile("(?:" + "|".join(terms) + ")", re.I)


@dataclass(frozen=True)
class ClauseSpec:
    """One datapoint expressed as label/unit requirements."""

    code: str
    label: str
    # At least one of these must appear in the row label or its section.
    require_terms: tuple[str, ...]
    # Any of these makes the row a different concept.
    exclude_terms: tuple[str, ...] = ()
    # Expected unit dimension ("mass", "energy", "volume", "ratio", ...);
    # None accepts any dimension (narrative or unconstrained datapoints).
    dimension: str | None = None
    # A total datapoint must not be satisfied by a component row.
    total: bool = False
    # Terms that mark a component or sub-item in this family.
    component_terms: tuple[str, ...] = ()
    # If set, the label must name one of these to be the group value.
    scope_terms: tuple[str, ...] = ()
    period_sensitive: bool = True
    note: str = ""

    def evaluate(self, label: str, section: str | None, unit: str | None, dimension: str | None):
        text = f"{section or ''} › {label}".casefold()
        if self.require_terms and not any(term.casefold() in text for term in self.require_terms):
            return False, "label_not_this_concept"
        for term in self.exclude_terms:
            if term.casefold() in text:
                return False, "label_is_other_concept"
        if self.scope_terms and not any(term.casefold() in text for term in self.scope_terms):
            return False, "label_not_group_scope"
        if self.total and self.component_terms and any(term.casefold() in label.casefold()
                                                       for term in self.component_terms):
            return False, "component_row"
        if self.dimension is not None and dimension is not None and dimension != self.dimension:
            return False, f"unit_dimension_{dimension}_not_{self.dimension}"
        return True, None


# Rules learned from the foundation datapoints, written once and reused by any
# clause that names the same concept. Codes here that also have a bespoke
# guard are documentation only; `guards.check` keeps the bespoke branch.
SPECS: dict[str, ClauseSpec] = {
    "E1-5_01": ClauseSpec(
        "E1-5_01", "总能源消耗",
        require_terms=("能源消耗", "能源消费", "能耗", "energy consumption", "total energy"),
        exclude_terms=("强度", "intensity", "减排", "节约", "saved", "占比", "share", "发电量", "generation"),
        dimension="energy", total=True, component_terms=("其中", "直接", "间接", "of which"),
    ),
    "E1-6_07": ClauseSpec(
        "E1-6_07", "范围1排放",
        require_terms=("范围一", "范围1", "scope 1", "直接排放"),
        exclude_terms=("范围二", "范围2", "范围三", "范围3", "scope 2", "scope 3", "1+2", "强度", "intensity"),
        dimension="ghg-emissions",
    ),
    "E2-4_02": ClauseSpec(
        "E2-4_02", "向空气排放污染物",
        require_terms=("氮氧化", "硫氧化", "二氧化硫", "颗粒", "烟尘", "挥发性有机", "vocs", "nox", "sox", "废气"),
        exclude_terms=("浓度", "concentration", "强度", "去除率", "removal", "化学需氧量", "cod", "氨氮"),
        dimension="mass",
    ),
    "E2-4_03": ClauseSpec(
        "E2-4_03", "向水体排放污染物",
        require_terms=("化学需氧量", "cod", "氨氮", "总氮", "总磷", "废水", "wastewater"),
        exclude_terms=("浓度", "concentration", "强度", "去除率", "removal", "氮氧化", "nox", "二氧化硫", "废气"),
        dimension="mass",
    ),
    "E3-4_11": ClauseSpec(
        "E3-4_11", "取水总量",
        require_terms=("取水", "withdrawal", "water intake", "用水总量"),
        exclude_terms=("耗水", "consumption", "排水", "discharge", "循环", "recycl", "风险", "risk"),
        dimension="volume", total=True, component_terms=("地下水", "地表水", "市政", "groundwater", "surface"),
    ),
    "E3-4_12": ClauseSpec(
        "E3-4_12", "排水总量",
        require_terms=("排水", "废水排放", "discharge", "effluent"),
        exclude_terms=("耗水", "consumption", "取水", "withdrawal", "循环", "recycl", "风险", "risk"),
        dimension="volume", total=True, component_terms=("地表", "地下", "海洋", "surface", "ground", "sea"),
    ),
}


def evaluate_spec(code: str, label: str, section: str | None, unit: str | None,
                  dimension: str | None) -> tuple[bool, str | None] | None:
    """Evaluate a declared spec; None when the code declares none."""
    spec = SPECS.get(code)
    if spec is None:
        return None
    return spec.evaluate(label, section, unit, dimension)
