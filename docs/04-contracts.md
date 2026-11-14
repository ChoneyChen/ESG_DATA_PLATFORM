# 合同与接口规范

本章解释跨模块语义。精确字段类型以链接的Pydantic/Schema为准；[HTTP参考](reference/http-api.md)列出当前注册、参数、返回类型及源码位置。运行时可从对应服务`/openapi.json`取得实际OpenAPI，不通过导入服务来生成文档。

## 权威来源与版本

| 合同 | 当前写入版本 | 权威源码 |
|---|---|---|
| 可执行单条 | clause-contract-1.4 | [ClauseContract](../ClauseESG/src/esg_clauses/contract.py)及[Schema](../ClauseESG/schemas/clause.schema.json) |
| 结构描述 | clause-descriptor-1.0 | [descriptor.py](../ClauseESG/src/esg_clauses/descriptor.py) |
| 报告资源/批次 | report-resource-1.0 / report-resource-batch-1.0 | [报告合同](../ExtractESG/contracts/src/esg_reports/contract.py) |
| 原值 | source-fact-2.0 | [事实合同](../ExtractESG/contracts/src/esg_contracts/facts.py) |
| 搜索与物理证据 | clause-search-plan-1.2 / object-search-page-1.0 | [编译器](../ClauseESG/src/esg_clauses/search.py)、[证据DTO](../ExtractESG/contracts/src/esg_contracts/evidence.py) |
| 阅读/属性卡 | clause-reading-card-1.1 / clause-enrichment-card-1.0 | ClauseContract.reading_card/enrichment_card |
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
| attributes | 属性ID→定义、读取指令、分类词典、补读词和local/report来源范围；不在展示层重复定义 |
| retrieval | 正负词、词组、来源优先级、锚点、候选类型和vocabulary_refs；检索从这里取得要求 |
| reading | 通用算子、自然语言指令、规则和词典；解释器消费声明，不维护另一份指标分支 |
| purposes | capture保留all_reported；enrichment指定可选字段；target_scope表达目标关系；comparison表达后续比较要求 |
| output_scopes、evidence | 所求口径及确认它的证据需求；不等于所有原值必须具有所有属性 |
| organization | 显示列、行表/分组/矩阵；引用属性ID，不负责生成新测量 |
| provenance、relationships | 标准来源与相关条款；关系不触发隐含执行依赖 |
| readiness、blocking_reasons、clause_digest | 是否可以执行、明确未填写内容、内容摘要；ready不是质量认证 |

展示组只列成员，命名集合只列条款ID。结构描述没有data_kind，不输出第三类事实。保存当前条款产生新摘要；运行输入冻结副本，编辑不能静默改写旧结果。

## 两类数据合同

共同外壳SourceFact必须能定位实际来源，包含source_fact_id、source_anchor_id、document_id、IR身份、interpretation_id、value及evidence；identity等附加字段可以未知。线上的判别键在`value.kind`：定量quantitative，定性assertion。条款data_kind的qualitative映射到事实assertion，不是第三类。

| 类型 | 必需原值 | 可选结构化信息 | 来源约束 |
|---|---|---|---|
| quantitative | 非空value_raw | 数值/上下界用十进制字符串，comparator、parse_status、unit_raw和extensions | 至少一份role=value的物理摘录等于原值 |
| assertion | 非空statement_raw | extensions及公共identity | 至少一份role=statement的物理摘录等于原句 |

区间、比例、强度、分项不是新增值种类，使用定量字段、单位、分母和身份安放。结构化数值解析失败时保留原文及未解析说明，不编零；不把Decimal转为浮点。缺单位也不能直接丢掉实际计数。

ReportedContext包含期间、主体、主体聚合角色、边界、方法、值角色、聚合角色、分母及开放dimensions。每个ReportedField独立表达stated/inherited/not_stated/not_read/conflict；已声称取得的值附raw_text及证据。未知不意味着冲突，目标固定分类不冒充实际分类。

EvidenceLocation保存object_id/type、原PDF的0基页号、excerpt、role、可选bbox/行列/字符范围和source_snapshot。界面显示页码时加1，不重排稀疏准备页。条款关系ClauseMapping独立保存clause_key/digest、source_fact_id、match、reason、scope_ids和flags。

## 端口及HTTP分工

| 连接 | 请求与响应 | 主要入口 |
|---|---|---|
| Console→编排18080 | ExtractionPlanRequest→ExtractionSession，预览、提交、读取、取消、续跑 | /api/report-sessions及子路径 |
| 编排→条款/提取18180 | RunRequest冻结目标；CreateRun携带request、target_snapshot和submission_key | /api/targets/freeze、/api/runs |
| 提取→文档18080 | DocumentReference；SearchIntent→ObjectSearchPage；对象+chunk→EvidenceFrame | /api/evidence/{ir_run_id}及search/read |
| 阅读→准备 | EvidencePreparationRequest携带条款、页意图、已准备页、原因和max_pages | 编排消费后调用locate-pages，再准备并续跑 |
| 提取→模型 | instructions/context/schema/max_tokens/cancel_check→引用选择及调用回执 | ModelPort，由本地适配器实现 |
| 提取→数据库18280 | 封存ZIP字节→IngestionReceipt | POST /v1/ingestion/exports |
| Console→数据库 | 接收/投影状态、预览、成员分页、显式发布/撤下 | /v1/ingestion、/v1/projections、/v1/datasets |
| Plat→数据库 | 有效发布的产品、观测、定义、来源、精确导出 | /v2/datasets及子路径 |
| Plat→文档 | 完整PDF散列→原PDF字节 | /api/report-sources/{source_sha256}/file |

DocumentPort、ModelPort、ArtifactPort、ResultPort的实际签名见[ports.py](../ExtractESG/extraction/src/esg_extraction/pipeline/ports.py)。部署HTTP和同进程适配都保持这些值接口，不以绝对服务器路径代替引用。

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
