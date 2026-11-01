"""Authoring starting points, copied into a clause; readers consume the saved clause only."""
from .attributes import AttributeDefinition

COMMON_ATTRIBUTES = {
    "reporting_entity": ("实际统计主体", "只读取原文明确说明的统计主体。发行人/报告标题不自动等于每项数据的主体；本公司等简称需要原文释义。", ["统计范围", "报告范围", "本报告涵盖", "reporting entities", "scope of this report"]),
    "reporting_boundary": ("实际统计边界", "引用本表、章节或明确适用于这些数据的报告范围说明。保留子公司、业务及场址差异，不用条款目标补齐。", ["本报告披露信息的覆盖范围", "本报告涵盖", "报告范围", "scope of this report", "reporting boundary"]),
    "measurement_method": ("测量方法", "只读取实际适用于该数值的方法说明和脚注；不能从条款规定反推实际采用的方法。", []),
    "reporting_period": ("实际测量期间", "保留每个实际披露期间。报告年度不默认填充无日期数值；只有本地明确引用报告期时才能继承对应说明。", ["报告期", "报告时间", "reporting period", "period covered"]),
    "entity_aggregation_role": ("主体范围类型", "只按明确的集团、子公司、业务分部、场址等文字识别主体类型，未知保留为空。", ["集团", "子公司", "业务分部", "group", "subsidiary", "segment", "site"]),
    "aggregation_role": ("汇总角色", "只由明确的总量/小计/分项/占比/强度文字识别。业务、场址或地区名称本身不证明其为分项。", ["合计", "总量", "其中", "total", "subtotal", "of which"]),
    "value_role": ("数值角色", "从实际、目标、基期等明确表述识别；不得因条款希望取得实际值而填入actual。", ["实际", "目标", "基期", "actual", "target", "baseline"]),
    "denominator": ("分母", "引用原文明确的比率或强度分母，不由相邻数值计算，不推测缺省分母。", ["分母", "每单位", "denominator", "per unit"]),
}


def attribute_start(axis, label, source):
    content = COMMON_ATTRIBUTES.get(axis)
    return AttributeDefinition(label=content[0] if content else label,source=source,
        reading_instruction=content[1] if content else None,context_terms=content[2] if content else [])
