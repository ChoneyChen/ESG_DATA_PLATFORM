# ESG Data Platform：当前项目手册与开发交接

> **2026-10-05 职责目录迁移**：标准系统唯一源已迁入 `ExtractESG/standards`，队列协调、报告处理、提取引擎及共享合同分别迁入 `control_plane`、`document_processing`、`extraction`、`contracts`；数据库代码唯一维护于根 `database`。旧源码/import路径兼容同一实体，PDF/OCR/IR/Bundle/SQLite路径未移动。当前手册和历史材料的职责见 [文档入口](docs/README.md)，逐批摘要、标准固定版本及回滚依据见 [结构迁移记录](docs/STRUCTURE_MIGRATION.md)。API组装实体暂留兼容宿主，真实业务准确性仍需独立验收。

> **2026-10-05 工作台 P0—P3 更新**：联合版本握手、独立报告处理、生命周期与结构化进度已完成本轮工程建设，765项工程测试与真实工作台只读验收通过。标准目录保持41个结构草稿包，49个主要原编号完整保留，16项配置就绪、33项待建设；核心条款与主要条款分别管理。三个工作台服务已协调重启，15份PDF、34个成果清单及已发布数据保持一致。代码、入口、测试与未完成的产品交互/真实业务验收见 [工作台实施与审计记录](ExtractESG/docs/WORKBENCH_P0_P3_IMPLEMENTATION.md)；依赖未知时可归档，不能据此清理来源。

> **2026-10-04主要指标平台更新**：标准包核心与平台主要条款分开；49主要原编号完整保留，当前E1–E3的16项已接通定向配置、候选/审核/规范发布、冻结版本和独立PlatESG展示。运行及审核连接见[PlatESG指南](PlatESG/README.md)，架构与验收边界见[实施记录](ExtractESG/docs/PRIORITY_PLATFORM_IMPLEMENTATION.md)。已完成本地工程建设；生产部署、多审核员权限和真实报告质量仍待业务验收。下文此前状态属于相应日期，不应将“尚无发布模块”作为最新现状。

> 本文件是整个仓库唯一的项目级事实入口，面向项目成员和新的对话。
> 内容以 2026-10-03 真实测试后的框架修复与工程验证为准。若历史聊天、旧方案或演示材料与本文件冲突，以实际代码合同为准并纠正文档。
> 报告级 Session、模型角色、跨标准中性来源理解和候选数据库接续已落地；真实报告准确性仍需业务验收。
> 需求拆解、宏观链路、代码审计和下一版工作包统一维护于 [系统重塑方案](ExtractESG/docs/SYSTEM_RESHAPING_PLAN.md)。实施对照、验证结果与剩余边界见 [升级实施报告](ExtractESG/docs/UPGRADE_IMPLEMENTATION_REPORT.md)。规划的历史审计与当前实现应分开阅读。

> 当前工程验收：Document 270、Targeted 329、标准包 27、数据库 8，共 634 项测试通过；数据库使用真实匿名 partial 出口与内存 PGlite。详细覆盖、历史调用回放及浏览器结果见 [修复实施报告](ExtractESG/docs/POST_TEST_REPAIR_IMPLEMENTATION_REPORT.md)。
> 2026-10-04 标准包阶段完成：E1–E3全量25包，ESRS2共用12包，MDR共用4包，合计512条；当前版本2.1.1 draft。核心条款属于包内，主要条款采用用户指定49项，另建未来占位与引用解析。详见 [标准包建设记录](ExtractESG/docs/ESRS_STANDARD_PACKAGE_BUILD.md)、[平台方案](ExtractESG/docs/ESRS_PRIORITY_PLATFORM_PLAN.md) 和 [原表目录](ExtractESG/docs/ESRS_DATAPOINT_CATALOG.md)。审核发布及独立展示仍在后续阶段。
> 用户此前发起的中国石油 E1-6 全选任务仍保持其真实历史：中断、无最终 Bundle、29 份无 Core 的检查点、261 个待核来源候选。本轮已按 [优化安排](ExtractESG/docs/POST_TEST_OPTIMIZATION_PLAN.md) 实施协议、来源身份、局部状态、资源和阶段成果修复；194 个原产物与 PDF 摘要均未变。没有重新运行真实模型，工程验收不等于报告准确率。

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
- 正式操作应将 OCR、Document IR、定向抽取三个重负载任务提交统一单 Worker 队列，不能并发抢占本机统一内存。普通 API/CLI 均提交同一 SQLite 队列，内部 worker 标记只用于隔离执行。
- 前端只负责选择、展示和操作，不得成为业务事实来源。
- 密钥只能进入 `.env.local` 或进程内 Secret Vault，不得写入代码、日志、任务 SQLite、输出包或文档。
- GitHub 推送必须使用已约定代理；未经用户明确要求，不自行推送。

推荐给新对话的开场语：

