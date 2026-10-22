# 报告事实 Agent：实现与验收边界

2026-10-03 协议与阶段成果修复后，本路径按报告 Session 固定 IR revision，形成标准无关源事实，并分别投影到选中的标准包。
需求与阶段边界见 系统重塑方案（已归档到 Git 历史）。本页说明当前代码，不把离线通过等同于真实业务验收。

## 目标与责任

目标是形成口径明确、可追溯、可跨标准要求复用的报告事实。标准包负责要求与关系；模型解释报告并判断要求适用性；
程序展开可靠原值、绑定物理来源、执行显式公式、持久化合同与组织输出。标准固定值不能作为报告实际披露的范围、方法或介质。

一个 ReportSession 对应固定报告输入与所选要求集合。每个标准包保留独立 projection job、标准版本、digest 与 Core snapshot。
同一 Session 的理解可跨包复用，包映射独立执行；不会把一个包的分类当作另一个包的报告事实。

`extraction_mode=auto` 对格式 2.0 的多指标主题、或声明事实粒度的现代指标启用 Agent。
同族只选一项也可启用。旧包、`per_metric` 对照和 E2-4_02/03/04 成熟路径保留直接填表。
成熟路径的已接受 reported Core 记录由确定性 adapter 补充中性 SourceFact 和显式绑定，无额外模型调用；
派生记录保留公式依赖，不冒充报告直接披露。历史 Bundle 只读兼容，不批量重写。

## 模型职责

| 角色 | 默认与范围 |
|---|---|
| `roles.semantic_brain` | 本地 Qwen；导航、报告理解、要求映射、映射冲突重访。可选明确授权的七牛 |
| `roles.specialist` | 本地 NuExtract；成熟直接填表。可显式选本地 Qwen 或授权七牛 |
| 未传 `roles` 的旧请求 | 原 `semantic_provider` 与 `semantic_model` 行为 |

云端调用必须有 `cloud_authorization.allowed=true`，不会因本地失败静默切换收费 Provider。
本地角色切换先释放另一模型，实际补查 query embedding 也先释放本地语义模型，再加载 embedding、生成向量并释放。
Qwen 与 NuExtract 共享 MLX 生命周期和直接填表公共能力，各自使用自己的 Provider 输入协议；Qwen 不继承 NuExtract 协议。
每次真实调用记录 role、provider、model 与阶段，summary 的 provider_match 按实际角色比较，保留 legacy 显示字段。

## 执行模型

```text
ReportSession：固定 IR revision / 报告 / 所选跨包要求
  → 每包独立 projection job 与输入快照
  → Embedding 索引及批量查询，释放 embedding
  → 发现候选并合并主题对象目录
  → 有界导航、理解物理或 LogicalTable 对象、阅读指定上下文
  → 开放报告身份字段、真实断言及可靠单元格原值 → 中性 SourceFact
  → 独立 metric_mapping：match / different / uncertain
  → 根据具体缺口补查、重映射或请求局部 IR repair
  → 来源/结果合同 → 内部依赖与显式计算 → 所选 Core 结果
  → 同一组织合同服务网页、XLSX、CSV 及数据库绑定
```

`DisclosureSession` 管理主题工具、来源解释与局部状态；`report_semantics.py` 定义导航、理解、映射小合同；
`report_facts.py` 定义中性 SourceFact 与 RequirementMapping，EvidenceGap 复用公共合同；
`source_facts.py` 直接编译投影到 SemanticAssignment，不将新源事实再序列化成旧直接填表响应。

## 小任务与工具

| 工具/任务 | 输入与作用 |
|---|---|
| `inspect_object` / source_understanding | 一个连贯对象、完整表头、当前至多 8 个物理行，并按字符/输出预算切分；必要脚注与同范围 crop |
| `read_object_context` | 指定对象、`header / footnote / crop / cell / identity` 与可选 group_ids/cell_ids；读取实际关联脚注或有界重读 |
| `map_object` / metric_mapping | 已解释紧凑源行、列身份，以及当前包要求的定义、粒度、身份轴和兄弟区别；无需重抄数 |
| `search_report` | 模型给出具体查询；词面与语义查询均使用新文本，实际 embedding 按文本、模型及 policy 缓存 |
| `request_local_repair` | 已知对象及具体 source/header 理由；保存有界 EvidenceGap/pending repair，暂停受影响映射并释放执行槽 |
| `finish` | 结束当前有界导航，保留剩余缺口；不宣称全报告未披露 |

理解字段采用稀疏 name/value；`fields` 使用中性身份轴目录及同轴别名，`extensions` 保留其它有据报告轴，解释不接收标准 elements 作为字段白名单。身份字段包括期间、实体、实体角色、边界、
总计/分项角色、量型、范围、方法、类别、能源载体、流向、污染物、介质、地点、业务板块、分母和额外拆分。
列身份随方法或实体变化时映射可以指定列，不能将全行复制到互斥要求。叙述用 `statement_fragments:[{ref,text}]` 引用真实 span 原文片段，可组合多片段；不绑定标题 bbox 冒充正文证据。非表数量用 `target_cells` 指定本行真实候选，`field_refs` 绑定跨行/跨页身份。不受原文、该 cell 表头或明确引用支持的字段进入 `unverified_identity_raw` 与质量问题；主值仍可保留，期间/主体不从请求补造。

