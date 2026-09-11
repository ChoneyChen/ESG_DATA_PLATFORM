# ESG Document IR v2

项目级现状、跨模块关系和新对话交接见仓库根目录 [`../../README.md`](../../README.md)。
本文只维护 OCR 与 Document IR 的详细运行和产物说明。

本目录 `ExtractESG/document_ir_extract` 是 Document IR 的唯一现行源码与本地运行根目录。
“v2”仅表示产品代际，不再对应工作区根目录中的独立 `v2/` 项目副本。
当前主线完成两块：

1. `PDF -> 本地优先 PaddleOCR-VL（API 可切换/回退） -> ocr_output`；
2. `ocr_output + 原 PDF -> validated/versioned Document IR`。

Document IR 链路不负责报告爬取、Kodo 文件索引、公司/年份/行业主数据或全局报告登记。
本地上传、路径和 URL 只是开发输入方式。当前系统到不可变 Document IR 为止，
不包含任何下游处理实现。

## 当前真实链路

```text
PDF
  -> local PDF canvas preflight (every page)
  -> provider-safe structural rewrite when required
       -> mixed/oversized canvas: per-page uniform scale
       -> rotated/non-zero-origin page: canonical page box
       -> incremental/Form PDF: flattened catalog structure
       -> no crop, no stretch, no page merge
  -> OCR Provider Router
       -> local_first (default)
       -> Local PaddleOCR-VL + MLX-VLM
       -> PaddleOCR-VL AI Studio API (explicit/fallback)
  -> one provider-neutral ocr-package-v1 contract
  -> raw JSONL / Markdown / OCR images
  -> isolated page-render worker (pypdfium2)
  -> native PDF cross-check (pdfplumber)
  -> Paddle raw layout parsing
  -> canonical coordinates
  -> page-bound coordinate normalization
  -> canonical visible-text normalization
  -> grounded Markdown-fallback reconciliation
  -> deterministic Block/Table/Figure deduplication
  -> table geometry recovery (layout/block/pdfplumber/cell union)
  -> structure reconstruction
  -> HTML table parsing + pdfplumber cell observations
  -> Table Graph / cross-page links
  -> semantic visual grouping
  -> table and figure crops
  -> horizontal spread detection (matched seam entities + structured RGB continuity)
  -> side-by-side spread artifact + SpreadIR candidate
  -> local spread preflight (content crossing / visual continuity / uncertain)
  -> visual-only continuity terminates locally without a cloud call
  -> typed quality routing (one risk -> one review question)
  -> deterministic candidate screening
  -> blocking-first provider-neutral review queue
  -> selected provider: local NuExtract3 MLX or Qiniu VLM
  -> Qiniu-only pacing (RPM backoff / TPD run circuit)
  -> resume Guard-passing Verifier checkpoints before new Reviewer work
  -> classify object type before structure reconstruction
  -> minimum AtomicPatch candidate
  -> local Patch Guard
  -> verifier pass (Qiniu different family / local isolated same-model pass)
  -> at most two feedback-guided automatic repair rounds (three reviewer rounds total)
  -> failure-classified PatchTransaction
  -> accepted local patch application
  -> human only for bounded semantic ambiguity
  -> corrected physical structure + LogicalTableIR rebuild
  -> readiness validation
  -> immutable Document IR revision
```

PaddleOCR-VL 是第一主解析器。本地和 API 只改变运行位置，不改变 OCR 包或
Document IR 合同。视觉模型只处理质量路由选出的复杂表格、
数据图表和冲突区域。普通插画、装饰和已经稳定建模的流程图不会因为“页面有图”
就创建模型任务。模型结果不能直接覆盖 OCR：主审结果必须先形成局部候选，
通过本地 Guard，再进入 Verifier。七牛模式使用不同模型家族；本地 NuExtract3
模式使用隔离上下文的同模型二次核验，并在 IR 中明确记录它不具备模型家族独立性。
本地运行、云 API、协议、预算或补丁校验失败进入
自动处理队列，不会被误算为人工审核。仍可按既定策略继续调用的任务是
`auto_review_pending`；重复 Guard 无效、协议耗尽或其他非重试型系统问题是
`repair_required`。只有可读证据在限定自动轮次后仍有阻断性语义分歧，才进入
`review_required` 的人工内容判断。

自动复核重试不是从头重读。任务会保存 `reviewer_pending`、`verifier_pending`、
`repair_pending`、`complete` 阶段、累计执行次数和 Reviewer/Verifier 逻辑调用次数。
如果候选已经通过 Guard、只因 Verifier 服务不可用而中断，下一 revision 会优先
验证原候选。七牛 `RPM` 在同一任务内节流退避；账号/API Key 范围的 `TPD` 一旦触发，
本轮剩余任务不再发送 HTTP，请求端也会在已知熔断期内阻止创建空重试版本。
每个重试 revision 的 manifest 和 quality report 都包含父子状态差异，前端据此显示
“选中、解决、剩余、TPD、RPM、跳过 HTTP、检查点续跑”，不会再把“任务进程完成”
误写成“阻塞已经解决”。

复核任务不再使用一个笼统的“看整页并修好”提示。Router 只收集风险、证据与
最终作用域，唯一的 `ReviewPlanCompiler` 在全部作用域合并后编译一次计划并写入
`contract_hash`；执行器只校验，不会再次生成或覆盖计划。页级、表格和视觉风险会先
拆成类型明确的任务；若页级覆盖风险已由同页的结构对象解释，则转成该结构任务的
上下文，而不是再让模型重复重写整页。每个任务都带一个
`ReviewPlan`，写清唯一问题、当前风险、证据核对顺序、允许的补丁操作和人工边界。
对象类型不确定时先分类：真表格才重建行列；信息图、装饰或重复碎片不能被强行
修成空表格。明显的小型稀疏无数字本地候选会先由确定性规则过滤；已经进入
canonical 的本地伪候选只能通过受 Guard 约束的 `retire_table_candidate` 退役，
原快照和证据保存在 observations 层。