```text
请先完整阅读仓库根目录 README.md，
再检查当前 git 状态和与任务相关的真实代码。CrawlESG 是保护区。
不要自行运行真实 OCR/VLM/抽取业务任务；修改后由我测试。
当前标准库有41个 `2.1.1 draft` 包，含E1–E3全量、ESRS2及MDR；原有E1-5/E1-6/E2-4的旧编译版本已退役但可读。
当前通过报告 Session 统筹多包投影，以中性来源事实跨标准复用；模型角色、LogicalTable阅读和内部依赖读取已经接入。
普通API/CLI使用唯一队列；保留成熟污染物专用路径。继续开发前阅读升级实施报告与相关模块合同。
不得把规划能力写成已完成，不得为新指标硬编码专用抽取链路。
```

## 2. 项目定位与当前边界

### 2.1 根本任务需求

输入是 ESG/可持续发展 PDF、用户选择的标准要求及资源/数据传输预算；输出是口径明确、可追溯、可跨标准复用的报告事实、所选标准的覆盖/缺口和机器表格，最终接续现有结果数据库。

一条事实不是“公司＋年份＋指标”，而是包含源行主题、列头、期间、实体与角色、边界、测量类型、适用方法/类别、原始值/陈述及证据的一次具体披露。
数值、定性断言、标准映射、派生计算、汇总视图各有身份；同值不证明同一事实，映射多项不增加源事实数量。
未知身份保留缺失，不能从请求中的固定分类补造报告事实。结果表、导出和数据库以同一后端合同为准。

质量目标是证据召回正确、语义归属正确、身份明确、覆盖诚实、运行可恢复，而不是让每个条款非空。
共享表理解首先服务准确性和统一身份，不以减少调用数替代质量。

### 2.2 当前宏观链路

报告级任务 → 可导航/可定位的 OCR/IR 证据 → 主表/独立分支/方法证据发现 → 模型理解源事实 → 标准映射 → 按具体缺口补查/局部读图/局部修复 → 确定性计算和组织 → 结果与数据库候选草稿。

模型负责语义与下一步证据动作；程序负责有界工具执行、资源、授权、来源、结构合同和持久化。
语义主脑由本地 Qwen 或用户选择的七牛通用 VLM 承担；NuExtract 保留成熟专用能力，embedding 负责检索，OCR 负责解析。
角色配置实际进入工作流：主脑负责导航、理解和映射，成熟污染物专用任务使用 specialist；云端须显式授权。表格原值由 IR 展开，不强制所有对象再由 NuExtract 重抄。

### 2.3 当前已实现的组成能力

当前已经真实落地的是：

1. 本地 PDF 资产管理；
2. OCR-first 文档解析；
3. Document IR 构建、质量路由、视觉复核和版本保留；
4. 标准要求包 Core Schema、E2-4/E1-5/E1-6 模块包及编译器；
5. 按标准任务表的定向检索、视觉/文本语义填表、Guard 和结果包；
6. 统一前端和串行任务控制面；
7. PostgreSQL/PGlite 结果数据库及 Result Bundle 候选导入、独立事实/映射审核；正式发布服务尚未部署。

### 2.4 真实能力边界

| 能力 | 2026-10-02 实现与验证状态 |
|---|---|
| 报告任务 | PDF/已有 IR 同一请求，一个 Session 下多标准投影，版本/摘要固定 |
| 模型职责 | semantic_brain / specialist / cloud_authorization 实际路由；共用 MLX 基础 |
| 共享报告理解 | 中性 SourceFact 跨包缓存；标准映射和 Core 结果独立可读 |
| 跨页阅读 | LogicalTable/Spread 阅读视图，物理页/单元格证据保留 |
| 动态补查 | 改写查询生成自己的 embedding，协调本地模型释放/重载 |
| 派生依赖 | 自动读取必要未选依赖，直接披露优先，所选输出不扩张 |
| 局部恢复 | 成功行独立保存，缺口按对象/行列/指标定位；显式证据版本转换 |
| 状态与运行 | 执行/成果双轴，partial 可消费；默认 API/CLI 同一有监督队列 |
| 固定证据 | 队列/Session pin 与包内容摘要；retention 不静默改写输入 |
| 事实组织 | 完整原始身份轴；替代/重复/派生关系显式确认，同值不自动合并 |
| 业务前端 | 报告库、提取任务、结果、待处理、设置五入口；共享 API/订阅/创建器 |
| 数据接续 | 真实 Bundle 候选导入 CLI，幂等、冲突回滚、事实/映射分开审核 |
| 验收边界 | 合同/假模型/临时库测试通过；真实模型质量、publication 未批准 |

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
| 统一前端 | `http://127.0.0.1:18081` | `document_ir_extract/frontend` | 五个业务入口、高级技术视图和底部任务工作台 |
| Document/OCR 后端 | `http://127.0.0.1:18080` | `document_ir_extract/backend` | PDF 资产、OCR、IR、复核、产物、统一队列 |
| 定向抽取后端 | `http://127.0.0.1:18180` | `targeted_table_extract/backend` | IR/标准目录、定向抽取、检查与下载 |
| 本地 PaddleOCR-VL 的 MLX VLM 服务 | `http://127.0.0.1:8111` | 桌面模型运行时 | 本地 OCR 流水线的视觉语言后端 |

