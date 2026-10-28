# 模型、条款口径、检索、条款库、数据库与 Console：六问调查与升级方案

日期：2026-10-28  
方式：只读。核对了真实提示词、检索实现、条款合同、真实运行产物、数据库 migration 与 Console 路由；未修改任何业务代码。

---

## 1. 我们现在怎样用模型

### 1.1 实际参与链路的模型

| 模型 | 角色 | 是否必须 | 真实调用位置 |
|---|---|---|---|
| PaddleOCR-VL 1.6 | 页面布局 + 不可读区域的视觉识别 | 是（扫描件/无文字层必须） | `document_processing/.../ocr_workflow.py` |
| Qwen3.5-9B-4bit | 表格语义、条款映射、导航、段落断言 | 是（语义判断无法穷举） | `foundation/kernel.py`、`models/report_semantics.py` |
| Qwen3-Embedding-0.6B | 语义召回（与 BM25 融合） | 可选（提升长尾召回） | `retrieval/embeddings.py`、`workflow.py:312` |
| NuExtract3 | 有界结构化读取 / 视觉复核选项 | 否（生产默认未用） | `models/nuextract.py`，仅在 specialist/visual_review 选项与阶段 0 对照 |
| 云视觉（七牛） | 显式授权的云端替代 | 否 | 需要显式授权，禁止静默回退 |

关键事实：**prompt 里没有任何数值抽取要求**。模型只返回"行 ID → 条款 ID""列 ID""期间标签""边界"，数值由后端从 IR 单元格读取。

### 1.2 foundation16 的真实输入/输出

输入（`render_table`，`foundation/tables.py:932`），一张表压缩成文本：

```text
TABLE t0001 | page 51
TABLE TITLE: 综合能源消耗
UNIT LINE AFTER TABLE: 单位：兆瓦时
PAGE HEADINGS: 环境 / 能源
VALUE COLUMNS: c1 = 2025年 › 本集团 | c2 = 2024年 › 本集团
ARITHMETIC: c1 = c3 + c4
§ 按能源类型
r0001 煤炭 [吨]: c1 1,234 | c2 1,100
r0002 天然气 [万立方米]: c1 567 | c2 540
NOTE n1: 能源消耗总量按综合能耗计算
```

输出（JSON Schema 强约束，`max_tokens` 按行数计算）：

```json
{"current_col":"c1","current_year":"2025年","current_scope":"whole_reporting_group",
 "items":[{"row":"r0001","target":"E1-5_01","note":"n1"}],
 "method_notes":["n1"]}
```

提示词核心规则（`kernel.py:155`）：

1. `current_col`：当前期真实值列，分/子公司分列时选合计列，禁止选上一年/目标/基准/变化/比率列；
2. `current_scope`：整集团 / 部分集团 / 不明；
3. `items`：只映射**直接报告**该条款的行，跳过"其中/小计/单一燃料/强度/变化/不同概念"，一行最多一个条款；
4. Scope 2 方法只从行标签或注释判定（地点法/市场法），都不写才用 `unspecified`；
5. 有污染物时额外要求 `method_notes`。

### 1.3 standard 路线的真实输入/输出

三段独立合同（`report_semantics.py`）：

- **理解**（`UNDERSTAND_INSTRUCTIONS`）：读一个物理对象（表/段/图），区分总量/小计/成员、消费/生产、绝对量/份额/强度/容量/变化，按列解释期间、主体与角色；"不得重打 IR 里已有的数字"；图表的 payload 只是提示，具体值要用 `cell_overrides` 在图上确认。
- **映射**（`MAP_INSTRUCTIONS`）：把已理解的事实映射到条款卡；禁止"Scope1+2 当 Scope1""容量当消费""可再生份额当能源量""位置法当市场法"；匹配必须给 `source_id`，无证据则 `uncertain`；每个指标都要有 coverage（reviewed/uncertain）。
- **导航**（`PLAN_INSTRUCTIONS`）：选择 `inspect_object / map_object / search_report / read_object_context / request_local_repair / finish`，一次最多两个动作；"重复失败动作不是进展"；`finish` 必须说明剩余缺口，**不是未披露的证明**。

### 1.4 我们真的需要模型吗——分步回答

