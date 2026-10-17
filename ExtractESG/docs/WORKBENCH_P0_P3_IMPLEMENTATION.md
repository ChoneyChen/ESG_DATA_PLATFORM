# 工作台 P0—P3 实施与完成审计

本记录对照仓库根目录的 [更新要求.md](../../更新要求.md)，说明本轮 P0—P3 的代码、接口、使用入口、测试覆盖和剩余边界。规划要求、代码已经具备的能力、工程测试结果、现场运行证据、真实报告业务验收分别记录；不能互相替代。

本轮保持 OCR → Document IR → 定向提取的职责。Document IR 仍是页面、正文、表格、对象引用和证据能力的结构层。此次将报告身份、独立报告处理、生命周期及结构化进度落实在现有单队列体系中，没有完成 P4—P7 的全部界面闭环、目录迁移或真实模型验收。

最终运行源码已冻结，四个本地服务已协调重启并完成现场只读验收。没有启动真实 OCR、IR 模型复核或定向提取，没有对历史报告、识别成果或提取成果进行清理。最终测试与现场证据见第七节。

## 1. 需求与本轮范围

| 阶段 | 需求 | 本轮交付 | 不能据此宣称的结果 |
|---|---|---|---|
| P0 | 恢复版本一致性、新标准包及配置适配 | 联合版本握手、业务准入、标准目录和主要清单读取、选择与预览合同 | 标准包已经获得真实提取业务批准 |
| P1 | 报告身份、独立处理、复用、恢复、修订 | 独立报告登记、自动 OCR→IR、三种模式、严格复用、固定修订、处理页面 | 每种真实报告均能准确处理；完整三栏问题修复体验已经完成 |
| P2 | 取消、归档、丢弃、引用保护、回收及恢复 | 按对象所有权管理操作、依赖检查、可恢复回收、来源 pin 保护 | 任意旧任务或文件均可无条件清理 |
| P3 | 中文阶段、真实计数、事件、错误、时间及预算 | 共享结构化进度、错误分层、持久化事件与 SSE、观测时间、计划预算 | 日志条数或模型调用预算代表业务完成率；观测空白代表模型一直工作 |

此处的“当前可用”指相应工程合同可以加载和使用，仍须通过真实任务确认报告质量。产品页面与开发工作台的完整分离、可视化重制和全目录迁移保留在后续阶段。

## 2. P0：版本、标准包与主要条款

### 2.1 联合版本握手与运行准入

启动时联合指纹覆盖相关代码、合同、标准目录、范围配置、指标字典和启动器。服务保存启动时指纹，并检查当前磁盘是否已改变。前端、Document 后端和 Targeted 后端不能仅凭普通健康检查相互认定兼容。

| 要求 | 实现与接口 | 使用入口 | 测试依据 |
|---|---|---|---|
| 区分服务存活与业务就绪 | [workbench_release.py](../scripts/workbench_release.py)、两后端 `runtime_compatibility.py`；`/health/compatibility`、`/api/pipeline/compatibility`、`/api/compatibility` | 工作台兼容诊断、创建前检查 | [test_workbench_release.py](../standard_packages/tests/test_workbench_release.py)、[test_runtime_api_guard.py](../targeted_table_extract/backend/tests/test_runtime_api_guard.py) |
| 旧服务或混合版本不能接收新工作 | Document/Targeted API 的准入守卫，拒绝前不创建任务或运行产物 | 报告处理、预览和提取创建 | 同上；[test_report_processing.py](../document_ir_extract/backend/tests/test_report_processing.py) 的不可用版本创建/恢复守卫 |
| 启动器检查真实版本合同 | [start_local_workbenches.sh](../scripts/start_local_workbenches.sh)、[serve_workbench_frontend.py](../scripts/serve_workbench_frontend.py)；前端兼容端点及无缓存响应 | 原有统一启动器 | 联合指纹与前端合同测试；现场证据待第七节登记 |

启动器遇到未知或过期的已占用端口会解释阻塞，不自动强杀其他服务。旧结果仍可以读取；恢复执行也属于新工作，须通过相应兼容检查。