统一启动器：

```text
$HOME/Desktop/启动ESG统一工作台.command
```

它调用 `ExtractESG/scripts/start_local_workbenches.sh`，启动三个 Web 服务，等待健康检查后打开 `18081`。关闭启动器终端或按 `Ctrl-C` 会停止该窗口创建的服务；已经存在且通过能力版本检查的服务会被复用。升级后若端口由旧服务占用，关闭原启动窗口再启动；启动器不会强杀旧服务或删除任务。

统一队列数据库：

```text
../.local/platform-runtime/pipeline-queue.sqlite3
```

队列只有一个 Worker。任务包括 OCR、IR 构建、IR 复核/返修、定向抽取和断点续跑。排队任务可以排序和取消，终态队列历史可以单独清除；清除队列历史不删除业务产物。

## 5. 端到端数据流

```text
PDF / 固定 Document IR
  → 报告请求预览（标准、角色、预算、数据授权）
  → 唯一 SQLite 队列 / 隔离 Worker
  → 必要 OCR 与基础 IR / 对象能力准入
  → ReportSession：中性来源理解、逻辑阅读、缺口工具循环
  → 独立标准投影 + 内部依赖/确定性派生
  → Core 六类记录 + 中性事实/绑定/关系审计 + 三结果视角
  → Result Bundle 候选导入现有数据库
  → 人工独立批准事实和映射（正式 publication 尚未部署）
```

PDF、OCR、IR 和结果分别有不可变或版本化合同。队列与 Session 固定 IR ID；保留策略保护排队、历史任务和 Session 引用。修复使用新 revision，经显式 evidence-transition 更新受影响投影，旧证据和结果仍可追溯。

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
9. 路由质量风险，并按选择调用本地 NuExtract3、Qwen3.5 或七牛 VLM；
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

视觉复核支持本地 NuExtract3、本地 Qwen3.5 和七牛 OpenAI-compatible 多模态接口。各 Provider 分别编译输入协议；模型不能直接覆盖 OCR/IR，只能提出受 `ReviewPlanCompiler + OperationRegistry` 限制的候选操作，经 Adapter、Patch Guard、事务协调器和收敛引擎后才能应用。

| readiness | 含义 |
|---|---|
| `ready_with_warnings` | `can_build_evidence=true`，可进入定向抽取 |
| `auto_review_pending` | 仍有可自动处理的阻塞项，暂不可进入抽取 |
| `repair_required` | 需要修本地合同/证据/路由或定向返修 |

基础 IR 默认不执行全报告可选 VLM 增强；复核/返修是显式动作。有限消费使用对象×能力可用性 v2，分别表达文本、图像、单元格、表头和来源位置。对象缺口不再自动排除同页可靠表格；全局身份/来源损坏仍阻塞。
OCR/IR/repair/retry 的旧入口作为统一队列薄适配保留，服务内直接执行线程已退役。

## 9. 定向抽取链路

代码根是 `ExtractESG/targeted_table_extract`。ReportSession 只读消费固定 IR revision，冻结各标准包及所选指标。一个 Session 统一共享来源理解，每包 Core 投影仍有独立 job 和 Result Bundle。limited 消费受请求授权和对象能力合同约束，覆盖始终标为 partial。

主脑解释标准无关 SourceFact，投影器绑定实际 Core 主键；成熟 E2-4 direct 输出通过确定性适配加入来源事实，不更换原专用抽取配方。

### 9.1 当前执行过程

1. 固定并快照 IR、标准包和请求；
2. 从 block、sentence、table row/cell、logical table、spread、figure 和 page fallback 建 Evidence Inventory；
3. Literal Harvester 用本地正则、Decimal、单位表和结构定位识别数字、量、百分比、年份、日期、单位和文本候选；
4. Query Compiler 从指标、elements、concept aliases、上下位关系、排除概念和固定维度编译查询；
5. BM25、FactPattern、Qwen3 Embedding 和 RRF 形成混合检索；
6. Sufficiency 记录检索充分性；兼容路径保留既有判定，新 Agent 不因字面资格筛选直接宣判 `not_found`；
7. 按单指标 Top 1–5 发现独立证据对象；v2 将同一披露主题的对象合并，另用共享对象预算选择互补证据；
8. 每张表、图或文本区域分别编译 Evidence Region，每次视觉调用最多一张对应 crop；
9. v2 模型先解释物理行列语义，后端展开 IR 原值；独立小调用映射相关指标。导航可改写查询补查或只重新映射，不因共享失败重抽整表。旧包、显式兼容模式和污染物介质主指标保留直接路径；
10. Guard 只拦截未知字段、伪造来源、跨对象混配、类型/基数违反等确定性错误；
11. 去除完全重复事实，物化六类 Core Records；
12. 写 checkpoint、JSONL、JSON、XLSX、CSV、SourceFact/Core 绑定、内部依赖归档和角色遥测。