本地表格候选先做页面边界归一化：轻微浮点/解析漂移可以裁剪，明显越界的候选
直接拒绝，不能靠裁剪伪装成合法表格。缺失表格区域依次尝试关联 Block、Paddle
layout、匹配的 pdfplumber observation 和单元格区域并集；全部失败后才路由整页
VLM。章节树同时过滤标点标题、句子型标题和高频卡片标签，并在 Validator 中记录
章节密度、单页章节比例、无父章节比例和可疑标题数。

如果同一张 OCR canonical table 对应多个 pdfplumber 局部候选，Parser Fusion
会用区域包含率和文本覆盖率把重复碎片保留为 observation，而不是再生成一张
`local_only_table_candidate`。Validator 同时报告 table bbox 和 cell bbox 两层
覆盖率；模型修表时本地 Patch Compiler 会继承已有 cell 坐标，Guard 会阻止整表
坐标被意外清空。

## 产品化输出包

OCR：

```text
ocr_output/<run_id>/
  manifest.json                     # 唯一入口和相对路径目录
  source/request.json
  source/preflight.json             # 全页画布、旋转、页框、结构风险和坐标变换
  source/provider-input.pdf         # 仅需规范化时生成；实际送往 Paddle 的副本
  provider/submit-response.json
  provider/poll-events.jsonl
  provider/result.jsonl             # 统一 Paddle layoutParsingResults 信封
  provider/local-resources/         # 仅本地 Provider：可追溯原始图片引用
  observations/pages/index.json
  observations/pages/page-0001.json
  content/pages/page-0001.md
  artifacts/index.json
  artifacts/images/page-0001/
  artifacts/layouts/page-0001.jpg
  integrity/files.json
```

OCR 输入层始终把用户上传的 PDF 作为不可变原件。系统会先用 `pypdf` 读取每页
`MediaBox/CropBox/Rotate`，再用 `pypdfium2` 对全部页面做低分辨率可渲染验证。
普通页面直接送入 Paddle；混合尺寸、超大画布、旋转页、非零页框原点、增量更新
或交互表单等高风险 PDF 会生成 `source/provider-input.pdf`。每一页只允许等比缩放
和结构重写，禁止裁切、拉伸、拼页或改变页序。

`source/preflight.json` 为每页保存原画布、供应商画布、缩放比例以及
`provider_to_source` 逆变换。OCR manifest 的来源 SHA-256、`document_id` 和后续
Document IR 都仍绑定原 PDF；规范化副本只解决供应商读取问题，不会成为新的报告
身份。Paddle 返回的坐标继续由现有 `CoordinateMapper` 按页面宽高映射回原 PDF
坐标，因此证据定位和原文复现不受供应商输入缩放影响。即使 Paddle 任务失败，
预检报告、提交响应、轮询事件和 provider job id 也会保留并在前端展示。

OCR 默认使用 `local_first`。本地 Provider 会先检查独立 Python 运行时、
PaddleOCR/PaddlePaddle/MLX-VLM 导入和模型权重完整性；本地可用时不会触碰 API。
本地不可用或执行失败时，只有在任务允许回退且提供了 API Token 的情况下才会
调用 AI Studio。`manifest.json` 的 `ocr_provider` 与 `provider_route` 会记录请求
模式、最终 Provider、每次尝试和回退原因。选择 `local_paddleocr` 可强制禁止云端，
选择 `paddle_api` 可强制沿用原 API 路径。前端只是这些后端参数的操作面，CLI 同样
支持：

本地 worker 会持续上报已完成页数、总页数、当前页等待时间和存活心跳。单页处理较慢
不会被视为失败；默认连续 `600` 秒没有完成任何新页面才判定为停滞，终止本地 worker，
并按 `local_first` 的既有许可决定是否回退 API。总任务时限仍独立保持 `7200` 秒。
两个阈值分别由 `LOCAL_PADDLEOCR_STALL_TIMEOUT_SECONDS` 和
`LOCAL_PADDLEOCR_JOB_TIMEOUT_SECONDS` 配置。

```bash
esg-v2 ocr --file report.pdf --provider local_first
esg-v2 ocr --file report.pdf --provider local_paddleocr
esg-v2 ocr --file report.pdf --provider paddle_api --token "$PADDLEOCR_VL_API_TOKEN"
```

Document IR：

```text
document_ir_output/<run_id>/
  manifest.json                     # Package 目录与准入状态
  canonical/document.json           # 语义主文件
  canonical/pages/
  canonical/tables/
  canonical/logical-tables/         # 跨页/跨片段逻辑表及 source-cell 映射
  canonical/figures/
  canonical/spreads/                # 左右跨页逻辑对象及页内像素放置关系
  canonical/relations/
  canonical/coordinates/
  observations/paddle-layout/
  observations/local-pdf/
  observations/retired-entities.jsonl # 被退役候选的完整快照与依据
  artifacts/index.json
  artifacts/page-images/
  artifacts/crops/tables/
  artifacts/crops/figures/
  artifacts/spreads/                # 左页+右页无缝拼接的复审证据图
  quality/
  review/
    patches/patch-transactions.jsonl # 独立提交/回滚的补丁事务与失败分类
  integrity/files.json
  exports/document-ir.snapshot.json # 可重建兼容快照
```

