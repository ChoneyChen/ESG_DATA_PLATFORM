# 全链路筛查报告（第三轮 · 只读）

日期：2026-10-28  
范围：标准条款合同、提取数据合同、两条提取路线、文档与检索、存储与发布、数据库、Console 状态  
方式：只读代码与数据；运行现有测试；不改业务代码、不迁移数据库、不重启服务、不调用模型、不读取正式库内容。

本轮回答的问题不是“还能加什么功能”，而是：

> 条款合同是否真正独立、可变更、被全链路消费；提取是否已有按数据种类划分的数据合同；各模块是否内聚并可分别演进。

---

## 0. 结论

**主链路已经真实成立**，而且各段的合同边界大部分是实的：

```text
PDF → OCR/文字层 → DocumentIR → 检索与阅读 → 来源事实 → 条款映射
    → 内容寻址成果包 → 交付/候选 → 审核与测量口径 → 报告级发布
    → 独立数据库结构 → 公开 DTO / 科学表 / 导出
```

**但“彻底模块化”还差三块关键拼图：**

1. **条款合同不是提取的权威接口。** 条款包（`clauses/<pkg>/<ver>/<SID>/`）是稳定、可校验的接口，
   但定向提取并不读它的检索/证据/验收；默认的 foundation16 路线仍把 39 条目录、29 条 spec、
   31 个条款分支写在 Python 里，条款修订不会自动改变它的阅读行为。
2. **没有“按数据种类”的数据合同。** 今天只有 `SourceFact.kind = quantitative | assertion` 两个值；
   区间、强度/分母、分项、陈述、时间序列、未披露分别靠字段约定散落在 5～7 条代码路径里，
   两条提取路线各自成形、事后适配，而不是共同实现一份种类合同。
3. **报告状态仍有多套投影。** `ReportRun` 只服务于报告列表和侧栏；后端 `/api/console/reports`
   与 `reviewReports.ts`、提取页、概览页仍各自拼状态。

另有三处需要立即修正的正确性问题：条款输出口径的聚合分类被描述文本启发式误判；
公开读取可通过旧 `dataset_version` 绕过报告撤下；平台字典对条款科学文本采用“冻结上一版”，
条款新增的排除项不会进入字典。

---

## 1. 现在的真实链路

| 段 | 宿主 | 对其他段暴露的合同 | 现状 |
|---|---|---|---|
| 报告资产与处理 | `ExtractESG/document_ir_extract` + `document_processing` | PDF 资产身份、OCR/IR 版本、页面与结构证据 | 文字层优先已对所有路线共享 |
| 检索与提取 | `ExtractESG/extraction` + `targeted_table_extract` | `targeted-result-bundle-v1`、`SourceFact`、Core 记录 | standard 路线按包合同；foundation16 仍按代码内条款程序 |
| 标准与条款 | `ExtractESG/standards` | 编译包 `package.json`、五文件条款包、目录、字典输入 | 可复现；目录 100 包/1,147 条款，`--check` 0 stale |
| 交付与审核 | `database/scripts/*` + `PlatESG/backend` | 导入、候选、审核、观测、报告发布 | 导入严格校验版本与内容摘要；报告级发布不可变 |
| 数据库 | `database/migrations` + `PlatESG/backend/migrations` | 规范化表、七类科学视图、公开 DTO | 结构独立；但存在两代科学模型与旧视图 |
| 操作台 | `ConsoleESG` | 只消费 HTTP；不解析 PDF、不写库 | 层级化已完成；状态投影仍分叉 |

服务之间总体是 HTTP 合同；但文件和目录仍是若干处的“事实接口”（见 F9）。

---

## 2. 条款合同：独立性、接口、可变更性

### 2.1 已经成立的部分

- **编译包是科学定义源头。** `measurement_contract` 声明身份轴、比较轴、单位维度、值角色、排除、
  聚合与语义版本；包版本不可变，登记在 registry。
- **条款包是稳定投影。** 每条条款一个目录、五个文件（`clause / retrieval / evidence / acceptance /
  provenance`），由 `ClausePackageCatalog.profile()` 重建并校验摘要
  （`clause_package.py:62-82`），模型字段见 `clause_profile.py:76-128`。
