# ESG 平台系统升级实施报告

日期：2026-10-02。依据：项目记忆、根 README、各模块合同及 `SYSTEM_RESHAPING_PLAN.md`。本文记录实际代码和工程验证；规划第三节保留升级前审计，不再作为当前缺口清单。

## 任务理解与架构边界

平台把报告中的具体披露变成可追溯、可复用的事实，并判断其对所选标准要求的适用性。事实身份包含源行主题、列头、实体与角色、期间、边界、测量类型、方法/Scope/类别、原始值或陈述和物理证据。同年、同值或同指标不能证明同一事实。

原系统已经具备可靠 OCR、物理 IR、标准编译、混合检索、成熟污染物抽取、Core 六集合和运行监督。问题集中在报告任务、模型职责、来源理解、状态和结果接续没有共同边界。本轮保留这些有效能力，在其上完成报告级收束。

当前主线为：

```text
PDF / 已有不可变 IR
  → 同一报告请求与计划预览
  → 唯一 SQLite 队列、隔离 Worker、执行槽和硬截止
  → 必要 OCR / 基础物理 IR / 对象能力可用性
  → ReportSession 共享中性 SourceFact
  → Agent 按缺口阅读、查询、映射或申请局部修复
  → 按包标准投影 / 内部依赖 / 确定性派生
  → 六类 Core 权威记录 + 来源/映射/关系审计
  → 来源事实、要求覆盖、关系组织三个结果视角
  → 现有 PostgreSQL/PGlite 候选导入与独立审核
```

`document_ir_extract` 负责报告准备、取证、复核和唯一执行控制面；`targeted_table_extract` 负责只读证据消费、来源理解和标准投影；`standard_packages` 继续定义标准语义及公式；`esg_database` 保存候选披露、映射和审核。前端只负责选择、展示和调用后端合法动作。

报告准备使用平台 `plan-*` 身份，原生提取使用 `report-*` Session；每包投影保留 `job-*` 身份与独立 Bundle。SQLite 的 Session 表仅是执行账本，来源事实在版本化产物中，没有另建平行事实数据库。

## W0：共同合同与模型角色

- 新报告请求支持 PDF/已有 IR、多标准选择、完整性档位、对象/动作/调用/总预算和有限证据授权。
- `RoleConfig` 区分 semantic_brain、specialist 和 cloud_authorization。默认 Qwen 负责导航、理解和映射，NuExtract 负责成熟专用任务；云模型需显式授权。
- `StageOutcome` 分离执行与成果状态；`EvidenceGap` 携带对象、行列与指标作用域；`AvailableAction` 提供真实接口、方法、载荷和调用成本。
- Qwen 与 NuExtract 共用 MLX 运行基础，各自保留 Provider 输入协议；生产遥测按实际任务角色校验 Provider。
- API Key/Token 不进入序列化请求、任务 SQLite、日志、结果或浏览器持久存储；控制面只用内存 Vault 转发给 worker。

## W1：唯一队列、可恢复状态与固定输入

OCR、IR build、repair、review retry、原生提取和报告 Session 的默认 API/CLI 均为统一队列适配。旧服务内直接执行线程、targeted ThreadPoolExecutor 和未调用的孤立派发器已退役。

已有 job/session 通过 `run-existing` 提交，不重新创建业务 ID。控制面按 active native ID 原子去重，并拒绝单独执行属于 Session 的子投影。内部 `--worker` 命令验证真实继承执行槽，不能仅凭字符串标记执行。

ReportSession 在同一隔离进程内顺序运行包投影。每次投影复制继承 fd，退出只关闭副本，保留主 Worker 的执行槽，避免第二个包丢失资源所有权。原任务硬截止 watchdog、主进程监督、取消和异常恢复保留。

已成功包不重复运行，未完成包以原 job ID 恢复检查点。取消保存已接受成果。finished、usable、partial 和 no_evidence_in_bound 分别表达执行及可消费结果；没有未完成投影时不提供空恢复动作。

