# 原值事实与条款个性化组织合同

日期：2026-10-31。范围为两类原值与条款声明驱动的显示组织。数据库交接另见[交接规范](delivery.md)，前端另见[Console](../../ConsoleESG/README.md)。

## 1. 权威与边界

| 所有者 | 权威内容 | 消费方式 |
|---|---|---|
| 两份值合同 | 定量原值、定性原文及可选解析资料 | 所有条款使用同一类准入要求 |
| 来源事实合同 | 文档身份、物理来源、实际期间/主体/维度及字段证据 | 独立存储事实，不携带某条款的目标分类作为实际身份 |
| 通用组织合同 | 字段角色、绑定、缺失状态、证据引用、行、分组、矩阵和扩展位置 | 骨干按声明投影，不写领域分支 |
| 单条 `clause.json` | 所需字段、中文列名、局部词典、确认要求、行轴与呈现方式 | 阅读卡按purposes.enrichment取得身份目标；组织器取得完整布局 |
| 报告资源合同 | PDF 编号、报告企业、发表年、行业、覆盖年度 | 随运行贯穿；与事实的测量身份分列 |

通用类型位于 `ExtractESG/contracts/src/esg_data/organization.py`；执行投影位于 `ExtractESG/extraction/src/esg_extraction/pipeline/organization.py`。两者没有 ESRS 编号或污染物、能源、人群词表。领域内容只出现在 `ClauseESG/clauses/<组>/<条>/clause.json`。

当前版本：条款 `clause-contract-1.3`，阅读声明 `clause-reading-1.1`，阅读卡 `clause-reading-card-1.0`，值 `extraction-data-2.0`，来源事实 `source-fact-2.0`，组织定义 `clause-organization-1.0`，组织结果 `organized-extraction-1.1`，运行事实包 `extraction-fact-bundle-2.2`。这些版本分别表示不同接口，不要求编号相同。

## 2. 两类值，共同组织主结构

定量只要求非空 `value_raw`，定性只要求非空 `statement_raw`；事实还必须有可绑定的物理证据。点值、区间、强度、比例、分项、时间序列不再各有一套生产者：它们以原值、可选解析、实际身份和独立行表达。

每个条款必须声明一个本类原值列。定量不能把解析点值冒充原值；定性不能以数值字段代替原文。原单位不强制换算，解析失败不拒收原字串。不同期间、主体、场址、方法、分母、污染物或分项各为独立事实，组织视图不合并它们。

```text
SourceFact（原值权威）
  ├─ value：原字串及可选解析
  ├─ identity：实际身份、补充维度、各自原文与证据
  └─ evidence：物理来源

ClauseMapping（该事实与此条的关系）
  └─ match / uncertain / different，以及原因和范围

OrganizedDataset（可重新投影的输出）
  └─ 每条一个 OrganizedTable
       ├─ definition：该条冻结的个性化字段及呈现要求
       ├─ rows：事实编号、各列值与字段证据引用
       ├─ groups：仅分组键与行编号
       └─ matrix：坐标与行编号，无聚合值
```

组织器先接收已经绑定的事实和映射。原值文件先落盘，组织失败不会抹除它。未能组织的事实编号和失败原因显式保留；不能只返回一张看起来完整的表。

## 3. 字段合同

每列包含 `key / label / role / binding / type_hint`；可选声明、局部词典及 `required_for_confirmation`。绑定是受控的 `source + key + attribute`，不支持 Python、脚本、任意表达式或 `eval`。

| 角色 | 含义 | 允许来源示例 |
|---|---|---|
| `raw_value` | 原值或原文 | `value.value_raw` / `value.statement_raw` |
| `parsed_value` | 可选点值、上下界、比较符 | `value.value_numeric` / `value.value_lower` |
| `unit` | 实际原单位 | `value.unit_raw` |
| `reported_identity` | 实际期间、主体、边界、方法 | `identity.reporting_period` |
| `reported_dimension` | 报告实际细分 | `dimension.pollutant` / `dimension.energy_carrier` |
| `target_definition` | 条款所求口径 | `target.fixed_axes.pollution_medium` |
| `resource_identity` | 报告身份 | `resource.company.name` |
| `source_label` | 原始行标签 | `fact.source_row_label` |
| `provenance` | 事实编号、PDF 页码等 | `fact.source_fact_id` / `evidence.pages` |
| `match_state` | 条款映射结论 | `mapping.match` |
| `annotation` | 附加资料 | `extension.<key>` |

类型校验禁止资源/目标列伪装成实际身份；条款目标不能填补实际缺失。`type_hint` 帮助排序和使用，不强制损坏原字串来满足显示类型。科学计算不消费显示用排序结果。

实际身份保留 `stated / inherited / not_stated / not_read / conflict`。单元格另有 `available / not_stated / not_read / conflict / not_applicable`，未知不等于零或否定。每个组织单元格的 `evidence` 引用原事实中 `fact / identity / dimension` 的具体证据索引，原始证据仍由事实持有。

条款没有预先列出的额外实际维度、事实扩展和原值扩展默认加入输出列。新增业务字段可以先进入扩展，再由某条款正式命名；不要求骨干增加领域代码。若主动关闭扩展显示，原始事实仍完整保存。

## 4. 条款自身决定细分与局部分类

以 `E2-4_02` 的空气污染物样本为例：