- **目录可复现。** 本轮 `build_clause_catalog.py --check`：`packages=100 clauses=1147 stale=0`。
- **验收绑定科学合同。** `original_contract_digest` 只摘要包 `measurement_contract` 的科学字段 +
  原编号 + 包 id（`clause_lifecycle.py:193-205`），合同变化即 `stale`
  （`clause_lifecycle.py:404-410`）。账本现有 254 条验收、73 条 accepted、覆盖 39 条条款。
- **平台字典可复现。** 以 1.4.5 为输入重建，与封存 1.5.0 字节一致
  （`c4d73f543d4a20aa604e44c422c7c3a41a50001332f18dbecaa05c1e5173c134`）。

### 2.2 条款合同并没有被提取链路消费

`STANDARD_CONTRACT.md:55` 声称定向提取读取条款包的“检索、证据、口径”用于“选页、检索、映射、guard”。
代码核验结果是：

| 条款文件 | 真实消费者 | 提取是否读取 |
|---|---|---|
| `clause.json` 的 `output_scopes` | foundation bundle 的覆盖判定 `foundation/bundle.py:315`；Console 目录/详情；DB 投影 | 仅口径覆盖，不参与选页/检索/映射 |
| `retrieval.json` | Console 条款页 | **否**。standard 路线从编译包概念编译查询（`query_compiler.py:26-193`）；foundation 路线用固定关键词组 |
| `evidence.json` | Console 条款页 | **否** |
| `acceptance.json` | Console 条款页 | **否**（真实验收走账本，不从条款文件读样例） |
| `provenance.json` | 无运行时消费者 | 否 |

foundation16 才是 Console 的默认路线（`ConsoleESG/src/pages/Extractions.tsx:209,276`），它的条款知识是：

- `foundation/catalog.py`：**39 条** `FoundationMetric` 字面量（标签、定义、metric_id、包 id）；
- `foundation/specs.py`：**29 条** `ClauseSpec`（require/exclude/单位维度/总量规则）；
- `foundation/guards.py`：**31 个** `code == …` 条款专用分支；
- `page_selection.py`：**17 组**固定关键词（注释自述“mirrors the foundation-16 source groups”）；
- 提示词使用 `BY_CODE[c].definition`（`kernel.py:145`、`text_pass.py:168`），不是包内 `reading_definition`。

启动时 `validate_contract_catalog()` 只校验 metric_id、package_id、单位维度
（`foundation/catalog.py:290-300`）；标签、定义、排除、guard 规则都不校验。
**因此：改一条条款的排除或措辞，默认提取路线的提示词与守卫不会变。**

### 2.3 条款身份与可变更性

- `clause_id` 内嵌包版本（`clause_profile.py:118-121`），摘要覆盖全部 profile 字段。实测
  E1-5 2.1.2 → 2.1.3：22 条条款的 `contract_digest` **全部改变**，其中 21 条内容除版本/身份外完全相同
  ——只有 E1-5_01 的科学定义改了。一个条款的修订使同包所有条款换身份。
- 条款编辑器的晋升只补丁三个概念字段（`clause_promotion.py:39-41,73-80`）；检索、证据、输出口径
  不能通过编辑器晋升，只能改作者源后整包重编。
- 条款包没有 JSON Schema；四个分文件的 `schema_version`/`clause_id` 不是模型字段，也不与
  `clause.json` 交叉校验；`disclosure_requirement`、`disclosure_role`、`obligation_class`
  在摘要之外（`clause_package.py:9-13`）。
- **接口内容大量是模板**：1,147 个 `acceptance.json` 中 **0 个**有样例；`retrieval.json` 的负词与
  结构锚点全空；`evidence.json` 是统一模板；0 个 `clause.json` 含 `evidence` 字段。

### 2.4 条款目录的派生缺陷（已确认）

`aggregation_for` 用标签+描述文本匹配“占比/比例/强度”等词决定输出口径的聚合类型
（`clause_build.py:39-46`），不识别否定和列举语境：