IR ID 在入队时固定。排队、历史任务和 Session 对 revision 的引用由 SQLite pin 与 Session marker 保护，retention 不再静默重定向输入。Session 冻结 manifest 与包内容摘要，正文、表格或图像被原地改动会阻止执行。

修复使用新 IR。显式 evidence-transition 校验同一文档、有限消费授权和影响范围，只失效相关指标及其派生依赖；无关检查点及旧物理来源保留，旧投影进入历史而不被覆盖。属于 Session 的投影不能单独删除。

## W2：报告来源事实与跨页阅读

SourceFact 是标准无关合同：原行/单元格、值/陈述、单位、列头、原始身份、语义解释、理解版本和全部物理来源。标准固定分类不用于补造报告源身份。

共享缓存键覆盖文档、IR/revision、对象、理解协议、Provider/模型及对象内容。同 Session 内 E1-5/E1-6 对同一源表只理解一次，各自独立映射。成熟 E2-4 direct Core 通过确定性适配加入来源事实，无额外模型调用，也不更换原介质召回和提示配方。

LogicalTable/Spread 已由 loader 和 inventory 加载为阅读视图，继承跨页列头及上下文，最终证据仍指向物理页/cell。只标记真实解释成功的行，未解释行和列头缺口可重新读取；局部失败不降级无关兄弟事实。

`source-facts.json` 与 `metric-source-mappings.json` 通过真实 `core_bindings` 关联 Core 主键，不按相同值猜测对应。恢复与证据转换合并来源归档时只保留最终有效 Core 绑定。

报告 facts API 汇集共享 cache 与各包实际导出，因此成熟直接结果也能显示。冲突来源 ID 局部隔离，返回 quarantined_facts 和作用域缺口；无冲突事实仍可读取。被明确修复范围替代的旧事实进入 historical_facts。

## W3：智能证据闭环与确定性计算

Agent 工具为 inspect_object、read_object_context、map_object、search_report、request_local_repair、finish。表头、脚注、局部 crop 和特定行/cell 可按具体缺口阅读；重复动作和总步数均受预算限制。

新的改写查询真正生成自己的 embedding，复用报告对象矩阵。本地脑模型释放后做查询 embedding，再重载继续语义工作，避免两类模型同时占用统一内存。

结构修复申请保存明确对象/指标缺口，暂停该对象并交由控制面后续排队；不持有抽取执行槽等待 IR 修复。纯通道/账号故障不被自动当成文档结构损坏。

内部读取已声明且启用的公式依赖，递归补齐必要操作数。直接披露优先；满足规则规定的身份、方法、单位和依赖后才派生。未选依赖不增加用户输出任务，内部事实及证据独立归档。每项派生保存操作数 ID、规则、操作和公式版本。

新 Agent 的冲突回到同一映射工具裁决，不再常规叠加旧 family 语义协议。成熟 direct 兼容路径仍保留必要既有裁决，失败或 uncertain 不被批量刷新为 completed。

## W4：五入口、三个结果视角与明确关系

普通入口为报告库、提取任务、结果工作台、待处理动作、系统设置。文件夹导入、多报告选择以及 OCR/IR/复核高级视图保留。

`sessions.js` 是 PDF/已有 IR 唯一创建器；api、subscriptions、status、task-store、standards、roles、actions、results 共同服务替代重复创建、HTTP、状态和轮询。旧页面书签重定向。密钥只在当前会话内存保存。

后端负责事实排序与关系，前端不做值相同去重。来源事实展示完整原始身份与真实物理页码；所选要求覆盖显示找到、范围和缺口；关系组织保留事实独立行，只展示显式确认的重复、替代单位或操作数关联。

动作读取后端 GET/POST/DELETE、payload、scope 和成本。页面不通过错误字符串猜测修复范围。数据库状态保持未导入，直到实际执行候选导入；没有虚假的已发布提示。

## W5：数据库候选接续、局部资产与退役

