# ESG Data Platform：当前项目手册与开发交接

> 本文件是整个仓库唯一的项目级事实入口，面向项目成员和新的对话。
> 内容以 2026-09-19 的实际代码、配置和目录为准。若历史聊天、旧方案或演示材料与本文件冲突，以代码合同和本文件为准。

## 1. 新对话从这里开始

工作区根目录（仓库外一层）：

```text
..
```

Git 仓库：

```text
.
```

当前 ExtractESG 主线：

```text
./ExtractESG
```

新对话开始开发前应先执行以下只读动作：

1. 完整阅读本文件。
2. 根据任务读取对应模块 README 和架构/合同文档。
3. 检查 `git status --short`，保留用户已有改动，不得重置工作树。
4. 检查真实配置、接口和现有产物，不根据旧聊天记忆猜测。
5. 修改链路后只运行单元、合同或假模型测试；真实 OCR、Document IR、VLM 和定向抽取业务测试由用户亲自发起，除非用户明确授权代理测试。

必须继续遵守的项目约束：

- `CrawlESG/` 是受保护的独立模块，本阶段不修改它的代码、数据或文档。
- 已退役的 `ExtractESG/infor_match/` 不属于当前平台，仓库中不再保留或维护。
- 报告爬取、Kodo 文件索引、公司/年份/行业主数据不属于 Document IR 模块职责。
- OCR、Document IR、定向抽取三个重负载任务通过统一单 Worker 队列串行运行，不能并发抢占本机统一内存。
- 前端只负责选择、展示和操作，不得成为业务事实来源。
- 密钥只能进入 `.env.local` 或进程内 Secret Vault，不得写入代码、日志、任务 SQLite、输出包或文档。
- GitHub 推送必须使用已约定代理；未经用户明确要求，不自行推送。

推荐给新对话的开场语：

```text
请先完整阅读仓库根目录 README.md，
再检查当前 git 状态和与任务相关的真实代码。CrawlESG 是保护区。
不要自行运行真实 OCR/VLM/抽取业务任务；修改后由我测试。
ESRS E1-5 已发布 draft `1.2.0`，E1-6 已发布 draft `1.4.0`；E1-6 已补齐事实粒度、混淆指标族和展示身份合同；
下一步继续做模型结果业务验证。不得为新指标硬编码专用抽取链路。
```

## 2. 项目定位与当前边界

目标是把 ESG/可持续发展 PDF 转换为可追溯、可复核、可按标准提取的机器数据。当前已经真实落地的是：

1. 本地 PDF 资产管理；
2. OCR-first 文档解析；
3. Document IR 构建、质量路由、视觉复核和版本保留；
4. 标准要求包 Core Schema、E2-4/E1-5/E1-6 模块包及编译器；
5. 按标准任务表的定向检索、视觉/文本语义填表、Guard 和结果包；
6. 统一前端和串行任务控制面；
7. 一个尚未接入正式发布流程的 PostgreSQL/PGlite 结果数据库雏形。

当前没有完成：

- Kodo/服务器生产部署；
- 分布式队列和多机 Worker；
- 全报告 Full Harvest 全量抽取；
- 正式 publication 入库、发布版本和权限治理；
- E1-5、E1-6 的完整真实报告业务验收与业务批准；
- 完整标准全集及跨标准映射；
- 大规模金标准评测。

## 3. 当前目录与职责

```text
ESG Database/
├── .local/
│   └── platform-runtime/          # 统一队列 SQLite、任务隔离目录、启动日志
├── pdf/                           # 当前本地报告资产目录及 report-catalog.json
├── Reference/
│   └── 2606.23050v1.pdf           # Unlimited OCR Works 外部论文，仅作技术参考
└── ESG_DATA_PLATFORM/
    ├── README.md                  # 本文件，项目级唯一事实入口
    ├── CrawlESG/                  # 独立爬取模块；保护区
    └── ExtractESG/
        ├── README.md              # ExtractESG 中文入口与模块导航
        ├── scripts/
        │   └── start_local_workbenches.sh
        ├── document_ir_extract/   # OCR、Document IR、统一前端、控制面后端
        ├── targeted_table_extract/# 定向抽取独立后端
        ├── standard_packages/     # Core Schema、标准模块源包与编译产物
        └── esg_database/          # 结果数据库 SQL 雏形
```

