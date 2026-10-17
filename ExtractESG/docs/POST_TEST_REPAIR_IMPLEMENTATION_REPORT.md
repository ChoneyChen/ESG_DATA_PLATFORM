# 中国石油测试后的框架修复实施报告

日期：2026-10-03。代码版本：Targeted backend `0.6.0`、Document backend `0.4.0`。

本次按用户“开始全面修复”的要求，实施 [优化安排](POST_TEST_OPTIMIZATION_PLAN.md) 中 P0–P5，并完成 P6 的离线合同、故障注入、浏览器和候选数据库联调。没有重新运行真实 OCR、IR、embedding 或语义模型，没有执行真实报告队列，没有修改原始测试产物或业务数据库。三个 `2.0.0` 标准包继续为 draft，标准要求、模型默认角色和云端授权方式保持原有合同。真实模型效果须由用户发起新任务验证。

## 1. 修复的目标和架构边界

本次失败不是原值提取单点出错。原 IR 年度列头和 170 个定量候选原值均可核对，但提示与接收类型不一致、表头作用域污染、来源身份缺证据、主题状态混用及最终 Bundle 依赖串成了失败链。只修一个 null、页码或增加超时，会在下一类报告上重现。

保留现有唯一控制面队列、报告 Session、共享中性 SourceFact、独立标准投影、Guard/Core Validator 和 ResultExporter。重构的是各层的合同与状态交接：

```text
固定 PDF / IR revision / 标准包 digest / 所选要求 / 资源计划
  → 有界真实对象目录与当前主题导航
  → 表格、正文和结构上下文分别解释
  → 真实物理 anchor + 不可变来源解释 revision + 质量缺口
  → 当前标准包小批次映射 + 独立片段落盘
  → Grounding Guard / Core Validator / 逐指标 checkpoint
  → 族级裁决与显式依赖计算 / 后处理 Core 落盘
  → 可重建阶段视图 / 合法 partial Bundle / 候选数据库
```

证据质量、执行进度、标准适用性、计算依赖及审核状态分别表达。读取了原值不等于身份已完整；候选存在不等于 Core 可用；检查了一个对象不等于全报告未披露；程序通过来源合同不等于人工批准。

## 2. P0：固定失败基线和回放入口

仓库外私有审计目录保存本次 PDF 摘要、194 个已检查产物的 SHA-256、94 次模型调用索引、35 项处理矩阵及睡眠/截止时间证据。实施结束后重新核对：194 个产物摘要与 PDF 摘要全部未变。原测试仍保持中断、无最终业务成果的历史状态，不用新代码倒写一个“成功”。

新通用脚本 `targeted_table_extract/backend/scripts/replay_protocol_artifacts.py` 只读历史调用，使用当前接收合同检查，输出摘要、分类、哈希和可保留的协议条目数。它不调用模型，不导出原始提示、报告内容或响应，输出位置不得落在原调用目录。

本次 94 条原响应的离线结果：

| 当前合同处理 | 调用数 | 含义 |
|---|---:|---|
| protocol_passed | 28 | 类型与引用合同通过；没有证明语义或证据正确 |
| isolated_invalid_parts | 46 | 存在可局部隔离的错误；合法兄弟片段可保留 |
| protocol_rejected | 13 | 不进入可用决策 |
| generation_incomplete | 6 | 长度截断，应拆任务而非格式润色 |
| no_model_output | 1 | 没有可检查输出 |

这些仍是旧提示生成的旧响应。28 次协议通过不能解释为 28 项正确事实，局部保留条目也没有自动进入真实报告 Core。真实响应留在私有审计目录；仓库内加入的是匿名坏响应、结构/正文/身份和中断夹具。

## 3. P1：生成协议与接收合同来自同一类型定义

主要实现：`models/report_semantics.py`、`models/structured_template.py`、Qwen/七牛 structured adapter。

