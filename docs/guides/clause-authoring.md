# 标准条款如何构造

适用：当前`clause-contract-1.3`。全披露期间属于capture策略；源码实现、部署和真实报告核验分别记录在[本轮执行记录](../implementation/2026-10-31/README.md)，文件符合格式不等于已经全量提取。

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

该代码需要已安装本仓库SDK。模板默认blocked，修改内容后由保存/校验接口重新生成摘要；无需手算`clause_digest`。完整格式见[Schema](../../ClauseESG/schemas/clause.schema.json)。

## 3. 填清这些内容

| 内容 | 填写方法 |
|---|---|
| ID与主要名字 | ID稳定且唯一；`labels.zh`写短名字，`definition`说清测量/陈述含义 |
| `data_kind` | 只有quantitative（定量）或qualitative（定性）；混合要求拆成独立值目标，关系只是说明 |
| `value` | 原值角色、单位维度、可选规范单位。报告原单位保留；缺规范单位本身不等于不能提取 |
| `retrieval` | 实际可能出现的中英文词/别名、表或正文优先级；必需词组慎用，避免把真实披露排除 |
| `reading` | 用简单句说明选哪些行/列/原文，排除标题、编号、脚注、化学式；不要让模型生成数值 |
| `purposes` | capture.period_policy固定all_reported，保留真实原值；enrichment列出尽量读取的属性；target_scope判断已知冲突；comparison只限制相应比较 |
| `output_scopes`与`evidence` | 声明所求口径及支持它的来源，不要求所有报告都披露同样丰富的属性 |
| `organization` | 原值列必有；单位/期间/主体/细分类按需显示，未知仍保留；局部词典附在本条 |
| `provenance`与验收 | 写可查来源说明；用真实试跑保留期望原值和反例，不把技术ready当科学验收 |

阅读要求示例：“保留表中所有已披露年份的能源消费原值，各期间各一行；单位、主体、场址和范围有则读取，未明确就留未知；不求和、不填零、不把报告主年份作为筛选条件。”

季度、累计、基准、目标或重述保留真实期间和角色。历史实际值仍是actual，不因年份早就改成baseline。单条的概念边界仍要清楚，例如消费量和强度不混成一个值。

## 4. 保存、试跑、使用

通过Console编辑/保存，系统更新索引与摘要。需要用API时，模板是`POST /api/clauses/template`，校验是`POST /api/clauses/validate`，保存是`PUT /api/clauses/{key}`；这些是提取服务18180的接口。更新已有条款需带`expected_digest`，不要手改索引或旧运行快照。

内容足以执行后才设技术ready，并用报告试跑；真实科学效果另行核验。每次运行自动冻结本条，后续编辑不会改写旧结果。选择这条不自动执行它的父条、兄弟条或展示组。

**条款不是直接入库的数据文件。** 条款供链路读取；PDF提取产生SourceFact，再由系统封存、交付接收。不要手写事实编号、伪造来源，或把条款JSON直接送进原值接收器。

[PDF制作说明](pdf-intake.md) · [37条未决说明](../audits/2026-10-31/unresolved-clauses.md)

纯表结构或成员组织要求使用[结构描述合同](../../ClauseESG/schemas/clause-descriptor.schema.json)，不填data_kind、不输出虚构陈述。可列相关值目标供导航；选择结构不会执行成员。含明确人数、数量或目标量的表也可以是一条返回多行的定量目标，无须按性别或年份机械拆条。[37项已处置说明](../implementation/2026-10-31/clause-dispositions.md)。