原来的工作区根目录 `v2/` 已完整迁移进 `ExtractESG/document_ir_extract/` 并清理。任何新代码、文档和启动配置都不得重新引用旧 `v2/` 路径。

## 4. 本机运行拓扑

| 服务 | 地址 | 代码位置 | 职责 |
|---|---|---|---|
| 统一前端 | `http://127.0.0.1:18081` | `document_ir_extract/frontend` | 七个业务页面和底部任务工作台 |
| Document/OCR 后端 | `http://127.0.0.1:18080` | `document_ir_extract/backend` | PDF 资产、OCR、IR、复核、产物、统一队列 |
| 定向抽取后端 | `http://127.0.0.1:18180` | `targeted_table_extract/backend` | IR/标准目录、定向抽取、检查与下载 |
| 本地 PaddleOCR-VL 的 MLX VLM 服务 | `http://127.0.0.1:8111` | 桌面模型运行时 | 本地 OCR 流水线的视觉语言后端 |

统一启动器：

```text
$HOME/Desktop/启动ESG统一工作台.command
```

它调用 `ExtractESG/scripts/start_local_workbenches.sh`，启动三个 Web 服务，等待健康检查后打开 `18081`。关闭启动器终端或按 `Ctrl-C` 会停止该窗口创建的服务；已经存在并通过健康检查的服务会被复用。

统一队列数据库：

```text
../.local/platform-runtime/pipeline-queue.sqlite3
```

队列只有一个 Worker。任务包括 OCR、IR 构建、IR 复核/返修、定向抽取和断点续跑。排队任务可以排序和取消，终态队列历史可以单独清除；清除队列历史不删除业务产物。

## 5. 端到端数据流

```text
本地 PDF 资产
  ↓
OCR Provider Router
  ├─ local_paddleocr
  ├─ paddle_api
  └─ local_first（本地失败时按配置回退 API）
  ↓
不可变 OCR Package
  ↓
Document IR 11 阶段构建
  ↓
不可变 Document IR Revision
  ↓
readiness / can_build_evidence 准入
  ↓
已编译 Standard Package
  ↓
Evidence Inventory + 混合检索 + Evidence Regions
  ↓
local NuExtract3 或 Qiniu VLM 直接语义填表
  ↓
Grounding / Contract Guard
  ↓
六类 Core Records + JSONL/XLSX/CSV
  ↓
未来 publication / 正式数据库（尚未接通）
```

PDF 原文件、OCR、Document IR、定向抽取结果分别有独立不可变或版本化合同。下游只能引用上游 ID 和相对产物路径，不能反向修改上游包。

## 6. 报告资产与身份

当前本地 PDF 目录默认是仓库外一层的 `../pdf`。`report-catalog.json` 是工作台对该目录生成的资产目录，记录文件名、大小、SHA-256 和关联 OCR run；它不是项目级公司/年份/行业主数据库，也不是 CrawlESG/Kodo 索引。

身份规则：

- PDF 真身份：完整文件 SHA-256；
- `document_id`：`doc-sha256-<完整SHA-256>`；
- OCR run：`ocr-YYYYMMDDTHHMMSSZ-<12hex>`；
- IR run：`ir-YYYYMMDDTHHMMSSZ-<12hex>`；
- 同一 PDF 可以有多个 OCR run；
- IR 使用 `lineage_id + ir_revision + parent_ir_run_id` 表示分支与修订；
- retention 只保留每个 OCR/IR 分支当前质量最好的 revision。

## 7. OCR 链路

代码入口：`document_ir_extract/backend/src/esg_v2/workflows/ocr_workflow.py` 和 `esg_v2/ocr/`。

请求支持三种路由：

| 值 | 含义 |
|---|---|
| `local_first` | 默认；优先本地 PaddleOCR-VL，允许时失败回退 PaddleOCR-VL API |
| `local_paddleocr` | 强制本地，不把失败伪装成成功 |
| `paddle_api` | 强制使用 PaddleOCR-VL API |