- 导航使用按 `tool` 区分的动作类型。选择阅读时只需阅读参数，结束不再被迫提供 object_id、query、metric_ids 等无关空字段。旧平铺响应中无关的 null 字段可无损丢弃；真正必需的 ID 仍禁止 null/空字符串。
- 导航、源解释和要求映射的 JSON Schema 从 Pydantic 接收类型生成，并按当前实际 object/group/cell/span/metric 目录限制引用。Qwen 的生成后验证与七牛的 provider schema 能力如实声明，不把提示 Schema 声称为本地原生受约束解码。
- 理解类型明确为 quantitative、assertion、context。断言无需虚构物理列；结构行无需虚构业务事实；合法负映射可以没有 matches，并保存 reviewed coverage。
- syntax、schema、reference、generation 和来源语义分层。理解/映射中的坏行或坏 match 局部隔离，正确兄弟片段保留；受影响要求改为 uncertain，不用错误引用蒙混通过。
- 格式修复携带完整原小任务、允许引用目录、原指标卡、Schema、来源类型及有界坏输出。它只修语法/形状，不能发明证据或猜测身份。无效引用交回当前任务，不默认转成 IR repair。
- Provider 或 MLX `finish_reason=length` 记录 generation_incomplete。拆小来源/映射任务；已小到一个不可分片段仍截断时保留明确失败，不反复重写巨量半截 JSON。

验收包括旧导航 null 冲突、负映射、未知列/来源 ID、合法兄弟隔离、provider schema 对齐、原任务修复上下文与截断拆分。未放宽源 ID 或 Guard。

## 4. P2：真实来源、身份轴和物理 anchor

主要实现：`evidence/inventory.py`、`harvester.py`、`target_cells.py`、`source_reading.py`、`report_facts.py`、`models/source_facts.py`、`grounding/guard.py`。

**表头与分区。** 真正列头与“财务与运营/环境”等分区行分开传递，分区不再当作全表列头传播。canonical page/object ID 来自真实 locator/结构上下文，取消旧零基页别名推算。PDF 页索引和印刷页码仍分别表达。

**正文数量。** 非表格数量也形成精确 T 候选，绑定真实 span/candidate ID 和 character occurrence，保留原值形式及单位。中文上下文不再被英文 identifier 检查误拒绝。没有可信单位时标记未知，不能从指标单位倒造来源单位。长正文按真实片段有界阅读。

**正文断言。** `statement_fragments` 引用真实 span 的原文，可连接多个片段。完整断言必须由相应正文支持，标题 bbox 不能冒充正文证据。Guard 按实际多片段与原文检查，保留大小写/数值，不用宽松归一化覆盖 source-proof。

**中性身份。** reporting_period、reporting_entity、boundary、scope、method 等字段必须由本单元格表头、本行、明确引用的关联片段或有界报告 frontmatter 支持；`field_refs` 给出真实片段。错误年份列头、不存在的字段引用、未证实的声明进入 unverified_identity_raw/quality_issues。没有可核身份就保持缺口，不从请求、标准固定值或公司简称推断。缺期间/主体的定量来源显式 needs_confirmation，导航能看到具体 identity_questions，原值仍可保存和重读。

**视觉边界。** 可靠网格 raw 永远保留。仅在网格确实不可信、图像实际发送、本 group 在所送范围内且 bbox 为真实正面积时允许视觉纠正。补单位或身份不会被伪称视觉读数。吨、万吨、亿吨等尺度必须有原文支持。

**物理身份与解释修订。** source_anchor_id 来自真实 group+cell 或 span+精确 character occurrence，断言来自真实 span/excerpt/位置；不依赖模型行顺序。source_fact_id 另含原值、身份、证据、语义 slot 与解释 digest。一处证据的新解释形成新 revision，旧解释进入 source-history 并写入共享 invalidations；不会用同一个 ID 覆盖不同含义。

这解决了两个相反风险：同值不同 cell 不能合并；同一个 cell 的重复相同解释不能双计 Core。去重保留完整证明组及原 source_indices，Core 绑定按实际所选解释 revision 建立，不能拼接兄弟证明制造一个新事实。

## 5. P3：片段工作状态、故障归属和依赖图

主要实现：`evidence/disclosure.py`、`contracts.py`、`results/derivation.py`、`workflow.py`、Session/cache/checkpoint。

### 完成和恢复

