# ESG Evidence Hub：Document IR 与定向抽取

本目录是从零开始的新主线实现，当前完成四块：

1. `PDF -> PaddleOCR-VL API -> ocr_output`；
2. `ocr_output + 原 PDF -> validated/versioned Document IR`。
3. `admitted Document IR -> deterministic Evidence Inventory`；
4. `Evidence Inventory + 标准任务表 -> local-first Targeted Recall -> guarded JSONL/XLSX`。

本模块不负责报告爬取、Kodo 文件索引、公司/年份/行业主数据或全局报告登记。
本地上传、路径和 URL 只是开发输入方式。当前只实现“按标准任务表定向抽取”
这一条候选事实路径；PDF-first Full Harvest、规范化、标准映射复审和正式发布库仍未建设。

## 当前真实链路

```text
PDF
  -> PaddleOCR-VL API
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
  -> horizontal spread detection (adjacent-edge geometry + pixel seam continuity)
  -> side-by-side spread artifact + SpreadIR candidate
  -> local spread preflight (content crossing / visual continuity / uncertain)
  -> visual-only continuity terminates locally without a cloud call
  -> typed quality routing (one risk -> one review question)
  -> deterministic candidate screening
  -> blocking-first dynamic Qiniu review queue
  -> Provider-aware pacing (RPM backoff / TPD run circuit)
  -> resume Guard-passing Verifier checkpoints before new Reviewer work
  -> classify object type before structure reconstruction
  -> minimum AtomicPatch candidate
  -> local Patch Guard
  -> independent Qiniu verifier (different model family)
  -> at most two feedback-guided automatic repair rounds (three reviewer rounds total)
  -> failure-classified PatchTransaction
  -> accepted local patch application
  -> human only for bounded semantic ambiguity
  -> corrected physical structure + LogicalTableIR rebuild
  -> readiness validation
  -> immutable Document IR revision
  -> can_build_evidence gate
  -> deterministic Evidence Inventory
  -> Disclosure Catalog (physical/logical table, paragraph and figure groups)
  -> task-template adapter + RequirementExecutionSpec v2 compiler
  -> specialized rule / conditional rule / generic local rule
  -> required slots (concept, value, unit, period, scope, dimensions, statement)
  -> deterministic numeric / period / unit-family / statement / index feature tools
  -> SQLite FTS5 / BM25 / trigram local retrieval
  -> optional local multilingual embedding retrieval
  -> concept / dimension / topic / combined retrieval lanes
  -> reciprocal-rank fusion + disclosure-group structural reranking
  -> independent per-candidate Requirement Verifier
  -> multiplicity policy (single / all instances / dimension groups / table bundle)
  -> report-search coverage ledger and top-k truncation abstention
  -> conditional applicability rules + independent result Guard
  -> immutable Targeted Recall package
  -> JSONL canonical answers + template-preserving XLSX export
```

PaddleOCR-VL 是第一主解析器。七牛模型只处理质量路由选出的复杂表格、
数据图表和冲突区域。普通插画、装饰和已经稳定建模的流程图不会因为“页面有图”
就创建模型任务。模型结果不能直接覆盖 OCR：主审结果必须先形成局部候选，
通过本地 Guard，再由不同模型家族复核。云 API、协议、预算或补丁校验失败进入
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
  provider/submit-response.json
  provider/poll-events.jsonl
  provider/result.jsonl             # PaddleOCR-VL 原始返回
  observations/pages/index.json
  observations/pages/page-0001.json
  content/pages/page-0001.md
  artifacts/index.json
  artifacts/images/page-0001/
  artifacts/layouts/page-0001.jpg
  integrity/files.json
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

Evidence Inventory：

```text
evidence_output/<run_id>/
  manifest.json
  inventory/atoms.jsonl
  inventory/index.json
  quality/validation-report.json
  integrity/files.json
```

Targeted Recall：