### 2.2 当前目录、原编号与两层优先级

必须保留两个独立概念：

- **核心条款**：在一个标准包内部承担核心语义的条款。
- **主要条款**：平台按用户指定选择、分阶段纳入提取及入库的条款。

主要条款应属于对应包的核心条款；包内核心条款不自动成为平台主要条款。不得用语义角色或包内核心标记代替产品范围配置。

本轮目录表达为：

| 对象 | 当前工程状态 | 解释 |
|---|---|---|
| 当前标准包 | 41 个 `2.1.1 draft` 结构草稿包 | E1—E3、ESRS 2、MDR 当前包可按合同加载；结构可编译不等于真实提取成熟 |
| 主要清单 | 49 个原始条款编号完整保留 | 采用用户指定选取，没有重编号或删去尚未建设项 |
| 当前主要配置就绪项 | E1—E3 共 16 项 | 可以进入当前定向配置及相应平台连接；仍需要真实报告验收 |
| 待建设主要项 | 33 项 | 显式保持待建设，不静默从清单消失，不进入执行覆盖分母 |
| 未来建设占位 | 保留规划状态 | 占位与可执行包分开表达 |
| 退役及历史版本 | 保留既有引用解析 | 不作为新任务默认活动版本；历史任务继续读取原版本 |

代码依据为 [标准目录](../targeted_table_extract/backend/src/esg_targeted/standards/catalog.py)、[主要范围配置](../standard_packages/profiles/esrs/2023-set1/platform-major.json)、前端 [standards.js](../document_ir_extract/frontend/src/standards.js)、[disclosure.js](../document_ir_extract/frontend/src/disclosure.js) 和 [sessions.js](../document_ir_extract/frontend/src/sessions.js)。包详情按需读取并核对摘要；单个目录或输入读取失败不会把其他已选择条款清空。

测试依据为 [test_standard_catalog_readiness.py](../targeted_table_extract/backend/tests/test_standard_catalog_readiness.py)、[test_environment_catalog.py](../standard_packages/tests/test_environment_catalog.py)、[test_pipeline_standard_proxy.py](../document_ir_extract/backend/tests/test_pipeline_standard_proxy.py) 和 [frontend_contract_test.cjs](../document_ir_extract/frontend/tests/frontend_contract_test.cjs)。

### 2.3 所选输出与共用依赖分开

报告预览分别返回所选输出、身份要求、ESRS 2/MDR 共用背景、公式操作数、内部依赖及排除项。只解析标准包声明且固定版本的共用引用。某个方法条款被使用，不会自动激活全部 ESRS 2 或 MDR；未知政策、行动或目标采用状态等待报告背景，不能由请求补造。

代码依据为 [report_session.py](../targeted_table_extract/backend/src/esg_targeted/report_session.py)，验收依据为 [test_preview_scope_contract.py](../targeted_table_extract/backend/tests/test_preview_scope_contract.py)。入口为“创建报告提取任务”的标准选择及预览。

## 3. P1：报告身份与独立 OCR→IR 处理

### 3.1 业务对象与固定来源

| 对象 | 身份与职责 | 实现 |
|---|---|---|
| PDF 资产 | 完整 SHA-256 确定内容身份；保留既有资产和路径 | [report_catalog.py](../document_ir_extract/backend/src/esg_v2/assets/report_catalog.py) |
| 报告 | 独立 `report_id`；保存发布者、报告名称、报告期、语言、类型及元数据修订 | [report_processing.py](../document_ir_extract/backend/src/esg_v2/control_plane/report_processing.py) 的 `reports`、`report_asset_versions`、`report_metadata_history` |
| 处理任务 | 独立 `processing_id`；固定 PDF、配置、构建版本、OCR/IR 及事件 | 同文件的 `report_processing_runs`、`report_processing_artifacts`、`report_processing_events` |
| 测量主体 | 每条事实的主体、边界与角色独立核对 | 报告发布者不自动填入事实测量主体 |