OCR 和 IR 的运行状态不属于不可变产物，统一写入 `.local/jobs/`。所有包内
路径均为相对路径；系统生成页文件统一从 `page-0001` 开始。完整命名、编号、
权威数据与审计分层规则见 `docs/ARCHITECTURE.md` 的
`Product Artifact Package Contract`。
首次构建、Targeted Repair 和审核重跑共用同一实时遥测合同；运行结束后，包内
`review/calls/model-calls.jsonl` 仍是不可变的最终模型调用审计依据。

每次 Package v1 写入都会自动检查 manifest 类型与入口、严格 run ID、目录逃逸、
漏文件/多余文件、字节数和 SHA-256。新建包不接受随意 run ID；旧版平铺产物保持
只读兼容，不会被原地改写。已存在的 run 目录也不能复用或覆盖。

### 多 PDF 身份、命名与版本归类

系统不再把 `ocr_run_id` 当作文档身份，也不会从文件名猜测公司、年份或报告类型。
Document IR 使用四层互不混淆的标识：

- `document_id`：`doc-sha256-<完整 64 位 SHA-256>`，标识 PDF 的精确字节内容。
  同一 PDF 改名、移动或重新 OCR 后仍是同一个 ID；同名但内容不同的 PDF 必然分开。
- `ocr_run_id`：一次 PaddleOCR-VL 处理尝试，仍采用
  `ocr-YYYYMMDDTHHMMSSZ-<12 hex>`。
- `lineage_id`：一个独立 IR 修订分支，采用 `irl-<24 hex>`。每次不指定父版本的
  独立构建都创建新分支并从 `r1` 开始。
- `ir_run_id`：一个不可变 IR 产物包，仍采用
  `ir-YYYYMMDDTHHMMSSZ-<12 hex>`；修复、复审或人工裁决继承父分支并递增
  `ir_revision`。

### 最佳 IR 单版本保留

每个 `ocr_run_id` 最终只保留一个质量最好的 Document IR 包。修复、模型复审和人工
裁决仍先写入一个完整、不可变、自包含的新 Revision；只有新包写入成功并通过合同校验
后，`DocumentIrRetentionManager` 才比较 Evidence 准入、阻断/错误数量、未完成复核、
readiness、人工闭环数量和 revision。新包晋升为最佳版本后，上一版本的 IR Package 与
本地任务状态会通过可回滚事务自动清理。

当前保留包已经包含从父版本继承的 Canonical 数据、页面/局部图、模型调用、Guard、
Verifier、Patch 和人工决策记录，因此删除父包不会删除已进入当前包的复核依据。排队中
的 IR 修复、复核重试和定向抽取任务会先自动改指向新的保留 Run ID；仍被运行中任务
读取的版本暂缓清理。保留状态和清理审计位于 `.local/storage-cleanup/ir-retention/` 与
`ir-retention-audit.jsonl`，不写回不可变 IR 包。诊断时可设置
`ESG_V2_DOCUMENT_IR_BEST_ONLY=0` 暂停该策略。

`document_label` 只用于前端显示，默认取原 PDF 文件名，不参与相等性判断。
`external_document_id` 是可选的上游文档索引透传字段；本模块不承担公司主数据、
报告年份或 Kodo 文件登记。若 OCR manifest 已记录来源 hash，IR 构建前会校验本机
PDF SHA-256 与该不可变 hash，防止批量任务串用错误 PDF。

前端的“PDF 与 Document IR 归类”以及 `GET /api/document-ir/documents` 会把相同
PDF 的多次 OCR、多个独立 IR 分支和各分支修订集中展示。历史 v0.11 包不会被改写；
目录读取器会根据其来源 hash 和父版本链动态推导 `document_id` 与 `lineage_id`。

`manifest.json` 中的 `readiness` 为：

- `ready`
- `ready_with_warnings`
- `auto_review_pending`
- `repair_required`
- `review_required`
- `failed`

`can_build_evidence` 是 Document IR 的质量准入字段，仅描述当前 revision 是否满足
后续消费条件，不代表本模块已经实现任何下游抽取流程。

局部问题不再必然阻塞全报告：新增 `can_build_limited_evidence` 和 `evidence_policy`，
只允许消费明确排除问题页面之后的部分。原 readiness、未解决任务和失败补丁保持原状；
全局结构/身份/文件完整性错误或无法定位的阻塞仍不可绕过。下游受限结果必须标为 partial。
04 复核页的 `continue_limited` 操作要求备注，创建子修订而不是把错误补丁标为通过。

Agent 主审协议使用无歧义的 `confirm / propose_patch / abstain`，并要求对每个
路由 scope 单独表态。模型不再提供可信的 `before_value`；该值由本地系统从当前
canonical 候选注入，Guard 再验证。旧模型偶尔返回的 `correct` 只在兼容层按
“有 patch/无 patch”归一化，新的审计结果不会继续写入含糊状态。

v0.8 继续兼容模型明确表达但命名不规范的结果，例如将
`reject_as_nontable` 归一化为 `retire_table_candidate`，而不是让 JSON 协议差异
浪费整轮调用。模型仍不能直接删除对象：只有本地来源、目标类型、证据、payload
和保护性约束全部通过 Guard，补丁才可生效。退役对象不进入 canonical 表格集合，
但完整快照仍可追溯。

