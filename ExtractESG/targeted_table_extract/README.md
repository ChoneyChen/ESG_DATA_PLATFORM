# ExtractESG 定向抽取链路

项目级现状、跨模块关系和新对话交接见仓库根目录 [`../../README.md`](../../README.md)。
本文只维护定向抽取的详细运行和产物说明。

本模块位于 Document IR 下游，只读消费一个完整 evidence-ready 或显式带 limited policy 的 Document IR revision，
并按照已编译的标准要求包，把报告中的直接披露转成可追溯、多行、机器可用的 Core records。

它不生产或修改 OCR、PDF 与 Document IR，也不把前端表格当成权威数据。标准包定义“填什么”，
本模块负责“到哪里找、把哪些证据交给模型、怎样校验来源、怎样物化结果”。

默认服务：

- 操作台（ConsoleESG）：`http://127.0.0.1:18081/`
- 定向抽取后端：`http://127.0.0.1:18180/`
- API 文档：`http://127.0.0.1:18180/api/docs`

## v2 报告事实 Agent

当前代际为 `report-fact-agent-v9` / `report-semantics-v8`（版本常量定义在 `esg_targeted/evidence/disclosure.py` 与 `esg_targeted/models/report_semantics.py`，测试直接引用常量）；现代2.0/2.1报告主题统一由中立来源读取后独立映射，长正文保留原span逐窗续读，部分返回数值继续留在阅读前沿。下文早期直接填表说明保留兼容历史含义，不能用来判断当前报告模式的路由。当前实现与限制见 本阶段记录（已归档到 Git 历史），业务操作验收由用户执行。

归档后的未引用中间缓存可使用原生服务的 `GET /api/report-sessions/{native_session_id}/intermediates/plan` 核对，`POST .../execute` 按固定计划回收，`POST .../restore` 恢复。核对要求read/admin权限，执行及恢复要求admin权限；请求固定计划标识、预期修订及理由，操作员由服务端凭据解析。只回收允许列表内、不在来源引用闭包的缓存，保留完整SQL来源guard；文件移到 `.lifecycle/intermediates` 并保存可恢复日志。缓存未恢复时不允许恢复父记录继续提取。此接口不等同于删除来源或重跑旧任务。

现状（2026-10-03 协议与阶段成果修复后）：报告级 `ReportSession` 固定 IR revision，汇集跨标准要求并创建各包独立投影 job。
同一 Session 按源对象、证据内容、解释版本及 provider/model 共享标准无关理解；各包保留各自标准快照、映射和 Core 合同。
新请求使用 `roles.semantic_brain`（默认本地 Qwen）负责导航、理解与映射，`roles.specialist`（默认 NuExtract）承担成熟直接填表；七牛须显式授权。
旧单包请求未传 `roles` 时继续使用原 `semantic_provider`。本地重模型分阶段释放；Qwen 和 NuExtract 共享运行时而各自保留 Provider 协议。
需求与验收范围见 系统重塑方案（已归档到 Git 历史）。

标准包格式 `2.0` 声明披露主题、指标角色及类型化关系。自动模式合并候选对象目录，让模型依据定义和具体缺口选择阅读、改写查询补查、单独重新映射或结束。共享理解的目标是正确、连贯地理解报告事实，不以减少调用数量代替质量。

理解与映射使用两个小合同。理解输出 `rows / columns / cell_overrides`，区分定量、原文断言和结构上下文；字段使用已定义身份轴及开放 `extensions`，跨行身份与断言片段必须引用真实证据。映射输出 `matches / coverage`，独立判断适用指标及方法列；真实断言的 `columns=[]`，负对象裁决无需伪造 source_id。Pydantic 接收类型直接生成 Provider JSON Schema，工具动作按 tool 区分字段；非法引用局部隔离，合法兄弟结果保留。后端直接投影到 SemanticAssignment，保留明确 source_fact_id/Core 绑定。