报告身份不从文件名猜测公司或年份。不同内容的 PDF 只有通过显式关联才成为同一报告的不同版本。元数据和版本关联使用修订号冲突检查。

接口位于 [api/report_processing.py](../document_ir_extract/backend/src/esg_v2/api/report_processing.py)：`/api/reports`、报告详情/修改、`/versions`。页面入口为“报告处理 → 报告身份与版本”。对应测试为 `test_report_identity_metadata_and_explicit_asset_version`。

### 3.2 一次操作及三种模式

从“报告库 → 处理报告”或一级“报告处理”入口，选择 PDF 并提交即可创建任务。全过程不要求选择标准包，也不要求用户手工填写 OCR/IR 文件路径。

| 页面模式 | 执行含义 | 历史成果 |
|---|---|---|
| 自动处理 | 先找兼容 IR，否则找兼容 OCR，执行缺少的阶段 | 保留 |
| 基于兼容识别成果重建结构 | 固定已登记、兼容的 OCR，只新建 IR；没有兼容 OCR 时明确拒绝 | 保留 |
| 重新识别并构建 | 新建 OCR 和 IR 处理版本 | 保留 |

父处理任务只协调并保存状态，OCR 和 IR 子任务依次进入既有唯一队列；父任务不占执行槽等待子任务。提交键和固定请求摘要用于幂等；相同提交键用于不同请求会返回冲突。活动中的相同工作不会重复入队。

主实现为 [report_processing.py](../document_ir_extract/backend/src/esg_v2/control_plane/report_processing.py)、[api/main.py](../document_ir_extract/backend/src/esg_v2/api/main.py) 的 `_dispatch_processing_step` 与协调器接线、[processing.js](../document_ir_extract/frontend/src/processing.js)、[index.html](../document_ir_extract/frontend/index.html)。接口为 `POST /api/report-processing/runs` 和列表、详情、事件接口。

测试覆盖独立创建无标准、父级不占 Worker、并发幂等、请求冲突、三模式以及无兼容 OCR 的拒绝；见 [test_report_processing.py](../document_ir_extract/backend/tests/test_report_processing.py)。

### 3.3 严格复用与真实证据条件

独立处理与自动提取准备共用 `resolve_document`。不再通过目录排序、最近一次运行或首个 OCR 结果推定可用性。自动复用必须同时满足：

1. PDF 完整 SHA 一致。
2. 阶段配置摘要一致；IR 同时固定相应 OCR 配置。
3. 识别/构建代码和已记录运行配置版本兼容。
4. manifest 身份、合同及保存时摘要一致。
5. OCR/IR 包完整性验证通过，IR 的 OCR 来源登记与完整性通过。
6. 证据能力符合本次完整或有限证据使用策略。

新提取计划只有携带固定处理配置和构建版本，且实际成果验证通过，才由 `record_plan_artifacts` 登记可复用来源。旧计划缺少生产者信息时仍可读取，但不自动提升为可信复用记录。

结果区分 `ready`、`ready_with_limits`、`needs_attention`。后续完整性损坏或构建版本改变时，历史执行状态不改写；`current_evidence` 独立返回当前可用性和阻塞，报告库使用同一验证函数，“开始提取”不再提供不可用的固定 IR。

构建指纹覆盖代码及已配置运行信息，不能据此声称已校验所有外部模型权重文件内容。真实运行时仍需确认部署配置、模型服务及成果合同一致。

对应测试涵盖配置漂移、构建版本漂移、内容损坏、manifest-only 假成功拒绝、有限证据策略、显式新计划登记、当前可用性与历史状态分离。

### 3.4 阶段恢复、取消及证据修订

OCR 完成后 IR 失败，恢复只重试 IR。队列已创建但父级尚未保存检查点时，通过固定阶段 dispatch key 找回原子任务。原生状态已创建但尚未入队的窗口，由 [native_dispatch.py](../document_ir_extract/backend/src/esg_v2/control_plane/native_dispatch.py) 的持久化原生身份预留关闭；阶段或请求摘要不一致时拒绝复用该身份。