- context 行可用 header/section/definition 等 context_role 完成阅读，产生零 SourceFact。这直接消除“剩余四个结构行必须生成事实，因此整表重读”的循环条件。
- 来源理解按物理行和内容规模切分，已成功行独立保存；一行失败不会丢掉兄弟原值。source complete 指结构片段完成，来源 quality 指语义身份质量，二者不混用。
- 已 processed 的行仍可用 `read_object_context` 按 identity/group/cell 显式重读；只重读指定 cell 时保留未选 cell。重读无效时重新打开该片段，不维持虚假 complete。
- 映射按最多 10 个紧凑源行、字符规模和最多 6 个要求卡分片。每成功片段保存独立 input fingerprint；合法负裁决同样保存。兄弟片段超时/预算失败后，已存 matches 可用，未完成 coverage 为 uncertain；恢复只重做改变或未完成的输入。
- 来源缓存身份包含文档、IR revision、真实证据、解释合同、provider/model；标准映射另有冻结 package digest。跨标准复用理解，不复用另一包的裁决。
- 解释 revision 变化后旧 Core 绑定失效。同 job 的受影响映射先重新评估再投影；跨 child 的完成状态转为待恢复，旧 Bundle 不能继续宣称当前可用。重新激活完全相同解释时不永久退休它；历史来源与当前来源分开展示。

### 故障路由

EvidenceGap 和步骤保留 topic、attempt、object/group/metric、failure_stage、open/resolved 等作用域。只检查本次主题动作及当前缺口，成功片段可清除自己解决的缺口，不扫描全部历史失败套到当前主题。

协议/引用错误、截断、传输、预算、身份未知及未覆盖分别记录。只有真实不可信网格/表头或明确结构证据才能请求局部 IR repair；JSON 错误、虚构列和网络超时不进入该通道。IR 修复仍通过唯一 Document 队列和显式新 revision 接纳，不在持有重任务槽时等待另一任务。

同一完整输入连续失败有界停止该任务，独立输入可以继续；报告总预算耗尽则停止后续指标和标准 child。它们都属于资源边界，但停止范围不同，不把一个坏片段升级为整个报告不可继续。

### 计算依赖

规则与 TaskOutcome 使用 dependency_status=not_applicable/ready/blocked 及 dependency_blocks。原因分别覆盖来源未执行/无 accepted operand、标准映射未收敛、身份不完整或不相交、单位未知/尺度不兼容、重复 occurrence、数值无效和证据不足。相同 unit_id 也不能使 tCO2e、MtCO2e、亿吨直接相加。

缺操作数不能证明目标未披露，因此 missing target 的 not_found 转为 unresolved/ambiguous 并保存阻塞原因；直接 reported 事实仍可用。公式只执行包声明的合法组合，不能用 Scope 1+2 合计替代单项或含 Scope 3 总量。内部未选操作数不会扩张用户输出的指标集合。

## 6. P4：报告级资源和睡眠生命周期

主要实现：`runtime_budget.py`、`models/budgeted.py`、MLX runtime、`storage/power.py`、`execution_supervisor.py`、Document worker/hooks/outcomes/scheduler/plans。

| 完整度预设 | 模型调用上限 | 格式修复上限 | 总输入 token 上限 | 总输出 token 上限 |
|---|---:|---:|---:|---:|
| quick | 64 | 8 | 350000 | 65000 |
| balanced | 128 | 16 | 750000 | 150000 |
| thorough | 240 | 24 | 1500000 | 300000 |

部分自定义预算仅覆盖指定字段，其余仍使用所选完整度预设；例如只改 call_timeout 不会把 quick 的其它预算悄悄恢复为 balanced。预览显示冻结的最终预算。这些是当前资源上限，尚不是中国石油实测完成所需调用数或时延保证。

模型调用在真正生成前做原子预留，覆盖 semantic brain、specialist、修复及族级调用，同一 Session 的所有 child 和恢复共享 ledger。文件锁防并发超支，临时写入、fsync 与 replace 保证原子性；未完成预留跨进程保留。ledger 只含计量/输入指纹/错误签名，无原始提示、报告内容或凭证。 structured 调用不被外层 adapter 重复计数。

调用输入规模先保守预估，有实际 provider token 计量时替换；输出没有计量或生成失败则保守收费到预留上限。生成已返回与合同已成功分别记账，坏 JSON/引用连续失败即使换了错误文本也不能清空计数。报告总预算耗尽保留当前通过检查的片段并停止进入后续指标/标准，不自动增加预算；恢复也不清零。若资源计划不足，应显式创建更大的新任务，不能以无限重试伪装恢复。

macOS worker/Session 执行期间使用按 owner PID 绑定的 caffeinate 空闲睡眠声明；嵌套只持一份，退出有界 terminate/kill 清理。用户强制睡眠仍可发生。watchdog 比较墙钟与单调时钟观察间隙，记录可能的 suspend/clock adjustment 和唤醒后的超期时间；不把该估算声称为精确睡眠测量。截止仍按既有墙钟语义，睡眠期间本机监督器不能实时运行，唤醒后重新判定超期并处理。

