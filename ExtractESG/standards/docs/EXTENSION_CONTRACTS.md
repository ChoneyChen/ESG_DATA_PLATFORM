# 延伸条款合同与全链路接入

本文件记录已实现的标准系统基础合同，以及其余服务必须满足的接入边界。它不把基础合同测试当作真实提取、审核发布或产品验收。

## 1. 已实现范围

`esg_standard_packages.extensions` 提供独立、严格类型的定义、建议、审核收据、编译成果、注册、选择和计算血缘合同。首个作者草稿属于 E1-6，父包为实际 `esrs.2023-set1.e1-6@2.1.1`，固定实际 `source_digest`，链接三个实际原条款。

| 身份 | 含义 |
| --- | --- |
| `E1-6_07` | 原数据点：范围1排放 |
| `E1-6_09` | 原数据点：范围2地点法排放 |
| `E1-6_10` | 原数据点：范围2市场法排放 |
| `project.esrs.2023-set1.e1-6.extension.scope1-scope2-total` | 项目延伸：范围1＋范围2合计 |

延伸定义没有 `source_datapoint_id`，禁止加造 E1-6_36 或把合计映射成任一范围。原 IG3 目录、当前 41 包及 49 个平台主要原编号不改变。一披露小号一包继续成立：延伸在父包的目录视图下展示，物理上为独立且可选择的 overlay。

`package_core` 表示作者对包内延伸核心的判断。平台是否把它纳入主要范围由平台配置决定；任务是否输出它由显式选择决定。任何一层都不自动激活上一层的全部条款。

目前首例仍为建议草稿，注册表为空。实际批准和发布接入尚须通过认证工作台操作并保存权威收据，不应在构建脚本中伪造批准记录。

## 2. 来源披露与系统计算

直接披露支持两个关键谓词：`ghg_scope=scope_1_2`、`aggregation_role=total`。原文、数值、单位、主体、期间、边界及物理证据仍由来源事实合同负责。合计一词不能证明毛排放、范围2方法、GWP 或合并口径。

缺少方法及排放性质时保留缺口。合同 `context_unknown_policy=preserve_missing_no_imputation` 不补造值；`comparison_verdict` 返回缺少的轴和不可比较原因。已审核的直接披露可以作为展示观测，不能进入缺失口径的比较集合。

同一延伸包含两条精确求和规则：

- 地点法合计：E1-6_07 ＋ E1-6_09。
- 市场法合计：E1-6_07 ＋ E1-6_10。

操作数必须是已审核的本期实际毛排放总量、数值点，使用版本化规则转换到相同的 tCO2e。必须明确匹配主体、企业类型、ISO 起止日期、报告边界、实际属性、GWP、合并方法和核算标准。缺失、未知、冲突不能满足匹配；区间不能变成假数值点。

范围2方法从范围2操作数投射到结果的 `scope_2_method`。两个操作数的 `measurement_method` 不简单要求字符串相等：范围1的燃料测量方法与范围2的地点法／市场法具有不同职责。`operand_selectors`、`shared_match_axes`、`target_axis_projections` 将这三种关系分别表达。

直接披露与计算结果均保存 `value_origin`。计算结果的审核状态先为 `pending`。两者相等可以形成交叉验证，但血缘仍独立；两者不一致保留双方并进入冲突审核，不能静默覆盖。方法未知的直接合计也不能用于反算未披露的单独范围。