同一轮模型返回的补丁不再被当成一个“全成或全败”的大包。系统按局部提交范围
建立 `PatchTransaction`：同一物理表的表格/单元格修订归组；对于左右跨页，
`confirm_spread`、横向 link 和参与续接的两侧物理表修订组成一个不可拆分的
Spread Composition Transaction。这样 Guard 会在同一候选状态上检查行对齐和
逻辑表构造，不能先接受一半表格、再用旧状态审查另一半；
互不依赖的修订分别经过 Guard、Verifier 和提交。Verifier 仍只调用一次，但必须
逐事务给出结论，某个错误补丁不会回滚同轮已经证明正确的独立修订。每个失败事务都记录
`model_protocol / system_contract / evidence_missing / verifier_disagreement /
model_service / rate_limit / repeated_failure` 等机器可判定类别和稳定指纹，供后续
重试选择不同策略，而不是无差别地重复同一次调用。

`repeated_failure` 只在“同一个 Review Task、同一组真实目标、同一组操作语义”
再次失败时成立。不同页面或不同图表即使得到相同 Guard 文案，也不会共享失败
指纹，更不会互相造成“禁止原样重试”。

图表模型输出先经过独立的 `ChartSpecNormalizer`。它只做可证明的字段归一化：
`type -> chart_type`、`x_categories -> categories`、`data/values -> points`，以及
对象式 categories 到 `series[].points` 的无损展开；人数与比例会保留为不同
series，页图/crop 会写入 `visual_evidence_refs`。无法确定转换的形状仍交给 Guard
拒绝，不会猜测。转换成功后直接重新走既有 Guard/Verifier，不追加模型调用。
Guard 会独立报告 schema 与证据结果，已存在的视觉证据不会再因 schema 失败被
误报为空。终局失败指纹包含 task、真实目标和操作语义。

模型 JSON 在进入上述语义归一化前还经过严格的 `ModelJsonObjectDecoder`。它只处理
一种可证明无损的语法缺口：数组中的前一对象少了闭合 `}`，而下一对象已经明确开始。
修复器只补容器符号，不改键、文字、数值或证据引用；修复后的完整内容仍须通过标准
JSON、Pydantic Schema、Guard 和 Verifier，并记录
`adapter_json_missing_array_item_object_closer_repaired`。其他损坏继续拒绝。
`invalid_response / not valid JSON` 归属 `model_protocol · model`，本地内部异常归属
`system_contract · system`，不再误显示为模型服务宕机。

默认 `ESG_V2_REVIEW_COMPLETENESS_MODE=true`：调度器把同轮所有阻塞任务和
非阻塞 Spread/Figure 增强纳入明确执行批次，默认不再制造 `scheduler_deferred`
积压。`ESG_V2_MAX_VLM_REVIEWS_PER_IR_RUN=0` 表示无全局硬上限；只有显式关闭
完整模式时，阻塞和可选队列才分别受 `ESG_V2_MAX_BLOCKING_VLM_REVIEWS_PER_IR_RUN`
与 `ESG_V2_MAX_OPTIONAL_VLM_REVIEWS_PER_IR_RUN` 约束。显式 targeted review 仍只处理
指定目标。

## 左右跨页如何处理

物理页永远不会被合并或删除。`HorizontalSpreadBuilder` 只扫描相邻页，并同时要求：

- 左页对象触达右装订边、右页对象触达左装订边；
- 两侧同类型对象经过页高归一化后形成真实的垂直配对；
- 表格、图片、图示或多段截断文字提供结构信号；
- 两页装订缝附近存在纹理、线条或局部颜色变化的结构连续性；纯同色背景不算；
- 可用时，印刷页码满足偶数左页和相邻奇数右页。

通过本地筛选的页对会生成一个 `SpreadIR` 和
`artifacts/spreads/spread-pXXXX-pYYYY.png`。随后 `SpreadPreflightClassifier` 根据
缝线两侧已经存在的 Block、Table、Figure、bbox 和语义类型区分：几何匹配的信息
对象是 `content_crossing`；只有图片版式连续的是 `visual_continuity`；同色背景、
没有匹配实体或重复表头的普通续表是 `standalone_pages`。后两类直接在本地终结，
保留拼接图和分类依据但不创建七牛任务。只有内容跨页或不确定页才由视觉模型复审
查看拼接图和两张原始物理页，并明确执行 `confirm_spread`、`reject_spread` 或
`abstain`。确认后可用
`link_horizontal_continuation` 连接左右页的表格、文字块或图示；表格段保留各自
页内坐标，同时记录 `continuation_axis=horizontal`。后续 Evidence 检索可以把
`SpreadIR` 作为一个逻辑阅读上下文，但溯源仍精确落在原 PDF 页和页内坐标。

`SpreadIR` 会保存 `classification`、`content_dependency`、
`requires_detailed_review`、`resolution_source`、`preflight_confidence` 和
`preflight_signals`。Quality Router 只允许需要详细复核的 Spread 合并成员页风险；
纯视觉 Spread 不会吞掉同页原本应独立处理的表格、图表或覆盖率问题。

物理表格与逻辑表格分层保存。`TableIR` 始终描述某一物理页上的真实网格；
`LogicalTableIR` 描述读者最终看到的完整表，支持：

- `vertical_stack`：下一页继续追加数据行；
- `horizontal_append_columns`：右页增加新的逻辑列；
- `horizontal_continue_last_column`：右页是一列跨装订缝内容的延续，而不是凭空
  产生第三列。