```text
targeted_fill_output/<run_id>/
  manifest.json
  input/task-template.xlsx
  input/task-set.json
  requirements/profiles.jsonl
  catalog/disclosures.jsonl
  retrieval/queries.jsonl
  retrieval/hits.jsonl
  retrieval/local-index.sqlite3
  assessment/results.jsonl
  assessment/verifications.jsonl
  evidence/selected-packets.jsonl
  quality/validation-report.json
  quality/search-coverage.jsonl
  audit/local-inferences.jsonl
  audit/cloud-calls.jsonl
  final/answers.jsonl
  exports/filled-result.xlsx
  integrity/files.json
```

`final/answers.jsonl` 是正式结果源，Excel 是模板保持型派生导出。`found` 和
`not_applicable` 必须绑定披露组、Evidence Atom、原文和槽位覆盖；定量正向结论
还必须形成 `FactInstance`。索引页只能参与路由，不能替代正文事实。候选达到上限
时不得输出确定的 `not_found`；`not_found`、`uncertain`、`system_failed` 相互独立。
默认云预算为零，Targeted 模块不导入七牛适配器。

OCR 和 IR 的运行状态不属于不可变产物，统一写入 `.local/jobs/`。所有包内
路径均为相对路径；系统生成页文件统一从 `page-0001` 开始。完整命名、编号、
权威数据与审计分层规则见 `docs/ARCHITECTURE.md` 的
`Product Artifact Package Contract`。
首次构建、Targeted Repair 和审核重跑共用同一实时遥测合同；运行结束后，包内
`review/calls/model-calls.jsonl` 仍是不可变的最终模型调用审计依据。

每次 Package v1 写入都会自动检查 manifest 类型与入口、严格 run ID、目录逃逸、
漏文件/多余文件、字节数和 SHA-256。新建包不接受随意 run ID；旧版平铺产物保持
只读兼容，不会被原地改写。已存在的 run 目录也不能复用或覆盖。

`manifest.json` 中的 `readiness` 为：

- `ready`
- `ready_with_warnings`
- `auto_review_pending`
- `repair_required`
- `review_required`
- `failed`

只有 `can_build_evidence=true` 的 revision 才允许进入 Evidence Inventory。

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

默认 `ESG_V2_REVIEW_COMPLETENESS_MODE=true`：调度器把同轮所有阻塞任务和
非阻塞 Spread/Figure 增强纳入明确执行批次，默认不再制造 `scheduler_deferred`
积压。`ESG_V2_MAX_VLM_REVIEWS_PER_IR_RUN=0` 表示无全局硬上限；只有显式关闭
完整模式时，阻塞和可选队列才分别受 `ESG_V2_MAX_BLOCKING_VLM_REVIEWS_PER_IR_RUN`
与 `ESG_V2_MAX_OPTIONAL_VLM_REVIEWS_PER_IR_RUN` 约束。显式 targeted review 仍只处理
指定目标。

## 左右跨页如何处理

物理页永远不会被合并或删除。`HorizontalSpreadBuilder` 只扫描相邻页，并同时要求：

- 左页对象触达右装订边、右页对象触达左装订边；
- 两侧对象在垂直方向对齐；
- 表格、图片、图示或多段截断文字提供结构信号；
- 两页装订缝附近存在有效像素连续性；
- 可用时，印刷页码满足偶数左页和相邻奇数右页。

通过本地筛选的页对会生成一个 `SpreadIR` 和
`artifacts/spreads/spread-pXXXX-pYYYY.png`。随后 `SpreadPreflightClassifier` 根据
缝线两侧已经存在的 Block、Table、Figure 和语义类型区分三类：两侧存在表格或
信息对象的是 `content_crossing`；只有图片版式连续、没有跨缝文字或表格语义的是
`visual_continuity`；本地证据不足的是 `uncertain`。纯视觉连续页直接在本地终结，
保留拼接图和分类依据但不创建七牛任务。只有内容跨页或不确定页才由七牛视觉复审
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
POST /api/document-ir/jobs/{run_id}/patches/{patch_id}/decision
POST /api/document-ir/jobs/{run_id}/review-tasks/{task_id}/decision
POST /api/document-ir/jobs/{run_id}/repair
GET  /api/document-ir/jobs/{run_id}/human-review/inbox
POST /api/document-ir/jobs/{run_id}/reviews/retry
GET  /api/models/status