两种 Provider 必须输出同一个 `ocr-package-v1`，因此下游 Document IR 不关心来源是本地还是 API。

真实过程是：PDF 预检与逐页等比画布规范化 → Provider 调用 → 页数覆盖校验 → 保存原始 JSONL、逐页 Markdown、layout 图、提取图片和 Provider 事件 → manifest 与 SHA-256 完整性清单。

```text
ocr_output/<ocr_run_id>/
├── manifest.json
├── source/{request.json,preflight.json,provider-input.pdf?}
├── provider/{submit-response.json,poll-events.jsonl,result.jsonl}
├── content/pages/*.md
├── observations/pages/
├── artifacts/{images,layouts,index.json}
└── integrity/files.json
```

OCR Package 是解析器原始事实与中间资产，不等于 Document IR，也不能直接作为 ESG 事实发布。

## 8. Document IR 链路

唯一运行根是 `ExtractESG/document_ir_extract`。当前语义合同为 `document-ir-v0.12`，目录合同为 `document-ir-package-v1`。

### 8.1 十一个阶段

1. 读取并验证不可变 OCR Package；
2. 以指定 DPI 渲染全部 PDF 页面；
3. 使用本地 PDF 工具完成文本、坐标、页图和表格取证；
4. 将 PaddleOCR 原始 layout 转为 IR candidate；
5. 融合本地取证，去重并重建阅读顺序、章节、标题和脚注；
6. 构造 Table Graph、Logical Table 与跨页表链接；
7. 构造 figure/table/page crop 等视觉区域；
8. 检测真正的信息型左右跨页，生成 spread/composite evidence；
9. 路由质量风险，并按选择调用本地 NuExtract3 或七牛 VLM；
10. 对接受的 Atomic Patch 执行事务化重建与复核收敛；
11. 运行 readiness 校验，写不可变包并执行最佳版本 retention。

### 8.2 主要对象与目录

IR 对象包括 page、section、block、table/cell、logical_table、figure、spread、structure_edge、review_task、atomic_patch 和 guard_result。

```text
document_ir_output/<ir_run_id>/
├── manifest.json
├── canonical/{document.json,pages,tables,logical-tables,figures,spreads,relations,coordinates}
├── observations/{paddle-layout,local-pdf}
├── artifacts/{page-images,crops}
├── review/{tasks,calls,results,patches,conflicts,decisions,candidates}
├── quality/
├── exports/
└── integrity/files.json
```

`manifest.json` 是入口目录；`canonical/` 是下游权威结构；`observations/` 保存未融合来源观察；`review/` 保存模型调用、补丁、Guard、Verifier 和决定。

### 8.3 复核与准入

视觉复核支持本地 `numind/NuExtract3-mlx-4bits` 和七牛 OpenAI-compatible 多模态接口。模型不能直接覆盖 OCR/IR，只能提出受 `ReviewPlanCompiler + OperationRegistry` 限制的候选操作，经 Adapter、Patch Guard、事务协调器和收敛引擎后才能应用。

| readiness | 含义 |
|---|---|
| `ready_with_warnings` | `can_build_evidence=true`，可进入定向抽取 |
| `auto_review_pending` | 仍有可自动处理的阻塞项，暂不可进入抽取 |
| `repair_required` | 需要修本地合同/证据/路由或定向返修 |

## 9. 定向抽取链路

代码根是 `ExtractESG/targeted_table_extract`。它只读消费一个 `can_build_evidence=true` 的固定 IR revision 和一个已编译 Standard Package，不修改 PDF、OCR、IR 或标准包。

统一前端允许在 E1-5、E1-6、E2-4 之间切换并同时保留各包的细分指标选择。一次批量提交按标准包原子创建多个相邻队列任务；它们共享同一个 IR 与模型配置，但每个后端 job 仍只消费一个 Standard Package，并分别固定 version、digest 和 Result Bundle。跨标准多选不改变“一任务一标准合同”的底层事实边界。

### 9.1 当前执行过程