- `E1-5_02` 化石能源消费总量（MWh）→ 描述结尾“……比例和强度” → 标成 `share`；
- `E1-5_05` 可再生能源消费总量（MWh）→ “能源强度……不是消费绝对总量” → 标成 `intensity`；
- `E1-6_07` 范围 1 绝对排放（tCO2e）→ 描述含“强度” → 标成 `intensity`；
- `E1-6_10` 市场法范围 2 绝对排放 → 描述含“比例” → 三个口径全部 `share`，且新增的“位置法口径”
  同时携带 `axes.measurement_method=market_based`（`clause_profile.py:148-154` 复用 `base_axes`）。

影响不是文案：`scope_match` 会把聚合类型当硬条件（`clause_scope.py:250-260`）。用合成事实复现：
`E1-5_05` 的 `aggregation_role=total` 事实与该口径 **conflict**，foundation 覆盖判定会产出
`scope_not_covered` 假缺口。全目录 171 条条款为 intensity/share（73 + 98），其中 95 条单位不是
ratio 也没有分母轴，属于同类风险（部分强度目标条款是合理的，需逐条判断，不能一律算错）。

### 2.5 平台策略与字典：不是平台层那么“薄”

`contracts/platform-policy.esrs-2023-set1.json` 名义上只负责比较与呈现，实际同时写了
`description`、`exclusions`、`canonical_unit`、`unit_dimension` 等科学/展示字段。核对结果：

- 55/55 平台的 `description` 与包内描述不同（平台是短展示文案，包是科学定义）；
- 55/55 平台的 `exclusions` 与条款排除不同（平台是通用规则 + 短句，包是条款专属排除）。

更关键的是字典构建的“冻结”语义（`scientific_contract.py:57-70`）：

```python
"description": previous_definition.description or policy.description
"exclusions": previous_definition.exclusions + policy.exclusions
"normalization": previous_definition.normalization or …
```

只要存在上一版字典，`description / exclusions / normalization / comparison_conditions` 就取上一版 +
平台策略，**不取当前包合同**。实测：包 2.1.3 给 E1-5_01 新增“不含作为原料使用的能源（非能源用途）”，
现行字典 1.5.0（已绑定 2.1.3）仍然只有四条旧排除，没有这一条。
所谓“条款一改、全链路跟随”，在这四个字段上不成立。

---

## 3. 提取链路：有没有“按数据种类的数据合同”

### 3.1 现状：只有一个两值信封

`SourceFact`（`report_facts.py:47-74`）是跨路线信封，`kind` 只有 `quantitative | assertion`；
`results/spec.py:14-45` 只声明六类 Core 记录的文件布局与主键；`result-bundle-contract-v1.json`
声明标识符、状态枚举与关系，不声明“数据种类”的字段要求。
包内的 `data_class / value_family / measurement_kind / measurement_contract` 只驱动检索与提示词
（`query_compiler.py:374-380`、`report_semantics.py:773-796`），不决定记录成形。

实际被当作“种类”处理的东西，全部是字段约定：

| 种类 | 今天的表示 | 散落位置 |
|---|---|---|
| 绝对数值 | `value_raw + unit_raw + numeric_value` | 两条路线各自成形 |
| 比例/占比 | `canonical_unit=ratio` + 范围校验 | 字典/发布校验 |
| 强度 | 无分母字段；`denominator` 只是自由身份轴 | `kernel.py:80-108`、`guards.py:214-219`、`review-evidence.mjs:188-342`、`derivation.py:370-375` |
| 区间/上下限 | `comparator` 字符串 + 可空上下界 | `tables.py:104-135`、`kernel.py:254-256`、`bundle.py:428-431`、`import-bundle.mjs:432-438`；**SourceFact 没有 comparator 字段** |
| 陈述 | `statement_raw` / qualitative_assertion | `report_facts.py:61`、`materializer.py:551-574`、`text_pass.py` |
| 分项/成员 | foundation 只放在结论 `children`；standard 用 dimension_value | `consolidate.py:175-184`、`bundle.py:379-406`、`materializer.py:665-701`，两套模型互不兼容 |
| 时间序列 | 每个事实各带期间；无 series 概念 | 多处正则与列头推断 |
| 未披露/覆盖 | 任务状态 + gap + 两套 coverage 表 | `contracts.py:196-199`、`plat.coverage_decisions`、`esg.coverage_decisions`（无写入） |

