# 阶段 18：条款即单位，链路只读条款合同

日期：2026-10-29
范围：标准条款合同、两条提取路线、选页、定量/定性数据合同、中立理解缓存
方式：代码重构 + 构建验证 + 全量测试；未改数据库 schema、未迁移正式库、未重启服务、未调用模型。

本轮按用户要求做了一次结构性大修，而不是继续在提取链路里补条款分支。核心原则：

> 条款的一切——种类、单位、阅读规则、词表、分组、关系、精确行名、输出口径——都属于条款合同；
> 提取链路只实现通用算子并读取合同。包只是条款的来源与版本出处，选择、提取、验收以单条条款为单位。

## 1. 条款合同 v2

`ClauseExtractionProfile` 升级为 `clause-extraction-profile-2.0`，新增：

| 字段 | 含义 |
|---|---|
| `clause_key` | 稳定原编号，链路的唯一选择键 |
| `clause_digest` | 只描述该条条款自身；包版本变化不改变未改动条款的摘要 |
| `data_kind` | `quantitative` / `qualitative`，由条款声明，链路不再按描述文本猜 |
| `reading` | 算子 + 规则列表 + 解析后的词表；规则见规范 |
| `group` / `text_group` / `group_terms` / `group_unit_terms` | 条款所属族与表/文本检索词 |
| `relationships` | 父条款、占比伙伴、同族条款、子项词、子项排除 |
| `exact_labels` | 该条款的精确行名 |
| `display_label` / `display_definition` | 条款自己的展示文案（提示词使用），缺失时回退包定义 |

作者源是一条条款一个文件：`ExtractESG/standards/clause-authoring/<clause_key>.json`；
共享词表在 `profiles/esrs/2023-set1/clause-lexicon.json`；选择清单在
`profiles/esrs/2023-set1/metric-profiles.json`。编译后写入条款包的 `clause.json`。

`clauses/catalog.json` 现在是双视图：`packages` 保留来源与版本，新增扁平的 `clauses`
（`clause_key`、`clause_digest`、`data_kind`、路径），前端、搜索与选择器读扁平视图。

## 2. 逐条核对用户的四项要求

### 2.1 条款真正内聚：链路需要什么，就加到条款合同上

| 原来在链路里的东西 | 现在在哪 | 验证 |
|---|---|---|
| 39 条 `FoundationMetric` 字面量 | 条款包（`clause.json`） | `catalog.py` 无 `FoundationMetric("…")` 字面量；运行时 39 条指标全部由条款包构建 |
| 29 条 `ClauseSpec` | `clause-authoring` 的 `reading.rules` | `foundation/specs.py` 已从运行时删除，仅保留标准侧迁移数据 |
| 31 个 `code == …` guard 分支 | 条款 `reading.rules` + 通用求值器 | `guards.py` 无 `code == "…"` 分支；785→787 个提取测试通过 |
| 17 组固定选页关键词 | 条款 `retrieval.json` | `page_selection.py` 无 `GROUPS` 表；选页按 `metric-profiles` 所选条款的词表生成 |
| 占比伙伴、同族行、精确行名 | 条款 `relationships` / `exact_labels` | kernel 不再有 `SHARE_PARTNERS`、`GROUP_SIBLINGS`、`EXACT_LABELS` 字面量 |
| 文本补读的 10 组代码分组 | 条款 `text_group` | `TEXT_GROUPS` 由条款包生成 |

提取链路里已经不存在任何条款清单、条款分支或条款专用词表。条款作者源属于标准树，
不属于 `esg_targeted`。

### 2.2 定量/定性两份合同

新增 `ExtractESG/contracts/src/esg_targeted/data_contracts.py`：

- `QuantitativeMeasurement`：`value_raw` 必填；比较符（exact/approx/range/at_least/at_most）
  与实际携带的值/上下界必须一致；上下界有序，数值不得越界。
- `QualitativeAssertion`：`statement_raw` 必填，不携带数值或单位。

`ResultContractValidator` 对每一份成果包执行两份合同；违反合同的记录使导出被拒绝，两条路线共用。
条款的 `data_kind` 决定它应产出哪一类记录；`value_type` 与 `data_kind` 在编译时强制一致。
新增 `test_data_kind_contracts.py` 覆盖四种形状；全量提取测试验证两条路线的真实记录都通过。

### 2.3 阅读算子与词表内聚于条款