确认 owner 停止后，监督器关闭 running 调用/步骤，再执行无模型阶段发布和合法 partial 导出。取消、异常、崩溃与超期路径清理执行槽、模型和电源声明。控制面保留 native interrupted/cancelled、真实 deadline reason 和 child errors，恢复清除本次已退役的终止原因。业务签名忽略 heartbeat/revision/elapsed 变化，保留真实 phase、token 进展档、成果计数、缺口和错误，避免心跳造成表格重绘。

## 7. P5：阶段成果贯通 API、页面和数据库

新 `results/stages.py` 从固定输入、通过合同的 checkpoint、当前来源缓存、映射片段、后处理和失效记录构建一个阶段视图。snapshot 是可重建投影，不是第二份事实数据库；读取不创建目录、不写缓存、不执行模型。

计数分别表示 selected、attempted、processed、processing、unfinished、not_executed，以及候选来源、真实 Core、有效绑定和 evidence。运行终止时 processing 转为 unfinished，未进入任务仍为 not_executed。只有 manifest 声称有几条记录不能构造事实；空的 accepted Guard 也显示 source_validation=not_run，而非虚假来源验证通过。

源理解结束、映射片段和 checkpoint 落盘后原子发布 snapshot。族级裁决/派生完成并再次通过 Core Validator 后，完整六类 Core 和 outcomes 保存到 `progress/core-postprocessing.json`，再发布 snapshot，再执行最终出口。导出前中断可以保留已持久化后处理，尚未落盘的内存结果不冒充存在。

来源、映射或内部操作数变化后旧后处理与旧依赖 verdict 失效，所选后处理目标标记待重算，即使目标原 checkpoint 没有 Core、旧 Core 仅由未选操作数计算得到也可恢复。旧 Bundle/snapshot 只有比当前输入版本新时才可被读取；失败 checkpoint、主键冲突和非法 postprocessing 隔离。facts 接口仅返回当前 stage 实际 Core ID 的绑定和证据，族级否决了旧 Core 但 SourceFact 仍合法时也不会泄漏旧导出关联。

Session detail/facts、job inspection 与 Document facade 使用这一真实状态。页面保留三种观察方式，展示候选质量、原始期间/主体/范围、有效 Core、未完成/未执行以及具体错误/缺口。仅有候选时显示可审查来源，没有伪造最终成果下载入口；状态变化才刷新业务表格。

中断时若有非空、通过 Core Validator 的 Core，可由现有 ResultExporter 生成 partial JSON/XLSX/CSV；必须具备冻结 document_id/IR 身份，evidence 必须属于同一文档。缺固定 provenance 时保留 snapshot/候选/错误，拒绝可导入 Bundle。只有 SourceFact 候选时不制造空业务 Bundle。

数据库联调消费的是匿名临时 checkpoint 经真实 ResultExporter 生成的中断 Bundle，使用内存 PGlite；不是手写理想 JSON，也未写业务 DB。1 条有效 Core 可幂等导入为 candidate，另 1 条未映射 SourceFact 保留在 Bundle，批准视图仍为 0。Core 可用、数据库 candidate、人工 approved 三个状态继续分开。

## 8. P6：实际执行的验证

| 验证层 | 结果 | 范围 |
|---|---|---|
| Targeted 完整离线回归 | 329 passed，1 项既有依赖 deprecation warning | 协议、来源、Guard、映射恢复、解释修订、依赖、预算、监督、阶段/出口/Session |
| Document 完整回归 | 270 passed，9 项既有 deprecation warnings | 控制面、worker、IR、native 错误传播、睡眠/唤醒、恢复与调度 |
| 标准包完整回归 | 27 passed | 编译/合同/保留；本轮未改包语义 |
| 数据库完整回归 | 8 passed，0 skipped | 内存 PGlite、真实 partial 出口、候选/审核隔离和幂等 |
| 浏览器合同 | PASS：实际阶段 serializer fixture + 全部业务 API 假 HTTP | 三种视角、候选/有效 Core、未完成/未执行、具体错误、刷新/订阅/授权 |
| 静态一致性 | 14 个前端 JS 语法、188 个后端 Python AST、版本声明、git diff --check 均通过 | 不加载模型；不生成业务产物 |
| 历史真实调用只读回放 | 94 条，分类见第 2 节 | 旧输出的当前合同处理，不重新生成 |
| 历史实物保护 | 194 个摘要 + PDF 摘要全部相同 | 未重写 OCR/IR/原测试结果 |