### 3.2 两条路线的事实成形并不相同

- **standard**：中立理解 → `source_facts()` 展开为 `SourceFact`
  （`report_facts.py:130-188`）→ 每指标重绑定 → `materializer` 按 element 的 `record_type` 生成 Core 记录。
- **foundation**：先出 `Observation` 与结论，`BundleWriter` 直接写 Core 记录
  （`foundation/bundle.py:215-281`），**事后**再用 `adapt_direct_core_facts`（`report_facts.py:201-274`）
  或 `foundation/source_facts.py:44-133` 造出另一种 `SourceFact`。

两者只在“Core 校验器”和 `SourceFact` 模型处汇合；来源列头、陈述片段、区间、摘要语义都不同。
所以“同一张表两条路线形成的来源事实可互相比较”这一阶段 2 验收，今天只能部分成立。

### 3.3 泛化能力

- **standard 路线**：新公司/新行业报告零代码改动即可运行，前提是 IR 可用、条款包已注册、字典标记 ready、
  请求选中的标准非空；检索词汇来自包概念 + 固定 `resources/esg_lexicon.json`（通用词表）。
- **foundation 路线**：对既有 39+16 条平台条款零代码改动；**新增条款必须改代码**（catalog + specs + guards）。
- 全仓库检索未发现按公司、报告 id、PDF 文件名或固定页码写死的特例；仅有脚本示例与验收账本中的报告标签。
  这是本轮最重要的正面结论之一。

### 3.4 复用

- standard 中立理解的缓存键是 document/ir/object/interpretation version/provider/model + 证据签名，
  **不含条款**（`evidence/disclosure.py:558-570`），可跨条款目标复用；这是正确的解耦方向。
- foundation 模型答案按 `request_sha` 缓存（`kernel.py:737-745`）。
- `ReportEvidenceStore.interpretation()` 把 `clause_contract` 放进键，但没有生产调用者（死代码）。

---

## 4. 文档与检索

- **文字层优先已共享**：`ocr_workflow.py:52,194-202` 对所有带本地 PDF 的请求启用文字层，
  仅扫描页/损坏页进入视觉识别。规划 §3.2 的限制已解决。
- **选页仍写死**：生产路径 `Foundation16PageSelector()` 无参数（`ocr_workflow.py:353`），
  使用 17 组固定关键词；`for_clauses()`（`page_selection.py:310-320`）只有测试调用；
  `contract_keyword_groups()`（`foundation/catalog.py:275-281`）无调用者。
  请求合同也只允许 `all | foundation16`（`api/main.py:1886`），没有把条款范围带进文档服务的通道。
- **standard 检索**由包合同编译（`query_compiler.py`），与字典的 `required/comparison` 轴一致；
  但 `retrieval.json` 的 `positive_terms` 有整句（如 E1-4_11 一条 156 字），直接拿来做页选词前必须
  拆分/截断，否则会把整句当关键词。
- 文档/检索层与条款层的接口目前是“间接”的：条款 → 包 → 查询编译；页面选择没有走这条路。

---

## 5. 存储、发布与数据库

### 5.1 已经成立

- 成果包是**内容寻址**的终端修订：`terminal-result-revision-v1`，材料化基准摘要、冻结输入摘要、
  每个产物的 sha256 都校验（`terminal_bundle.py:1-90`、`import-bundle.mjs:28-55`）。
- 导入是**严格版本门**：只接受 `targeted-result-bundle-v1` + `targeted-extraction-result-v1` +
  `contract_validation=passed`，未知版本拒绝而非迁移（`import-bundle.mjs:53-55`）。
- 数据库是**规范化结构**，不是 bundle 形状：disclosure / structured_values / qualitative_claims /
  dimensions / sources / mappings / contexts / curated_observations / report_publications 分层；
  观测不可变由触发器保护；报告发布有独立成员表与版本。
- 公开 DTO 是**显式字段清单**（`public_contracts.mjs:1-3`），包含 `reported_dimensions`、
  `source_row_label`、`context_sources`、`period_source`；导出与科学表从数据库/视图读取。
- 报告级发布/撤下/删除已实现；撤下只改状态，不改写不可变观测（设计正确）。

### 5.2 关键缺陷