理解按连续物理行、输入和输出预算切分；结构行完成后不会制造事实或无限重读。可靠 IR 单元格保留精确 raw 值，模型英语摘要或不同数值不能覆盖它；不可靠网格只有实际给图、作用域相符且 bbox 可靠时可视觉纠正。期间/实体/单位缺证据时保留 unknown 与质量问题，不从文件名、标准固定值或邻列猜补。LogicalTable 继承表头及跨页阅读上下文，证据仍指向物理页和 cell。

`extraction_mode=auto` 对 v2 多项主题、或有明确事实粒度的现代指标启用 Agent；同族只选一项也可启用。`per_metric` 保留兼容对照。初始共享预算提示 `shared_object_budget=1..8`（默认 3）、每指标候选 `retrieval_object_top_n=1..5`、每主题工具动作 `agent_max_steps=1..24`（默认 8）分别显示。shared budget 是导航提示，实际阅读受动作预算约束。工具包括 `inspect_object / read_object_context / map_object / search_report / request_local_repair / finish`。局部 repair 只保存有界证据缺口，由控制面排队修复并显式接纳新版 IR，不持抽取执行槽等待。依赖自动补齐仅用于已声明策略；内部操作数及其证据保存在独立 archive，不扩大用户输出选择。直接披露优先，派生保留公式版本、操作数身份和明确依赖关系。

E2-4_02/03/04 是三个单项成熟主题，沿用介质检索和直接填表路线。旧包及旧结果保持可读。Agent 的调用、理解、映射和主题状态保存在 `agent/`；逐指标投影在 `disclosures/`。最终导出 `results/source-facts.json`、`metric-source-mappings.json`、`disclosure-views.json`，完整 JSON 带这些可选集合。展示引用不会生成额外 Core 事实。

没有最终 Bundle 时，检查 API 读取有效 checkpoint 和源候选，返回 `stage_summary`、局部 gaps、质量与证据。已处理、处理中、已进入未完成、未执行分别计数，未执行不表示未披露，来源候选不自动批准为 Core。派生另有 `dependency_status/dependency_blocks`：来源、映射、身份、单位或证据不足显示具体阻塞原因，已直接披露的有效事实保持原有结论。

操作台的提取创建器统一读取设置中的角色和预算；条款成果页展示真实调用阶段、工具、覆盖及具体缺口。高级 IR 复核仍支持本地 NuExtract、Qwen 与七牛。E1 v2 保留源行主题，后端组织期间、实体及口径，前端读取返回顺序。完整设计、PDF/OCR/IR 审核取舍与验收边界见 [REPORT_FACT_AGENT.md](docs/REPORT_FACT_AGENT.md)。真实业务验收由用户发起。

## 兼容直接填表路径

```text
Evidence-ready Document IR + Compiled Standard Package
                         |
                         v
Evidence Inventory + Literal Harvester
                         |
                         v
Query Intent / FactPattern + BM25 + Qwen3 Embedding + RRF
                         |
                         v
Local Evidence Sufficiency Gate
              /                              \
     全报告无可信证据                    存在合理证据
            |                                  |
            v                                  v
  可审计 not_found                  Evidence Selection
                                                |
                                                v
                                  Coherent Evidence Regions
                                      /                    \
                             本地 NuExtract3          七牛云 VLM
                                      \                    /
                                                v
                                  物理行优先语义填表 JSON
                                                |
                                                v
                                宽松 Grounding / Contract Guard
                                                |
                                                v
                           Core records + JSONL + XLSX + CSV
```

### 1. Inventory 与本地字面量

Inventory 从 Document IR 读取正文、句子、表格行、表格单元格、表头路径、图表说明、坐标和 crop
引用。Literal Harvester 用本地代码识别数值、数量、百分比、年份、日期和单位；通用单位目录当前
覆盖质量、能源、GHG CO2e、货币与强度复合单位。它们只增加检索与溯源
线索，不决定指标归属，也不会预先制造最终业务事实。