| 步骤 | 能否不用模型 | 结论 |
|---|---|---|
| 文本页读取 | 能（原生文字层直读） | 已有，且应是默认 |
| 扫描页读取 | 不能 | 必须有视觉模型 |
| 表格网格重建 | 大部分能（词几何 + 线） | 已有；视觉只在低可靠处 |
| 选列（当前期） | 多数能（表头年份 + 算术关系） | 规则优先，模型只在歧义 |
| 行→条款映射 | **不能** | 1126 条款 × 任意中文表述，规则无法穷举 |
| 边界/分项识别 | 部分能（词表） | 词表先行，模型兜底 |
| 负例判断（不是这个条款） | **模型价值最大** | 规则只能覆盖已知反例 |
| 段落断言抽取 | 不能 | 长尾表述 |

因此模型的价值集中在**三类语义判断**：这段/这行是什么意思、它属于哪个条款、它为什么不是另一个条款。凡是能用结构、单位、算术、词表判定的，都应先由确定性代码完成。

### 1.5 是否需要换模型 / 更强配置

阶段 0 已用同一 IR、同一请求比较 Qwen 与 NuExtract：两者在固定表上都 16/16 取到正值，差异在负例拒绝与稳定性。这说明**当前瓶颈不是模型能力，而是调用位置与信息供给**。

更强配置下的分层路由建议：

1. 结构/单位/算术 → 代码（零模型）；
2. 普通表格行映射 → 现役 Qwen；
3. 有界表格读取与格式修复 → NuExtract（需先统一 JSON 尾部协议，已有 `decode_first_json_object`）；
4. 跨页续表、视觉冲突、双语长尾 → 更强模型或视觉复核（按对象计费）；
5. 每种路由都以"同一对象、同一输出合同"度量：准确率、人工修改量、耗时、峰值内存。

**结论：先不要换模型。** 先把"每张候选表都调用一次模型"改成"结构可判定的表不调用、只在歧义处调用"，收益大于换模型。

---

## 2. 条款的输出口径：现在有没有从源头分类

### 2.1 现状：四层各自表达

| 层 | 表达位置 | 内容 |
|---|---|---|
| 条款合同 | `measurement_contract`（identity_axes / comparison_axes / exclusions / positive_definition） | 该条款要求什么 |
| 报告识别 | `source_facts.identity_raw`（`foundation/source_facts.py:60`） | period / start / end / entity_basis / column_header / unit_source / comparator / boundary / pollutant / energy_carrier / method |
| 审核归一 | `database/scripts/review-evidence.mjs` | 把原文维度归一到字典维度 |
| 平台字典 | `build-dictionary.py` + `compile_scientific_contract` | required / comparison / fixed dimensions |

四层共同覆盖了：期间（列头年份、报告期）、边界（集团/子公司/基地/海外/香港等词表）、污染物、能源载体、Scope2 方法、单位维度、比较符。

### 2.2 缺什么（这是"根上分类"的缺口）

**没有预先定义的输出口径空间**。现在的做法是"遇到什么识别什么"，而不是"先声明这个条款可以有哪些口径，再按口径去找"。真实缺口：

1. **期间口径**：年度 / 财年 / 累计（如"近三年"）/ 单季 / 期末时点 / 目标期 / 基准期 / 重述期——目前只有"列头年份"和报告期，无法区分"2025 年"与"2025 财年"，也无法表达累计或目标。
2. **主体口径**：集团 / 合并 / 母公司 / 子公司 / 事业部 / 板块 / 生产基地 / 工厂 / 站点 / 项目公司 / 地区（境内/境外/香港/大陆）/ 门店——词表能识别一部分，但没有"主体层级"与"是否可加总"的语义。
3. **值角色**：实际 / 目标 / 基准 / 预测 / 重述——数据库有 `value_role`，报告侧识别只在少数条款生效。
4. **聚合角色**：总量 / 小计 / 成员 / 分项 / 单列——目前靠模型与 `aggregation_role`，但没有在条款层声明"允许哪些聚合角色作为输出"。
5. **方法/限定语**：位置法/市场法/混合、GWP 依据、合并方法、核算标准；约/≥/≤/区间、含/不含、其中。

所以对"不同子公司、分公司、事业部、特殊定语、特殊情境"的处理，现在是**词表 + 模型 + 人工审核**，不是先验分类。这会导致：同一份报告里"集团总量"和"某基地总量"被当成同一指标的两个值（有时正确，有时冲突），不同年份的历史列需要靠列头启发式判断，特殊定语（"不含XX"）只能靠排除词。

### 2.3 建议：口径空间（Scope & Qualifier Taxonomy v1）

定义五组受控枚举，条款与报告都引用同一套代码：