**F1 公开读取可绕过报告撤下。** 所有公开端点都接受 `dataset_version`（`main.py:713-731`）；
带版本时 `snapshot()` 走 `readDataset()`（`service.mjs:43-44`），而 `readDataset` 只查
`dataset_membership`，**不按报告发布状态过滤**（`publication.mjs:478-493`）；
无版本时才走 `readPublished()` 的报告过滤（`publication.mjs:500-521`）。
撤下/删除报告不修改 `dataset_membership`（`publication.mjs:638-680`），公开前端又支持
`?dataset=<version>`（`PlatESG/frontend/src/App.tsx`、`useObservations.ts:4-12`）。
结果：任何旧链接（或手工参数）都能继续看到已撤下报告的数据。这违反“公开站只显示当前已发布报告”。

**F2 观测来源未按报告过滤。** `hydrateObservations` 读取该观测的**全部** `observation_sources`
（`publication.mjs:424-426`）。若同一观测由两份报告共同支持、其中一份被撤下，公开详情仍会带出
被撤下报告的标题、原文与换算。边角但真实。

**F3 提取器内部标识进入公开契约。** 公开证据暴露 `ir_object_id`、`pdf_page_index`、
`source_fact_ref`（`public_contracts.mjs:37-42`）；数据库列含 `source_ir_run_id / ir_revision`；
`import_batches.bundle_ref` 存绝对路径（`import-bundle.mjs:299-302`），交付状态还会回读该目录
（`delivery_status.mjs:205-209`）。可追溯性是目的，但应改为“公开定位符 + 内部引用”，
存储位置变化不应改变公开合同。

**F4 两代科学模型并存。** `0015` 的视图直接来自候选/观测表；`0016` 的 v2 模型需要手动投影
（`project-scientific-model.mjs`），`esg.coverage_decisions` 没有写入者，
`esg_api.scientific_coverage_decisions_v2` 因此恒空；`v_reported_disclosure_dimensions` 无运行时消费者；
`0013` 之后 `dataset_versions` 仍是第二套全局控制面。

---

## 6. Console 状态

`ReportRun`（`ConsoleESG/src/reportRun.ts:70-171`）是正确的方向：把身份、证据、提取、交付、审核、发布
六轴与“每报告一个主行动”放在一个投影里。但今天只有报告列表与侧栏使用它（`useReportRuns.ts`）：

- 后端 `/api/console/reports` 仍自带一套 `next_action`（`api/console.py:85-119`），与前端 `decide()` 规则不同；
- 报告详情同时读 aggregate/assets/identity/processing/sessions/candidates 六类资源，并自行回退统计
  （`Reports.tsx:166-173`）；
- 提取页完全不使用 `ReportRun`，自有进度与终态逻辑（`Extractions.tsx:49-59,518-533`）；
- 审核与发布使用第二套报告级投影 `reviewReportUnits`（`reviewReports.ts:39-69`）；
- 概览页再各自聚合五个资源（`Overview.tsx`）。

状态分裂的“数量”减少了，但根因还在：没有一个后端投影是唯一事实。

---

## 7. 与《全链路检查报告与升级规划》逐条对照