核对依据：GHG Protocol 将地点法和市场法作为不同的范围2方法；IG3 的 E1-6_12／13 是包含范围3的总排放，不能用范围1＋2合计替代。[GHG Protocol Scope 2 Guidance](https://ghgprotocol.org/scope-2-guidance)、[EFRAG 原 IG3 E1 数据点](https://knowledgehub.efrag.org/eng/interactive/ig/ig3/esrs-e1-climate-change/2023-07-31-da)。本项目继续固定用户原始工作簿，不自行切换到新版 ESRS 编号。

## 3. 标准系统 API

| 接口 | 职责与约束 |
| --- | --- |
| `ExtensionProposal` | 人或模型提出建议；状态只能为 proposed；不能携带批准字段 |
| `decide_proposal` | 由可信服务端认证 principal 提供 `ReviewAuthority`，保存批准或拒绝收据 |
| `compile_extension` | 校验父包 ID、版本、摘要、原链接、操作数及单位维度；要求精确定义摘要的批准 |
| `validate_release` | 校验编译成果、定义、批准和内容摘要一致，重查父包 |
| `register_extension` | 返回新注册表；同身份同版本必须同内容、同路径，禁止覆盖 |
| `write_extension_release` | 创建不可变 release；检查相对路径、符号链接逃逸及注册身份 |
| `load_registered_extension` | 按注册记录读取、校验，不能扫文件夹猜当前版本 |
| `validate_selection` | 固定 release 身份及显式来源／规则选择，给出内部操作数，不改原编号分母 |
| `DerivedExtensionLineage` / `validate_lineage` | 检查精确数值、规则、选择器、共享身份及方法投射；返回 pending 结果 |
| `comparison_verdict` | 检查显式比较轴；不修改、不补造审核内容 |

`ReviewAuthority` 是可信 HTTP handler 的内部参数，不能在建议 HTTP 请求中接受。库本身不认证令牌，也不证明审核收据的真实性。接入服务必须从服务端认证结果形成角色、把审核收据持久化，并按收据 ID 从数据库验证批准。摘要用于不可变内容校验，不是审核签名或身份认证替代物。

定义批准与来源事实批准是两件事。批准一个公式并不批准其操作数，也不批准报告里被模型提出的事实。计算血缘中审核决定 ID 必须通过数据库解析，不能信任客户端递交的 UUID。

## 4. 提取与计划合同接入

保持现有 `standards` 原条款选择与 `selection_snapshot.source_datapoint_ids`。增加独立 `extension_selections`，使用 `ExtensionSelection` schema。空列表等于没有延伸输出；建议不能自动成为本次输出。

执行预览至少分开返回：

- 原条款输出：保留原编号及原输出数量。
- 延伸输出：extension ID、定义版本、release 摘要、父包、标签及包内核心属性。
- 原条款内部操作数：由显式启用的规则得到，不能增加原输出分母。
- 身份与方法上下文：仅为选定原条款／延伸读取。
- 待审核建议：不参与任务完成分母或正式发布。

`validate_selection` 的 `original_output_datapoint_ids=[]` 明确该延伸没有伪造原输出编号，`extension_output_count=1` 只计一个延伸概念；`internal_operand_metric_ids` 只含选择规则所需原指标。只选 reported 不必提取计算操作数；只启用地点法不自动提取市场法。

运行时应使用组合合同 `RuntimeStandardSnapshot`：分别固定 base package ID/version/digest、extension release 摘要、选定规则和组合摘要。可构造统一的执行视图，但不能修改原 `CompiledStandardPackage.metrics` 或冒用父包原摘要代表新组合。Bundle 需要分别保存原标准快照及延伸快照，导入器从组合合同解析 clause kind。

原事实阅读继续保存报告真实词句，延伸仅改变语义映射目标。模型遇到未注册表述时可以返回候选建议；建议包含父包、拟议概念、所关联原点、证据和理由。服务端不能执行模型给出的公式代码。通过审核、编译、注册后，用户创建新的计划版本／附加映射投影；已有任务继续引用原组合快照，不能原地扩大范围。

对于中国石油基线，原条款负例继续成立：合计不能进入范围1／2；新增延伸后同一来源成为延伸正例。不能通过金标注入来源事实或伪造提取完成。

## 5. 平台字典与数据库接入

现有 `metric_definition_versions` 将字典版本放在定义行，seed 会拒绝相同定义加入不同字典版本。延伸升级应先把**指标定义版本**与**字典成员快照版本**分开：独立 `metric_dictionary_versions` 和 `metric_dictionary_membership`。原 49 个定义可以被新字典复用，旧发布版本仍固定旧成员集合。

指标身份增加类型：`original_datapoint` 或 `project_extension`。原项保存原 `source_datapoint_id`；延伸保存 `extension_id` 和相关原点列表，原编号列为空。不要把 extension ID 填入名为原编号的列。平台主要延伸配置独立于原 49 项，校验被选延伸是包内核心且已批准登记。

来源披露仍走事实审核 → 延伸映射审核 → 规范上下文 → 发布检查。现有 `standard_requirements` 可增加 extension 类型并链接父包；其原编号字段语义必须明确。发布器应按 clause identity 找定义，不能只用 `requirement_code` 推定原数据点。

正式计算属于审核发布域。计算处理器读取固定输入数据集、已审核原指标观测与规范上下文，按注册公式执行。现有提取期 `DerivationExecutor` 可形成候选，但不能将提取期尚未审核的 Core 直接当正式操作数。正式计算的最少新增记录为：

| 对象 | 必须保存 |
| --- | --- |
| 计算任务／请求 | idempotency key、输入数据集、extension release、所选规则、状态及阻塞原因 |
| 计算观测 | value_origin=derived、规范值／单位、完整身份、公式版本、独立审核与发布状态 |
| 操作数血缘 | 已发布 observation ID、指标定义版本、context ID/revision、事实及映射审核决定、精确原规范值、单位转换规则摘要 |
| 冻结计算收据 | rule digest、输入数据集、算术版本、输出精确值；与 `DerivedExtensionLineage` 一致 |

数据库必须检查所有血缘引用属于输入数据集且字段与权威行一致。公式审批、计算审核、操作数当前审核／发布状态都需要检查。重述、撤回或公式变更产生新数据集，不改旧观测；恢复／重试按计算身份幂等。发布事务与 outbox 保存同一结果，不允许先出公开数据后补血缘。

操作数的 `metric_id` 是平台概念 ID，`standard_metric_id` 是标准包指标 ID，二者分别保存。平台通过被冻结的定义 `standard_binding` 解析标准身份，并与父包、原编号和选择器核对；例如平台 `ghg.scope1` 不能被误当成标准 `esrs.2023-set1.e1-6.dp07`，也不能仅凭名称猜匹配。

直接披露的合计缺少方法时，可以保存已审核数值展示，比较状态为不可比较并返回原因；原单位亿吨可精确换算为 tCO2e，但单位换算不会补造方法或毛排放属性。计算目标不再要求虚构“结果本身在 PDF 上”的物理位置，而是通过每个操作数返回其原文证据。

## 6. 前后端接口分工

建议读接口：

- 标准目录按父包返回 `original_clauses` 与 `extensions`，原 41 包计数不变。
- 认证工作台返回建议、批准、构建、选择与审核 DTO，以及可执行操作和具体阻塞原因。
- 公开指标返回 clause kind、extension ID、父包业务名称、相关原编号、含义及必要方法。
- 公开观测返回 `value_origin` 与科学计算说明，来源证据按操作数展示；不返回内部审核人或运行诊断。
- 比较 API 读取固定发布版本中的延伸观测，不在浏览器临时相加。返回可比较组和被排除原因。

工作台必须能：查看延伸、选择只提取直接披露、选择指定公式、审核建议、逐项核对操作数、处理披露／计算冲突。状态区分建议、定义已批准、已编译登记、已选输出、事实已审核、已发布、符合比较口径。

产品网站以“范围1＋范围2合计”名称展示直接披露／计算值及方法说明；未知方法只展示，不伪造趋势组或比较。原编号展示为“关联 E1-6_07、E1-6_09、E1-6_10”，不能给延伸伪造官方条款序号。计算来源允许访问对应原观测及原文。所有产品读取仍固定 dataset_version。

## 7. 验收边界

本批隔离测试覆盖：真实父包及原点链接、版本／摘要错配、模型仅建议、能力授权、审批后修改、不可变注册和文件、路径逃逸、49 原清单保持、显式输出与内部依赖、未知方法及净排放排除、两个范围2方法投射、长精确数值及错算、身份／方法冲突、禁止任意表达式、schema 与代码一致。

后续必须另行证明：

1. 身份认证服务批准后能登记真正的不可变 release，草稿不会被默认提取。
2. 原 49 条及历史固定版本仍可读取，新增延伸不改原编号或默认输出。
3. 原文真实合计只映射为延伸，年份、主体、单位和披露含义正确。
4. 已审核范围1／2在两种方法下自动生成正确的独立计算观测，未知口径或不相容来源具体阻塞。
5. 计算血缘在数据库闭环、重试幂等、撤回／重述更新新版本，旧公开版本不变。
6. 工作台可选择与审核，公开页及比较页正确表达来源和方法，无内部工程字段泄漏。

完成这些真实链路证据后，才能宣称延伸条款已完成 P4–P7 适配。

## 8. 已实现的运行与候选交付合同

执行适配器位于 `extraction/src/esg_targeted/standards/runtime_snapshot.py`。原包快照继续写入 `input/standard-package.json`；延伸选择另存 `input/runtime-standard-snapshot.json`。`RuntimeStandardView.model_dump()` 刻意返回原包，避免既有快照写入器把组合内容冒充原包。需要完整执行目录的读取器使用 `execution_catalog_dict()`。

组合快照 schema 为 `runtime-standard-snapshot-1.0`，保存 `parent_package_id`、`parent_package_version`、`parent_source_digest`、`selections`、完整 `extension_releases` 和 `runtime_digest`。组合摘要是去掉 `runtime_digest` 后整个规范 JSON 的 SHA-256。运行、检查、阶段成果和恢复会验证请求、组合摘要、批准 release 及实际父包一致；恢复不依赖当前可选注册表仍有旧版本。选择解析按 extension ID、定义版本、release 摘要精确匹配，不能用同 ID 的新版本覆盖显式旧版。

新请求通过 `ESG_EXTENSION_ROOT` 指定权威服务投影的运行注册目录，未设置时读取标准根下的 `extensions`。生产入口应指向 `.local/extensions`。该目录需要合法空注册表才能表达尚未批准任何延伸；缺失或损坏不会被当作批准。读接口是 targeted 的 `GET /api/extensions` 和报告门面的 `GET /api/pipeline/extensions`，只返回校验通过的完整 release，建议草稿不参与选择。

报告请求可以只传 `extension_selections`、令原 `standards=[]`。服务根据精确批准 release 建立父包行，原 `metric_ids=[]`，不会默认激活父包全部原点。预览分别返回 `original_output_count`、`extension_output_count`、`internal_operand_count`。native 报告会话及 standalone 任务在入队前固定完整组合；PDF 门面同样在报告处理入队前保存权威预览返回的完整 `frozen_runtime_standard_snapshots`，在创建 native 会话前再次逐字段核对原父包及组合快照。缺失、损坏或版本变化会明确阻塞，不能重建或换版。该请求字段只是冻结期望，不能凭客户端快照取得批准；新 native 任务仍需核验当前批准登记。已创建任务恢复使用自身完整快照，登记撤回不会改变旧任务内容。

Bundle 的可选字段是：

| 字段 | 内容 |
| --- | --- |
| `runtime_standard_snapshot` | 完整组合快照 |
| `execution_metric_catalog` | 原父包指标及本次选中的延伸执行定义；延伸的原编号为 null |
| `extension_computations` | 明确启用的计算请求，初始 `awaiting_reviewed_operands`、`reviewed_publication`、输出审核 pending |
| `internal_dependency_scope` | 独立的原操作数候选交付，见下文 |

`summary.standard.source_digest` 和 binding 的 `package_digest` 始终是父包摘要；组合另存 `runtime_standard_digest`。延伸 binding 另外保存 clause kind、延伸身份／版本／release 摘要及相关原编号。CSV 和检查 API 同样使用 nullable 原编号，不能将 extension ID 当原编号回填。

`internal_dependency_scope` 与 `results/internal-dependencies.json` 使用 `internal-dependency-facts-v2`：

- 身份：父包 ID、版本、原摘要、运行摘要及 `selected_output_metric_ids`。
- 原白名单：`metric_ids`、`source_datapoint_ids`、逐条 `metrics`，每条保存真实原编号、`original_datapoint`、依赖职责及关联延伸。
- 固定职责：`required_by_extensions` 保存延伸 ID／版本／release、所选规则、内部操作数及已选输出操作数；职责为 `reviewed_publication_operand`。
- 可审核材料：独立六类 `records`、`outcomes`、`core_bindings`、绑定引用的 `source_facts` 和物理 `evidence_references`。顶层证据与内部 Core 证据完全一致。
- 边界：`scope_role=internal_dependency`、`output_policy=excluded_from_selected_output_and_coverage`、独立 `contract_validation`。

导出时重查真实原编号、完整固定依赖图、每个规则／release、逐条职责和 Core 合同。内部材料可以交付候选审核，不能直接变成已发布操作数。平台导入器应按该白名单接收内部原事实，标注 `is_internal_dependency` 与关联延伸；事实、映射、规范上下文通过审核并正式发布后，才可执行该延伸公式。selected records／mappings 和输出覆盖分母不会混入内部事实。

纯 derived 选择不会向模型索取“计算结果在 PDF 上的披露”，不会产生量化结果，只创建等待审核操作数的任务和请求。单独 reported 选择不调度公式操作数。未知的范围2方法、毛／净性质、GWP、合并及核算标准保留空值；阅读合同增加这些可选真实轴，来源身份另外固定字段定义摘要，旧字段协议的缓存不会被误用。

离线回放工具 `targeted_table_extract/backend/tests/extension_replay.py` 提供 `build_reported_extension_bundle(empty_output_root, compiled_release)` 和 `build_internal_extension_bundle(empty_output_root, compiled_release)`，也支持 `python -m tests.extension_replay --kind reported|internal --release ... --output ...`。需将 targeted backend 根目录加入 PYTHONPATH。它仅生成已知物理 fixture 的固定响应，不调用真实模型或联网，不创建批准、登记或公开数据。输入 release 必须先在平台回放的**隔离权威数据库**核验；工具不会将库合同摘要当审核身份认证。reported 回放数值是 30 tCO2e、2024、Fixture Company、中国境内运营，方法及其余比较轴仍未知。internal 回放在白名单中交付 E1-6_07 的 20 和 E1-6_09 的 10 两个原报告事实，selected 输出仍为一个等待操作数审核的延伸；未知主体与科学口径不会被补造，也不会产生 derived 数值。这份样本只能证明候选交付，不能证明科学计算或正式发布。

当前隔离验收同时覆盖 reported 合计进入 Core／Bundle、主体期间边界与原证据、无方法／GWP补造、derived-only 内部20／10两个独立 reported 原事实可审核、禁止伪造 PDF 计算值、双版本显式选择、完整冻结恢复、原目录不扩、阶段输出分母不扩。真实报告准确率和平台正式审核发布仍需由整体 P4–P7 验收另行证明。
