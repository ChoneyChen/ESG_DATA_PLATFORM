# 标准条款如何构造

先读[需求](../01-requirements.md)和[单条合同](../04-contracts.md)。条款是目标声明，不是直接入库的事实文件；从PDF产生原值后，系统自行封存交接。

## 一条一个完整文件

`ClauseESG/clauses/<展示组>/<条款ID>/clause.json`。

展示组只有简名和成员；16/32集合只是一起选择的ID。需要定义、词典、范围或新属性时修改这条合同，不到搜索器/引擎再写一份指标内容。纯表结构用ClauseDescriptor，不假造定性陈述。

## 生成草稿

优先在Console“条款”中新增。SDK方式在已安装项目包的Python中运行：

```python
from pathlib import Path
from esg_clauses.templates import new_clause

draft = new_clause("PROJECT.energy_use", "PROJECT", "quantitative", "能源消费量")
Path("clause-draft.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
```

模板默认blocked，填完内容再设ready。不要把示例草稿直接作为可用条款。完整字段形状由[Clause Schema](../../ClauseESG/schemas/clause.schema.json)定义，不手拼一份简化格式。

## 填写顺序

| 内容 | 写法 |
|---|---|
| ID/名字/定义 | ID稳定唯一，labels短而清楚；definition写核心测量或陈述 |
| 类型和值 | 只有quantitative/qualitative；量/比例/强度/区间均是定量，定性保留原文 |
| 概念边界 | distinguish_from/exclusions明确总量/分项、实际/目标、消费/强度等区别 |
| attributes | 每属性定义一次，含读取说明、分类词典、context_terms及local/report层级 |
| retrieval | 报告实际可能出现的中英文别名/词组、表/文优先级；vocabulary_refs引用本条已有词表 |
| reading | 说明选哪些实际行列/句子；规则和词典跟随单条，不要求模型生成量值 |
| purposes | capture.period_policy为all_reported；enrichment尽量取属性；target_scope判断目标冲突；comparison表达后续可比性 |
| scopes/evidence | 目标口径与支持它的证据，不要求所有报告提供同样丰富的维度 |
| organization | 原值列及实际身份展示；binding引用attributes，不重复写属性语义 |
| provenance | 可查的标准名称、版本、条文来源及必要说明，未知不伪造 |

阅读指令示例：“读取所有已披露期间的能源消费原值，分主体、场址和方法分别保留；没有单位或主体时留未知；不求和、不填零、不按报告主年份过滤。”

补读词要指向该字段真正相关的说明。统计主体/业务/场址通常使用local；明确的报告统计边界可以允许report。空context_terms只使用直接证据；局部行列标题和脚注本来就是证据，不必补一组泛用词。缺属性不阻止原值保存。

现行声明不使用reading.options驱动执行。新行为必须有明确消费者：条款语义在单条声明，通用执行能力在所属模块实现；不能添加一个没人读的JSON开关就算完成。

## 保存并用真实报告试跑

Console编辑保存；API模板是POST /api/clauses/template，保存是PUT /api/clauses/{key}，端口18180，更新已有条款带expected_digest。保存生成内容摘要和导航索引。

选择本条执行真实PDF，查看值、全部期间、单位、关系和来源；错误含义改本条，物理错误改文档，通用阅读错误改提取。ready只表示可执行，不设科学成熟度或样本配额。新编辑不改旧任务冻结合同；成员关系不自动触发兄弟/父条。

新增模型/字段并非每次条款工作必需；先用现有合同表达。输入PDF整理见[PDF指南](pdf-intake.md)。