### 2. 检索与充分性判断

查询由标准包的指标语义、元素、概念别名、上下位概念、排除概念和固定维度动态编译。BM25 负责
精确词与数字邻近，Qwen3 Embedding 负责语义召回，FactPattern 检查同一 block 或 table row 内能否
形成“主题 + 披露角色 + 合适数值”的组合。FactPattern 是排序和明确命中的信号，不是替代模型的
最终资格判定：词面组合不完整、但混合检索仍找到高排名且带数量的结构化对象时，Evidence Selection
会把它标记为 `semantic_model_review_fallback` 并送模型判断。只有完整扫描后既没有主题信号、也没有
可送审结构化对象时，才本地生成带扫描证明的 `not_found`。

检索匹配统一处理繁简体、Unicode、范围一/范畴一/Scope 1 等编号写法和 LaTeX `CO_{{2}}e`；这些
归一化只解决排版差异，不决定业务适用性。重复的完全相同语义组会在查询编译时去重，标准并未要求
报告逐字出现的“总量/Gross”等词不得被追加成额外硬门槛。

任务级 `Top N`（1–5，默认 3）限制的是独立证据对象总数，不是零散 span 数量。选择器先在该总预算内
组合主测量、方法上下文和对账上下文等标准包声明的证据角色，再以最终检索得分补足；不是把一个按
数字密度排序的列表机械截断。每个入选对象保存角色和候选强度，供检查页解释为什么进入模型材料。
一个对象可以是一张表、一个图或一个文本区域；命中一张表后仍保留其相关表头和连续行结构。
概念下位词会按标准包声明的适用上下文过滤，例如空气污染不会扩展 COD/BOD 水污染别名。
互斥介质的专属下位概念还会进入负向合同，因此只写 `COD` 而未重复写“废水”的表格行也不会进入
空气污染模型输入；该机制由概念关系动态编译，不依赖 E2-4 条款号硬编码。

### 3. Evidence Selection 与 Regions

`Evidence Selection` 是一个指标的完整审计证据选择。命中表格后会保留该表的可用行、单元格、
堆叠表头、坐标和精确 crop，不因模型窗口较小而提前丢掉整张表。

`Evidence Region` 是实际送模材料。一个 region 只属于一张表、一个图或一个文本区域；大表只按连续
行组和输出容量切分。每个 region 明确携带标准字段合同、原始证据、候选字面量、视觉来源和最大
输出行数，每次模型调用最多发送一张对应 crop。定量指标先对物理源行作一次适用性判断，再在
`values` 中展开该行的多个实体/期间单元格；行级字段与单元格级 value/period/entity 字段互斥。定性
指标使用独立 `assertions`，不再伪装成数值单元格。多个污染物、年份、公司、矿区、分项、总计或组成
项仍形成独立事实。全部 region 完成后只合并“同一物理语义组 + 同一 element/value”的重叠区域重复，
不会因为两个实体恰好披露相同数值而丢失其中一行。

### 4. 模型直接填表

2026-09-06：主证据之外增加独立小预算 `linked_context`，携带同页/邻页或全报告相关的方法、
脚注与边界说明；`L` 引用可追溯，不是额外数量任务。能源/GHG 量型的强语义数值对象和相邻行
不再被词面资格检查裁掉；污染物质量/介质选择保持已验证路径。

每个 `row_group` 增加 `metric_match=match|uncertain|different`，以及按需的
`interpretation_note/context_refs`；公共行字段在 `shared_fields`，各实体/期间值在 `values`。只有模型
认定 match 的有据行才按固定标准标签物化；其他已观察测量保留在决定和检查页。
易混淆指标还会收到同族兄弟指标的定义和固定范围，用模型语义比较防止同一位置法单元格同时进入
市场法；这不是关键词门槛或新增 Guard。`skipped_targets` 表达主动跳过的数字，`missing_context` 表达缺少的说明。正常停止不触发
“覆盖所有数字”的催补，仅真实输出截断继续写。不新增语义 Guard，不把未知方法的 Scope 2
直接同时标成位置法和市场法。