检索不是单纯关键词匹配。FactPattern 与 Embedding 负责候选排序。能源/GHG 主量型允许强语义命中的数字对象和相邻行送模，不再以字面排除词决定业务归属；已验证的污染物质量/介质选择保持原路径。模型结合指标定义、完整表头与独立预算的方法脚注判断适用性。

模型看到一个连贯 Evidence Region、候选短 ID、原文和最多一张 crop。v2 共享路径提供该对象相关的小指标族定义及关系；兼容路径仍提供单指标动态 element 合同。`C` 表示候选字面量，`S` 表示原文 span，`T` 表示物理值单元格，`V` 表示本次 crop 的视觉来源。

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

当前目录合同是 `targeted-result-bundle-v1`，结果合同是 `targeted-extraction-result-v1`。Core 随任务快照固定：E2-4 使用 `extractesg.core@1.0.0`，E1-5/E1-6 使用向后兼容的 `extractesg.core@1.1.0`。当前41个模块新任务使用 `2.1.1 draft` 包并引用Core 1.2.0；旧任务仍固定其原Core和标准包版本，可从快照或退役目录加载。

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

历史 `esrs.2023-set1.e2-4@1.0.0` 包含 20 个 datapoint、127 个 elements、36 个 concepts、10 个 dimensions、3 个关系、4 个派生规则和 5 个校验规则。E2-4_02/03/04 是当前实测最充分的三个指标，其余不能宣称达到同等成熟度。v2 保持这三个主指标的元素合同及独立主题。

历史 E1 版本（新任务的 v2 版本见下文）也保持 `draft`：

- `esrs.2023-set1.e1-5@1.0.0`：23 个 datapoint、167 个 elements、22 个 concepts、5 个 dimensions、3 个关系、7 个派生规则和 11 个校验规则；
- `esrs.2023-set1.e1-6@1.0.0`：35 个 datapoint、182 个 elements、34 个 concepts、8 个 dimensions、4 个关系、4 个派生规则和 11 个校验规则。
- `esrs.2023-set1.e1-5@1.1.0`：23 个 datapoint、203 个 elements、27 个 concepts、7 个 dimensions；增加能源载体层级、报告实体和指标聚合角色；
- `esrs.2023-set1.e1-6@1.1.0`：35 个 datapoint、211 个 elements、35 个 concepts、10 个 dimensions；增加 Scope 写法、繁简与 CO2e 公式别名、报告实体和指标聚合角色。

`extractesg.core@1.1.0` 没有增加领域记录或固定业务列，只补充跨模块通用的能源、GHG、货币、能源强度、GHG 强度单位及货币/量级代码集。E1 源包由 `standard_packages/scripts/build_e1_packages.py` 确定性生成，编译产物仍以十个 JSON 源文件和聚合 digest 为准。

同一 metric 找到多个污染物、年份、地点、板块或口径时，应产生多条 observation。前端列来自标准包 elements，不应全局硬编码成污染物表。

### 10.1 2026-09-06 工程更新与验收边界

以下保留为历史演进记录；其中队列、有限可用、身份分组等能力并不代表全系统闭环，当前边界以 2.4、10.3 和第 16 节为准。