每个逻辑单元格都保留 `source_cell_ids`，可回到左右物理页的原始单元格、bbox 和
OCR 证据。对于“左边两列、右边一列接续左页最后一列”但行数尚未对齐的情况，
Guard 会拒绝直接确认，并要求先修复物理网格；Validator 会保持
`repair_required`，不会输出一个结构上好看但语义错误的三列表。

VLM 不只负责判断“拼接/不拼接”。在当前页对和其成员对象的证据范围内，它还可
提出文字块、单元格、完整表格网格、图例、标题、视觉类型、坐标和跨缝续接修订。
Guard 只限制目标范围、证据来源、坐标合法性、数值保留和结构一致性，不替代
VLM 的视觉语义判断，也不允许模型直接覆盖 canonical IR。

v0.9 将“普通内容改写”和“视觉证据确认的 OCR 纠错”拆开。一般
`replace_block_text` / `replace_cell_text` 仍必须保留原数字；只有
`correct_ocr_text` 可以修正类似页码 `77 -> 117` 的 OCR 错字，而且必须是单个
Block/Cell、带可解析视觉证据、模型置信度不低于 0.85，并由不同模型家族独立复核。
已有图内文字使用 `bind_block_to_figure` 绑定到 Figure，不再为了补关系而复制文字块。

图表结构使用显式坐标契约。VLM 优先返回相对 Figure crop 的 `0..1`
归一化 bbox；本地系统确定性映射为 canonical PDF page points。v0.9 也兼容旧模型
误标成 page points 的 crop-local pixels，并借助 crop 尺寸、crop 在整页渲染图中的
位置和页坐标系完成换算。Guard 最终只接受位于目标 Figure 内、非退化且可追溯的
元素坐标。

跨页候选同时承载漏读、表格或重要视觉结构风险时属于阻塞复审；仅有版式连续性
时由本地预审直接结束；确实无法从现有结构判断且不阻塞 Evidence 时，才进入
非阻塞增强队列。这样不会因为一本报告采用大量双页设计，就把所有候选一次性
变成付费调用。

若三轮模型仍无法判断，前端人工区会同时显示拼接总览与左右原页，只要求选择
“应左右拼接”或“两页独立”；无法判断时保持任务开放，不强行通过。

## 嵌入式文档预览如何处理

有些页面会放入另一份报告、证书或鉴证声明的微缩预览。PDF 原生文字层可能仍
包含预览里的整页小字，而 OCR 合理地只提取本页正文和“详见附录”等说明。系统
会本地识别“小面积未知视觉对象 + 密集微缩原生文字”组合，并把这些微缩文字从
本页正文覆盖率比较中排除，避免误报 OCR 漏读。

该处理不会删除任何内容：原 PDF、原生文字、预览图、bbox 和 Figure 都保留，
页面与 Figure 会写入明确质量标记。已识别为 `chart` 的小图不会套用此规则，
仍由视觉结构复审核对数据和标签。

## 后端启动

```bash
./scripts/run_backend.sh
```

默认地址：`http://127.0.0.1:18080`。

后端不依赖前端，也可以直接使用 CLI：

```bash
cd backend
source .venv/bin/activate
esg-v2 ocr --file ../sample.pdf
esg-v2 ir \
  --ocr-run-id ocr-20260718T120000Z-0123456789ab \
  --pdf ../sample.pdf \
  --render-dpi 144
```

只重试一个已路由目标：

```bash
esg-v2 ir \
  --ocr-run-id ocr-20260718T120000Z-0123456789ab \
  --pdf ../sample.pdf \
  --execute-vlm-reviews \
  --review-target-id table-p0004-0001
```

基于旧 IR 建立子 revision：

```bash
esg-v2 ir \
  --ocr-run-id ocr-20260718T120000Z-0123456789ab \
  --pdf ../sample.pdf \
  --parent-ir-run-id ir-20260718T130000Z-abcdef012345
```

主要 API：

```text
POST /api/ocr/jobs
GET  /api/ocr/jobs
GET  /api/ocr/jobs/{run_id}/manifest
GET  /api/ocr/jobs/{run_id}/artifacts

POST /api/document-ir/jobs
GET  /api/document-ir/jobs
GET  /api/document-ir/jobs/{run_id}/manifest
GET  /api/document-ir/jobs/{run_id}/document
GET  /api/document-ir/jobs/{run_id}/validation-report
GET  /api/document-ir/jobs/{run_id}/quality-report
GET  /api/document-ir/jobs/{run_id}/pages
GET  /api/document-ir/jobs/{run_id}/pages/{page_index}
GET  /api/document-ir/jobs/{run_id}/tables
GET  /api/document-ir/jobs/{run_id}/tables/{table_id}
GET  /api/document-ir/jobs/{run_id}/logical-tables
GET  /api/document-ir/jobs/{run_id}/logical-tables/{logical_table_id}
GET  /api/document-ir/jobs/{run_id}/figures
GET  /api/document-ir/jobs/{run_id}/review-tasks
GET  /api/document-ir/jobs/{run_id}/correction-patches
GET  /api/document-ir/jobs/{run_id}/model-calls
GET  /api/document-ir/jobs/{run_id}/reviewer-results
GET  /api/document-ir/jobs/{run_id}/atomic-patches
GET  /api/document-ir/jobs/{run_id}/patch-transactions
GET  /api/document-ir/jobs/{run_id}/guard-results
GET  /api/document-ir/jobs/{run_id}/verifier-results
GET  /api/document-ir/jobs/{run_id}/final-decisions
GET  /api/document-ir/jobs/{run_id}/candidate-revisions
GET  /api/document-ir/jobs/{run_id}/reviews/{task_id}
GET  /api/document-ir/jobs/{run_id}/conflict-groups
GET  /api/document-ir/jobs/{run_id}/structure-edges
GET  /api/document-ir/jobs/{run_id}/artifact-index
GET  /api/document-ir/jobs/{run_id}/artifacts
GET  /api/document-ir/revisions/{ocr_run_id}
GET  /api/document-ir/documents
GET  /api/document-ir/documents/{document_id}/revisions
POST /api/document-ir/jobs/{run_id}/patches/{patch_id}/decision
POST /api/document-ir/jobs/{run_id}/review-tasks/{task_id}/decision
POST /api/document-ir/jobs/{run_id}/repair
GET  /api/document-ir/jobs/{run_id}/human-review/inbox
GET  /api/document-ir/review-worklist
POST /api/document-ir/jobs/{run_id}/reviews/retry
GET  /api/models/status
```