本地 `NuExtract3` 和七牛云 VLM 共用同一动态字段合同与结果结构，但按各自能力使用不同预算：

七牛返回 `account_billing_suspended` 时，适配器把它记录为账户级外部阻塞，当前任务立即
结束，不再对同一账户的后续指标重复请求，也不把接口错误写成指标 `not_found`、
`regions_unresolved` 或人工语义判断。只有服务商明确拒绝 `response_format` 时才尝试
兼容格式。统一队列保留业务状态：全部指标通过为 `completed`，部分指标通过为 `partial`，
启用语义填表、零指标通过且没有任何有效模型响应时为 `failed`。旧成果保持不可变，检查页会根据其保存的原始接口
错误解释历史 `account_billing_suspended`，账户恢复后可通过现有续跑入口重新执行。

- 本地通道使用较小的连贯 region 和最多一张对应图，降低统一内存压力与输出截断风险；
- 云端通道使用更大的 region 与更高输出预算，但仍坚持一次一个视觉对象、最多一张对应 crop；
- 两条通道都直接判断语义归属、读取表格、组合表头和单元格，并输出全部适用事实行；
- 固定值字段由标准包物化，不要求模型重复输出。

模型上下文显式给出 `value_family`、语义意图、固定范围、直接行主题和堆叠表头。语义意图中的词表仅
用于解释召回方向，不是逐词核对清单；模型负责区分总量/子总计/组成项、消费/生产、数量/比例/强度/
容量以及 Scope 1 与 Scope 1+2。局部规则不得因为宽表标题同时出现“排放量及密度”就预先裁掉正确行。

有专门实体字段的标准包，由模型优先填入 `reporting_entity`，适配器不再把相同表头强行复制到
`additional_breakdown`。无此字段的 E2-4 保留原有表头补全。遇到多层表头的明确结构矛盾，目标 cell
携带 `header_alignment_uncertain`：保留原文和完整 crop，由模型判读，不能用错位 IR 表头覆盖视觉结果。

模型字段来源采用短别名：

- `C`：本地已识别的精确字面量；
- `S`：Document IR span 中的精确可见字符串；
- `V`：模型从本次实际发送的 crop 直接读取的值，即使 OCR 或正则没有提前识别也可以使用。

### 5. Guard 的边界

Guard 不重新判断模型的 ESG 语义，不要求视觉值先被 OCR 或正则发现，也不会因为报告没有披露某个
上下文字段而丢弃已找到的主数值。缺少字段保留为空并记为警告。

Guard 只阻止确定性工程错误：未知任务、未知字段、未知行组、伪造来源、`C` 与原文不一致、`S`
不是可见子串、`V` 未绑定本次图片、跨表/跨行错误混配、定量行没有且仅有一个主值，以及违反
标准包明确基数合同的输出。这样既让模型承担语义和视觉工作，也守住可追溯结果不能凭空生成的底线。
模型把合法的单一 group 写成 `['G2']` 时，适配器只做无损单元素解包；多元素数组仍按合同错误保留。
相同错误输出连续重复时直接停止无效重试；连续两次返回 found/partial 但没有任何匹配 assignment 也
会停止。比较结构化内容本身，不增加输出哈希链；格式纠错只针对结构字段，不重做模型语义判断。

`not_found` 另行保存检索充分性：只有完整扫描且不存在可送审对象时才是
`not_found_after_complete_search`；Top N 有界送审无命中为 `not_found_after_bounded_search`；截断或充分性
不明为 `retrieval_uncertain`。后二者保持 partial/uncertain，不能伪装成完整搜索后的确定未披露。