导航采用按 `tool` 区分的动作合同，理解区分 `quantitative / assertion / context`；结构行通过 `context_role` 完成阅读，不产生 SourceFact。Provider Schema 从这些 Pydantic 接收类型生成，并按本次真实 group/cell/span/metric 目录收窄引用。ID 不允许 null/空字符串，集合使用 `[]`；断言映射没有物理列，使用 `columns=[]`，负对象裁决可以 `matches=[]` 并保留 reviewed coverage，无需制造来源。旧平铺动作中无关的空字段保持无损兼容，合法字段仍严格验证。

可靠网格的数值与原单位由对应 IR 单元格展开；`cell_grid=false` 时原 OCR 数值不作为可靠事实展开。
可靠 cell 的不同 raw override 被隔离，原字符串精确保留。只有不可信网格、实际发送的图像范围和真实正面积 bbox 同时成立时才可视觉纠正；单纯补单位/身份不标为视觉读数。unit 仍需要原文支持，模型摘要不参与机械数值展开。LogicalTable 提供继承表头、逻辑行列和连贯阅读；Spread 提供跨页上下文，
所有 SourceFact/evidence 仍引用原始物理对象、页索引和 bbox。未关联对象不会因处于同一 spread 而被合并。

稀疏映射中某要求在当前对象 `reviewed` 后，未列出源行组合表示模型判断 different。
`reviewed` 只说明当前对象检查完成，不是全报告检索充分性结论。现代 Agent 的同源语义冲突回到同一 metric_mapping 工具；
旧 family_adjudication 协议仅用于成熟直接填表，未完成的复判保持局部未解决状态。

## 缓存与来源身份

Session 缓存位置为 `output_root/.report-sessions/<report_session_id>/sources/<source_key>.json`。
身份包含 document_id、ir_run_id、ir_revision、物理/逻辑对象、规范化实际证据、解释合同版本、Provider/model 与视觉通道。
证据签名使用真实 group/cell IDs，排除随包变化的 G/T 短别名；linked_context 也从对象物理上下文规范化。
跨包命中必须来源身份一致；合法已读块可复用，未完成块继续局部读取。SourceFact 保留 raw value/unit/statement、完整列头、开放身份和物理定位。
包版本与 digest 属于 RequirementMapping，不收窄源解释。不同版本/不同 Provider 的解释不会被错误复用。

`results/metric-source-mappings.json.core_bindings` 明确关联 core_record_id 与 source_fact_id。
数据库据此做同源事实的幂等映射；没有显式绑定的旧 Bundle 保守视作独立记录，不因同值猜测合并。
相同 source_fact_id 对应不同 payload 是局部合同缺口，保留原固定来源，不悄悄覆盖。
`source_anchor_id` 标识真实 group/cell、精确 span/character occurrence，或有坐标的视觉单元格/断言片段，与模型 source_row_id/output ordinal 分离。`source_fact_id` 另含原值、已绑定身份、证据及语义 slot 的 interpretation_digest；调换模型行可形成新解释 revision，但物理 anchor 不变。语义修订生成新 ID 并失效相关投影，旧解释保存在 history。同列多个 occurrence 按精确 anchor 绑定，旧无 anchor 数据仅允许唯一列 fallback，不按同值猜关联。完全相同物理 anchor 与身份/语义只形成一个 Core，绑定保留所选解释 revision。

## 预算、失败与恢复

- `retrieval_object_top_n=1..5`：每指标初始候选对象预算。
- `shared_object_budget=1..8`：每主题初始阅读提示，额外对象仍须模型按缺口选择。
- `agent_max_steps=1..24`：每主题实际工具动作上限，默认 8；导航轮数也有界。
- `call_timeout_seconds` 可覆盖调用截止；未指定时采用对应本地/云端配置。
- `call_budget.max_model_calls/max_format_repairs/max_input_tokens/max_output_tokens` 限制整份 Session 真实调用总量，失败与修复同样计入，预览显示最终预算。

调用预留在生成前原子记账，跨角色、标准投影和进程恢复共用；有实际 provider token 计量时替换保守预估。传输返回不会清空协议失败计数，同一完整输入连续失败即使错误文字不同也会停止该任务；其它输入仍可继续。报告总预算耗尽停止后续指标和标准投影，未进入任务保持未执行。恢复不会重置既有预算；更大的资源计划需要显式创建新任务。

初始共享预算不是所有对象阅读的硬上限；真实动作和截止时间有界。FactPattern/词面只影响现代对象排序，不取代模型适用性。
新查询缓存记录 cache_hit、query/model/policy 身份；保留已有索引矩阵，不复用原指标查询向量冒充新 query embedding。