## 前端启动

```bash
./scripts/run_frontend.sh
```

打开 `http://127.0.0.1:18081`。

完整平台使用桌面上的 `启动ESG统一工作台.command`，或运行：

```bash
./ESG_DATA_PLATFORM/ExtractESG/scripts/start_local_workbenches.sh
```

启动器拉起三个服务：Document/OCR 后端 `18080`、定向抽取后端 `18180`、统一前端
`18081`。前端关闭不会影响 CLI 或 HTTP API；关闭启动器终端会停止由该窗口创建的服务。
日志和统一任务状态位于工作区 `.local/platform-runtime/`。
Document/OCR 后端默认不开启 Uvicorn 热重载，避免开发期间修改源码时重启 worker、把正在
运行或排队的持久化任务错误标记为 `interrupted`。只有明确设置
`ESG_V2_DEV_RELOAD=1` 时才启用开发热重载；运行真实 OCR/IR 任务时不要开启该变量。

统一前端分为七个业务页面：报告资产、OCR 任务、Document IR 任务、IR 复核检查、
定向抽取、抽取结果、07 链路控制。页面底部是全局任务工作台，集中显示队列、日志和系统状态。

07 计划 API 为 `GET/POST /api/pipeline/plans` 和 `POST /api/pipeline/plans/{id}/{pause|resume|cancel}`。
标准选择通过同一 Document 后端的 `GET /api/pipeline/standards` 及其包详情接口读取；
后端再使用自身配置的定向抽取服务地址，避免浏览器残留旧端口时把标准区域显示为空白。
05 页面仍直接调用定向抽取服务以执行任务，但初始化失败会在短超时后从
`GET /api/pipeline/status` 自动恢复当前服务地址；只有连接成功的地址才写入浏览器存储。
支持文件夹 PDF 导入、资产多选、跨标准指标多选、模型和 Top N 配置、成功 OCR/IR 复用。
计划保存在同一 SQLite 的 `pipeline_plans` 表，只派发现有队列任务，不直接运行模型。
子步骤按 OCR → IR → 各标准包抽取推进；派发键唯一，后端重启可恢复；一个报告失败不阻止其他报告。
暂停只停后续派发，当前步骤完成；重试由用户显式选择，不对失败步骤无限重试。
密钥不持久化；后端重启后须具备相应环境凭证。旧编译标准退役后，排队抽取使用入队快照。
计划引用的终态队列记录保留，不由普通队列历史清理删除。
OCR、IR 构建/返修/复核重试以及定向抽取/续跑都通过 `加入任务` 进入同一个持久化
SQLite 队列。单 worker 每次只运行一个隔离进程，排队任务可拖动排序；终止运行任务
会停止整个子进程组，避免模型进程继续占用统一内存。
“单 worker”只限制同时执行数，不限制提交数。OCR、Document IR、复核/返修和定向抽取
按钮只在入队 HTTP 请求期间短暂禁用，入队成功后立即恢复，可继续选择其他报告或任务
加入队列。

三个任务页面的输入互不借用页面状态：Document IR 页面直接浏览全部 OCR Package，
不需要先去 OCR 页面选择或打开某个运行；定向抽取页面直接浏览全部 Document IR
Revision，不需要先去 Document IR 页面打开版本。选择卡完整显示报告文件名、识别年份、
页数、OCR Provider 或 IR Schema/Revision、准入状态、生成时间和完整 Run ID；当前选中项
另有确认卡。搜索和状态筛选只影响当前页面的目录视图，不会修改其他页面的选择。

专用报告目录默认为仓库外一层的 `../../pdf`。其中
`report-catalog.json` 只记录 PDF 文件身份、大小、hash 和已完成 OCR run 索引，不保存
OCR/IR 业务内容。OCR 页面默认从该目录选择，并明确标识未处理和已处理报告。

前端是独立操作台，保留 OCR 上传、状态、Manifest、Markdown 和全部文件浏览，
并增加：

- 11 个后端节点对应的可视化链路；
- 当前阶段、总耗时、阶段耗时、逻辑模型调用、实际模型请求、同模型重试、
  成功/协议无效响应和当前模型任务的实时遥测；
- 全部左右跨页候选的本地分类、是否进入详细复核、判断信号和拼接图；
- OCR/IR 运行历史和 revision；
- readiness、Evidence 准入门槛和覆盖率；
- 页图、Paddle layout 坐标框和对象详情；
- 物理表格单元格、Table Graph 与跨页 LogicalTableIR；
- 图片/图表区域截图；
- 七牛云与本地 NuExtract3 的并列 Provider 选择及独立健康状态；
- 复合 Review Task、每一轮模型调用、Reviewer 结果和模型健康状态；
- Atomic Patch、修正前后 diff、本地 Guard、独立 Verifier 和最终决策；
- 将“人工内容判断”“自动修复待处理”“非阻塞增强”“已解决”分开的审核收件箱；
- IR 复核检查页首先提供跨报告的“未完成复核任务表”，只汇总每条 Lineage 最新
  revision 的当前待办。可按报告/Task/Target 搜索，按任务归属和 Evidence 影响筛选，
  点击整行直接载入对应报告、IR revision、页码、证据和原有决策面板；已晋升版本的
  旧父包会自动清理，不会重复制造当前待办。