取消运行中任务先显示 `cancelling`，等待子执行退出后才成为 `cancelled`。取消观测不会调度后续 IR。源 PDF 或构建版本不兼容时拒绝继续原调度；成功阶段和历史尝试保留。

修复生成的新 IR 必须由用户显式选择。切换校验同一 PDF、固定 OCR 和成果完整性，保留旧证据引用及转换记录。未知修复生产配置不会伪装成原自动构建配置供以后自动复用。开始定向提取时向创建器传递固定 `irRunId`；目录暂时不可读时保留选择并阻止静默更换。

对应接口为 `/cancel`、`/resume`、`/evidence`、`/evidence-versions`。测试依据为 [test_report_processing.py](../document_ir_extract/backend/tests/test_report_processing.py)、[test_native_dispatch.py](../document_ir_extract/backend/tests/test_native_dispatch.py)、[test_pipeline_control_plane.py](../document_ir_extract/backend/tests/test_pipeline_control_plane.py)。[processing_contract_test.cjs](../document_ir_extract/frontend/tests/processing_contract_test.cjs) 覆盖无标准提交、中文阶段、有限证据、正文/表格展示、固定 IR 传递及兼容守卫。

### 3.5 当前处理页面与规划差距

当前页面已有处理列表、企业/报告期搜索、三模式、高级配置、实际阶段与可用操作、PDF 原文、页码选择、正文和表格结构、被排除对象说明、报告元数据、版本关联、复用诊断及归档历史。

与要求中的完整三栏交互仍存在明确差距：

| 规划项 | 当前实现 | 后续工作 |
|---|---|---|
| 页面缩略图、目录、问题定位 | 左栏为页码按钮 | 建设缩略图、章节和问题索引 |
| PDF 原文位置高亮 | 中栏使用 PDF iframe；右栏可显示对象坐标 | 形成可点击对象与原文高亮联动 |
| 问题数量及类型汇总 | 阶段错误、当前证据阻塞及被排除对象说明 | 统一页面/对象问题 DTO 与数量、类型筛选 |
| 同报告中完成复核再继续 | 固定 IR 跳转现有复核页，显式选择修复版本后可继续 | 完整往返操作、上下文保留和修复后继续的浏览器业务验收 |

当前能力不是完整独立 ConsoleESG 重制。以上界面完善属于 P4 的连续建设，不能因已有三栏排布而宣称缩略图和位置高亮已完成。

## 4. P2：生命周期与引用保护

### 4.1 操作含义与对象所有权

| 操作 | 行为 | 条件 |
|---|---|---|
| 取消 | 请求停止执行，等待进程退出 | 活动任务；不会删除成果 |
| 归档 | 从日常列表隐藏，保留来源、成果和 pin | 终态且子执行已停止；可恢复 |
| 丢弃未入库成果 | 经依赖检查后释放所属引用，必要时把成果移入回收区 | 必须确认来源未被候选、导入、正式数据或其他任务引用 |
| 清理中间文件 | 针对经过预览、复核的准确路径进入回收区 | 检查内容身份、所有权、依赖及保留权限 |
| 恢复 | 恢复原记录、原引用或原路径 | 原内容摘要可核对；不覆盖后来的不同文件 |
| 彻底清除 | 回收保留期后显式执行 | 重新检查依赖及保留状态 |

旧删除入口采用归档语义，不再把成功隐藏解释为物理文件已删除。清空队列历史也不会清空业务成果。提取子任务归属于报告 Session，不能通过单独子任务操作绕过父级生命周期。

报告处理记录丢弃只注销自身可复用登记和所属 pin，物理 OCR/IR 保留在来源库存中。当前尝试、历史尝试及未写入父检查点的准确 dispatch-key 子任务一起核对；其他父任务的 pin 保留。恢复重新验证原 manifest 并恢复所属引用。

### 4.2 多库依赖未知时必须停止清理

