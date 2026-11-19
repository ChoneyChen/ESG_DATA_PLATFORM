# 合同与接口规范

本章解释跨模块语义。精确字段类型以链接的Pydantic/Schema为准；[HTTP参考](reference/http-api.md)列出当前注册、参数、返回类型及源码位置。运行时可从对应服务`/openapi.json`取得实际OpenAPI，不通过导入服务来生成文档。

## 权威来源与版本

| 合同 | 当前写入版本 | 权威源码 |
|---|---|---|
| 可执行单条 | clause-contract-1.4 | [ClauseContract](../ClauseESG/src/esg_clauses/contract.py)及[Schema](../ClauseESG/schemas/clause.schema.json) |
| 结构描述 | clause-descriptor-1.0 | [descriptor.py](../ClauseESG/src/esg_clauses/descriptor.py) |
| 报告资源/批次 | report-resource-1.0 / report-resource-batch-1.0 | [报告合同](../ExtractESG/contracts/src/esg_reports/contract.py) |
| 原值 | source-fact-2.1（兼容读取 2.0） | [事实合同](../ExtractESG/contracts/src/esg_contracts/facts.py) |
| 搜索与物理证据 | clause-search-plan-1.2 / object-search-page-1.0 | [编译器](../ClauseESG/src/esg_clauses/search.py)、[证据DTO](../ExtractESG/contracts/src/esg_contracts/evidence.py) |
| 阅读/属性卡 | clause-reading-card-1.1 | ClauseContract.reading_card |
| 结果/组织视图 | extraction-fact-bundle-2.4 / organized-extraction-1.1 | [结果合同](../ExtractESG/contracts/src/esg_contracts/results.py) |
| 数据库交接 | extraction-export-1.2 | [导出合同](../ExtractESG/contracts/src/esg_contracts/exports.py) |
| 接收回执 | extraction-receipt-1.0 | 同上IngestionReceipt |
| 模型信封 | model-call-1.0 | [model_calls.py](../ExtractESG/shared_runtime/src/esg_runtime/model_calls.py) |
| 观测/投影/产品 | capture-observation-1.0 / capture-projection-1.0 / capture-dataset-version-1.0 | [产品DTO](../database/src/esg_database/products_contracts.py) |

观测DTO版本与转换算法版本是两个概念；当前`TRANSFORM_VERSION=capture-observation-1.1`定义在projection.py。IR为document-ir-v0.12，OCR/IR封装分别是ocr-package-v1/document-ir-package-v1；内部物理模型在document模块。

## 单条合同

| 字段 | 含义与消费者 |
|---|---|
| clause_key、group_id、labels、definition | 稳定条款身份、导航组、短名字与核心定义；UI及阅读器消费 |
| distinguish_from、exclusions、exact_labels | 概念边界、排除与精确别名；归单条所有 |
| data_kind、value | quantitative/qualitative及目标值语义；定性没有数值规范单位 |
| attributes | 属性ID→定义、读取指令、分类词典、补读词、local/report来源范围及可选value_pattern。分类代码用于目标冲突时须声明有证据别名的词表。value_pattern按本条语义约束已引用属性的字符串叶子，未声明时保留既有JSON值类型 |
| retrieval | 正负词、词组、来源优先级、锚点、候选类型和vocabulary_refs；检索从这里取得要求 |
| reading | 通用算子、自然语言指令、规则和词典；解释器消费声明，不维护另一份指标分支 |
| purposes | capture保留all_reported；enrichment指定可选字段；target_scope表达目标关系；comparison表达后续比较要求 |
| output_scopes、evidence | 所求口径及确认它的证据需求；不等于所有原值必须具有所有属性 |
| organization | 显示列、行表/分组/矩阵；引用属性ID，不负责生成新测量 |
| provenance、relationships | 标准来源与相关条款；关系不触发隐含执行依赖 |
| readiness、blocking_reasons、clause_digest | 是否可以执行、明确未填写内容、内容摘要；ready不是质量认证 |

展示组只列成员，命名集合只列条款ID。结构描述没有data_kind，不输出第三类事实。保存当前条款产生新摘要；运行输入冻结副本，编辑不能静默改写旧结果。

## 两类数据合同

SourceFact 包含原值、事实和来源标识、可选实际身份及页位置。线上类型为 `value.kind` 的 `quantitative` 或 `assertion`，分别对应条款的 `quantitative`、`qualitative`。2.1 允许用 `pdf_page` 记录原页转录。老事实 2.0 按原物理摘录约束读取。

| 类型 | 必需原值 | 可选结构化信息 |
|---|---|---|
| quantitative | 印刷值 `value_raw` | 印刷单位、十进制字符串、上下界、比较符及解析状态 |
| assertion | 原文陈述 `statement_raw` | 通用实际身份及扩展属性 |

模型输出 `items` 数组，每项提供 `clause_key`。原句或单个数值使用 `value`，表格同一行用 `measurements` 列出各期间的 `value`、`period`。每个期间可覆盖行级 `unit`、`relation` 和 `scope_ids`，并按字段覆盖 `attributes`。行上可共用 `label` 和上述字段。程序以定量/定性合同保存，沿用共享数值和期间解析。解析不出仍保留原值，未知属性留空。报告元数据不填入未声明的实际期间或统计主体。每个披露期间分别保存，保留印刷精度和比例。

`EvidenceLocation` 的页下标为 0 基，界面显示加一。页级事实以记录内容产生事实 ID，原始页及印刷量值另外保留来源锚点。页级 excerpt 是模型转录，不代表逐字或逐格独立核验。来源使用 `DocumentReference.source_kind=pdf` 和原 PDF SHA-256，沿用交接字段 `ir_run_id=pdf-<sha256>` 表达稳定文档引用，页码属于原件。已有 Document IR 可传入有效修订以提供页提示。旧对象、旧引文和候选诊断仍可随既有封存读出。