syntax/schema/reference/generation 分开记录；JSON/形状错误只使用有界错误和小合同修复，非法 mapping sibling 隔离但合法 coverage 保留。长度截断记作 `generation_incomplete` 并拆小任务，不做巨量格式重写。只有真正展开数量/原文断言或明确完成结构上下文的行进入 processed_groups；省略、空定量、失败块保留待处理。已经 processed 但语义身份需要重访时可显式 `read_object_context(identity)`，不会因 processed 永久跳过，未知轴也不会自动引入无限重读。
EvidenceGap 保留 object/group/cell/metric 作用域，unknown 引用或单个方法缺口不污染已明确的其他要求。
账户阻塞与取消停止当前通道；网络/账户错误不得要求 Document IR repair。

局部 repair 只产生 pending action，不在持执行槽时等待 Document 队列。控制面执行局部 IR 修复后显式接纳新 revision、创建新投影，
合法未受影响 checkpoint 保持原 task/source 身份；受影响要求重算。finalize 合并已有来源/绑定并剔除不存在的 Core 绑定，
保证普通续跑和新版 IR 接纳不会因本轮未重新理解对象而清空来源。

API/CLI 的默认重任务只由统一队列派发；旧第二 runner 已移除。SupervisedJob 保留资源槽、PID/进程出生身份及外部 watchdog，
native prefill 无 token 返回时仍可中断并保留 checkpoint。partial 与局部缺口不会变成所有报告阶段失败。

## 计算、关系与展示

有明确 `depends_on`、formula 与派生策略时，选中目标的未选操作数以 internal_metric_ids 自动补齐。
`results/internal-dependencies.json` 保存完整内部 records、evidence、outcomes 和来源绑定；用户输出仍只含所选指标。
公式仅对完整且一致的期间、边界、实体、口径与单位执行，reported 优先，身份不足保留原因。
`derivations.json.rules[*].dependency_bindings` 明确记录 derived_observation_id、source_observation_ids、rule_id、formula_version、operation 与 identity。
规则及 TaskOutcome 使用 `dependency_status=not_applicable|ready|blocked` 与具体 `dependency_blocks`，区分来源未执行/无 accepted operand、映射未收敛、身份不完整/不相交、单位不明/不兼容、重复 occurrence、数值无效和缺证据。阻塞写 topic/metric scoped `kind=dependency` gap，不自动要求 IR repair；不降低已经直接披露的 found/partial 事实，不能将缺操作数解释成目标未披露。

FactOrganizer 同时读取 Core 非固定字段与中性 source identity/semantics；源中未投影的身份轴也参与分组。
固定标准常量不能使报告身份完整。不同物理来源的同值或单位换算仅为 needs_confirmation 关系；
显式 confirmed/approved 关系，或完整身份下的同一物理来源且同一原始值/单位，才允许归为一个展示测量。
原始 Core records、证据和模型决定保留。exporter 与 inspection 使用同一组织合同，展示引用不产生新的 reported/derived observation。

## 产物与验收边界

`agent/state.json` 保存步骤、当前模型阶段、局部 gaps/pending repairs；`agent/calls/` 保存真实输入、合同、原输出、错误和耗时；
`sources/` 与 `mappings/` 分别保存理解和包映射；`topics/` 保存可恢复主题结论；`disclosures/projections/` 保存逐指标审计和明确 Core 绑定。
完整结果包括 `source-facts.json`、`metric-source-mappings.json`、`disclosure-views.json`、内部依赖及 derivations。
`progress/snapshot.json` 是只读可重建的阶段合同；inspection 优先仍符合当前来源版本的最终 Bundle，否则读取有效 checkpoint、来源与映射。族级裁决与公式完成后，经过 Core Validator 的 records/outcomes 写入 `progress/core-postprocessing.json`，导出前中断也能保留这些成果；尚未持久化的内存数量不会被假装存在。来源、映射或内部操作数变化后，旧后处理失效，所选目标标记待重算，不沿用旧依赖裁决。事实接口仅返回阶段有效 Core 的绑定和证据，历史导出不能覆盖当前投影。阶段候选与有效 Core 分开展示，执行状态以 job ledger 为准；已进入未完成与未执行分开，具体 errors/gaps 出现在常规页面。中断且仅有候选时不会伪造 Core Bundle。
`agent.model_calls` 包含失败/格式修复/历史调用；`agent_model_calls_this_run` 只计本轮，mapped_rows 不是最终独立事实数。

离线回归覆盖真实跨包 cache 复用、分块恢复、改写 query 向量、局部 repair、limited 同页对象、逻辑表物理来源、
单选派生及完整内部 evidence、checkpoint 来源保留、直接路径 SourceFact、身份分组、Provider 协议和监督。
没有运行真实 OCR/IR/VLM/报告任务，没有消费云端费用、重写历史业务 Bundle 或改动 CrawlESG。
真实业务验收仍须确认召回、行义、期间/实体/方法、位置法/市场法、生物源、断言可读性、重复关系及污染物成熟路径质量。