来源保护不只检查当前 SQLite。共享 [PublicationReferenceGuard](../shared_runtime/src/esg_runtime/lifecycle.py) 调用 PlatESG 内部引用接口，核对候选、导入记录、正式观测和 PDF/IR/任务身份。文件回收的保留请求与候选导入使用同一发布库互斥合同，避免检查后又出现新引用。

[PlatESG/backend/main.py](../../PlatESG/backend/main.py) 还检查已知历史和其他本地数据目录的覆盖范围。仅当前展示数据库为空，不证明其他数据库没有引用。额外目录未核查、保护服务未连接、凭据缺失、合同异常或响应未确认，均返回具体阻塞并停止丢弃/来源清理；归档仍可用。

`PLATESG_LIFECYCLE_STORE_SCOPE=isolated` 表达隔离测试库边界，不是跳过真实多库依赖的生产开关。现有真实多库统一核查及权限配置仍须部署验收，不能将工程接口实现写成当前所有历史来源均已核查或均可清理。

### 4.3 代码、入口与验收

| 对象 | 实现 | 使用入口/接口 | 测试依据 |
|---|---|---|---|
| 队列历史和独立来源 pin | [store.py](../document_ir_extract/backend/src/esg_v2/control_plane/store.py)、[execution_api.py](../document_ir_extract/backend/src/esg_v2/control_plane/execution_api.py)、[queue_lifecycle.py](../document_ir_extract/backend/src/esg_v2/storage/queue_lifecycle.py) | 历史记录；`/api/pipeline/history`、归档/恢复、来源引用预检与解除 | `test_queue_reference_lifecycle.py`、`test_pipeline_control_plane.py` |
| 报告处理 | [processing_lifecycle.py](../document_ir_extract/backend/src/esg_v2/storage/processing_lifecycle.py) | 报告处理详情，归档筛选；`/api/report-processing/runs/{id}/lifecycle` | `test_processing_lifecycle.py` |
| 平台父计划与原生 Session | [plan_lifecycle.py](../document_ir_extract/backend/src/esg_v2/control_plane/plan_lifecycle.py)、[Targeted lifecycle.py](../targeted_table_extract/backend/src/esg_targeted/storage/lifecycle.py) | 报告提取详情、所属父任务入口 | `test_business_progress.py`、Targeted `test_lifecycle.py`、`test_lifecycle_api.py` |
| PDF 与 OCR/IR 库存 | [report_catalog.py](../document_ir_extract/backend/src/esg_v2/assets/report_catalog.py)、[inventory.py](../document_ir_extract/backend/src/esg_v2/storage/inventory.py) | 报告库归档、库存预览与回收；`/api/storage/inventory`、`/api/storage/trash` | `test_asset_lifecycle.py`、`test_source_revision_lifecycle.py` |
| 可恢复回收区 | [trash.py](../shared_runtime/src/esg_runtime/trash.py) | 生命周期/回收记录 | [test_lifecycle.py](../shared_runtime/tests/test_lifecycle.py) |
| 候选及正式数据引用 | [PlatESG main.py](../../PlatESG/backend/main.py)、[lifecycle.mjs](../../PlatESG/backend/lifecycle.mjs)、[0003_artifact_lifecycle.sql](../../PlatESG/backend/migrations/0003_artifact_lifecycle.sql) | 内部引用与保留接口，由工作台调用 | PlatESG `test_api.py`、`service.test.mjs` |

前端 [actions.js](../document_ir_extract/frontend/src/actions.js)、[lifecycle.js](../document_ir_extract/frontend/src/lifecycle.js) 及处理页按后端可用操作执行。清理类操作先读取权威依赖预检，再填写理由和确认；提交时后端再次检查。响应不确定的跨服务操作保存意图和稳定身份，通过重试核对远端状态，不能盲目重复清理。

测试覆盖未知依赖失败关闭、候选/正式引用阻塞、归档保留 pin、准确所有权释放、历史修订恢复、内容变化拒绝、回收移动失败回滚、响应丢失恢复、路径/符号链接边界和保留期。