- E1-5/E1-6 发布 `1.2.0`（元素/概念数量与 1.1.0 相同），补充量型、Scope 2 方法及总量/分项语义。新包原子发布并验证成功后，旧编译目录移至 `dist/.retired/<package>/<version>`，不再出现在新任务目录。源包、历史结果、排队快照保留；历史版本可恢复、可加载。禁止同版本覆盖不同内容。
- E1-6 发布 `1.3.0`：E1-6_02/04/05/06 的清单成员绑定到 `qualitative_assertion`，每个成员拥有独立事实身份和证据；总计/表头不再作为类别成员。结果物化按父记录与 element 统一计数，task 固定值只生成一次。每个指标在写入可复用 checkpoint 前单独通过结果合同；单指标合同失败会隔离为 `system_contract_failed`，其余指标仍可形成 partial 成果。旧 checkpoint 可由保存的 packet、决定和 Guard 结果无模型重物化。
- E1-6 发布 `1.4.0`：35 项逐项声明事实粒度、measurement kind、必须区分的语义轴、易混淆指标族、证据形态和 reported/derived 策略。09/10、12/13、26/27、30/31、33/34/35 等家族不再只靠相似标题区分；派生合同额外要求期间、边界和报告实体一致。旧 `1.3.0` 编译产物已自动退役，源包与历史结果仍保留。
- E1-6 发布 `1.5.0`：01/03 与既有结构型指标统一改为独立可举证 assertion；补齐 `metric_family`、`evidence_roles`、`depends_on` 和 `derivation_policy`。11 的类别成员不再固定为 subtotal，33 增加“营业收入”语义别名；旧 `1.4.0` 编译产物自动退役，源包和历史结果不删除。
- 模型输出按数据类型拆分：定量指标使用物理行优先的 `row_groups`，行级语义与单元格级 value/period/entity 字段互斥；定性/清单指标使用 `assertions`，每条必须给出可读 statement 或清单成员。适配器只对旧输出执行无损结构迁移，期间/纯数字不得被回填成断言。
- Evidence Selection 不再机械截取分数前 N，而是在用户 Top N 总预算内优先组成 `primary_measurement`、`method_context`、`reconciliation_context` 证据角色组合；候选不足时保留低相关候选标签交给模型判断。易混淆指标把兄弟指标及固定范围一并送入语义裁决上下文，不新增业务语义 Guard。
- 结果阶段把来源验证、合同验证、模型语义决定和展示就绪度拆开；Grounding 通过的新结果保持 `pending`，历史 `auto_verified` 仅作兼容显示。相同物理单元格若被互斥的固定方法重复使用，会按 `metric_family` 进入一次族级模型联合复判；模型返回适用指标集合，后端只做结构分发/隔离，原始逐指标决定继续保留，不通过新增 ESG 语义 Guard 猜测归属。
- 模型输入增加独立小预算 `linked_context` 方法/脚注；按概念而非重复别名计分，兼容 location-based/location based。模型输出增加 `metric_match`、按需简短解释、`context_refs`、`skipped_targets` 和 `missing_context`。未映射的有据测量保留在决定及检查视图，不被固定标准标签强行物化。
- 删除模型正常停止后的目标数字覆盖催补，仅真实输出截断续写；不新增语义 Guard。原值作为主证据，完全重复事实归并保留多处证据。
- 后端 `FactOrganizer` 统一 API、JSONL、JSON、XLSX/事实 CSV 的顺序，输出逻辑测量与原始单位展示关联，不删除原始记录、不新增换算事实、不把替代单位相加。前端已移除独立事实/列排序。
- IR 整体 readiness 不伪装为通过。局部问题可产生 `can_build_limited_evidence` 与明确排除页面清单；抽取只用其余页面、结果标为 partial。全局完整性错误仍阻止消费。04 页面可创建“隔离问题页面继续”的子修订，不接受失败补丁。修正跨页事务把子对象操作误视为整页确认的覆盖关系。
- 07 使用同一 SQLite/单 Worker 队列，配置 PDF 资产/文件夹导入、OCR/IR/抽取模型、Top N、跨包细分指标；成功产物可复用，按依赖推进，支持暂停后续步骤、取消、重启恢复和手动重试失败步骤。派发键避免队列/计划检查点间的重复任务；密钥只在内存，重启依赖环境凭证。计划引用的队列记录不会被普通“清除历史”删除。

本轮验证仅为单元/合同/假模型测试及历史证据离线回放；没有运行真实 OCR、IR 或模型抽取，也没有改写旧结果。Top N 仍是用户配置的对象总数（07 默认 2，旧任务默认不改），现在按证据角色分配，不自动扩大。混淆指标族当前在发现同源互斥冲突后合并为一次多指标模型复判；不是所有同族指标都预先共用一次检索/首轮调用。派生执行器已经接入 `data_processing` 阶段，但只在期间、边界、实体、单位与依赖全部显式一致时计算，直接披露优先；完整 LogicalTable 原生对象消费仍是后续项。E2-4_04 的真实黄金结果样本仍需用户补齐；不得以代码测试替代三项污染物真实质量回归。

### 10.2 2026-10-01 披露主题与源事实更新

三个模块发布 `2.0.0`，统一包格式升级为 `2.0`。指标声明 `disclosure_topic` 与 `disclosure_role`；关系明确区分分解、并列方法、独立披露、计算依赖、方法说明和展示投影，包含适用身份轴。标准包不存 Top、模型、prompt 或执行顺序；运行引擎不写条款号分支。

E1-6 按 Scope 1/2/3 主量与总量、ETS、生物源、合同工具、分类、方法、强度和收入划分主题。E1-5 按能源结构、可再生消费、化石载体、生产、强度和收入组织。E2-4 的空气、水体、土壤各自单独成主题，其余指标按所选小指标族共享。

共享模型先输出报告源行的语义、原始行名、单元格或真实断言，再映射到相关指标。物化复用原来源 Guard 与六类 Core。共享失败或某项仍不确定时，只对相应指标/对象补漏，保留已接受的部分事实。主指标缺失不会使独立分支被跳过。直接披露、计算派生与来源展示引用分别保留身份。