1. 固定并快照 IR、标准包和请求；
2. 从 block、sentence、table row/cell、figure 和 page fallback 建 Evidence Inventory；
3. Literal Harvester 用本地正则、Decimal、单位表和结构定位识别数字、量、百分比、年份、日期、单位和文本候选；
4. Query Compiler 从指标、elements、concept aliases、上下位关系、排除概念和固定维度编译查询；
5. BM25、FactPattern、Qwen3 Embedding 和 RRF 形成混合检索；
6. Sufficiency 记录检索充分性；只有完整扫描且没有可送模证据时才产生本地 `not_found`，词面组合不完整的强结构化命中交给模型判断；
7. 选择 Top 1–5 个独立证据对象，默认 Top 3；
8. 每张表、图或文本区域分别编译 Evidence Region，每次视觉调用最多一张对应 crop；
9. NuExtract3 或七牛 VLM 先按物理源行作 `match/uncertain/different` 判断，再在该行内展开适用的实体/期间值；
10. Guard 只拦截未知字段、伪造来源、跨对象混配、类型/基数违反等确定性错误；
11. 去除完全重复事实，物化六类 Core Records；
12. 写 checkpoint、JSONL、JSON、XLSX、CSV 和遥测。

检索不是单纯关键词匹配。FactPattern 与 Embedding 负责候选排序。能源/GHG 主量型允许强语义命中的数字对象和相邻行送模，不再以字面排除词决定业务归属；已验证的污染物质量/介质选择保持原路径。模型结合指标定义、完整表头与独立预算的方法脚注判断适用性。

模型看到的是单个指标、动态 element 合同、一个连贯 Evidence Region、候选短 ID、原文和最多一张 crop，而不是整份 IR。`C` 表示候选字面量，`S` 表示可验证原文 span，`V` 表示从本次 crop 视觉直读。

### 9.2 六类权威结果

| 集合 | 含义 |
|---|---|
| `reporting_tasks` | 一份报告中一个标准 metric 的执行与找到状态 |
| `quantitative_observations` | 一个数值、单位、期间、边界和维度组合一行 |
| `qualitative_assertions` | 可由证据独立支持的定性陈述 |
| `attribute_values` | 标准包定义、但不属于 Core 固定列的类型化属性 |
| `dimension_values` | 污染物、场地、板块、Scope、能源来源等开放维度 |
| `evidence_references` | PDF 页、印刷页码、IR 对象、bbox、摘录和哈希 |

权威来源是 `results/*.jsonl`；网页、XLSX 和 CSV 是派生视图。

```text
targeted_extract_output/<job_id>/
├── manifest.json
├── failure.json?
├── input/
├── inventory/
├── retrieval/
├── packets/
├── decisions/
├── guards/
├── checkpoints/
├── model-inputs/
├── results/*.jsonl
└── exports/{extraction-result.json,machine-fill.xlsx,task-summary.csv}
```

当前目录合同是 `targeted-result-bundle-v1`，结果合同是 `targeted-extraction-result-v1`。Core 随任务快照固定：E2-4 使用 `extractesg.core@1.0.0`，E1-5/E1-6 使用向后兼容的 `extractesg.core@1.1.0`；E1-5 当前运行时包为 `1.2.0`，E1-6 为 `1.4.0`。

## 10. Standard Packages

代码根是 `ExtractESG/standard_packages`。它分为 Core Schema、标准模块源包和编译运行时包。Core 不是 ESRS 标准，也不是抽取脚本；它定义所有模块共同遵守的机器输出结构。模块包定义“找什么”，通用抽取链路负责“怎样找和怎样填”。

```text
packages/<framework>/<version>/<module>/<package_version>/
├── manifest.json
├── metrics.json
├── elements.json
├── concepts.json
├── dimensions.json
├── code_sets.json
├── relations.json
├── derivations.json
├── validation_rules.json
└── sources.json
```

编译产物为 `standard_packages/dist/<package_id>/<package_version>/package.json`。不得为新指标复制专用 Python 链路，差异优先表达在标准包数据中。

当前 `esrs.2023-set1.e2-4@1.0.0` 是 draft，包含 20 个 datapoint、127 个 elements、36 个 concepts、10 个 dimensions、3 个关系、4 个派生规则和 5 个校验规则。E2-4_02/03/04 是当前实测最充分的三个指标，其余不能宣称达到同等成熟度。

当前新增的两个包也保持 `draft`：