| 规划条目 | 状态 | 依据 |
|---|---|---|
| §0.3 统一一份条款科学合同 | **部分** | 包合同是源；条款目录是派生；平台策略仍复制科学字段；foundation 仍代码化 |
| §0.3 统一一条中立事实主干 | **部分** | SourceFact 存在；两路线分头成形、事后适配 |
| §0.3 统一一套报告状态投影 | **部分** | ReportRun 只覆盖列表/侧栏 |
| §1.1 两条提取路线 | **仍存在** | foundation16 默认；standard 为通用路线 |
| §1.2 职责表 | **大体** | 文档层嵌入条款族关键词；PlatESG 读 IR 目录 |
| §2 模型与提示词 | **保持** | 未换模型；foundation 提示词仍用代码内定义 |
| §3.2 文字层共享 | **达成** | `ocr_workflow.py:52,194-202` |
| §3.3 检索顺序 | **标准路线达成，foundation 未达成** | `query_compiler` 对包；页选固定词 |
| §3.4 增量证据与复用 | **部分** | 中立缓存 clause-free；页选未接合同 |
| §4.2 科学定义双写 | **明显改善，未清零** | catalog/specs/guards；字典冻结；平台策略重复 |
| §4.3 四类信息 | **达成** | neutral axes、coverage、evidence levels |
| §4.4 条款固定分类 vs 报告细分类 | **达成** | `reported_dimensions` + `source_row_label` |
| §4.5 一份定义驱动七件事 | **部分** | 身份/单位/比较/发布/验收/DB 可以；选页/guard 不行 |
| §4.6 编辑器生命周期 | **部分** | 草稿→晋升存在；只能改 3 个字段；验收样例为空 |
| §4.7 条款库组织 | **部分** | 包/Profile/四视图；共享语义注册表未成形 |
| §5.2 数据库分层 | **达成** | 规范化表 + 不可变快照 + 报告发布 |
| §5.3 A/B/C/D | **A/B/C 达成，D 达成** | 公开维度、字段证据、证据层级、导出表 |
| §5.4 七类科学表 | **表存在，v2 覆盖表恒空** | `0015`/`0016` |
| §6.1 状态分裂 | **部分** | 见 §6 |
| §6.2 创建流程 | **部分** | 同一 schema；两条前端组装路径 |
| §6.3 信息架构 | **达成** | 报告库/标准中心/发布报告页 |
| §6.4 ReportRun | **部分** | 前端投影，非唯一 |
| §7 阶段 0–6 | **0/4/5 基本达成；1/2/3/6 部分** | 见上 |

---

## 8. 缺陷与风险清单（按严重度）

| # | 现象 | 证据 | 影响 |
|---|---|---|---|
| F1 | 公开 `dataset_version` 绕过报告撤下 | `service.mjs:43-44`、`publication.mjs:478-493`、`main.py:721-731` | 已撤下/删除报告的数据仍可被旧链接读到，违反发布承诺 |
| F2 | 条款输出口径聚合由描述文本启发式决定 | `clause_build.py:39-46`；E1-5_02/05、E1-6_07/10 实测 | 覆盖判定假缺口、目录口径错误、下游展示错误 |
| F3 | 字典对科学文本“冻结上一版” | `scientific_contract.py:57-70`；E1-5_01 2.1.3 新增排除未进入 1.5.0 | 条款修订不进入审核与公开定义，“一份合同”不成立 |
| F4 | 条款包不是提取权威接口 | `STANDARD_CONTRACT.md:55` 与 `query_compiler`/`page_selection` 实况 | 条款检索/证据/验收改了，提取行为不变 |
| F5 | 无按数据种类的数据合同 | `report_facts.py:59`、`results/spec.py`、七处约定 | 同一语义多路径实现，新种类要改多处 |
| F6 | foundation16 是代码内条款程序 | 39 catalog + 29 specs + 31 guard 分支 + 17 关键词组 | 新增条款必须改代码；条款修订不传播；默认路线不可泛化 |
| F7 | 条款身份绑定包版本 | 2.1.2→2.1.3：22/22 摘要变化，21 条内容未变 | 一个条款修改使同包全部条款换身份，历史引用碎片化 |
| F8 | 条款接口半成品 | 0/1147 验收样例；0 个 clause.json 含 evidence；无五文件 Schema；分文件头不校验 | 接口看似齐全，真实可用内容少 |
| F9 | DB 条款投影缺字段 | `scientific-model.mjs:16-57` 读 `clause.evidence`；acceptance 不传入 | `clause_versions.evidence` 恒 `[]`，`acceptance_state` 恒 `untested` |
| F10 | 报告状态多套投影 | `console.py:117-118`、`reviewReports.ts`、`Reports.tsx:166-173`、`Extractions.tsx` | 同报告不同页面给出不同下一步/计数 |
| F11 | 目录是事实接口 | `PLATESG_IR_ROOT`（`main.py:996`）、`ESG_TARGETED_OUTPUT_ROOT`（`console.py:44`）、`bundle_path`、Console 拼 URL（`api.ts:88`） | 移动/清理目录会破坏跨服务读取 |
| F12 | 两代科学模型与死视图 | `0016` 手动投影；`esg.coverage_decisions` 无写入；`v_reported_disclosure_dimensions` 无消费者 | 导出的表可能为空或过期；两套真相 |
| F13 | 公开契约暴露内部 IR/事实引用 | `public_contracts.mjs:37-42`；`source_ir_run_id` 列 | 公开合同绑定提取器内部标识 |
| F14 | 文档与实现漂移 | `STANDARD_CONTRACT.md:55,59`；`SYSTEM_STATE.md` 测试数；README 字典 1.4.5（实为 1.5.0） | “唯一当前事实”文档不再完全可信 |
| F15 | 死代码/未接线 | `for_clauses`、`contract_keyword_groups`、`select_blocks`、`ReportEvidenceStore.interpretation`、legacy views | 误以为能力已启用；维护成本 |