```text
period:      annual | fiscal_year | cumulative | quarter | instant | target_period | baseline_period | restated_period
entity:      group | consolidated | parent | subsidiary | business_unit | segment | site | plant | project | region | office
value_role:  actual | target | baseline | forecast | restated | estimate
aggregation: total | subtotal | member | single_line | intensity | share | capacity | change
method:      location_based | market_based | mixed | na | <条款自定义枚举>
qualifier:   approx | at_least | at_most | range | including | excluding | of_which
```

条款合同里声明：`output_scopes: [ {period, entity, aggregation, method, required?} ... ]`——即"这个条款允许产出哪些口径组合"。检索与提取按这份清单工作：每个口径一个期望输出，缺哪个就报哪个的覆盖状态。

报告侧识别把原文映射到同一枚举，并保留 `printed_label` 原文与置信度；映射不到时记 `unknown`，**不允许猜**。

这一步做完，"特殊定语/特殊情境"就从"模型临场判断"变成"枚举组合 + 原文证据"，可以逐条验收。

### 2.4 统一提取框架（与条款解耦）

```text
ClauseExtractionProfile
├─ clause_id / version / parent requirement
├─ concept:    positive_definition, distinguish_from, exclusions
├─ value:      value_type, canonical_unit, unit_dimension
├─ scopes:     允许的输出口径组合（上节枚举）
├─ evidence:   每个口径需要的字段级证据（行/列/表头/脚注/正文）
├─ retrieval:  正向词、排除词、结构锚点、依赖条款
├─ acceptance: 金标样例（真实报告 + 期望值/期望未披露）
└─ publication: 聚合、比较、展示
```

链路只认这份 profile：选页、检索、映射、审核、入库都由它生成。新增条款 = 新增一份 profile，不改代码。**这就是"条款如何变活都能用"的答案。**

### 2.5 前端新指标录入

可以做，而且应该做成**条款包编辑器**（结构化表单），不是自由表单：

- 只能组合已注册的算子（求和/比率/单位换算/区间解析），不能写 Python/eval/自由 prompt；
- 保存前强制校验：口径组合非空、单位维度一致、排除词不与自己冲突、至少一条验收样例；
- 保存后进入阶段 5 的生命周期（草稿→编译→试提取→验收→可发布），未验收不得标可发布；
- 确需新能力时走代码插件（有测试、有版本）。

---

## 3. 搜索逻辑：现在到底怎么找

### 3.1 两条链的真实顺序

**foundation16**

```text
文字层逐页打分（关键词组 strong/weak）→ 选页
→ OCR（仅这些页）→ IR
→ 表格打分（表题/行标签/单位/结构）
→ 每张候选表一次模型映射（选列 + 行→条款）
→ 正文补查（关键词）
→ 后端从 IR 单元格绑定数值/单位/期间
```

**standard**

```text
IR 全量 inventory（span/group/candidate）
→ 条款查询编译（概念/元素/固定值/排除概念 → 词项组）
→ BM25 + Embedding + RRF + 结构加权
→ 充分性评估（完整事实模式 + 类型化数值 + 角色覆盖）
→ 导航：inspect / map / search / read_context / repair / finish
→ 模型理解对象 → 模型映射条款 → 后端展开真实单元格
```

### 3.2 各环节用什么技术

| 环节 | 技术 | 是否需要模型 |
|---|---|---|
| 选页 | 文字层正则（繁简折叠、术语词表） | 否 |
| 版面/表格网格 | 布局模型 + 词几何 + 线 | 视觉模型只补不可读区 |
| 页内召回 | BM25（报告内语料）+ 向量 + RRF | 向量可选 |
| 充分性 | 规则（事实模式覆盖） | 否 |
| 对象理解 | 模型 | 是 |
| 条款映射 | 模型 + 合同约束 | 是 |
| 数值/单位/期间 | 后端从 IR 读取与换算 | 否 |

### 3.3 是否合理

合理之处：分层正确（结构→召回→语义→绑定），数值从不经过模型，覆盖状态显式。

不合理之处：

1. **选页仍是关键词组驱动**（阶段 3 已加合同驱动入口，但默认词表仍是静态的）；关键词漏了，后面再准也没用。
2. **"先全表理解，再映射"在简单表上浪费**：一张 3 列的能源表，选列完全可由表头年份 + 算术关系决定，却调用了一次模型。
3. **BM25 语料是单份报告**，IDF 意义有限；真正起作用的是事实模式（topic/role 词项组）与结构锚点。向量召回的增量价值尚未单独度量。
4. **充分性判断依赖 query 编译质量**，条款写得含糊就会把"没找到"判成"证据不足"。
5. **段落/脚注的检索弱于表格**：正文断言往往需要跨段落阅读，目前靠导航反复 `search_report`。