Grounding 通过只代表来源与形状正确，不代表 ESG 语义已经人工确认。新结果分别记录
`source_validation_status`、`contract_validation_status`、`semantic_decision_status` 和
`display_readiness_status`，审核状态保持 `pending`；`auto_verified` 仅用于兼容旧 bundle。后台会审计
同一物理单元格是否被互斥的包固定维度同时使用（例如位置法与市场法），发现时保留原决定并标为待
联合复判。具有同一 `metric_family` 的冲突项会把兄弟指标定义、固定范围、同源事实和证据摘录合并为
一次族级模型调用；模型返回每个来源适用的指标集合，后端只按集合分发/隔离结果。复判原文写入独立
审计产物，原始逐指标决定不改写；模型不支持或仍不确定时继续进入人工复核，不新增业务语义 Guard。

所有指标完成后检查标准包声明的 `data_processing` 派生规则；只有目标指标同时声明 `depends_on` 与
`derivation_policy=reported_then_derived_when_dependencies_complete` 才实际执行。报告期、报告边界、报告
实体、所需维度和单位必须全部显式一致，直接披露目标值优先。派生事实使用 `value_origin=derived` 并
继承操作数 evidence 为 supporting evidence；身份缺失、单位不兼容或重复来源不能唯一解释时只写审计
原因。旧 E2-4/E1-5 包未声明新版执行策略，因此不会因本轮 E1-6 改动自动产生新事实。

## 启动

首次安装或依赖变化后：

```bash
cd ExtractESG/targeted_table_extract
./scripts/setup.sh
```

独立启动后端：

```bash
./scripts/run_all.sh
```

停止：

```bash
./scripts/stop_all.sh
```

桌面 `启动ESG统一工作台.command` 会启动或复用 Document/OCR 后端 `18080`、定向抽取后端
`18180` 和操作台静态入口 `18081`。三条重负载处理链路由统一控制面的单 worker 队列串行调度，业务逻辑
仍在各自后端中独立执行。

默认模型目录：

- `~/Desktop/model/NuExtract3-mlx-4bits`
- `~/Desktop/model/Qwen3-Embedding-0.6B-8bit`
- `~/Desktop/model/Qwen3.5-9B-4bit`（`local_qwen`，`QWEN_VLM_MODEL_PATH` 可覆盖）

新的有界 Agent 模型接口为 `generate_structured(context, instructions, template,
image_path, max_tokens)`：每次只处理一个明确的小问题。NuExtract 将字段合同编译为其原生
`verbatim-string/string/number/enum` 类型模板；Qwen 使用普通聊天及正式 JSON Schema，默认关闭
thinking；七牛使用 JSON Schema，并保留服务商明确拒绝格式时的兼容降级。三个 Provider 返回
同一 `(raw_output, telemetry)`，不共享供应商私有 prompt 协议。本地调用可上报加载、prefill、
生成 token 进度；执行进程外的 watchdog 负责原生计算不返回时的硬终止。Schema 的来源与接收合同相同；本地 Qwen 由生成后校验约束，云端是否接受 Provider Schema 记录在 telemetry。长度截断是 `generation_incomplete`，拆小任务并保留成功块，不把巨量输出交给格式修复。

已有 E2-4 兼容 `decide` 路径及其提示词保持不变；新小任务接口不会偷偷重写历史产物或增加语义 Guard。

七牛 API Key 只通过控制面的内存 Secret Vault 注入 worker，不写入任务请求、SQLite、日志、文档
或 Result Bundle。

## 无界面运行（只跑 API 与命令行）

```bash
./scripts/run_backend.sh

$HOME/Desktop/model/.runtime/venv/bin/esg-targeted catalog
$HOME/Desktop/model/.runtime/venv/bin/esg-targeted run \
  --ir-run-id <IR_RUN_ID> \
  --package-id esrs.2023-set1.e2-4 \
  --package-version 2.0.0 \
  --metric esrs.2023-set1.e2-4.dp02
```