05/07 的 `extraction_mode=auto` 自动使用 v2 主题共享；`per_metric` 用于兼容对照。`shared_object_budget` 为每主题 1–8 个对象（默认 3），`retrieval_object_top_n` 仍为每指标 1–5 个独立检索/补漏对象。前端展示主题关系、共享对象、未选依赖、映射和源输出下载；不扩大用户选择的指标集合。

源对象解释、映射和展示引用分别写入 `results/source-facts.json`、`metric-source-mappings.json`、`disclosure-views.json`，并加入完整结果 JSON 的可选字段。当前 source-facts 实际是单 job 的对象解释导出，不等于已经建成标准无关报告事实库。展示引用不新增业务事实、不证明 ISO/GHG 分类适用。E1 新事实保留源行主题，后端按源行主题—期间—实体—口径组织导出；旧 Bundle 不重写。发布成功后旧编译目录移入 `.retired`，源包与历史任务快照保留。v2 源包由 `standard_packages/scripts/build_v2_packages.py` 从历史源版本生成。

### 10.3 报告事实 Agent 与执行可靠性升级

当前实现为“模型导航 → 中性行列理解 → IR 原值展开 → 标准映射 → 具体缺口补证”。来源理解按文档、IR版本、对象、理解协议、Provider/模型与对象内容缓存，跨 E1-5/E1-6 复用；格式修复仍只处理结构协议问题。

Agent 支持 inspect、read_object_context、map、search、request_local_repair、finish；表头/脚注/局部图像按预算读取。结构修复申请形成持久缺口，释放当前任务后通过合法动作修复，再明确采用新 IR，避免在占有执行槽时等待另一重任务。
改写查询实际生成新 embedding；本地脑模型按边界释放后执行 embedding 再重载。依赖读取与所选输出分区，派生记录显式保存操作数、规则和公式版本。

进程硬截止、取消、partial 和检查点恢复保留；默认 API/CLI 进入唯一控制面队列，ReportSession worker 在同一隔离进程内执行各投影。详细协议见 [报告事实 Agent 文档](ExtractESG/targeted_table_extract/docs/REPORT_FACT_AGENT.md)。工程测试不代表真实报告准确性验收。

## 11. 统一前端与 API

普通入口为报告库、提取任务、结果工作台、待处理动作、系统设置。OCR/IR/复核等细节保留为高级技术视图。PDF/已有 IR 使用 `sessions.js` 唯一创建器，所有 HTTP 与轮询通过共同 API/订阅服务，密钥只保留在内存。

Document API：`http://127.0.0.1:18080/docs`。主线使用 `/api/report-sessions/preview`、创建/list/detail/facts、resume/cancel/evidence-transition。报告准备和执行均派发同一控制面队列；旧 `/api/ocr/*`、`/api/document-ir/*`、`/api/pipeline/*` 保留薄适配。
Targeted API：`http://127.0.0.1:18180/api/docs`。原生 Session 和 `/api/jobs` 管理快照、结果及审计；默认创建/恢复通过 Document 队列提交，`enqueue=false` 仅用于控制面内部组织。已有 job/session 的 `run-existing` 队列适配保持原 ID，不重新创建请求。

前端不推断成功、修复 scope 或事实合并；三个结果视角为来源事实、所选要求覆盖、明确关系组织。数据库当前通过 `esg_database` CLI 导入/审核，页面不会自动显示已入库或已发布。

## 12. 本机模型与配置

| 模型 | 默认路径 | 用途 |
|---|---|---|
| PaddleOCR-VL 1.6 | `~/Desktop/model/PaddleOCR-VL-1.6` | 本地 OCR |
| NuExtract3 MLX 4-bit | `~/Desktop/model/NuExtract3-mlx-4bits` | IR 复核、定向填表 |
| Qwen3.5 9B 4-bit | `~/Desktop/model/Qwen3.5-9B-4bit` | 通用视觉复核、报告语义理解、映射和证据导航 |
| Qwen3 Embedding 0.6B 8-bit | `~/Desktop/model/Qwen3-Embedding-0.6B-8bit` | 语义索引与查询 |

运行时位于 `~/Desktop/model/.runtime/paddleocr-vl/bin/python` 和 `~/Desktop/model/.runtime/venv/bin/python`。

Document/OCR 密钥和配置放在被 Git 忽略的 `document_ir_extract/.env.local`，模板见 `.env.example`。密钥不得写入本文件。定向抽取默认仍为本地 NuExtract3、语义搜索和视觉开启、Top N=3；Qwen 权重可由 `QWEN_VLM_MODEL_PATH` 覆盖，七牛模型和预算由环境变量或任务配置选择。新增 Provider 不静默更改默认模型或消费云端费用。

## 13. 业务产物与历史样本