### 3.4 建议的目的导向顺序

```text
0. 报告画像（一次）：期间、主体、目录、章节树、表格清单、语言
1. 条款 → 口径空间：要哪几个口径（年度/集团/分项…）
2. 目标对象召回：按口径找"可能承载该口径的对象"（表/段/图）
3. 结构定位：行 × 列 × 表头路径（多数不需要模型）
4. 确定性绑定：值/单位/期间/边界/证据
5. 只在歧义处调用模型：含义、条款归属、负例
6. 充分性 → 覆盖状态（未覆盖/未映射/证据不足/确认未披露）
7. 局部视觉修复（按对象，不整本）
```

### 3.5 需要处理多少 PDF

- 文字层可读页：**0 次 OCR**，只用词坐标与布局；
- 扫描页或文字层损坏页：必须视觉识别；
- 含目标概念的页：进 IR、进候选；
- 其余页：只留页码与目录信息。

当前真实运行里，一份 36 页可读报告只对命中页做视觉识别；中文年报常见 60–120 页，命中页通常是 10–30 页。**"目的导向"就是把 OCR 预算压到命中页，并让新增条款复用已有 IR（阶段 3 已实现增量）。**

### 3.6 一片数据（表格/文字）到底怎么解析

- 表格：先用几何重建网格 → 表头路径 → 行标签 → 单元格；模型只做"这行是什么、属于哪个条款"；
- 文字：段落切分 → 断言识别（数值/单位/主体/期间）→ 模型判断断言含义；
- 图表：视觉读数，逐点确认，图表结构只作解释元数据，绝不当作原文。

模型在这三类的价值不同：表格是"分类器"，文字是"读者"，图表是"视觉核对者"。

---

## 4. 条款库的组织

### 4.1 现状

- 包 = 披露要求（如 E1-5），内含 `metrics.json`（多条条款）、relations、core、版本；
- 延伸条款走另一套：authoring 草稿 → proposal → approval → release → registry；
- 条款的检索词在 retrieval contract，证据要求在 measurement_contract，验收在阶段 5 的账本，展示策略在平台字典。

**一个问题：条款是编辑单位，包是发布边界，但条款的六类信息分散在五个地方。**

### 4.2 建议：一条条款一个目录包

```text
clauses/esrs.2023-set1.e1-5/E1-5_09/2.3.0/
  clause.json        # 身份、概念、值/单位、口径空间、聚合、比较、排除
  retrieval.json     # 正向词、排除词、结构锚点、依赖页型/表型
  evidence.json      # 字段级证据要求（行/列/表头/脚注/正文）
  acceptance.json    # 金标样例与验收规则（引用真实运行）
  provenance.json    # ESRS 段落/AR、变更说明、审阅人
```

包（`E1-5/2.3.0/package.json`）只保留"条款引用 + 顺序 + 版本锁定 + 发布状态"，不再复制合同。

好处：新增/修改/删除条款只动一个目录；版本差异按文件对比；验收随条款走；前端可以按条款而不是按包编辑。

### 4.3 前端怎么展现

```text
条款中心
├─ 列表：按主题（E1…G1）/ 成熟度（草稿…可发布）/ 依赖 / 最近变更
├─ 详情：合同（只读渲染）+ 口径空间表 + 检索词 + 证据要求 + 验收历史
├─ 编辑器：结构化表单（分 5 个标签），保存即生成草稿版本
├─ 版本差异：逐字段 before/after（阶段 5 已实现后端）
├─ 依赖影响：哪些条款/延伸/已发布观测受影响
└─ 操作：试提取（选真实报告）→ 验收 → 提交批准 → 退役
```

---

## 5. 数据库

### 5.1 现状结构

40+ 张表，三层：

```text
导入/候选：import_batches → esg_disclosures → disclosure_dimensions
           → disclosure_standard_mappings → measurement_contexts → review_decisions
发布：     curated_observations → observation_sources → dataset_versions
           → report_publications → report_publication_members
参考：     standard_frameworks / framework_versions / standard_requirements
           / canonical_concepts / metric_definition_versions / unit_definitions
           / dictionary_versions / normalization_rule_versions
延伸：     extension_proposals → decisions → releases → import_snapshots
           → derivation_requests → derived_candidates → operand_lineage
科学视图： 0015 的七类只读投影
```

### 5.2 问题