合计 634 项自动测试通过；浏览器和只读回放另计。测试数量是工程覆盖证据，不是准确率。相对上一轮 500 项，新验收明确覆盖以下非理想场景：

1. 不符合接收合同的模型导航/映射、无效 sibling 和不完整生成。
2. 四类结构上下文完成零事实、正文数量与多 span 断言、缺身份保留候选。
3. 局部重读成功/失败、相同 source ID 不同 immutable payload、共享解释失效和重新激活。
4. 同值不同位置、模型行重排的稳定 anchor、重复同位置仅一条 Core、正确 binding revision。
5. 跨主题失败隔离、协议不能触发 IR repair、片段超时/预算后恢复不重做正确兄弟。
6. 报告级原子预算、跨进程预留、返回后合同失败计数、不同错误文本仍有界。
7. 睡眠观察间隙和唤醒超期、owner 清理、control-plane native 结果/错误传播。
8. 来源/映射阶段可读、只有 manifest 数字不能声称 Core、已进入未完成与未执行分开。
9. 合法 partial Bundle 的真实 exporter→importer 联调，缺冻结 provenance 时不导出。
10. 族级否决后的旧绑定不泄漏、导出前后处理保留、内部操作数变化打开重算且不沿用旧 ready；检查页面以当前 unfinished 状态优先，旧任务行不会冒充重新评估完成。

全部自动测试使用匿名 IR、假 provider、tmp 状态或内存数据库；没有启动业务后端队列或调用模型。浏览器只启动临时静态测试服务，API 全部拦截为假响应。

## 9. 下一次用户真实复测的顺序与通过条件

工程改造已落地，真实模型采用新 Schema、上下文和分片后的行为尚未实测。启动新版本服务前停止旧版本服务，由用户发起新任务；不要直接运行旧测试生成的 IR repair，也不要在旧产物中改值来制造对照。

第一步使用相同 IR revision 和 E1-6 `2.0.0` 的小要求集，优先核对 dp01、排放总量相关项、dp15 与 dp33：能否读取真实表头/主体、正文总结、结构说明、局部方法和营收，Scope 1+2 合计是否保持真实范围，质量问题是否明确。不是要求这些项目都非空。

第二步发起完整 35 项任务，比较：实际首轮协议错误、总调用/修复次数、来源对象/身份质量、检索菜单选择、片段完成、每项准确结论及中断可见性。运行时间重新测量，不把旧 2 小时 24 分钟或新预算上限当作新版本承诺。

第三步验证 E1-5、E2-4 及紫金/越秀的现有重点样本，检查成熟污染物路径、跨标准 SourceFact 复用、互斥方法、重复披露和 internal dependencies。新版本需同时满足旧成熟路径质量和新报告级合同。

中国石油业务复测尤其需要确认：主体是中国石油天然气集团有限公司；Scope 1+2 五年合计不能拆出未披露的单独 Scope 或 Scope 2 两种方法；期间和亿吨尺度保留；dp01/dp15 允许有原文支持的局部成果；dp33 按冻结包允许的营业收入总额评估，不能套 dp34 分母的额外要求。摘要 30524.6 亿元和表内 30525 亿元分别保留，关联必须有证据。

最终通过条件是每项结论正确且可复核、故障影响范围清楚、合法成果可读/可恢复、资源有界。合法 JSON、候选数量、运行结束和自动测试通过都不能替代这项业务验收。

## 10. 交接和保留边界

详细合同见 `targeted_table_extract/docs/REPORT_FACT_AGENT.md` 与 `RESULT_BUNDLE_SPEC.md`；项目总入口为根 README。本报告替代“尚未实施”的状态声明，优化安排保留其原先依赖顺序与验收要求。前一轮 `UPGRADE_IMPLEMENTATION_REPORT.md` 仍记录当时结果，不改写历史结论。

保留已有未提交工作；不 reset、commit、push 或覆盖用户文件。CrawlESG 保护区未改动，其既有工作树状态保持原样。未安装模型、切换默认 provider、消费云端费用、执行旧错误 IR repair、迁移业务数据库或批准候选。实际复测前仍需用户正常启动新服务并提交新任务；本次交付提供可检查的代码、匿名验收和实施记录。