- `esrs.2023-set1.e1-5@1.0.0`：23 个 datapoint、167 个 elements、22 个 concepts、5 个 dimensions、3 个关系、7 个派生规则和 11 个校验规则；
- `esrs.2023-set1.e1-6@1.0.0`：35 个 datapoint、182 个 elements、34 个 concepts、8 个 dimensions、4 个关系、4 个派生规则和 11 个校验规则。
- `esrs.2023-set1.e1-5@1.1.0`：23 个 datapoint、203 个 elements、27 个 concepts、7 个 dimensions；增加能源载体层级、报告实体和指标聚合角色；
- `esrs.2023-set1.e1-6@1.1.0`：35 个 datapoint、211 个 elements、35 个 concepts、10 个 dimensions；增加 Scope 写法、繁简与 CO2e 公式别名、报告实体和指标聚合角色。

`extractesg.core@1.1.0` 没有增加领域记录或固定业务列，只补充跨模块通用的能源、GHG、货币、能源强度、GHG 强度单位及货币/量级代码集。E1 源包由 `standard_packages/scripts/build_e1_packages.py` 确定性生成，编译产物仍以十个 JSON 源文件和聚合 digest 为准。

同一 metric 找到多个污染物、年份、地点、板块或口径时，应产生多条 observation。前端列来自标准包 elements，不应全局硬编码成污染物表。

### 10.1 2026-09-06 工程更新与验收边界

- E1-5/E1-6 发布 `1.2.0`（元素/概念数量与 1.1.0 相同），补充量型、Scope 2 方法及总量/分项语义。新包原子发布并验证成功后，旧编译目录移至 `dist/.retired/<package>/<version>`，不再出现在新任务目录。源包、历史结果、排队快照保留；历史版本可恢复、可加载。禁止同版本覆盖不同内容。
- E1-6 发布 `1.3.0`：E1-6_02/04/05/06 的清单成员绑定到 `qualitative_assertion`，每个成员拥有独立事实身份和证据；总计/表头不再作为类别成员。结果物化按父记录与 element 统一计数，task 固定值只生成一次。每个指标在写入可复用 checkpoint 前单独通过结果合同；单指标合同失败会隔离为 `system_contract_failed`，其余指标仍可形成 partial 成果。旧 checkpoint 可由保存的 packet、决定和 Guard 结果无模型重物化。
- E1-6 发布 `1.4.0`：35 项逐项声明事实粒度、measurement kind、必须区分的语义轴、易混淆指标族、证据形态和 reported/derived 策略。09/10、12/13、26/27、30/31、33/34/35 等家族不再只靠相似标题区分；派生合同额外要求期间、边界和报告实体一致。旧 `1.3.0` 编译产物已自动退役，源包与历史结果仍保留。
- 模型输出升级为物理行优先的 `row_groups`：先判断整行适用性，再展开该行各实体/期间单元格；原始可见 target 值在唯一可解析时可无损回绑到目标 cell。定性事实必须给出可读 statement 或清单成员，期间/纯数字不得被回填成断言。
- 结果阶段把来源验证、合同验证、模型语义决定和展示就绪度拆开；Grounding 通过的新结果保持 `pending`，历史 `auto_verified` 仅作兼容显示。相同物理单元格若被互斥的固定方法重复使用，会写入跨指标冲突审计并进入待联合复判，不通过新增 ESG 语义 Guard 删除模型输出。
- 模型输入增加独立小预算 `linked_context` 方法/脚注；按概念而非重复别名计分，兼容 location-based/location based。模型输出增加 `metric_match`、按需简短解释、`context_refs`、`skipped_targets` 和 `missing_context`。未映射的有据测量保留在决定及检查视图，不被固定标准标签强行物化。
- 删除模型正常停止后的目标数字覆盖催补，仅真实输出截断续写；不新增语义 Guard。原值作为主证据，完全重复事实归并保留多处证据。
- 后端 `FactOrganizer` 统一 API、JSONL、JSON、XLSX/事实 CSV 的顺序，输出逻辑测量与原始单位展示关联，不删除原始记录、不新增换算事实、不把替代单位相加。前端已移除独立事实/列排序。
- IR 整体 readiness 不伪装为通过。局部问题可产生 `can_build_limited_evidence` 与明确排除页面清单；抽取只用其余页面、结果标为 partial。全局完整性错误仍阻止消费。04 页面可创建“隔离问题页面继续”的子修订，不接受失败补丁。修正跨页事务把子对象操作误视为整页确认的覆盖关系。
- 07 使用同一 SQLite/单 Worker 队列，配置 PDF 资产/文件夹导入、OCR/IR/抽取模型、Top N、跨包细分指标；成功产物可复用，按依赖推进，支持暂停后续步骤、取消、重启恢复和手动重试失败步骤。派发键避免队列/计划检查点间的重复任务；密钥只在内存，重启依赖环境凭证。计划引用的队列记录不会被普通“清除历史”删除。