- `reading.operator`：`numeric_label`、`ghg_scope`、`pollutant`、`water`、`waste`、`statement`。
- 规则种类：require/forbid/component/flag/unit/ratio/ghg_scope/pollutant/water/waste 等 15 种；
  条款 JSON 携带实际词表，运行时不按代码分支判断。
- standard 路线：`MetricQueryCompiler` 接收条款合同，把条款标签、正向定义、易混概念、排除、
  检索正/负词加入查询；`MetricQuery` 记录 `clause_key` / `clause_digest`。
- 选页：Document 服务新增 `page_selection=clauses`（`foundation16` 保留为兼容别名），
  `clause_profile` 指定选择清单；`clause_groups()` 读取所选条款的 `retrieval.json`，
  不再有固定关键词组。人工与批量准备都传入实际 `metric_profile`。
- 实测：`E2-4_02` 的 standard 查询包含条款检索词，`clause_key=E2-4_02`。

### 2.4 纯按条组织 + 提取核心清理

- 一条条款一个目录、一个作者文件、一个 `clause_key`、一个 `clause_digest`；
  包不再是选择单位，只是来源与版本。
- **单条变更不再重编全包身份**：实测 E1-5 2.1.2 → 2.1.3，只有 E1-5_01 的 `clause_digest` 变化，
  其余 21 条保持不变；新增守护测试 `test_one_clause_edit_does_not_re_identify_its_package_siblings`。
- 已删除/退役：`foundation/specs.py`、catalog 指标字面量、guards 条款分支、page-selection 固定词表、
  `report_cache.py` 与共享 `sources/`（跨条款中立理解复用）。
- 选择清单（base16/priority32/quantitative/universal）是纯数据，位于 `metric-profiles.json`；
  “基础 32 条”只是 32 个独立条款键的集合。

## 3. 同时修正的正确性问题

**条款输出口径的聚合分类。** 原实现扫描描述文本中的“占比/比例/强度”，不识别否定语境：

- E1-5_02 化石能源消费总量（MWh）→ `share`
- E1-5_05 可再生能源消费总量（MWh）→ `intensity`
- E1-6_07 范围 1 绝对排放（tCO2e）→ `intensity`
- E1-6_10 市场法范围 2 绝对排放 → 三个口径 `share`，且“位置法口径”带市场法轴

现在聚合类型只从包 `measurement_contract` 的 `measurement_kind`、`value_family`、
`unit_dimension`、`aggregation` 推导；已固定方法的条款不再追加相反方法口径。
全目录分布从 `total 976 / intensity 73 / share 98` 变为 `total 1060 / intensity 6 / share 81`；
“绝对单位却标强度/占比”的疑似项从 95 条降到 11 条（其余为真实强度目标）。
`scope_match` 对 total 事实不再与 E1-5_05 的口径冲突。

## 4. 验证

| 项目 | 结果 |
|---|---|
| 条款目录 | 100 包 / 1,147 条 / `--check` 0 stale |
| 字典重建 | 与封存 1.5.0 字节一致 `c4d73f54…` |
| 标准包测试 | 184 通过（含 3 条新守护：运行时无条款程序、条款声明完整、单条身份稳定） |
| 提取后端 | 787 通过、12 跳过（含 4 条数据合同测试） |
| Document 后端 | 676 通过 |
| Console | 240 通过 |
| 数据库 | 64 个测试：62 通过、2 跳过 |
| PlatESG | Python 79；Node 72 个测试：62 通过、10 跳过 |
| 运行时条款目录 | 39 条 foundation 指标、56 个代码（含 Scope 2 未定方法伪目标）、18 个族、4 个选择清单 |

## 5. 边界与后续

- **平台字典仍读编译包的 `measurement_contract`**：字典是平台比较/发布层，不是提取链路；
  它以同一不可变包为科学源，并由平台策略补充比较规则。若要让字典也只读条款包，需要在条款合同
  中携带完整测量合同快照，另行设计。
- **数据入库未改**：用户本轮要求聚焦 PDF → 提取数据生产。数据库投影仍按旧字段读取条款包，
  `clause_versions.evidence`/`acceptance_state` 的既有缺口留待入库专题。
- **前端未重制**：只把 `pipeline.ts` 的“快速通道”标签条件扩展到 `clauses` 模式；
  标准中心/条款页仍读取目录 API，自动显示新增字段。
- **旧任务/旧发布冻结**：条款摘要与包版本仍随任务冻结；新合同只影响新任务。
- 旧的 `clause-authoring` 生成脚本与 `legacy_foundation_specs.py` 是标准侧迁移工具，
  运行时不导入；后续新增条款应直接编写 `clause-authoring` 文件。
