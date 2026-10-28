"""Controlled output-scope vocabulary shared by clause contracts and report facts.

A clause declares which output scopes it may produce (period, entity, value role,
aggregation, method, qualifiers, plus clause-specific axes). A report fact is
normalized into the same codes. Coverage is then a comparison of two controlled
vocabularies instead of a free-text judgement.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import StrictModel


def _labels(pairs: dict[str, str]) -> dict[str, str]:
    return dict(pairs)


PERIOD_CODES = _labels({
    "annual": "年度", "fiscal_year": "财年", "cumulative": "累计期", "quarter": "季度",
    "instant": "期末时点", "target_period": "目标期", "baseline_period": "基准期",
    "restated_period": "重述期", "multi_year": "多年范围", "unknown": "期间未识别",
})
ENTITY_CODES = _labels({
    "group": "集团合并", "consolidated": "合并口径", "parent": "母公司", "subsidiary": "子公司",
    "business_unit": "事业部/板块", "segment": "业务分部", "site": "基地/工厂/站点",
    "plant": "生产厂", "project": "项目公司", "region": "地区", "office": "办公场所",
    "unclassified_subset": "未分类子范围", "unknown": "主体范围未识别",
})
VALUE_ROLE_CODES = _labels({
    "actual": "实际值", "target": "目标值", "baseline": "基准值", "forecast": "预测值",
    "restated": "重述值", "estimate": "估计值", "context": "背景陈述", "structure": "结构/目录",
    "unknown": "值角色未识别",
})
AGGREGATION_CODES = _labels({
    "total": "总量", "subtotal": "小计", "member": "成员", "single_line": "单行",
    "intensity": "强度", "share": "占比", "capacity": "容量", "change": "变化量", "unknown": "聚合角色未识别",
})
QUALIFIER_CODES = _labels({
    "approx": "约", "at_least": "不低于", "at_most": "不高于", "range": "区间",
    "including": "含", "excluding": "不含", "of_which": "其中",
})
METHOD_CORE = _labels({
    "location_based": "位置法", "market_based": "市场法", "mixed": "混合法",
    "measured": "实测", "calculated": "计算", "estimated": "估算", "unknown": "方法未识别",
})

TAXONOMY: dict[str, dict[str, str]] = {
    "period": PERIOD_CODES, "entity": ENTITY_CODES, "value_role": VALUE_ROLE_CODES,
    "aggregation": AGGREGATION_CODES, "qualifier": QUALIFIER_CODES,
}
AXIS_LABELS = {"period": "期间", "entity": "主体范围", "value_role": "值角色",
               "aggregation": "聚合角色", "method": "方法", "qualifier": "限定语"}

_PERIOD_YEAR = re.compile(r"(?:19|20)\d{2}")
_PERIOD_RANGE = re.compile(r"(?:19|20)\d{2}\s*[年\-—/]\s*\d{1,2}|至|到|~|～|–")
_CUMULATIVE = re.compile(r"累计|近\s*[一二三四五六七八九十\d]+\s*年|since|年度累计|cumulative")
_QUARTER = re.compile(r"[Qq][1-4]|第[一二三四]季度|季度")
_TARGET = re.compile(r"目标|target|承诺|planned|规划")
_BASELINE = re.compile(r"基准|baseline|base\s*year")
_RESTATED = re.compile(r"重述|restated|调整后")
_INSTANT = re.compile(r"年末|期末|截至|as\s+at|end\s+of\s+year")
_FISCAL = re.compile(r"财年|fiscal")

_REGION_TERMS = re.compile(r"境外|海外|overseas|outside\s+(?:china|hong\s+kong)|国内|mainland|中国大陆|"
                           r"香港|hong\s+kong|澳门|macau|台湾|taiwan")
_SITE_TERMS = re.compile(r"基地|工厂|厂区|园区|数据中心|门店|矿山|矿区|电站|站点|\bsites?\b|\bplants?\b|"
                         r"\bfacilit|\bbase\b|\bdata\s+cent|\bstore\b|\bmine\b")
_UNIT_TERMS = re.compile(r"事业部|板块|业务单元|业务分部|分部|segment|business\s+unit|division")
_SUBSIDIARY_TERMS = re.compile(r"子公司|下属公司|附属公司|subsidiar")
# Printed boundary codes already exist in the report side; map them explicitly
# instead of re-matching their English identifiers against Chinese terms.
BOUNDARY_ENTITY = {
    "group": "group", "subsidiary": "subsidiary", "battery_production_bases": "site",
    "refining": "business_unit", "overseas": "region", "hong_kong": "region",
    "mainland": "region", "part_of_group": "unclassified_subset",
}
_PROJECT_TERMS = re.compile(r"项目公司|项目\s|project\s+company")
_OFFICE_TERMS = re.compile(r"办公|写字楼|办公室|\boffices?\b")
_GROUP_TERMS = re.compile(r"集团|公司整体|全公司|全集团|本集团|合并口径|合并报表|全球|\bgroup\b|consolidated|company[-\s]wide")


def stable_scope_id(axes: dict[str, Any]) -> str:
    text = json.dumps(axes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "scope-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class OutputScope(StrictModel):
    """One expected output of a clause, expressed entirely in controlled codes."""
    scope_id: str = Field(min_length=3)
    label_zh: str = Field(min_length=1)
    period: str = "annual"
    entity: str = "group"
    value_role: str = "actual"
    aggregation: str = "total"
    method: str | None = None
    qualifier: str | None = None
    axes: dict[str, str] = Field(default_factory=dict)
    required: bool = True

    @model_validator(mode="after")
    def controlled_codes(self):
        for axis, allowed in (("period", PERIOD_CODES), ("entity", ENTITY_CODES),
                              ("value_role", VALUE_ROLE_CODES), ("aggregation", AGGREGATION_CODES),
                              ("qualifier", QUALIFIER_CODES)):
            value = getattr(self, axis)
            if value is not None and value not in allowed:
                raise ValueError(f"Unknown {axis} code: {value}")
        if self.method is not None and not self.method.strip():
            raise ValueError("A method code cannot be blank")
        for key, value in self.axes.items():
            if not key.strip() or not str(value).strip():
                raise ValueError("Clause-specific scope axes require a name and a value")
        return self

    def canonical(self) -> dict[str, Any]:
        body: dict[str, Any] = {"period": self.period, "entity": self.entity,
                                "value_role": self.value_role, "aggregation": self.aggregation}
        if self.method:
            body["method"] = self.method
        if self.qualifier:
            body["qualifier"] = self.qualifier
        body.update(self.axes)
        return body


def make_scope(label_zh: str, *, period: str = "annual", entity: str = "group",
               value_role: str = "actual", aggregation: str = "total", method: str | None = None,
               qualifier: str | None = None, axes: dict[str, str] | None = None,
               required: bool = True) -> OutputScope:
    scope = OutputScope(scope_id="pending", label_zh=label_zh, period=period, entity=entity,
                        value_role=value_role, aggregation=aggregation, method=method,
                        qualifier=qualifier, axes=axes or {}, required=required)
    return scope.model_copy(update={"scope_id": stable_scope_id(scope.canonical())})


# --- report side normalization ---------------------------------------------

def period_code(period_raw: str | None, *, start: str | None = None, end: str | None = None,
                value_role: str | None = None) -> str:
    text = (period_raw or "").strip()
    if value_role in {"target", "baseline"}:
        return "target_period" if value_role == "target" else "baseline_period"
    if not text and not (start and end):
        return "unknown"
    if _CUMULATIVE.search(text):
        return "cumulative"
    if _QUARTER.search(text):
        return "quarter"
    if _RESTATED.search(text):
        return "restated_period"
    if _TARGET.search(text):
        return "target_period"
    if _BASELINE.search(text):
        return "baseline_period"
    if _INSTANT.search(text):
        return "instant"
    if _FISCAL.search(text):
        return "fiscal_year"
    years = _PERIOD_YEAR.findall(text)
    if len(years) > 1:
        return "multi_year"
    if years:
        return "annual"
    if start and end:
        return "annual"
    return "unknown"


def entity_code(entity_basis: str | None, boundary: str | None, label: str | None = None) -> str:
    if boundary and boundary in BOUNDARY_ENTITY:
        return BOUNDARY_ENTITY[boundary]
    text = " ".join(item for item in (boundary, label) if item)
    if _SUBSIDIARY_TERMS.search(text):
        return "subsidiary"
    if _SITE_TERMS.search(text):
        return "site"
    if _UNIT_TERMS.search(text):
        return "business_unit"
    if _PROJECT_TERMS.search(text):
        return "project"
    if _OFFICE_TERMS.search(text):
        return "office"
    if _REGION_TERMS.search(text):
        return "region"
    if _GROUP_TERMS.search(text):
        return "group"
    if boundary == "group":
        return "group"
    if entity_basis == "subset" or boundary:
        return "unclassified_subset"
    return "unknown"


def aggregation_code(aggregation_role: str | None, *, intensity: bool = False) -> str:
    code = (aggregation_role or "").strip()
    if code in AGGREGATION_CODES:
        return code
    if intensity:
        return "intensity"
    return "unknown"


def qualifier_codes(comparator: str | None, label: str | None = None) -> list[str]:
    out: list[str] = []
    mapping = {"approx": "approx", "~": "approx", "约": "approx",
               ">=": "at_least", "≥": "at_least", "<=": "at_most", "≤": "at_most"}
    if comparator and mapping.get(comparator):
        out.append(mapping[comparator])
    text = label or ""
    if "其中" in text or "of which" in text.lower():
        out.append("of_which")
    if "不含" in text or "不包括" in text:
        out.append("excluding")
    return out


def fact_scope(identity: dict[str, Any], *, value_role: str | None = None) -> dict[str, Any]:
    """Normalize a report fact identity into the shared scope vocabulary."""
    role = value_role or ("actual" if (identity.get("value_qualifier") or "exact") in {"exact", None, ""} else "actual")
    return {
        "period": period_code(identity.get("reporting_period"), start=identity.get("period_start"),
                              end=identity.get("period_end"), value_role=role),
        "entity": entity_code(identity.get("entity_basis"), identity.get("reporting_boundary"),
                              identity.get("source_row_label")),
        "value_role": role,
        "aggregation": aggregation_code(identity.get("aggregation_role")),
        "method": identity.get("method") if isinstance(identity.get("method"), str) else None,
        "qualifier": qualifier_codes(identity.get("value_qualifier"), identity.get("source_row_label")),
        "axes": {key: value for key, value in identity.items()
                 if key in {"energy_carrier", "pollutant", "ghg_scope", "category", "gender", "region"}},
    }


def scope_match(scope: OutputScope, fact: dict[str, Any]) -> Literal["match", "conflict", "unknown"]:
    """Compare one declared scope with one normalized fact scope.

    ``unknown`` means the fact does not yet carry the evidence to decide; it is
    never treated as a match, and never as absence.
    """
    if scope.period != "unknown" and fact.get("period") != scope.period:
        return "conflict" if fact.get("period") not in (None, "unknown") else "unknown"
    if scope.value_role != "unknown" and fact.get("value_role") != scope.value_role:
        return "conflict" if fact.get("value_role") not in (None, "unknown") else "unknown"
    if scope.aggregation != "unknown" and fact.get("aggregation") not in (None, "unknown"):
        if fact.get("aggregation") != scope.aggregation:
            return "conflict"
    if scope.entity != "unknown":
        entity = fact.get("entity")
        if entity in (None, "unknown"):
            return "unknown"
        if entity != scope.entity and not (scope.entity == "consolidated" and entity == "group"):
            return "conflict"
    if scope.method and fact.get("method"):
        if fact["method"] != scope.method:
            return "conflict"
    elif scope.method and not fact.get("method"):
        return "unknown"
    for key, value in scope.axes.items():
        observed = (fact.get("axes") or {}).get(key)
        # A clause-fixed axis describes the clause, not a field the report must
        # print. Silence is not a contradiction; a stated different value is.
        if observed in (None, ""):
            continue
        if observed != value:
            return "conflict"
    if scope.qualifier and scope.qualifier not in (fact.get("qualifier") or []):
        return "unknown"
    return "match"


def scope_coverage(scopes: list[OutputScope], facts: list[dict[str, Any]]) -> list[dict]:
    """Per-scope coverage over the facts bound for one clause in one report."""
    rows = []
    for scope in scopes:
        matches, conflicts, unknowns = [], [], []
        for fact in facts:
            verdict = scope_match(scope, fact_scope(fact.get("identity_raw") or {}, value_role=fact.get("value_role")))
            (matches if verdict == "match" else conflicts if verdict == "conflict" else unknowns).append(fact)
        # ``out_of_scope`` means the report disclosed this clause under another
        # scope (e.g. a subsidiary only); that is not a contradiction of the
        # group claim and must not be reported as a conflict.
        state = ("covered" if matches else "unconfirmed" if unknowns else
                 "out_of_scope" if conflicts else "missing")
        rows.append({"scope_id": scope.scope_id, "label_zh": scope.label_zh, "required": scope.required,
                     "state": state,
                     "fact_refs": [fact.get("source_fact_id") or fact.get("observation_id") for fact in matches][:20],
                     "reason": None if state == "covered" else {
                         "out_of_scope": "报告只在其他口径下披露，本口径未出现",
                         "unconfirmed": "有事实但期间/主体/方法仍不足以确认该口径",
                         "missing": "本次运行没有覆盖该口径",
                     }[state]})
    return rows