## 5. P3：进度、事件、错误、时间和预算

### 5.1 共享结构化进度

[shared_runtime/progress.py](../shared_runtime/src/esg_runtime/progress.py) 定义 `BusinessProgress`、`ExecutionTiming` 和 `TaskError`；Document 和 Targeted 共享相应中文阶段合同。队列记录结构化状态和修订，原始日志保留为诊断事件，不能通过日志中的 `10/11` 推定完成。

| 阶段 | 当前显示依据 | 限制 |
|---|---|---|
| OCR | Producer 提供的实际识别页数/总页数 | 没有有效总页数时不构造比例 |
| IR | 已完成构建阶段数/实际阶段总数、阶段明细 | 当前是构建阶段计数，不能声称是页面或结构对象完成率 |
| 定向提取 | 所选输出条款、已处理条款、事实/缺口计数 | 已处理不等于有合格事实、审核通过或正式发布 |
| 模型理解 | 当前工具动作、对象、调用阶段与记录耗时 | 没有可靠业务总量时显示未知进度；调用额度不作为完成率 |
| 历史记录 | 已保存的结构化观测与成果 | 缺少计数时明确显示未记录，不回填虚构百分比 |

前端 [progress.js](../document_ir_extract/frontend/src/progress.js) 用于队列、处理和报告任务展示。部分成果、未完成范围、来源候选成熟度及当前错误由原生结果合同接续；“执行结束”“可审核事实”“已发布”分别表达。

### 5.2 四类时间与观测不确定性

| 时间类别 | 合同/来源 | 如何解释 |
|---|---|---|
| 排队等待 | `queue_wait_seconds` | 单独展示，不计为正在推理 |
| 执行 | `wall_elapsed_seconds`、`active_observed_seconds` | 墙钟与已连续观测的执行分别显示；后者也不等于 GPU 推理时长 |
| 单次模型调用 | `current_call`、Producer 调用及重试遥测 | 显示真实记录的调用阶段/耗时；未记录时保留未知 |
| 阻塞、重试、睡眠/时钟差异 | 调用尝试、`unobserved_seconds`、`sleep_or_clock_adjustment_seconds`、资源事件 | 只把可识别差异标为睡眠或时钟差异；无法连续观测的区间单独标记，不假装全程执行或全程睡眠 |

时钟观测使用 wall/monotonic 比较并记录缺口。恢复观测后重新检查执行时限。这里不声称存在已准确拆分全部等待原因的统一“阻塞总秒数”；真实模型服务及主机睡眠仍需业务复测。

### 5.3 计划预算与子进程硬时限分开

报告计划的 `report-execution-budget-v1` 按已开始的子执行计费：排队和人工复核等待排除；可识别睡眠/时钟差异单列并排除；未知观测缺口保守计入。显式恢复开始新的 allowance，历史用量保留。剩余额度传入后续子任务。

子进程监督器仍有单次执行的墙钟硬截止，用于防止进程无限运行。它与计划累计预算是不同规则。主机睡眠期间监督器不能即时执行终止；唤醒后会核对截止并处理退出。不能通过排除计划睡眠用量承诺子进程硬时限也随之延长。

代码依据为 [plans.py](../document_ir_extract/backend/src/esg_v2/control_plane/plans.py)、[scheduler.py](../document_ir_extract/backend/src/esg_v2/control_plane/scheduler.py)、[runtime_telemetry.py](../document_ir_extract/backend/src/esg_v2/runtime_telemetry.py) 及 Targeted 资源/调用合同。

### 5.4 持久化事件与错误分层

队列事件有持久化序号和状态修订；相同心跳或无业务变化的轮询不持续生成重复进度事件。`GET /api/pipeline/events/stream` 支持 `after` 与 `Last-Event-ID` 续读；SSE 不拥有 Worker。前端 [event-stream.js](../document_ir_extract/frontend/src/event-stream.js) 去重并恢复连接，页面隐藏时停止连接，保留轮询回退。报告处理也保留独立事件序列接口。

