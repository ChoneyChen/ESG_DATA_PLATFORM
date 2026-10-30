"""Controlled scope declarations. Unspecified axes constrain neither admission nor matching."""

from __future__ import annotations
import hashlib, json
from typing import Any
from pydantic import Field, model_validator
from .base import StrictModel


def _labels(pairs: dict[str, str]) -> dict[str, str]:
    return dict(pairs)


PERIOD_CODES = _labels(
    {
        "annual": "年度",
        "fiscal_year": "财年",
        "cumulative": "累计期",
        "quarter": "季度",
        "instant": "期末时点",
        "target_period": "目标期",
        "baseline_period": "基准期",
        "restated_period": "重述期",
        "multi_year": "多年范围",
        "unknown": "期间未识别",
    }
)
ENTITY_CODES = _labels(
    {
        "group": "集团合并",
        "consolidated": "合并口径",
        "parent": "母公司",
        "subsidiary": "子公司",
        "business_unit": "事业部/板块",
        "segment": "业务分部",
        "site": "基地/工厂/站点",
        "plant": "生产厂",
        "project": "项目公司",
        "region": "地区",
        "office": "办公场所",
        "unclassified_subset": "未分类子范围",
        "unknown": "主体范围未识别",
    }
)
VALUE_ROLE_CODES = _labels(
    {
        "actual": "实际值",
        "target": "目标值",
        "baseline": "基准值",
        "forecast": "预测值",
        "restated": "重述值",
        "estimate": "估计值",
        "context": "背景陈述",
        "structure": "结构/目录",
        "unknown": "值角色未识别",
    }
)
AGGREGATION_CODES = _labels(
    {
        "total": "总量",
        "subtotal": "小计",
        "member": "成员",
        "single_line": "单行",
        "intensity": "强度",
        "share": "占比",
        "capacity": "容量",
        "change": "变化量",
        "unknown": "聚合角色未识别",
    }
)
QUALIFIER_CODES = _labels(
    {
        "approx": "约",
        "at_least": "不低于",
        "at_most": "不高于",
        "range": "区间",
        "including": "含",
        "excluding": "不含",
        "of_which": "其中",
    }
)
METHOD_CORE = _labels(
    {
        "location_based": "位置法",
        "market_based": "市场法",
        "mixed": "混合法",
        "measured": "实测",
        "calculated": "计算",
        "estimated": "估算",
        "unknown": "方法未识别",
    }
)

TAXONOMY: dict[str, dict[str, str]] = {
    "period": PERIOD_CODES,
    "entity": ENTITY_CODES,
    "value_role": VALUE_ROLE_CODES,
    "aggregation": AGGREGATION_CODES,
    "qualifier": QUALIFIER_CODES,
}
AXIS_LABELS = {
    "period": "期间",
    "entity": "主体范围",
    "value_role": "值角色",
    "aggregation": "聚合角色",
    "method": "方法",
    "qualifier": "限定语",
}


def stable_scope_id(axes: dict[str, Any]) -> str:
    text = json.dumps(axes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "scope-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class OutputScope(StrictModel):
    """One expected output of a clause, expressed entirely in controlled codes."""

    scope_id: str = Field(min_length=3)
    label_zh: str = Field(min_length=1)
    period: str | None = None
    entity: str | None = None
    value_role: str | None = None
    aggregation: str | None = None
    method: str | None = None
    qualifier: str | None = None
    axes: dict[str, str] = Field(default_factory=dict)
    required: bool = True

    @model_validator(mode="after")
    def controlled_codes(self):
        for axis, allowed in (
            ("period", PERIOD_CODES),
            ("entity", ENTITY_CODES),
            ("value_role", VALUE_ROLE_CODES),
            ("aggregation", AGGREGATION_CODES),
            ("qualifier", QUALIFIER_CODES),
        ):
            value = getattr(self, axis)
            if value is not None and value not in allowed:
                raise ValueError(f"Unknown {axis} code: {value}")
        if self.method is not None and not self.method.strip():
            raise ValueError("A method code cannot be blank")
        for key, value in self.axes.items():
            if not key.strip() or not str(value).strip():
                raise ValueError(
                    "Clause-specific scope axes require a name and a value"
                )
        return self

    def canonical(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "period": self.period,
            "entity": self.entity,
            "value_role": self.value_role,
            "aggregation": self.aggregation,
        }
        if self.method:
            body["method"] = self.method
        if self.qualifier:
            body["qualifier"] = self.qualifier
        body.update(self.axes)
        return body


def make_scope(
    label_zh: str,
    *,
    period: str | None = None,
    entity: str | None = None,
    value_role: str | None = None,
    aggregation: str | None = None,
    method: str | None = None,
    qualifier: str | None = None,
    axes: dict[str, str] | None = None,
    required: bool = True,
) -> OutputScope:
    scope = OutputScope(
        scope_id="pending",
        label_zh=label_zh,
        period=period,
        entity=entity,
        value_role=value_role,
        aggregation=aggregation,
        method=method,
        qualifier=qualifier,
        axes=axes or {},
        required=required,
    )
    return scope.model_copy(update={"scope_id": stable_scope_id(scope.canonical())})


# --- report side normalization ---------------------------------------------