新增迁移 `0005_candidate_import_and_decisions.sql`，增加导入批次/项目、源事实身份、关系和独立审核记录，以及范围值/可读陈述审核视图。已有迁移不原地改写，现有本地数据库本轮未操作。

候选导入读取实际 Bundle 的六类权威 JSONL、标准/请求快照、来源事实、Core 绑定、关系和内部依赖。显式上游组织/报告/版本/文件引用是必要输入；不从文件名推断权威身份。

同 Bundle 重复导入幂等；真实同一 SourceFact 可映射多个标准。原始身份、证据或理解版本漂移、重复/悬空/错误包绑定、报告 SHA256 内容冲突均拒绝并回滚整批。旧包无可靠中性来源合同则按 job/record 保守保存，不跨包凭值合并。

范围/不等式保存上下界，不能冒充精确单点。定性事实保存真实可读 statement，不造三元组。事实质量与标准映射独立批准，每项审核要求操作者与理由，并保存决定记录；approved 不自动产生 publication。

局部 IR 修订在 macOS 使用 clonefile、Linux 使用 FICLONE，失败回退 copy2。页面/crop/spread 保留自包含路径与独立文件，修改子图不污染父包。repair targets/scopes 可合并同轮目标；整包结构重建和验证仍保留，完整对象增量存储不在本轮伪装完成。

退役清单：旧 API 内直接重负载线程、targeted 独立 executor/孤立派发器、Agent 的旧 direct JSON 中转/未调用联合 parser、新主线第二套 family 协议、重复前端创建器/各页默认轮询、默认全报告可选模型增强。成熟 direct、来源 Guard、原生监督和已有复杂图表/跨页能力保留。

## A01–A19 实施对照

| 审计项 | 实施位置与结论 |
|---|---|
| A01 角色混用 | contracts/模型路由：报告角色实际执行，legacy 单包仍可读 |
| A02 按包重复理解 | report_session/控制面：一个 Session，多包投影共享阅读 |
| A03 标准绑定源字段 | report_facts/disclosure：中性 SourceFact + 单一 Core 投影 |
| A04 补查旧向量 | workflow/embeddings：新查询实际嵌入，资源切换 |
| A05 跨页未消费 | loader/inventory：逻辑表与 Spread 读视图，物理证据 |
| A06 未选依赖未执行 | Session依赖计划/workflow/derivation：内部读取与输出分区 |
| A07 错误 processed | disclosure：只记录成功行，未解释行可恢复 |
| A08 缺口扩散 | 共同 EvidenceGap/映射：按对象、行列和指标作用域 |
| A09 双裁决入口 | 新Agent统一重映射；direct 兼容协议局部保留 |
| A10 Provider继承 | mlx_runtime/Qwen/NuExtract：运行基础与业务协议分离 |
| A11 默认全量增强 | 基础IR不默认执行；复核/增强是显式能力 |
| A12 整页排除 | validator/loader：对象×能力可用性，保留同页好对象 |
| A13 绕过队列 | API/CLI薄适配、run-existing去重、真实继承槽校验 |
| A14 partial中断主线 | StageOutcome/计划/Session：部分成果可推进，双轴状态 |
| A15 引用重定向 | retention/pin/包摘要/evidence-transition：固定与显式变更 |
| A16 全资产复制 | copy_on_write + 多目标修复；整包结构验证保留 |
| A17 重复前端 | 五入口、唯一创建器、共同API/订阅/状态服务 |
| A18 误合并 | 完整原始身份、来源绑定与明确关系确认 |
| A19 无入库器 | 既有库候选导入、幂等/回滚、事实/映射独立审核 |

## 操作入口与升级方式

关闭旧工作台启动窗口后，使用原统一启动器或仓库命令：

```bash
zsh ExtractESG/scripts/start_local_workbenches.sh
```

默认前端 18081、Document API 18080、Targeted API 18180。启动器检查报告 Session 能力；旧服务占端口会提示先关闭，不会强杀用户服务。Document 版本 0.3.0、Targeted 版本 0.5.0。