本轮验证仅为单元/合同/假模型测试及历史证据离线回放；没有运行真实 OCR、IR 或模型抽取，也没有改写旧结果。Top N 仍是用户配置的对象数（07 默认 2，旧任务默认不改）；自动扩大到 5 的自适应策略、Scope 2 联合一次抽取和完整 LogicalTable 原生对象消费仍是后续项，不宣称已实现。E2-4_04 的真实黄金结果样本仍需用户补齐；不得以代码测试替代三项污染物真实质量回归。

## 11. 统一前端与独立 API

统一前端有七个页面：报告资产、OCR 任务、Document IR、IR 复核检查、定向抽取、抽取结果、07 链路控制。底部任务工作台提供串行队列、拖动排序、终止、历史清理、日志和系统健康。

Document/OCR API 文档：`http://127.0.0.1:18080/docs`。主要接口为 `/api/report-assets`、`/api/pipeline/tasks`、`/api/pipeline/tasks/targeted-extraction/batch`、`/api/pipeline/standards`、`/api/ocr/*`、`/api/document-ir/*`、`/api/storage/*` 和 `/api/models/status`。批量定向抽取接口要求各包共享 IR 与执行配置、每包至少一个指标且 package/version 不重复，并在一个 SQLite 事务中按前端顺序入队。07 通过统一后端读取标准目录；05 的独立定向服务地址只在连接成功后保存，失效时按统一后端状态自动恢复。

定向抽取 API 文档：`http://127.0.0.1:18180/api/docs`。主要接口为 `/api/ir-runs`、`/api/standards`、`/api/standards/diagnostics`、`/api/result-catalog`、`/api/semantic-indexes`、`/api/jobs` 以及 inspection/artifacts/download。前端常显全部有效标准包；结果空态同时展示任务索引与 Result Bundle 目录的真实计数。若任务 SQLite 丢失但完整终态 Result Bundle 仍在，后端启动时会自动重建任务索引。

前端是静态 HTML/CSS/JavaScript，无构建步骤。CLI、API 和统一队列调用相同后端工作流，不依赖前端。
一键启动器为每次浏览器打开附加启动标识，防止 Safari 仅聚焦旧标签页而继续运行升级前的 DOM/ES module；
标识不改变同源 `localStorage` 中的任务选择和界面设置。

## 12. 本机模型与配置

| 模型 | 默认路径 | 用途 |
|---|---|---|
| PaddleOCR-VL 1.6 | `~/Desktop/model/PaddleOCR-VL-1.6` | 本地 OCR |
| NuExtract3 MLX 4-bit | `~/Desktop/model/NuExtract3-mlx-4bits` | IR 复核、定向填表 |
| Qwen3 Embedding 0.6B 8-bit | `~/Desktop/model/Qwen3-Embedding-0.6B-8bit` | 语义索引与查询 |

运行时位于 `~/Desktop/model/.runtime/paddleocr-vl/bin/python` 和 `~/Desktop/model/.runtime/venv/bin/python`。

Document/OCR 密钥和配置放在被 Git 忽略的 `document_ir_extract/.env.local`，模板见 `.env.example`。密钥不得写入本文件。定向抽取默认使用本地 NuExtract3、开启语义搜索和视觉，Top N=3；七牛模型和预算可由环境变量覆盖。本地与云端按各自能力使用不同预算，不做伪公平比较。

## 13. 当前资产快照