Result Bundle 是结果检查的权威来源；SQLite 索引丢失时可由完整终态 Bundle 重建。
任务目录数、retained IR 数量、各报告 readiness 与模型健康均是动态状态，不能用 2026-09-04 的快照当作当前状态。
本轮未启动服务、读取密钥或运行真实业务任务；没有重新给各报告作语义质量批准。实际产物以目录 manifest、当前 revision 和任务记录为准。

此前已保留越秀/紫金 E1-5、E1-6、E2-4 的不同尝试用于复盘；历史成功不等于当前新提示词或新模型已经通过验收。

## 14. 测试规则

```bash
cd ExtractESG/document_ir_extract/backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider

cd ExtractESG/targeted_table_extract/backend
PYTHONDONTWRITEBYTECODE=1 "$HOME/Desktop/model/.runtime/venv/bin/python" -m pytest -q -p no:cacheprovider
```

v2 必要离线验证覆盖共享解释与恢复、局部错误隔离、Core 物化导出、派生总量口径、标准包兼容和队列配置传递。测试使用夹具/假模型，真实业务任务由用户发起。
污染物保护采用必要的历史回放。此前离线记录中的两份紫金 E2-4 bundle 各为 106 条 observation（空气 82、水体 24），字段与证据曾保持兼容；越秀及完整土壤黄金样本未齐，不能宣称完整三介质回归通过。2026-10-02 未重新执行回放或测试；历史记录不等于新模型效果保证。

## 15. E1-5 与 E1-6 构造状态

E1-5、E1-6 的 `1.0.0` 已完成首轮紫金/越秀真实任务并暴露检索假阴性、复杂能源表语义混入和 region 标量合同问题；此前 E1-5 `1.2.0`、E1-6 `1.5.0` 为工程升级版本。当前三个模块连同新建E1–E3/ESRS2/MDR包均为 `2.1.1 draft`，真实模型效果待业务验收。

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

### 15.3 此前工程记录与下一步

此前制包与工程记录包含 IG 3/法规核对、Core 1.1 通用量纲、两个十文件源包、确定性编译、JSON Schema、包驱动查询测试、通用单位识别、语义角色驱动的结构字段绑定/结果检查/前端列与排序，以及 E2-4 无模型回归。本次仅审查本地实现，不代表重新逐条核验外部法规或运行这些测试；包仍为 draft。

此前采用模型优先优化：繁简/Scope/CO2e 归一化、重复查询组去重、强结构化证据的有界送模后备、模型语义提示和报告实体字段、单 group 数组无损解包、相同错误输出停止重试。未新增 ESG 语义 Guard 或输出哈希链。
单位保留完整的 `kWh/m²`、`tCO2e/GWh`；CO2e 不作为普通污染物质量。若多层表头出现“单位列下是实体名”的结构矛盾，只标记 `header_alignment_uncertain`，不改 IR；模型用完整 crop 判读年份/实体，适配器不以不可靠表头覆盖其视觉结果。
历史只读证据回放曾确认越秀范围一主表 6 个、紫金范围一主表 8 个目标单元格进入送模材料。紫金附录存在重复披露，E1-5 宽表仍保留部分待模型判断的数量；送模数量不得直接解释为最终事实数量。

下一步先按系统重塑方案完成共同合同和报告级纵向切片，再由用户用实际所选标准版本复跑紫金/越秀重点指标。验证顺序为证据召回、源解释、指标映射、独立补漏、单位/期间/实体和证据绑定；发现问题优先修概念、上下文或通用合同，禁止加入 `E1-5_xx`、`E1-6_xx` 条款号分支。

直接披露与计算继续解耦。报告直接给出的总量、占比或强度照常抽取；语义填表阶段不临时计算。全部指标完成并经过族级冲突处理后，通用派生执行器只执行标准包 `derivations.json` 中同时由目标指标 `depends_on` 与 `derivation_policy` 明确启用的 `data_processing` 规则：直接披露优先，期间、边界、实体和单位必须显式一致，来源证据作为 supporting evidence 传递；不满足条件只记录原因，不制造数值。旧 E2-4/E1-5 包未声明新版执行策略，因此保持只审计、不自动计算。

## 16. 验收与后续边界

1. 当前41个 `2.1.1` 包仍为draft；标准包建设与静态验收不等于真实报告准确性或业务批准。
2. E1-5/E1-6、独立方法/扩展分支和完整 E2-4 金样本仍需用户真实报告验收；没有全行业准确率结论。
3. 候选导入和独立审核已可操作；数据库 HTTP/UI 工作流、正式 publication、权限和服务器运维尚未部署。
4. Kodo、分布式 Worker、全报告 Full Harvest、大规模金标准评测仍为明确非目标。
5. 局部 IR 修订保留自包含包及来源；基础结构重建/验证仍有整包计算，后续对象增量优化应在真实数据规模下评估。
6. 重启升级工作台前应停止旧服务；本轮未自动启动真实队列或执行真实 OCR/VLM 任务。

### 16.1 本次实施记录