- 将不可继续自动重试的系统阻塞单独放入“系统修复”队列，并展示失败类别、指纹、
  最近一次事务和可执行动作；
- 每项只展示一个明确问题、风险、三步证据清单、为何需要人、原页/局部图、
  Guard 状态和 Verifier 分歧；
- 自动联动页面与表格浏览器，支持单任务/全部阻塞任务续跑；
- 支持显式运行全部非阻塞增强；也允许操作员把某项可选增强标记为
  `accepted_nonmaterial_difference`。该动作只关闭可选队列，不会把候选 Spread
  或图表语义伪装成已经验证；
- 只允许接受通过 Guard 的 `human_required` 补丁；拒绝补丁不会误关闭任务，
  人工必须再明确“保留当前 IR”或发起 Targeted Repair，判断依据为可选；
- `document_ir_output` 全部中间文件预览。
- OCR/IR 文件按 Package 入口、输入、服务商原件、可读内容、观察、Canonical、
  图像、质量、复核、完整性和兼容导出分组浏览；切换后端会清空旧上下文，
  不接受上一个后端的迟到响应。
- OCR、Document IR 和复核表单保留全部 Provider、回退、渲染、父版本、目标范围与
  临时凭证选项，但提交语义统一为“加入任务”。
- 报告资产同时展示 `/pdf` 原文件目录和 `document_id -> OCR run -> 当前最佳 IR`
  产物树，并标识“当前唯一保留版”。
- 报告资产展示包/状态完整性、文件数量、占用空间、父子修订和 Evidence 准入，
  并可在不进入文件系统的情况下预览每个中间文件。
- 清理操作先生成依赖计划。运行中任务、仍被 IR 使用的 OCR、仍有子修订的 IR
  默认禁止删除；执行前查看级联范围和下游依赖警告，再单击确认即可，无需输入口令。
- 批量清理只纳入失败、中断或不完整且未运行的记录；包目录、任务状态和上传副本
  作为同一事务移动，失败时回滚，审计记录保存在不可变产物之外。
- 定向抽取页保留 IR/标准包/指标及三种模型开关；有效标准包同时以常显卡片和原生
  下拉框呈现。E1-5、E1-6、E2-4 卡片之间切换时，各包已经勾选的细分指标持续保留，
  卡片与跨标准总览分别显示每包和全局选中数。一次提交通过批量队列接口原子创建
  多个相邻任务：共享同一个 IR 与模型配置，但每个标准包仍固定自己的 version、digest
  和 Result Bundle，不能把不同标准合同混成一个后端 job。标准目录校验失败数也会显示。
- 抽取结果页保留合同完整性、下载、搜索筛选、结构化事实、证据、Guard、检索、
  Packet/模型诊断和全部中间文件；空列表会同时核对任务 SQLite 与 Result Bundle
  目录并显示真实计数/路径，请求失败不再静默伪装成“尚无记录”。
- Document IR 与定向抽取各自拥有独立、版本感知的输入目录；OCR 浏览历史、IR 检查器
  和任务输入选择之间不再存在隐式联动。

统一控制面 API：

```text
GET  /api/pipeline/tasks
PUT  /api/pipeline/tasks/order
POST /api/pipeline/tasks/{task_id}/cancel
GET  /api/pipeline/tasks/{task_id}/events
GET  /api/pipeline/status
POST /api/pipeline/tasks/ocr
POST /api/pipeline/tasks/document-ir
POST /api/pipeline/tasks/targeted-extraction
POST /api/pipeline/tasks/targeted-extraction/batch
POST /api/pipeline/tasks/targeted-extraction/{job_id}/resume
GET/POST/DELETE /api/report-assets
```

原 OCR、Document IR、定向抽取 API 和 CLI 保留，供独立后端集成与迁移使用；统一前端
只走新的队列入口，避免三条重模型链路在本机并发。

## 密钥

前端可以选择本地 NuExtract3 或七牛，并临时填写 PaddleOCR 和七牛 API Key。
本地模式不需要 API Key；密钥只随请求发送，不写入产物。
也可以放到被 Git 忽略的 `document_ir_extract/.env.local`：

```text
PADDLEOCR_VL_API_TOKEN=...
PADDLEOCR_VL_TRUST_ENV_PROXY=false
ESG_V2_OCR_PDF_CANVAS_NORMALIZATION=true
ESG_V2_OCR_PROVIDER_MAX_CANVAS_POINTS=1200
ESG_V2_OCR_PROVIDER_MAX_LOCAL_FILE_BYTES=104857600
ESG_V2_OCR_PROVIDER_MAX_PDF_PAGES=1000
LOCAL_PADDLEOCR_HEARTBEAT_SECONDS=15
LOCAL_PADDLEOCR_STALL_TIMEOUT_SECONDS=600
LOCAL_PADDLEOCR_JOB_TIMEOUT_SECONDS=7200
QINIU_API_KEY=...
QINIU_BASE_URL=https://api.qnaigc.com/v1
QINIU_VLM_MODEL=...
ESG_V2_REVIEW_PROVIDER=local_nuextract
NUEXTRACT_MODEL_PATH=~/Desktop/model/NuExtract3-mlx-4bits
NUEXTRACT_RUNTIME_PYTHON=~/Desktop/model/.runtime/venv/bin/python
NUEXTRACT_TIMEOUT_SECONDS=900
ESG_V2_MAX_VLM_REVIEWS_PER_IR_RUN=0
ESG_V2_MAX_BLOCKING_VLM_REVIEWS_PER_IR_RUN=64
ESG_V2_MAX_OPTIONAL_VLM_REVIEWS_PER_IR_RUN=64
ESG_V2_REVIEW_COMPLETENESS_MODE=true
ESG_V2_PDF_RENDER_TIMEOUT_SECONDS=1800
```