截至 2026-09-04，`targeted_extract_output` 与定向抽取任务 SQLite 各有 12 个终态任务，包含越秀、紫金的 E1-5、E1-6、E2-4 本地/云端对照结果。Result Bundle 是当前结果检查的权威来源；SQLite 索引丢失时可由完整终态 Bundle 重建。

| 报告 | readiness | 可进入抽取 |
|---|---|---|
| 腾讯控股、宁德时代、中国移动、紫金矿业、中国银行、越秀地产 | `ready_with_warnings` | 是 |
| 中国石化、中国石油 | `repair_required` | 否 |
| 隆基 | `auto_review_pending` | 否 |

这些是业务状态，不是包损坏。

## 14. 测试规则

```bash
cd ExtractESG/document_ir_extract/backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider

cd ExtractESG/targeted_table_extract/backend
PYTHONDONTWRITEBYTECODE=1 "$HOME/Desktop/model/.runtime/venv/bin/python" -m pytest -q -p no:cacheprovider
```

当前本轮回归为定向抽取 125 项、标准包合同 26 项；Document IR 的既有回归未在本轮重复执行。测试使用夹具/假模型，不自行启动真实业务任务。
污染物保护采用只读历史回放。本机当前仍保留的两份紫金 E2-4 bundle 均保持 106 条 observation（空气 82、水体 24），三次已接受输出及字段/证据引用未变；两份原固定越秀 bundle 已不在输出目录，因此完整四包脚本会明确报告缺失，不能把缺失样本宣称为已回归。这不等于重新调用模型后的效果保证。

## 15. E1-5 与 E1-6 构造状态

E1-5、E1-6 的 `1.0.0` 已完成首轮紫金/越秀真实任务并暴露检索假阴性、复杂能源表语义混入和 region 标量合同问题；`1.1.0` 是历史工程升级版本，E1-5 当前为 `1.2.0`，E1-6 当前为 `1.4.0`，均仍处于 `draft`，尚未获得业务批准。

参考底稿是用户提供、未提交到仓库的 `EFRAG IG 3 List of ESRS Data Points` 工作簿。

该表只作为 datapoint 清单和来源线索；语义、条件、公式和 AR 已回到 Commission Delegated Regulation (EU) 2023/2772 正文核对。非权威 IG 3 与法规冲突时以法规为准。

### 15.1 E1-5 — 能源消耗与能源结构

`E1-5_01` 至 `E1-5_23` 共 23 项已经逐项建模，覆盖总能源消耗、化石/核能/可再生来源、各类燃料和购入能源、能源生产、高气候影响行业能源强度及净收入 reconciliation。

模块至少需要表达：主值、原始值、能源单位、期间、边界、能源来源类别、载体（燃料/电/热/蒸汽/冷却）、消费/生产、购入/自发、化石燃料类型、高气候影响行业、强度的分子分母、货币和 evidence。

E1-5_18 按法规正文建模为 `MWh/货币单位` 的能源强度，而不是沿用 IG 3 表格中的 `percent` 标签。化石能源明细、高气候影响行业和能源生产通过结构化条件关系激活；总量、占比和强度只在 `data_processing` 阶段声明派生，直接披露值优先。

### 15.2 E1-6 — Scope 1、2、3 与温室气体总量

`E1-6_01` 至 `E1-6_35` 共 35 项已经逐项建模，覆盖 Scope 1、location/market-based Scope 2、Scope 3、GHG 总量、生物源 CO2、ETS、contractual instruments、primary data、GHG intensity、边界变化、方法、假设和排放因子。

模块至少需要表达：`ghg_scope`、`scope_2_method`、`scope_3_category`、tCO2e、期间、边界、控制法、价值链范围、可扩展分解维度、生物源 CO2、ETS/合同工具/primary data 比例、强度分子分母、方法和 evidence。

包内概念关系明确区别 avoided emissions、carbon credits/offsets、removals 与 gross GHG emissions，并把 Scope 1/2/3、location/market-based 互斥关系编译为负向合同。GHG Protocol 与 ISO 14064-1 结构使用替代组；适用的 Scope 3、总量、强度及对账数据点带有小于 750 名员工首年 phase-in 条件。

### 15.3 已完成与下一步

