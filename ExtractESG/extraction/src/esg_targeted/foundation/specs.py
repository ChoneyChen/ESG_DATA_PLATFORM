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
# clause that names the same concept. A code that also has a bespoke guard keeps
# that branch for applicability; its spec still supplies the declared dimension,
# and any code without a bespoke branch is judged by its spec.
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
    # Platform-major clauses outside the sixteen. These carry concept language
    # only; their labels and positive definitions live in the 2.3.0 packages.
    "E4.IRO-1_14": ClauseSpec(
        "E4.IRO-1_14", "位于或邻近生物多样性敏感区域的场址",
        require_terms=("生物多样性敏感", "biodiversity-sensitive", "biodiversity sensitive", "敏感区域"),
        exclude_terms=("数量", "number of", "面积", "area of"),
    ),
    "E4.SBM-3_04": ClauseSpec(
        "E4.SBM-3_04", "受到影响的生物多样性敏感区域",
        require_terms=("敏感区域", "sensitive area", "sensitive areas"),
        exclude_terms=("数量", "number of", "面积", "area of", "场址清单", "list of sites"),
    ),
    "E4-5_01": ClauseSpec(
        "E4-5_01", "受负面影响的保护区或重点生物多样性区域场址数量",
        require_terms=("场址", "site", "sites"),
        exclude_terms=("面积", "area"),
        dimension="count",
    ),
    "E4-5_02": ClauseSpec(
        "E4-5_02", "受负面影响的保护区或重点生物多样性区域场址面积",
        require_terms=("面积", "area"),
        dimension="area",
    ),
    "S2-1_05": ClauseSpec(
        "S2-1_05", "价值链政策对人口贩运、强迫劳动和童工的覆盖",
        require_terms=("贩运", "trafficking", "强迫劳动", "forced labour", "forced labor",
                       "童工", "child labour", "child labor"),
        exclude_terms=("申诉", "channel", "grievance"),
    ),
    "S2-3_02": ClauseSpec(
        "S2-3_02", "价值链劳动者提出关切的渠道",
        require_terms=("渠道", "channel", "申诉", "grievance", "投诉", "complaint"),
        scope_terms=("价值链", "value chain", "供应商", "supplier"),
        exclude_terms=("报复", "retaliation"),
    ),
    "S2-3_06": ClauseSpec(
        "S2-3_06", "价值链劳动者申诉的反报复政策",
        require_terms=("报复", "retaliation"),
        scope_terms=("价值链", "value chain", "供应商", "supplier"),
    ),
    "S2-4_11": ClauseSpec(
        "S2-4_11", "价值链中的严重人权问题与事件",
        require_terms=("严重人权", "severe human rights", "人权事件", "human rights incident"),
    ),
    "S3-1_01": ClauseSpec(
        "S3-1_01", "防止和应对原住民影响的政策条款",
        require_terms=("原住民", "indigenous"),
        exclude_terms=("申诉", "channel"),
    ),
    "S3-3_11": ClauseSpec(
        "S3-3_11", "受影响社区提出关切的渠道",
        require_terms=("渠道", "channel", "申诉", "grievance"),
        scope_terms=("社区", "community"),
        exclude_terms=("报复", "retaliation"),
    ),
    "S3-4_02": ClauseSpec(
        "S3-4_02", "对受影响社区实际重大影响的补救行动",
        require_terms=("补救", "remedy"),
        scope_terms=("社区", "community"),
    ),
    "S3-4_11": ClauseSpec(
        "S3-4_11", "与受影响社区相关的严重人权问题与事件",
        require_terms=("严重人权", "severe human rights"),
        scope_terms=("社区", "community"),
    ),
    "S4-1_01": ClauseSpec(
        "S4-1_01", "管理消费者和终端用户重大影响、风险和机遇的政策",
        require_terms=("政策", "polic"),
        scope_terms=("消费者", "consumer", "客户", "customer"),
    ),
    "S4-3_02": ClauseSpec(
        "S4-3_02", "消费者和终端用户提出关切的渠道",
        require_terms=("渠道", "channel", "申诉", "grievance", "投诉", "complaint"),
        scope_terms=("消费者", "consumer", "客户", "customer"),
        exclude_terms=("报复", "retaliation"),
    ),
    "S4-3_13": ClauseSpec(
        "S4-3_13", "报告期内收到的消费者投诉数量",
        require_terms=("投诉", "complaint"),
        scope_terms=("消费者", "consumer", "客户", "customer"),
        exclude_terms=("率", "%", "比例", "rate", "share"),
        dimension="count",
    ),
    # Universal clauses that many reports print; concept language only.
    "E5-4_05": ClauseSpec(
        "E5-4_05", "二次使用或再生材料占比",
        require_terms=("再生材料", "回收材料", "二次使用", "再生组分", "recycled content",
                       "secondary material", "recycled material"),
        exclude_terms=("未回收", "吨", "tonnes", "weight", "废弃物", "waste"),
        dimension="ratio",
    ),
    "E5-5_11": ClauseSpec(
        "E5-5_11", "未回收废弃物占比",
        require_terms=("未回收", "未资源化", "未循环利用", "non-recycled", "not recycled"),
        exclude_terms=("回收率", "回收利用比例", "recycling rate", "回收量"),
        dimension="ratio",
    ),
    "S1-13_03": ClauseSpec(
        "S1-13_03", "按性别划分的平均培训小时数",
        require_terms=("培训", "training"),
        scope_terms=("男性", "女性", "按性别", "male", "female", "by gender"),
        exclude_terms=("商业道德", "合规培训", "ehs", "安全培训", "leadership", "专题"),
        dimension=None,
    ),
    "S1-14_02": ClauseSpec(
        "S1-14_02", "工伤或职业病死亡人数",
        require_terms=("工亡", "死亡人数", "死亡事故", "因工伤死亡", "fatalit"),
        exclude_terms=("率", "rate", "天数", "days", "交通事故", "traffic", "承包商", "contractor"),
        dimension="count",
    ),
    "G1-4_02": ClauseSpec(
        "G1-4_02", "反腐败罚款金额",
        require_terms=("罚款", "罚金", "fine", "penalt"),
        scope_terms=("腐败", "贿赂", "corruption", "bribery"),
        exclude_terms=("数量", "number", "件数", "事件数"),
        dimension="currency",
    ),
    "G1-4_04": ClauseSpec(
        "G1-4_04", "已确认腐败或贿赂事件数量",
        require_terms=("已确认", "确认的", "confirmed"),
        scope_terms=("腐败", "贿赂", "corruption", "bribery"),
        exclude_terms=("定罪", "conviction", "罚款", "fine", "金额", "amount"),
        dimension="count",
    ),
    "S4-4_11": ClauseSpec(
        "S4-4_11", "与消费者和终端用户相关的严重人权问题与事件",
        require_terms=("严重人权", "severe human rights"),
        scope_terms=("消费者", "consumer", "客户", "customer"),
    ),
    "G1-1_03": ClauseSpec(
        "G1-1_03", "未制定符合联合国反腐败公约的反腐败或反贿赂政策",
        require_terms=("反腐败", "反贿赂", "anti-corruption", "anti-bribery", "corruption"),
        exclude_terms=("培训", "training", "定罪", "conviction", "罚款", "fine", "事件", "incident"),
    ),
}


def evaluate_spec(code: str, label: str, section: str | None, unit: str | None,
                  dimension: str | None) -> tuple[bool, str | None] | None:
    """Evaluate a declared spec; None when the code declares none."""
    spec = SPECS.get(code)
    if spec is None:
        return None
    return spec.evaluate(label, section, unit, dimension)