POST /api/evidence/build
GET  /api/evidence/runs
GET  /api/evidence/runs/{run_id}

GET  /api/targeted-fill/capabilities
GET  /api/targeted-fill/templates
POST /api/targeted-fill/plan
POST /api/targeted-fill/jobs
GET  /api/targeted-fill/jobs
GET  /api/targeted-fill/jobs/{run_id}
GET  /api/targeted-fill/jobs/{run_id}/manifest
GET  /api/targeted-fill/jobs/{run_id}/requirements
GET  /api/targeted-fill/jobs/{run_id}/requirements/{requirement_id}
GET  /api/targeted-fill/jobs/{run_id}/results
GET  /api/targeted-fill/jobs/{run_id}/validation-report
GET  /api/targeted-fill/jobs/{run_id}/artifacts
GET  /api/targeted-fill/jobs/{run_id}/export
```

## 前端启动

```bash
./scripts/run_frontend.sh
```

打开 `http://127.0.0.1:18081`。

也可以同时启动前后端：

```bash
./scripts/run_all.sh
```

Document IR 页面为 `http://127.0.0.1:18081/index.html`；按表抽取页面为
`http://127.0.0.1:18081/targeted.html`。两个页面共享 Backend URL，但业务控制器和
页面状态独立。关闭前端不会影响 CLI 或 HTTP API。

## 按表抽取

纯本地严格模式不需要安装任何模型：

```bash
esg-v2 targeted plan --template /path/to/task.xlsx
esg-v2 evidence build --ir-run-id ir-...
esg-v2 targeted run --ir-run-id ir-... --template /path/to/task.xlsx --mode local_strict
```

本地语义增强是可选依赖，不会自动下载模型：

```bash
pip install -e '.[semantic]'
esg-v2 targeted run --ir-run-id ir-... --template /path/to/task.xlsx \
  --mode local_semantic --allow-local-model-download
```

模型不可用时运行会显式记录 `fallback` 并改用 `local_strict`，不会调用云 API。
当前首个模板适配器支持 ESRS 43 条试填表和同列结构的任意任务条数。抽取核心只
依赖通用 `RequirementExecutionSpec v2`：当前 43 条中 28 条条件式任务走明确的
政策/行动/目标规则，15 条定量任务全部命中专门规则。未来未命中专门规则的新条款
会显式标记为 `generic_local_rule`，本地推断概念、数值、单位、期间和维度槽位并在
预检中提示，不会假装已经有专门规则。其他工作簿格式通过新模板适配器接入。

macOS 桌面入口为 `启动ESG-v2前后端.command`。双击后会检查两个服务、
复用已经运行的正确实例、等待健康检查通过并自动打开前端。启动日志保存在
`.local/logs/`；关闭启动器终端会停止由本次启动器拉起的服务。

前端是独立操作台，保留 OCR 上传、状态、Manifest、Markdown 和全部文件浏览，
并增加：

- 11 个后端节点对应的可视化链路；
- 当前阶段、总耗时、阶段耗时、逻辑模型调用、真实 HTTP 请求、同模型重试、
  成功/协议无效响应和当前模型任务的实时遥测；
- 全部左右跨页候选的本地分类、是否进入详细复核、判断信号和拼接图；
- OCR/IR 运行历史和 revision；
- readiness、Evidence 准入门槛和覆盖率；
- 页图、Paddle layout 坐标框和对象详情；
- 物理表格单元格、Table Graph 与跨页 LogicalTableIR；
- 图片/图表区域截图；
- 复合 Review Task、每一轮模型调用、Reviewer 结果和模型健康状态；
- Atomic Patch、修正前后 diff、本地 Guard、独立 Verifier 和最终决策；
- 将“人工内容判断”“自动修复待处理”“非阻塞增强”“已解决”分开的审核收件箱；
- 将不可继续自动重试的系统阻塞单独放入“系统修复”队列，并展示失败类别、指纹、
  最近一次事务和可执行动作；