CLI 与 API 默认提交统一流水线队列，不依赖浏览器。`TargetedExtractionWorkflow` 由队列隔离 worker 调用。
目标后端与 Document/OCR 控制面必须同时可用；队列不可用时明确报错，不启动第二条独立重任务派发器。

### 进程监督、截止时间与恢复

统一队列在隔离 worker 中执行真实抽取，不把 MLX 放进 HTTP 服务线程。每个任务由独立轻量 watchdog
监督；`supervised_model_call` 在进入完整模型调用前登记硬截止时间，因此即使 native prefill/eval
不返回 token，取消与截止仍能生效。截止保留已有 checkpoint，并记录 `interrupted` 和实际调用原因；
不是指标“未披露”，也不是 ESG 语义 Guard。

后端重启不会把仍活着的同一 worker 清空或重复启动。任务使用 PID 与进程出生时间确认所有者；
活任务继续接管观察，死亡任务才进入可恢复中断。仍存活的 worker 未结束时不允许续跑或删除其产物。
队列执行输出写入持久 `worker.log`，不依赖 Web 服务线程承载计算。原来的前台启动器关闭
仍会停止其拥有的 Web 服务；在重新启动平台前，不要手动重跑尚存活的抽取 worker。

统一队列的 OCR/IR/抽取 worker 共享
`workspace/.local/platform-runtime/model-execution.lock` 的 OS `flock`。队列锁文件描述符由 worker
继承，父服务退出不会提前释放执行槽。监督层遇到忙槽时明确显示 `execution_slot_conflict`、保留
checkpoint。路径可统一通过 `ESG_EXECUTION_SLOT_PATH` 覆盖；watchdog 不继承执行槽。
升级前未持锁的旧 worker 也不会被忽略：新抽取入场前只读检查已有任务登记的 PID，遇到仍活着的
旧任务会显示其 job/task ID 和 PID，并保留新任务供稍后续跑；不会扫描全系统或自动杀掉旧进程。

## 单次任务产物

```text
targeted_extract_output/<job_id>/
├── manifest.json
├── failure.json                         # 仅任务级失败时存在
├── input/
│   ├── request.json
│   ├── ir-manifest.json
│   └── standard-package.json
├── inventory/
│   ├── index.json
│   ├── spans.jsonl
│   └── candidates.jsonl
├── retrieval/
│   ├── semantic-index.json              # 启用语义检索时存在
│   └── <metric_id>.json
├── packets/
│   ├── <metric_id>.json                 # 完整 Evidence Selection
│   └── <metric_id>.regions.json         # 实际送模的连贯区域
├── decisions/<metric_id>.json           # 原始输出、适配结果、遥测与接受决定
├── guards/<metric_id>.json
├── family-adjudication.json              # 发生并执行族级联合复判时存在
├── semantic-conflicts.json               # 仅发现跨指标固定语义冲突时存在
├── derivations.json                       # 包声明派生规则时存在
├── checkpoints/<metric_id>.json
├── agent/
│   ├── state.json                        # 工具动作、调用数、当前模型阶段
│   ├── calls/<attempt>-<tool>.json       # 实际输入、小合同、原输出及错误
│   ├── sources/<object>.json            # 与主题无关的报告行列语义
│   ├── mappings/<topic>-<object>.json
│   └── topics/<topic>.json
├── results/                              # 六类权威 Core records
└── exports/
    ├── extraction-result.json
    ├── machine-fill.xlsx
    └── task-summary.csv
```

`packets/<metric>.json` 用于来源审计；兼容路径实际输入见 `packets/<metric>.regions.json`，
Agent 实际模型输入见 `agent/calls/`。模型失败原文保留在相应调用产物中；来源合同未通过的事实不进入 Core。