19 项审计与 W0–W5 工作包按 [系统重塑方案](ExtractESG/docs/SYSTEM_RESHAPING_PLAN.md) 实施，对照、退役清单、具体接口和测试见 [升级实施报告](ExtractESG/docs/UPGRADE_IMPLEMENTATION_REPORT.md)。
保留成熟污染物、可靠取证、小合同、来源/主键检查和运行监督；没有引入平行事实数据库或多 Agent 业务执行集群。

### 16.2 真实测试后的框架修复

2026-10-03 只读检查用户发起的中国石油 E1-6 测试：主表原值已进入来源候选，任务却因模型协议/引用失败和最后格式修复调用的硬截止中断。最终没有有效标准成果；其中 170 个定量候选原值与 IR 单元格一致，但存在期间、主体、正文锚点及细粒度坐标缺口，不能直接视为业务验收通过。

已依次实施失败基线、类型/引用协议、来源身份、片段状态/故障路由、资源生命周期和阶段成果，完成分层离线验收。Targeted backend 为 `0.6.0`，Document backend 为 `0.4.0`。完整实现、代码边界、实际测试和真实复测顺序见 [修复实施报告](ExtractESG/docs/POST_TEST_REPAIR_IMPLEMENTATION_REPORT.md)。

生成 Schema 与接收类型统一；真实表头/分区、正文数量/断言及结构上下文分别读取；来源使用稳定物理 anchor 和不可变解释 revision，变更失效旧投影。局部正确片段可独立恢复，技术失败不被当作未披露或 IR 修复。派生规则明确来源/映射/身份/单位依赖阻塞，reported 仍保留。

报告预算覆盖角色、修复和所有标准 child，跨进程恢复不清零；总预算耗尽停止进入后续任务，单一输入失败不阻断独立输入。工作期间管理空闲睡眠声明，唤醒后重新检查墙钟截止。合法 checkpoint 和后处理 Core 增量落盘，API/Session/页面/partial 出口消费同一阶段状态；旧导出不能覆盖当前有效绑定和 evidence。

真实主脑使用新协议后的遵从率、身份质量、总调用成本及完整 E1-6/E1-5/成熟 E2-4 业务质量仍待用户发起新任务验收。未自动重跑报告、调用云模型、执行旧错误 IR repair 或重写业务产物。

私有审计和原始文件摘要位于仓库外 `../.local/audits/2026-10-03-petrochina/`，不提交真实报告结果。历史升级实施报告保持其当时的工程验证记录；后续能力声明应同时读取本节。

## 17. 文档治理

| 文档 | 唯一职责 |
|---|---|
| 本文件 | 项目总览、状态、新对话交接、跨模块计划 |
| `ExtractESG/docs/SYSTEM_RESHAPING_PLAN.md` | 跨模块需求、改造前审计与工作包；当前完成度见实施报告 |
| `ExtractESG/docs/UPGRADE_IMPLEMENTATION_REPORT.md` | 本次实现对照、退役清单、具体接口与工程验收 |
| `ExtractESG/docs/POST_TEST_OPTIMIZATION_PLAN.md` | 真实测试之后的工程依赖顺序、交付物与验收门槛；保留计划要求 |
| `ExtractESG/docs/POST_TEST_REPAIR_IMPLEMENTATION_REPORT.md` | 2026-10-03 P0–P5 实现、P6 离线证据、版本与用户真实复测边界 |
| `document_ir_extract/README.md` | OCR/IR 详细使用 |
| `document_ir_extract/docs/ARCHITECTURE.md` | IR 内部合同与架构 |
| `targeted_table_extract/README.md` | 定向抽取使用与结果阅读 |
| `targeted_table_extract/docs/ARCHITECTURE.md` | 定向抽取组件边界 |
| `targeted_table_extract/docs/RESULT_BUNDLE_SPEC.md` | Result Bundle 合同 |
| `standard_packages/README.md` | 标准包总体说明 |
| `ExtractESG/docs/ESRS_STANDARD_PACKAGE_BUILD.md` | E1–E3、ESRS2/MDR全量建设、核心/主要分层与共用接口 |
| `standard_packages/docs/AUTHORING_GUIDE.md` | 模块包制作规范 |
| `esg_database/README.md` | 候选导入、审核和迁移边界 |
| `CrawlESG/**` | 独立爬取模块文档；保护区 |

不再维护逐轮 `CONSTRUCTION_PROGRESS.md`。重要现状更新本文件；精确行为更新对应模块 README/合同；历史由 Git 记录承担。

## 18. Git 与产物规则

- OCR、IR、定向结果、密钥、本地数据库、模型、日志和语义索引不提交；
- 标准源包、编译包、schema、迁移和测试应提交；
- 修改前后运行 `git diff --check`；
- 不重置或覆盖用户现有改动；
- GitHub 推送走代理；
- 已退役的 `infor_match` 不得重新引入；若未来恢复相关能力，应按当前定向抽取合同重新设计。