错误合同区分运行、协议、来源、身份、映射、发布和依赖。Producer 已提供分类时优先采用，避免“错误文字中含 JSON”把来源失败误归协议。技术原文保留在详情，用户界面展示原因和后端返回的可用操作。

测试依据为 [test_business_progress.py](../document_ir_extract/backend/tests/test_business_progress.py)、[test_runtime_outcomes.py](../document_ir_extract/backend/tests/test_runtime_outcomes.py)、[test_runtime_telemetry.py](../document_ir_extract/backend/tests/test_runtime_telemetry.py)、[test_pipeline_control_plane.py](../document_ir_extract/backend/tests/test_pipeline_control_plane.py) 和 Targeted [test_runtime_resources.py](../targeted_table_extract/backend/tests/test_runtime_resources.py)。覆盖日志不能推定完成、IR 完成阶段计数、调用预算不替代条款进度、SSE 游标重放、事件去重、错误因果优先级、睡眠观测与未知间隔、预算排除排队，以及 partial/中断成果接续。

## 6. P4—P7 后续边界

| 后续阶段 | 仍需建设和验收 |
|---|---|
| P4 独立工作台 | 报告/处理/提取/标准/审核/发布完整闭环；处理页缩略图、目录、问题汇总及原文高亮；父子上下文和修复往返交互 |
| P5 产品网站 | 公开展示与工程控制彻底分开；首页、企业、指标、趋势、比较及来源的产品化视觉和交互 |
| P6 结构迁移 | 分批整理模块、标准作者源、构建、注册表及文档；迁移必须保持既有路径和历史身份可解析 |
| P7 业务验收 | 中国石油历史失败样本回放、真实小任务、完整报告、准确性、可比较性及提取—审核—发布全链路验证 |

现有独立 PlatESG 的指标字典、候选/审核/发布连接和展示是此前建设成果。本轮不能把这些工程存在等同于生产部署、多审核员权限或真实报告准确率已通过。

## 7. 验收记录与真实复测

### 7.1 最终测试与现场证据

2026-10-05（北京时间）完成最终核对。下列计数按不同测试集合统计，重复的聚焦回归不重复累加：工程测试共 765 项通过，另有两套前端合同验收及真实只读浏览器验收通过。Document 测试使用其专用虚拟环境；Targeted 测试使用模型运行虚拟环境。

| 验收项 | 最终结果 | 证据/运行时间 |
|---|---|---|
| Document 后端全量测试 | 322 项通过 | `pytest document_ir_extract/backend/tests`；最后执行 36.41 秒 |
| Targeted 后端全量测试 | 357 项通过 | 与标准及共享集合联合执行；最后联合 421 项通过，16.70 秒 |
| 标准包测试 | 55 项通过 | 同一最终联合执行，未重复计数 |
| 共享运行时测试 | 9 项通过 | 同一最终联合执行，未重复计数 |
| PlatESG API/数据库生命周期测试 | API 10 项、数据库服务 12 项通过 | `PlatESG/tests/test_api.py`、`PlatESG/backend/tests/service.test.mjs`；隔离数据目录/内存数据库 |
| 前端合同测试 | 两套通过 | `frontend_contract_test.cjs`、`processing_contract_test.cjs`；模拟 HTTP，不调用模型 |
| 联合指纹与实际服务握手 | 三服务同版本、aggregate ready=true | `f9797a4449f03e739cc19a1ab3437eee18fa4d71e4024b3bcdb3906815b31a48`；另复核 55 项握手/平台用例及原生 HTTP 守卫 |
| 只读现场页面及接口核对 | 66 次浏览器请求；0 写请求、0 JS/控制台/HTTP 错误 | 41 活动草稿、58 规划占位、54 历史版本；49 原号、16 选中、33 待建设；包详情按需 5→6；处理/队列/提取历史与历史结果可读 |
| 历史报告及产物保持验证 | 15 份 PDF、34 个 manifest SHA-256 一致；公开数据集及观测逐项一致 | 13 条队列记录、10 个原生提取任务、1 个报告会话保留；无新增真实处理任务 |
| 持久化事件现场续读 | 通过 | 实际 SSE 请求同时使用 `after` 与 `Last-Event-ID`，续读正确事件 50272；连接不触发执行 |
| 当前来源保护覆盖 | 当前已登记本地范围 verified=true | 活动库为 `PlatESG/.local/demo-data`；两处默认旧库目录不存在，没有发现实际自定义数据库配置；不能据此承诺检查任意外部数据库 |