- `data_kind=quantitative` 决定使用定量值合同。
- `reading` 决定什么属于排放总量、排除什么、需要什么证据。
- `retrieval` 提供搜索词、负词、主题/角色词组及对象优先级。
- 可用 `retrieval.required_term_groups` 声明必需词组：每组内任选命中一个，组间必须同时满足；同一要求传到 PDF 选页和对象检索。仅在某条明确需要时配置，未配置的条款不受额外词组限制。另有对象级 `excluded_object_terms`，样本用来排除缩写表/释义对象；它不在选页时删除整页。ASCII 词遵守单词边界。以上都是召回约束，不证明披露符合条款。
- `organization.fields` 声明实际污染物、实际介质、场址、地区、主体、期间、边界、原单位等列。
- 实际污染物列自己持有 SO2、NOx 等局部代码、标签和别名。
- 目标 `pollution_medium=air` 单独列为目标定义；报告未说实际介质时，实际列继续未知。
- `presentation.matrix` 以污染物等为行、实际期间为列，值区保留原值及可选解析。

空气与水体样本分别持有本介质的局部词典，不把另一介质的代码列表交给模型。方法样本明确包含空气、水体及土壤/地下水监测，无需强行寻找数值或特定化学物质名。

局部分类必须同时有报告实际值和支持它的原字串。`SO₂`、`二氧化硫` 可按这条的别名识别为 SO2；NO2 不自动改成 NOx。PFAS 等未列分类以原词保留，标为 `unlisted`；代码与原文冲突时不给出已确认代码。识别码用于组织索引，原报告值、原词和证据不被改写。

能源样本可声明能源载体/业务板块；人员样本可声明人群、性别、地区；定性方法样本使用原文陈述视图。它们均使用同一组织主结构，骨干不认识这些领域类别。

## 5. 分组、矩阵和多值约束

`row_axes` 声明本条关心的行身份；`presentation.columns / group_by / sort_by` 决定使用方式。矩阵额外声明行轴、列轴、值字段，所有引用只能指向本条自己的列；矩阵必须保留本条原值列。

分组键和矩阵坐标是展示索引，带 `grouping_key_only=true`，不冒充新的测量身份或新证据。同一坐标可引用多个事实行，状态为 `multiple`，不取首值、不相加。没有事实的坐标不生成零。`aggregation_performed=false` 是合同约束。各事实完整的原词、状态、证据留在行内。

这同时避免了“污染物 × 年度”表丢失重复披露、未知场址或不同边界的问题。未来审核可以核对多值的原因，但当前组织器不能自行批准、消歧或聚合。

## 6. 阅读端口与独立性

条款以 `reading.instructions` 持有自然语言阅读指令。骨干将条款的定义、区分/排除、值目标、范围、证据要求、阅读指令及 `requested_identity_fields` 交给模型。后者只包含应从报告读取的身份字段及局部词典，排除资源列、展示排序、矩阵布局和条款关系。检索词表、程序正则和守卫细则不再占用模型阅读卡；检索器、绑定器仍分别消费冻结条款中的完整声明。模型返回可见物理对象的引用，绑定器回读原文。

`relationships` 预留父项、兄弟项、配对项等信息关系。它不是执行依赖：选择一条不会自动选择、读取或加载另一条；关联文件缺失不阻止独立运行。32 条集合仅是 32 个条款编号一起被选中。每条独立检索和调用，无跨条款理解缓存。

1113个值条款采用同一平行合同，另13个条目为非执行结构描述。priority32为组织示例；原37项已逐条处置，内容验收记录另存，不将技术可执行当科学完备。`curation=sample` 仅表示本轮配置过组织字段，不是业务验收或可发布声明。本轮不扩大为全部条款内容工程。

## 7. 接口与落盘

| 提取服务 18180 | 内容 |
|---|---|
| `GET /api/organization-contract` | 主组织定义及结果 JSON Schema |
| `GET /api/clauses/{key}/organization` | 本条个性化组织声明与非执行关系说明 |
| `GET /api/clauses/{key}/reading-card` | 实际阅读端口的数据 |
| `GET /api/runs/{id}/organized` | 本次冻结目标对应的组织输出 |
| `GET /api/runs/{id}/targets` | 本次实际冻结的完整条款快照，包含摘要校验 |
| `GET /api/runs/{id}/facts` | 原值事实、条款映射、范围、组织结果状态和链接 |
| `GET /api/runs/{id}/facts/{fact_id}` | 独立事实及实际证据 |

文档服务提供 `/api/pipeline/organization-contract`、`/api/pipeline/clauses/{key}/organization`、`/api/report-sessions/{id}/organized` 转发，不读取提取产物目录。尚未产出组织结果时显式返回待处理/不可用状态，不伪造空的已完成数据集。

保存于产物端口自身的 `results/organized.json`。消费者取得 API 标识与链接，无需了解这个内部路径。布局/词典变更也进入条款全内容摘要和冻结快照；本次已固化的结果不会被后台按新布局静默改写。代码/合同类型变更要求服务重启，条款内容修改可通过仓储接口独立生效。

本轮后续出口方向见 [审核、发布与数据库对接规划](delivery.md)。

当前组织行区分target/other/unresolved；字段缺失只保留补充状态，不使执行partial。矩阵坐标按显示语义处理，stated/inherited仍在原字段证据中，不能造成相同年份重复列。默认平表只显示有值的主要列，单位保留原文，解析数据按需查看。