- 每项只展示一个明确问题、风险、三步证据清单、为何需要人、原页/局部图、
  Guard 状态和 Verifier 分歧；
- 自动联动页面与表格浏览器，支持单任务/全部阻塞任务续跑；
- 支持显式运行全部非阻塞增强；也允许操作员写明依据后，把某项可选增强标记为
  `accepted_nonmaterial_difference`。该动作只关闭可选队列，不会把候选 Spread
  或图表语义伪装成已经验证；
- 只允许接受通过 Guard 的 `human_required` 补丁；拒绝补丁不会误关闭任务，
  人工必须再明确“保留当前 IR”并写依据，或发起 Targeted Repair；
- `document_ir_output` 全部中间文件预览。
- OCR/IR 文件按 Package 入口、输入、服务商原件、可读内容、观察、Canonical、
  图像、质量、复核、完整性和兼容导出分组浏览；切换后端会清空旧上下文，
  不接受上一个后端的迟到响应。

## 密钥

前端可以临时填写 PaddleOCR 和七牛 API Key。密钥只随请求发送，不写入产物。
也可以放到被 Git 忽略的 `.env.local`：

```text
PADDLEOCR_VL_API_TOKEN=...
PADDLEOCR_VL_TRUST_ENV_PROXY=false
QINIU_API_KEY=...
QINIU_BASE_URL=https://api.qnaigc.com/v1
QINIU_VLM_MODEL=...
ESG_V2_MAX_VLM_REVIEWS_PER_IR_RUN=0
ESG_V2_MAX_BLOCKING_VLM_REVIEWS_PER_IR_RUN=64
ESG_V2_MAX_OPTIONAL_VLM_REVIEWS_PER_IR_RUN=64
ESG_V2_REVIEW_COMPLETENESS_MODE=true
ESG_V2_PDF_RENDER_TIMEOUT_SECONDS=1800
```

PaddleOCR 客户端默认不继承操作系统或进程环境代理，避免通用代理对百度 OCR
域名造成 TLS 中断。只有部署环境明确要求 PaddleOCR 走代理时，才将
`PADDLEOCR_VL_TRUST_ENV_PROXY` 设置为 `true`。

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

## 测试

```bash
cd backend
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
常见别名协议归一化，以及运行阶段/真实模型请求遥测计数。Targeted Recall 测试另
覆盖 IR 准入硬门槛、Evidence 溯源、零云调用、条件式条款适用性、表格必要维度、
目录索引不得冒充正文、相近指标排除、交叉维度不得由分散行伪造、变长任务表、未知
条款通用编译、多披露实例保留、召回截断禁止假阴性、Package v2 完整性、CLI/API
独立运行和 Excel 仅修改允许列。当前完整后端测试为 132 项。
PDFium 渲染在受监督子进程中执行，逐页显式释放 image、bitmap 和 page；页数
进度会回写到任务状态。原生渲染器若超时、异常退出或收到 `SIGSEGV`，只会使该
Document IR 任务失败并留下明确原因，不会再拖死 FastAPI 后端。渲染超时可通过
`ESG_V2_PDF_RENDER_TIMEOUT_SECONDS` 调整。
真实 OCR/Document IR/VLM 全链路测试由用户主动发起；开发修改后默认只运行本地
单元测试和静态检查，随后等待用户生成新结果再做只读分析。

## 下一阶段

当前 Targeted Recall v2 本地语义契约与可解释链路已完成，下一阶段建设：

- Full Harvest 与覆盖账本；
- 面向全量抽取和跨报告复用的 Evidence Packet/检索服务增强；
- 可发布级数值、单位换算、期间与组织范围规范化；
- Concept 与 Requirement Mapping；
- 独立事实验证、人工审核和 Publication。
- 本地语义模型基准集、可选 reranker/NLI 和跨运行向量缓存；
- 其他 ESRS/HKEX/GRI/用户自定义任务表适配器。