**正面结论（同样重要）**：文字层共享；无报告特例；成果包内容寻址与导入版本门严格；
报告级发布不可变；公开 DTO 显式；`reported_dimensions`/`context_sources` 已落地；
条款目录可复现；字典可字节复现；测试基线健康（见 §10）。

---

## 9. 具体改进方案

### P0 — 先修正确性问题（每项 1–2 天）

**P0-1 修正条款输出口径派生。**
改 `clause_build.py`：聚合类型从 `measurement_contract.aggregation / value_family / measurement_kind +
unit_dimension` 推导，不再扫描描述文本；`_scope_for_contract` 不得在已固定 `measurement_method`
的条款上追加相反方法口径；新增一致性校验（intensity 必须有 ratio/intensity 单位维度或分母轴；
share 必须是 ratio 单位；固定方法不得与方法口径冲突）；重建目录并逐个复核 171 条 intensity/share。
验收：E1-5_02/05、E1-6_07/10 口径正确；合成 total 事实不再产生 `scope_not_covered`；
`build_clause_catalog.py --check` 0 stale。

**P0-2 关闭公开读取旁路。**
公开端点带 `dataset_version` 时，`readDataset` 必须与 `report_publications` 交集，或在公开层忽略该参数；
`hydrateObservations` 只返回仍处于 published 的报告来源。前端去掉 `?dataset=` 的默认传播（保留旧链接时
按当前发布状态解释）。验收：发布 A → 撤下 A → 用旧 `dataset_version` 请求，A 的观测不出现；
测试覆盖共同支持观测的来源过滤。

**P0-3 字典跟随条款科学文本。**
`compile_scientific_contract` 的 `description / exclusions / normalization / comparison_conditions`
改为以当前包合同为准；平台只保留显式的展示覆盖（`display_overrides` + 原因），不再用“上一版自动冻结”。
新增对账测试：字典每条绑定包的科学字段必须等于包合同（除非有登记的覆盖）。
验收：E1-5_01 2.1.3 的“原料用能”排除出现在新字典与公开指标定义中；字典仍可字节复现。

**P0-4 修 DB 条款投影。**
`clauseVersionFromPackage` 读取 `evidence.json`（或由目录传入 evidence），`acceptance_state` 从生命周期
账本同步或在投影文档中明确为未同步；`project-scientific-model.mjs` 只投影当前活动版本。
验收：`clause_versions.evidence` 非空且与条款包一致；验收状态与账本一致。

### P1 — 补上两块架构拼图（1–2 周）

**P1-1 新增“数据种类合同”。**
位置建议：`ExtractESG/contracts/src/esg_targeted/data_kinds.py` +
`ExtractESG/extraction/src/esg_targeted/resources/data-kind-contract-v1.json`。
判别联合：`numeric_absolute | ratio | intensity | interval | statement | member_component | derived`
（`not_disclosed` 是覆盖决定，不是观测）。每种声明：
值字段、必需身份轴、单位维度规则、证据层级、比较符/上下界规则、Core 记录映射、DB 映射、允许的 guard 算子。
两条路线都产出该合同，`results/validation` 按种类校验，`import-bundle` 按种类映射；
`SourceFact` 增加 `data_kind` 与 `comparator/bounds/denominator/parent` 的规范位置。
验收：同一张表由两条路线处理得到等价的种类信封；新增一种数据种类不需要修改两条路线；
导入对种类/字段不匹配给出明确拒绝。