已完成 IG 3/法规核对、Core 1.1 通用量纲、两个十文件源包、确定性编译、JSON Schema、包驱动查询测试、通用单位识别、语义角色驱动的结构字段绑定/结果检查/前端列与排序，以及 E2-4 无模型回归。

本轮采用模型优先优化：繁简/Scope/CO2e 归一化、重复查询组去重、强结构化证据的有界送模后备、模型语义提示和报告实体字段、单 group 数组无损解包、相同错误输出停止重试。未新增 ESG 语义 Guard 或输出哈希链。
单位保留完整的 `kWh/m²`、`tCO2e/GWh`；CO2e 不作为普通污染物质量。若多层表头出现“单位列下是实体名”的结构矛盾，只标记 `header_alignment_uncertain`，不改 IR；模型用完整 crop 判读年份/实体，适配器不以不可靠表头覆盖其视觉结果。
只读证据回放已确认越秀范围一主表 6 个、紫金范围一主表 8 个目标单元格进入送模材料。紫金附录存在重复披露，E1-5 宽表仍保留部分待模型判断的数量；送模数量不得直接解释为最终事实数量。

下一步由用户用 E1-5 `1.2.0`、E1-6 `1.4.0` 复跑紫金/越秀重点指标。验证顺序仍为 Query/FactPattern、Evidence Selection、实际模型输入、物理行裁决、单位/期间/实体和证据绑定；发现问题时优先修概念、模型上下文或通用合同，禁止加入 `E1-5_xx`、`E1-6_xx` 条款号分支。

直接披露与计算继续解耦。报告直接给出的总量、占比或强度照常抽取；未披露时不在语义填表阶段临时计算。`derivations.json` 声明计算关系，执行计算属于后续数据处理模块。

## 16. 已知缺口

1. E1-5、E1-6 已完成首轮真实报告诊断，当前分别为 `1.2.0` 与 `1.4.0`，但尚未通过复跑验收与业务批准；
2. E2-4 只有 02/03/04 经过多轮真实结果优化；
3. 本地 OCR 长报告性能仍需用户逐份验证，API fallback 继续保留；
4. 三份 retained IR 尚未 evidence-ready；
5. 视觉/语义模型仍可能漏列、串列或错误扩展维度，需要证据核对和回归样本；
6. `esg_database` 尚未自动消费定向抽取 JSONL；
7. Kodo、服务器、分布式 Worker、publication、权限和全量抽取未部署；
8. 当前没有全行业准确率结论。
9. GWh/TJ 替代单位的“多个原始展示 → 一个逻辑测量”分组尚未实施；本轮仍保留原始披露，不做单位换算或跨展示汇总。

## 17. 文档治理

| 文档 | 唯一职责 |
|---|---|
| 本文件 | 项目总览、状态、新对话交接、跨模块计划 |
| `document_ir_extract/README.md` | OCR/IR 详细使用 |
| `document_ir_extract/docs/ARCHITECTURE.md` | IR 内部合同与架构 |
| `targeted_table_extract/README.md` | 定向抽取使用与结果阅读 |
| `targeted_table_extract/docs/ARCHITECTURE.md` | 定向抽取组件边界 |
| `targeted_table_extract/docs/RESULT_BUNDLE_SPEC.md` | Result Bundle 合同 |
| `standard_packages/README.md` | 标准包总体说明 |
| `standard_packages/docs/AUTHORING_GUIDE.md` | 模块包制作规范 |
| `esg_database/README.md` | 数据库雏形边界 |
| `CrawlESG/**` | 独立爬取模块文档；保护区 |

不再维护逐轮 `CONSTRUCTION_PROGRESS.md`。重要现状更新本文件；精确行为更新对应模块 README/合同；历史由 Git 记录承担。

## 18. Git 与产物规则

- OCR、IR、定向结果、密钥、本地数据库、模型、日志和语义索引不提交；
- 标准源包、编译包、schema、迁移和测试应提交；
- 修改前后运行 `git diff --check`；
- 不重置或覆盖用户现有改动；
- GitHub 推送走代理；
- 已退役的 `infor_match` 不得重新引入；若未来恢复相关能力，应按当前定向抽取合同重新设计。