`ClauseMapping` 单独记录条款摘要、事实 ID 和 `match`、`different`、`uncertain`。覆盖范围和实际值分开保存。

## 端口及HTTP分工

| 连接 | 请求与响应 | 主要入口 |
|---|---|---|
| Console→编排18080 | ExtractionPlanRequest→ExtractionSession，预览、提交、读取、取消、续跑 | /api/report-sessions及子路径 |
| 编排→条款/提取18180 | RunRequest冻结目标；CreateRun携带request、target_snapshot和submission_key | /api/targets/freeze、/api/runs |
| 提取→文档18080 | PDF 引用、原生文字和原页图像；可选既有 IR 引用 | /api/report-assets/{asset_id}/reading-source、reading-pages、reading-pages/{page_index}/image；/api/evidence/{ir_run_id} |
| 提取→模型 | instructions/context/schema/images/max_tokens/cancel_check → 页提示或转录及调用回执 | ModelPort，由任务选择的本地Qwen或DeepSeek适配器实现 |
| 提取→数据库18280 | 封存ZIP字节→IngestionReceipt | POST /v1/ingestion/exports |
| Console→数据库 | 接收/投影状态、预览、成员分页、显式发布/撤下 | /v1/ingestion、/v1/projections、/v1/datasets |
| Plat→数据库 | 有效发布的产品、观测、定义、来源、精确导出 | /v2/datasets及子路径 |
| Plat→文档 | 完整PDF散列→原PDF字节 | /api/report-sources/{source_sha256}/file |

DocumentPort、ModelPort、ArtifactPort、ResultPort的实际签名见[ports.py](../ExtractESG/extraction/src/esg_extraction/pipeline/ports.py)。部署HTTP和同进程适配都保持这些值接口，不以绝对服务器路径代替引用。

提取请求的`model_selection`只含`provider`和`model`。旧请求缺字段时沿用本地Qwen，`deepseek`需显式指定`deepseek-flash`或`deepseek-v4-pro`。另有可选 `document_model_selection`，可选择本地 Qwen 或 DeepSeek Flash 为弱文字页定位。DeepSeek V4 Pro 仅接受文字。预览返回模型选择，任务冻结两阶段配置，续跑沿用原选择。`GET /api/model-providers`提供只读状态及模型清单，`PUT /api/model-providers/deepseek/credential`单独写入密钥，仅返回同一状态。密钥不进入会话、检查点、调用回执或导出。

## 封存交接与幂等

manifest声明authority=facts，列出八类成员：facts、properties、evidence、relations、coverage、targets、document、diagnostics。文件大小/摘要/记录数随包保存；targets包含本次冻结合同，document及sources保存证据快照。展示矩阵不是权威成员。

export_id、内容摘要和接收回执标识同一交接；相同包重送返回幂等结果，不能借同一ID覆盖不同字节。接收状态accepted/partial/rejected；delivery状态prepared/delivered/partial/failed/unknown。接收不等于投影完成或发布。

当前接收器明确支持已保存的export-1.1和1.2。FactBundle读取器还接受2.2/2.3；这是已有冻结数据的边界，不代表恢复旧foundation/standard或旧指标字典。其他合同兼容范围按源码声明，不虚构“全版本兼容”。

## 变更、分页与错误

- 稳定ID不随名字变化；内容摘要随语义变化。接口改动同轮更新消费者和生成类型，破坏性变化提升合同版本并说明旧数据读取政策。
- 新增可选字段需保持旧冻结内容含义；不能用默认公司/主年填补旧事实。字段必填只用于表示物理原值所必需的内容。
- 读取分页采用limit/cursor，带查询摘要/快照；筛选改变后丢弃旧cursor。页面不能只取首批就宣称完整。
- 典型错误：404未知身份；409修订/预览或生命周期冲突；422请求语义不合法；503依赖不可用；413超出实际容量。具体以端点实现为准。
- 网络写入不盲目自动重放；用submission_key、export_id等业务幂等标识处理重试。输入输出不泄露凭据或内部敏感配置。

## 内部worker与数据库RPC

队列worker行协议由`esg_contracts/worker.py`定义：标准前缀`ESG_PIPELINE_EVENT `后接含kind的JSON，消费者在native_dispatch及worker中解释进度/日志/结果。执行进程仍需继承运行时槽位；日志行不是可以随意伪造的业务完成状态。输入/输出、模型调用和最终事实合同各自独立。

数据库内部桥为DatabaseBridge→capture_worker.mjs，长驻单连接、序列化JSON行：请求`{id, method, params}`，响应`{id, result}`或`{id, error:{code,message}}`。方法由capture_worker.mjs的显式分派表定义，SQL不向外部模块暴露；超时写入先查业务回执，不能自动重放。

| 命令组 | 内部方法举例 | 所有者 |
|---|---|---|
| 接收/健康 | health、ingestion_health、ingest_export、export_receipt、capture_facts | ingestion/captures查询 |
| 投影 | projection_input、store_projection、projection_status、pending_projections、record_projection_failure | capture_products查询 |
| 预览/产品 | product_input、store_preview、preview_page、publish_dataset、withdraw_dataset | products/publication查询 |
| 观测/定义/来源 | observation_page、observation_detail、product_definitions、dataset_sources、public_source | publication查询 |
| 生命周期 | source_references、reserve_source、update_source_reservation、archive_projections、purge_projections | sources/products查询 |

外部服务使用HTTP或独立产品客户端，不自行启动该SQL worker。完整内部方法表以[分派源码](../database/runtime/capture_worker.mjs)为准；更改该表需要同步database/client调用及所属事务，不增加另一个跨模块私有RPC。