PaddleOCR 客户端默认不继承操作系统或进程环境代理，避免通用代理对百度 OCR
域名造成 TLS 中断。只有部署环境明确要求 PaddleOCR 走代理时，才将
`PADDLEOCR_VL_TRUST_ENV_PROXY` 设置为 `true`。

PDF 输入阈值全部可配置。默认把供应商输入的最长页边控制在 `1200` PDF points，
本地文件上限为 `100 MiB`、页数上限为 `1000`；账号或 API 版本若采用更低限制，
部署时应收紧对应变量。阈值只控制 OCR 输入适配，不修改原 PDF。

`QINIU_VLM_MODEL` 可以填写逗号分隔的固定降级链。未设置时，系统从 `/models`
建立已批准且未退役的视觉模型候选顺序，并交错不同模型家族。2026-07-23
官方目录与实时 `/models` 核对后的候选包括：

- `qwen/qwen3.5-plus`
- `doubao-seed-2.0-pro`
- `qwen3.5-397b-a17b`
- `stepfun/step-3.7-flash`
- `moonshotai/kimi-k2.6`、`moonshotai/kimi-k2.5`
- `doubao-seed-2.0-mini`
- `minimax/minimax-m3`
- `moonshotai/kimi-k3`

实际运行仍以实时 `/models` 返回为准。网络/5xx、模型输出协议错误和账号额度
分别计数并使用独立策略：网络连续失败 2 次熔断 15 分钟，协议连续失败 3 次
熔断 5 分钟，账号级额度错误立即停止本次跨模型重试。每个模型的健康状态会原子
写入 `document_ir_output/.state/model-health.json`，后端重启后仍保留熔断窗口和
失败计数；该文件只保存模型运行元数据，不保存密钥、报告文字或模型原始回复。
前端模型状态会分别展示三类失败。七牛官方模型目录见
<https://www.qiniu.com/ai/models>，API 接入说明见
<https://developer.qiniu.com/aitokenapi/13379/real-time-ai-interface-api>。
本地视觉输入会压缩为七牛建议的 JPEG data URI；服务器阶段优先使用 Kodo/CDN
地址，避免大体积内联传输。

本地 NuExtract3 运行在独立 MLX 虚拟环境中，由后端管理常驻 worker，首次真实调用
才加载权重，后续任务复用同一进程。它接收与七牛相同的 ReviewPlan、上下文和页图，
返回相同的 `ReviewerPayload` / `VerifierPayload`；调用结果仍写入
`review/calls/model-calls.jsonl`，候选仍必须经过同一 Patch Guard、事务与版本冻结。
因此后续完全关闭七牛只需选择 `local_nuextract` 或设置
`ESG_V2_REVIEW_PROVIDER=local_nuextract`，不需要改 Document IR 或下游消费者。

## 测试

```bash
cd ESG_DATA_PLATFORM/ExtractESG/document_ir_extract/backend
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

当前测试覆盖页图渲染、坐标映射、Paddle raw layout、结构关系、Table Graph、
区域截图、模型路由、自动确认、自动修正、云故障延期、双模型分歧、Patch Guard、
受限表格插行、人工子版本、Targeted Repair、Package v1 路径/哈希校验、OCR
脱敏与原始响应保真、PaddleOCR 代理隔离、旧版 IR 图片迁移和 FastAPI 中间视图。
当前本地自动化测试覆盖确定性伪表格退役、
非标准模型回复归一化、OCR 长于原生文字时不误路由、视觉区域不误绑侧边正文、
退役快照持久化、非表格视觉对象重建、abstain 跨模型家族复核、拒绝补丁后必须
显式关闭人工任务、跨缝最后一列续接、逻辑单元格 source mapping、独立补丁事务、
非重试型系统阻塞分流、持久化模型健康状态、Package/API 逻辑表读取、跨目标
失败指纹隔离、`needs_repair` 兼容、Spread 原子组合事务、Figure 归一化/crop
像素坐标换算、非阻塞增强的不可变审计式关闭、纯视觉跨页本地终结、Verifier
常见别名协议归一化，以及运行阶段/真实模型请求遥测计数。
PDFium 渲染在受监督子进程中执行，逐页显式释放 image、bitmap 和 page；页数
进度会回写到任务状态。原生渲染器若超时、异常退出或收到 `SIGSEGV`，只会使该
Document IR 任务失败并留下明确原因，不会再拖死 FastAPI 后端。渲染超时可通过
`ESG_V2_PDF_RENDER_TIMEOUT_SECONDS` 调整。
真实 OCR/Document IR/VLM 全链路测试由用户主动发起；开发修改后默认只运行本地
单元测试和静态检查，随后等待用户生成新结果再做只读分析。

## 当前边界

本模块当前终止于 Document IR。所有下游流程将在新方案确定后另行建设。