1. 候选层结构是**提取过程的镜像**（source_fact → disclosure → mapping → context），换一条提取路线就要改表；
2. 条款定义在三处（standard_requirements、metric_definition_versions、dictionary_membership），延伸条款又一套；
3. 科学视图是事后投影，字段级证据与覆盖状态没有成为一等公民；
4. `extension_*` 一族表持续横向扩张，与主模型的关系靠外键零散连接。

### 5.3 目标科学模型（与提取解耦）

```text
clause_version(clause_id, version, contract_json, status, acceptance_state)
observation(observation_id, entity_ref, clause_id, clause_version,
            period_start, period_end, value_numeric, unit, value_role,
            method, aggregation_role, publication_state)
observation_dimension(observation_id, axis, code, printed_label, confidence)
observation_evidence(observation_id, field, ir_object_id, page, bbox, quote)
observation_source(observation_id, raw_value, raw_unit, raw_label, report_ref)
coverage_decision(entity_ref, clause_id, period, state, reason, scope)
publication / publication_version / publication_member
clause_acceptance(clause_version, contract_digest, run_ref, checks, outcome)
```

提取侧只向数据库提交两样东西：**物理来源事实**（source_fact）与**条款映射**（mapping）。数据库据此产出科学观测，不保存 Bundle 形状。

### 5.4 迁移策略

新增表/视图 → 双写（旧链路继续工作）→ 对账（同一报告两套产出一致）→ 切换读取 → 保留旧表只读。**不重写历史快照。**

---

## 6. Console 重构

### 6.1 现状（本轮已改）

- 5 条生产主线 + 3 个工具（批量生产/标准中心/运行中心）；
- 报告库六轴（身份/证据/提取/交付/审核/发布）+ 单一主行动；
- 运行监视按报告聚合；共享 `ReportRun` 投影。

### 6.2 仍然紊乱的地方

1. 单份与批量仍是两个后端入口（报告会话 / 生产计划），前端入口统一但概念未统一；
2. 审核页是一张候选长表，即使有报告分组，逐条核对仍靠表格；
3. 发布页 6 个 tab（按报告/阻塞/收据/已发布/事件/覆盖）把不同层次混在一起；
4. 运行中心、报告处理、页面读取三处诊断入口仍有重叠；
5. 条款中心只能看，不能编辑（阶段 5 只做了验收与退役）；
6. 中间产物（OCR/IR/模型调用/Bundle）没有统一的"对象抽屉"。

### 6.3 目标信息架构

```text
报告库       报告身份、证据、提取、交付、审核、发布六轴
提取任务     选报告（单/多）→ 选条款集 → 预览范围与缺口 → 执行；任务详情=业务区 + 技术抽屉
审核中心     报告为单元；报告内按原因分组（冲突/主体期间/映射/单位/自动批准抽查）
发布库       待发布、已发布版本、阻塞、撤下/删除、成员变化
条款中心     条款目录、合同、口径空间、检索词、验收、版本差异、依赖、退役、编辑
运行中心     队列、模型、存储、事件；OCR/IR/模型/Bundle 诊断收编于此
```

### 6.4 关键交互

- 报告行：六段状态 + 一个主按钮；点开进概览；
- 报告详情：六个固定标签（概览/原文与证据/提取成果/审核/发布记录/产物与生命周期）；
- 提取详情：上部业务（条款、值、期间、边界、审核、证据），下部技术抽屉（OCR/IR/模型/队列/Bundle）；
- 审核：报告卡 → 原因分组 → 单条核对（左原文右表单），不跨报告批量批准；
- 发布：报告卡上直接发布/撤下/删除，成员变化在抽屉里；
- 运行监视：按报告聚合，展开看内部步骤。

---

## 7. 升级顺序（建议阶段 7–12）

| 阶段 | 内容 | 验收 |
|---|---|---|
| 7 | 口径枚举 v1 + 条款 profile 格式 | 一条条款声明 3 个口径，链路按口径产出 3 个结果或 3 个明确缺失 |
| 8 | 条款目录包拆分（一条款一目录） | 新增/修改一条条款只动一个目录；旧任务按冻结版本读取 |
| 9 | 检索顺序重构（结构优先，模型只在歧义） | 同一批报告，模型调用次数下降，准确率不降 |
| 10 | 数据库科学模型 + 双写对账 | 同一报告两套产出一致；公开读取切到新模型 |
| 11 | 条款编辑器前端 | 不改代码新增一条条款并跑通试点提取与验收 |
| 12 | Console 全面重构 | 六个页面按目标 IA 上线；状态与计数完全同源 |

每一步都以真实报告验收，不以"结构完成"作为完成。