主线接口：

| 接口 | 用途 |
|---|---|
| POST /api/report-sessions/preview | 标准/角色/预算/复用与限制预览 |
| POST /api/report-sessions | 创建报告任务，PDF/IR同合同 |
| GET /api/report-sessions、/{id}、/{id}/facts | 任务/双轴状态/事实与来源 |
| POST /api/report-sessions/{id}/resume、cancel | 同ID恢复未完成部分/保存成果取消 |
| POST /api/report-sessions/{id}/evidence-transition | 明确采用修复revision |
| POST /api/pipeline/tasks/targeted-extraction/{id}/run-existing | 既存单包job进入唯一队列 |
| POST /api/pipeline/tasks/report-session/{id}/run-existing | 既存原生Session进入唯一队列 |
| POST /api/pipeline/tasks/native/{id}/cancel | 同控制面取消已有native任务 |

数据库在 `ExtractESG/esg_database` 使用：

```bash
pnpm db:import --bundle /absolute/path/to/result --report-metadata /absolute/path/to/metadata.json
pnpm db:review --decision-file /absolute/path/to/decision.json
```

元数据与审核 JSON 字段见数据库 README。导入器会应用必要迁移。本轮验证只使用临时/内存库，真实 Bundle 由用户按业务验收安排导入。

## 工程验证与真实业务验收

最终回归共 **500 项测试通过**，另通过前端浏览器合同与跨模块出口联调：

| 验证范围 | 结果 |
|---|---|
| Document/OCR/控制面完整 pytest | 255 passed，36.18 秒 |
| Targeted 完整 pytest（含报告API/Session/监督） | 211 passed，4.69 秒 |
| Standard Packages 完整 pytest | 27 passed，0.28 秒 |
| 内存 PGlite 候选导入/审核 | 7 passed |
| 前端 headless Chrome + fake HTTP | 创建、角色/授权、partial、三结果视角、订阅、动作方法与凭据边界通过 |
| 真实出口合同联调（假模型生成） | v2 E2-4 Bundle 合同通过，1个候选成功导入，第二次同批 imported=0/idempotent=true |
| macOS 文件系统 clonefile | 实际clone成功，父子inode独立，修改child后parent字节不变 |
| 脚本与差异检查 | zsh语法、前端JS语法、git diff --check 通过 |

Python 测试仅有现有 FastAPI/Starlette 弃用提示，没有失败。标准保留测试的一处旧活跃包 fixture 路径改为其实际 retired 路径，未改变标准语义。
所有模型调用使用 fake/model-free fixture，不执行真实 OCR、IR、VLM 或报告提取。

有业务意义的工程案例包括：E1-5/E1-6 同表只理解一次；单选 E1-6 总量读取三内部操作数并得到 600，最终只输出所选指标且证据合同完整；未解释行重新读取；局部缺口保留正确兄弟结果；跨页物理定位；证据转换只失效相关检查点；并发入队只登记一项；同 worker 连续投影保留执行槽；源身份/报告内容碰撞事务回滚；事实与映射分别批准；范围与可读陈述不造值。

另以假模型实际生成通过合同的 Result Bundle，并成功导入独立临时 PGlite。这验证生产出口与导入适配衔接，不等于真实模型质量通过。

本轮未修改 CrawlESG，未重写真实历史结果、标准语义或用户数据库，未推送；开始时已有改动与受保护模块删除状态保留。

仍需用户发起真实报告验收：E1-6 主表及独立方法/扩展分支、E1-5 能源身份/单位/生产消费边界、E2-4 三介质成熟路径及完整金样本；逐项检查证据召回、来源身份、映射、缺口和恢复行为。

三个标准 2.0.0 仍为 draft。Kodo/服务器生产部署、分布式 Worker、Full Harvest、大规模准确率结论、权限治理及正式 publication 不属于本轮目标。数据库当前可操作入口为 CLI，HTTP/UI审核与发布流程没有宣称完成。
