# 标准条款如何构造

适用：当前`clause-contract-1.4`。单条声明属性、词典、搜索和阅读；做完后直接跑真实报告。

## 1. 一个文件就是一条完整条款

```text
ClauseESG/clauses/<展示组>/<条款ID>/clause.json
```

条款包含“找什么、怎么找、怎样读取、如何安放与显示”。展示组只起名字；16/32等集合只是一起选择的条款ID，不是另一种提取方式。不要到提取引擎中添加这条的词表、年份或专用判断。

## 2. 先生成完整模板，再填写

在Console“条款”中新增，或从SDK生成草稿；不要凭一个简略示例手拼全部JSON：

```python
from pathlib import Path
from esg_clauses.templates import new_clause

draft = new_clause("PROJECT.energy_use", "PROJECT", "quantitative", "能源消费量")
Path("clause-draft.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
```

该代码需要已安装本仓库SDK。模板默认blocked，填写内容后保存并启用；保存自动生成摘要。完整格式见[Schema](../../ClauseESG/schemas/clause.schema.json)。

## 3. 填清这些内容

| 内容 | 填写方法 |
|---|---|
| ID与主要名字 | ID稳定且唯一；`labels.zh`写短名字，`definition`说清测量/陈述含义 |
| `data_kind` | 只有quantitative（定量）或qualitative（定性）；混合要求拆成独立值目标，关系只是说明 |
| `value` | 单位维度、可选规范单位与目标固定分类；报告原单位和真实细分仍保留 |
| `attributes` | 以属性ID为键，填写含义、读取说明、来源与值词典；每个属性只定义一次 |
| `retrieval` | 实际可能出现的中英文词/别名、表或正文优先级；必需词组慎用，避免把真实披露排除 |
| `reading` | 用简单句说明选哪些行/列/原文，排除标题、编号、脚注、化学式；不要让模型生成数值 |
| `purposes` | capture.period_policy固定all_reported，保留真实原值；enrichment列出尽量读取的属性；target_scope判断已知冲突；comparison只限制相应比较 |
| `output_scopes`与`evidence` | 声明所求口径及支持它的来源，不要求所有报告都披露同样丰富的属性 |
| `organization` | 原值列必有；显示列通过binding引用属性ID，属性词典由attributes提供 |
| `provenance` | 保存标准原文来源说明 |

`attributes.<属性>.context_terms`决定补读该字段的检索词；空列表表示本轮只从直接证据读取该字段。报告范围使用明确的报告级表述；方法词应与所求数据有关，例如“综合能源折算方法”，不要只填“统计方法”而命中其他指标的说明。字段补读缺失时原值仍接收。

局部表头、行标签、章节与脚注也属于直接证据，无须为了让它们参与补读而填写泛用词。报告企业是资源资料；子公司、业务和场址的实际主体按各行列来源读取，不自动继承报告企业或集团名称。

阅读要求示例：“保留表中所有已披露年份的能源消费原值，各期间各一行；单位、主体、场址和范围有则读取，未明确就留未知；不求和、不填零、不把报告主年份作为筛选条件。”

季度、累计、基准、目标或重述保留真实期间和角色。历史实际值仍是actual，不因年份早就改成baseline。单条的概念边界仍要清楚，例如消费量和强度不混成一个值。

## 4. 保存、试跑、使用

Console新增/编辑中的“属性与词典”和“输出组织”分别填写语义与展示。需要用API时，模板为`POST /api/clauses/template`，保存为`PUT /api/clauses/{key}`，服务端口18180。更新已有条款带`expected_digest`。

设为ready后直接用真实报告提取，看原值、年份和属性；哪里不合适就修改该单条。运行自动固定本条，后续编辑不改写已经生成的事实。选择这条不自动执行父条、兄弟条或展示组。

**条款不是直接入库的数据文件。** 条款供链路读取；PDF提取产生SourceFact，再由系统封存、交付接收。不要手写事实编号、伪造来源，或把条款JSON直接送进原值接收器。

[PDF制作说明](pdf-intake.md) · [37条未决说明](../audits/2026-10-31/unresolved-clauses.md)

纯表结构或成员组织要求使用[结构描述合同](../../ClauseESG/schemas/clause-descriptor.schema.json)，不填data_kind、不输出虚构陈述。可列相关值目标供导航；选择结构不会执行成员。含明确人数、数量或目标量的表也可以是一条返回多行的定量目标，无须按性别或年份机械拆条。[37项已处置说明](../implementation/2026-10-31/clause-dispositions.md)。