**P1-2 条款阅读算子进入合同。**
在 `ClauseExtractionProfile` 增加 `reading`（算子引用 + 参数），由包 `measurement_contract` +
算子注册表编译；把 foundation 的 31 个分支和 29 条 spec 迁为注册算子（总量/成员、排除、单位维度、
强度分母、区间、陈述）；`catalog.py` 退化为编译后的展示适配（只保留展示名）；
提示词改用合同的 `reading_definition`；页选通过 `for_clauses()` 接入（新增 `page_selection: clauses`
模式，检索词按标点/长度拆分，整句不入关键词）。
验收：改条款的排除/措辞后重建，默认提取的提示与 guard 行为随之改变（无需改 Python）；
一条不在固定关键词组里的新条款能在真实报告中选到页；`STANDARD_CONTRACT.md` 的消费表与实现一致。

**P1-3 条款身份与版本解耦（设计决定）。**
建议保留稳定 `clause_key = package_id:source_datapoint_id`，另设 `clause_version_id =
clause_key@package_version` 与 `contract_digest`；验收与 DB 引用稳定 key + 摘要，目录/展示用版本 id。
若决定维持现状，则应在规范中明确“包是版本边界，条款不独立版本化”，并停止宣称单条款可独立变更。
验收：只改 E1-5_01 时，其余 21 条条款的稳定 key 与摘要不变。

### P2 — 内聚与清理（持续）

1. **单一报告投影**：把 `ReportRun` 移到后端（`/api/console/report-runs`），六轴与 `next_action`
   只算一次；Reports/Extractions/Sidebar/Review/Publication/Overview 全部消费同一 DTO；
   `reviewReports.ts` 退化为筛选器；删除 `/api/console/reports` 的重复 `next_action`。
2. **产物句柄**：document/targeted/publication 之间传 artifact handle/URL，不传目录路径；
   `PLATESG_IR_ROOT`、`ESG_TARGETED_OUTPUT_ROOT`、`bundle_path`、Console 手拼 `/document/...`
   全部改为服务返回的定位符（公开 PDF 已按内容哈希解析，可作为样板）。
3. **数据库收敛**：确定 v1（候选表上的视图）为稳定产品；v2 模型要么由投影任务自动维护并加入对账测试，
   要么删除；`esg.coverage_decisions` 补写入或删表；旧 API 视图标注 deprecated 并在无消费者后移除。
4. **公开契约分层**：区分“公开定位符”（报告 ref、页码、引文、证据层级）与“内部引用”
   （IR object id、source_fact_ref、bundle 路径）；公开 DTO 只出前者。
5. **文档同步**：修正 `STANDARD_CONTRACT.md` 的消费表与验收摘要说明；更新 `SYSTEM_STATE.md` 测试数
   （见 §10）与 README 字典版本；把本报告加入文档索引。

---

## 10. 测试基线（本轮实测）

| 套件 | 结果 |
|---|---|
| ConsoleESG | 240 通过（23 个文件） |
| Document 后端 | 675 通过 |
| Targeted 提取后端 | 785 通过、12 跳过 |
| 标准包 | 181 通过 |
| 数据库（pglite） | 64 个测试：62 通过、2 跳过 |
| PlatESG Python | 79 通过 |
| PlatESG Node | 72 个测试：62 通过、10 跳过 |
| 条款目录 | 100 包 / 1,147 条款 / 0 stale |
| 字典重建 | 与封存 1.5.0 字节一致 `c4d73f54…` |

与 `SYSTEM_STATE.md` 的差异：标准包 169→181、Console 242→240、Document 677→675、
Targeted 791/6→785/12、数据库 63/61→64/62。这些是快照数字过期，不是功能回退。

---

## 11. 本轮边界

- 未修改任何业务代码、迁移、数据库、服务或模型；未读取正式库 `PlatESG/.local/data` 的内容。
- 公开撤下旁路、口径误判均以代码路径与合成事实复现确认；未在运行中的正式库上执行“发布→撤下→旧链接”
  的实库演练（只读约束）。P0 修复时应补这一实测。
- 测试库为 pglite 隔离实例，与正式库无关。
- 外部目录 `标准要求包/` 未读取、未修改；它不是本链路来源。