每个指标物化后会立即单独执行 Result Contract 校验，只有通过的记录才能进入汇总结果并把
checkpoint 标为可复用。单指标的主键或关联合同失败会写入 `contract-errors/`，该指标标记为
`system_contract_failed`，其他指标仍可形成 `partial` 成果。checkpoint 保存标准包 digest、物化器版本
和校验状态；新 Agent 还保存语义链路版本，不能自动信任旧联合抽取的语义。用户显式合同重建只复用保存的决定、不调用模型；它不纠正业务语义。历史 Bundle 不重写。

## 成果检查

操作台允许同时选择 E1-5、E1-6、E2-4 中的细分指标。跨卡片要求归属一个报告 Session；
控制面按标准包创建独立投影 job，跨包共享中性源理解，因此每个 Result Bundle 仍只绑定一个 package version
和 digest，不会把不同标准的 elements、校验规则或 Core snapshot 混写在一起。

操作台的“条款成果”页按当前报告和指标展示一条事实一行、一个标准 element 一列；列顺序、主维度
和排序提示由 element `semantic_role` 动态生成，不再假定所有模块都是污染物表。未披露字段为空。
页面同时展示证据页码与摘录、模型 provider/model、region、图片数、输入输出规模、重试、Guard 警告
和召回依据，并区分来源、合同、语义与展示四阶段状态。只有固定包常量、没有报告期/实体等非固定身份
的多事实会提示“语义身份待补全”；同一身份下的兼容 GWh/TJ 替代展示不会被该提示阻塞。该页面来自
只读 `GET /api/jobs/{job_id}/inspection` ViewModel，不修改权威结果。

`GET /api/result-catalog` 同时报告任务 SQLite 记录数、Result Bundle 目录数、孤立目录和缺失目录。
后端启动时会用同时具有 `input/request.json` 与 `manifest.json`/`failure.json` 的终态 Result Bundle
重建丢失的任务索引；不会猜测没有终态标记的半成品状态。标准目录的有效包和校验失败详情分别由
`GET /api/standards` 与 `GET /api/standards/diagnostics` 提供，前端不得静默隐藏无效包。

任务终态可调用 `DELETE /api/jobs/{job_id}` 清除该任务的全部中间产物和成果；Document IR、OCR、
标准包、模型权重与可复用语义索引不会被删除。

输出编号、六类记录与关联规则见 [Result Bundle 规范](docs/RESULT_BUNDLE_SPEC.md)，组件边界见
[架构文档](docs/ARCHITECTURE.md)。当前项目状态与后续任务只在仓库根 README 维护。

## 当前范围与验证

当前已有报告 Session、中性 SourceFact、角色路由、逻辑表阅读、真实动态 query embedding、局部 gap/repair、断点恢复及内部依赖计算。
完整报告身份由标准 identity_axes 与实际源事实字段共同判断；固定标准常量不能补造报告身份。不同物理来源的同值/换算单位仅为待确认关系，显式确认关系或完整身份下的同一物理来源才能合并展示，所有原始 Core records 保留。
网页、XLSX、CSV 使用同一后端组织合同；派生记录与中性来源显式关联，供报告 Session 事实读取和数据库幂等导入使用。正式批准、publication、人工编辑写回、Kodo 与跨服务器分布式队列不在本模块范围。
本轮回归使用假模型与固定 IR，尚未执行真实报告模型验收，不能据此宣称实际业务抽取质量已通过。

无模型回归测试：

```bash
cd backend
$HOME/Desktop/model/.runtime/venv/bin/python -m pytest -q
```

测试使用固定 Document IR 夹具和假模型，不启动 OCR、Document IR、NuExtract3 或七牛云任务。

保留四个污染物历史 Result Bundle 时，还可从 `backend` 执行只读回放：

```bash
PYTHONPATH=src "$HOME/Desktop/model/.runtime/venv/bin/python" ../scripts/replay_e2_baseline.py
```

该脚本重新适配并校验已保存的模型原文，直接比较字段和证据引用，不调用模型、不写结果包、不新增哈希。
它保护历史输出的兼容性；新提示词下的真实提取效果仍须由用户复跑确认。