运行入口为 [统一工作台](http://127.0.0.1:18081) 和 [独立数据平台](http://127.0.0.1:8765)。现场证据已保存至工作区的 [备份与验收目录](../../../.local/platform-runtime/backups/p0-p3-20261004T172518Z)：`preservation-baseline.json`、`runtime-acceptance.json`、`engineering-tests.json`、两个原始 SQLite 备份、重启前的演示发布库副本，以及 `live-workbench/acceptance.json` 和六张截图。

中国石油历史会话仍为中断：35 个所选条款、29 个已处理、2 个已进入但未完成、4 个未执行，261 条待核来源候选、0 条有效 Core。页面没有把候选当作批准事实。IR 构建进度采用实际完成阶段数；历史记录没有可靠计数时显示未知。

最终复测还纠正了一项测试隔离问题：原生生命周期 API 测试曾通过默认 localhost 探测连接演示发布库，只为合成 `ir-fixture-e2-4` 样本写入保留锁。已严格核对测试目录及唯一 scope，恢复该临时测试回收记录并释放保留锁，保留审计 tombstone；未涉及历史记录或观测。恢复证明保存在 `synthetic-test-reservation-recovery.json`。测试 fixture 已改为默认离线 transport，最终联合 421 项重新通过。原生 Job 的 GET 进度投影也不再把刷新时间伪称为最近业务进展时间。

最后目视审计还补齐真实历史页面：主摘要采用后端中文分类，英文原文折叠保留；`repair_format` 达到硬截止属于运行时限，不能因动作名误归协议错误。终态任务的旧活动调用阶段显示为历史记录，不冒充正在生成；已真实记录为中断的调用仍显示中断。24 组终态/旧活动 phase 的前端合同通过。旧创建至更新时间的 8617 秒明确标为“任务记录跨度（含排队；执行与未观测时间分解未记录）”，单次调用的已记录 238 秒独立展示。错误摘要跨完整行，真实浏览器验证其宽度至少为容器的 90%，原文展开后的截图也已目视核对。

只读现场测试入口为 [live_workbench_readonly_test.cjs](../document_ir_extract/frontend/tests/live_workbench_readonly_test.cjs)。它用于核对真实服务版本、标准目录/主要清单和界面入口，不创建处理、提取或清理任务。页面截图不是后台重负载链路已通过的证据。

### 7.2 尚需由用户发起的真实业务复测

1. 选择一份真实 PDF，不选择标准包，完成独立本地 OCR→IR；核对页数、结构、证据状态和新任务身份。
2. 对同一报告验证自动复用、只重建 IR、重新识别三种模式；核对实际配置、登记来源和历史版本保留。
3. 在 OCR 完成而 IR 未完成时测试中断/恢复；确认不重复识别，取消等待真实进程退出。
4. 用有限证据报告核对受限页面/对象，确认所选条款按真实能力准入，而非全报告一律可用。
5. 从处理页进入证据复核，生成新 IR，显式固定新修订，继续同报告定向提取；历史任务保持原输入。
6. 验证实际单次模型调用、重复协议失败、主机睡眠/唤醒及预算结果；核对墙钟、观测时间、未知间隔和终止原因。
7. 在经过明确核查的隔离或正式来源范围内验证归档/恢复、候选或正式引用阻塞、未核查多库失败关闭；禁止为通过验收而绕过真实依赖。
8. 最后完成主要原编号的事实、单位、主体、期间、方法、原文证据、审核与发布检查。工程测试通过不能代替这一业务验收。
