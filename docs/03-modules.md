# 模块设计与文件责任

完整文件清单见[生成源码索引](reference/source-map.md)。下表定义责任与修改落点，索引负责枚举文件；两者不重复维护文件数量。RQ编号见[需求](01-requirements.md)。

## ClauseESG：独立条款（RQ-01、06）

| 路径（相对模块） | 责任与修改原因 |
|---|---|
| `clauses/<group>/<key>/clause.json` | 单条权威资产，含目标、属性、词典、检索、阅读、用途、证据与组织 |
| `clauses/_catalog.json`、`group.json`、`selections.json` | 导航/成员索引；通过repository生成或维护，不是指标执行路线 |
| `src/esg_clauses/contract.py`、`parts.py`、`attributes.py` | 条款1.4、组成字段及属性定义；扩展语义接口在这里完成 |
| `descriptor.py`、`catalog.py` | 非执行结构描述及展示组合同，不制造第三类事实 |
| `repository.py` | 加载、保存、摘要、冻结/解冻、选择、重建索引；新编辑不改旧任务快照 |
| `search.py` | 单条检索计划；词典引用及允许单位种类传给消费者 |
| `reading.py`、`scope.py`、`evidence.py` | 合同规则解释、范围匹配、证据层级；通用解释器不写条款ID分支 |
| `templates.py`、`attribute_templates.py` | 完整草稿和属性起点；模板blocked不代表填写内容已完成 |
| `schemas/`、`tests/` | 生成接口及当前SDK测试；不以历史报告摘要维持测试 |

SDK不导入OCR、提取器、模型或数据库。条款根通过`ESG_CLAUSE_ROOT`独立配置。单条部署无需加载一个“大标准包”的执行器。

## ExtractESG/contracts：轻量公共合同（RQ-03—05、10）

| 包/文件 | 责任 |
|---|---|
| `esg_contracts/data_kinds/quantitative.py`、`qualitative.py` | 两类原值字段与原文优先语义 |
| `esg_contracts/facts.py` | SourceFact、身份字段、证据位置、ClauseMapping、覆盖、运行预算 |
| `evidence.py`、`search.py` | 文档引用、页/对象搜索、分页、EvidenceFrame和补页请求 |
| `sessions.py`、`results.py` | 编排会话、原生运行、结果概览、分页事实和组织结果 |
| `exports.py` | 封存清单、文件角色、交付状态、接收回执 |
| `esg_reports/contract.py`、`repository.py`、`importer.py` | PDF级资源身份、批次、文件夹及登记/导入 |
| `esg_data/units.py`、`quantities.py`、`literals.py`、`periods.py` | 共享单位、精确数值和期间语义；不决定指标目标 |
| `esg_data/organization.py`、`categories.py`、`text_search.py` | 组织合同、分类编码、通用词项匹配 |
| `schemas/` | 对外生成Schema；字段精确类型从源码生成 |

该包不能反向依赖document、pipeline、extraction。接口变更需同时考虑条款SDK、生产者、数据库和生成前端类型。

## ExtractESG/document：资源与物理证据（RQ-05、06）

| 路径/文件 | 责任 |
|---|---|
| `api/main.py`、`config.py` | 文档应用入口与配置；Console组合根在Console模块，不是领域逻辑 |
| `assets/report_catalog.py`、`api/report_resources.py` | PDF字节、资源元数据、文件夹和散列读取 |
| `ocr/page_selection.py`、`text_layer.py`、`pdf_preflight.py` | 消费页查询、原生文字检查、必要识别范围 |
| `ocr/local_worker.py`、`providers.py`、`output_writer.py` | OCR/布局提供者执行、适配和当前包写入 |
| `document/paddleocr_converter.py`、`parser_fusion.py`、`deduplicator.py` | 观察转物理IR、融合与去重 |
| `coordinate_mapper.py`、`table_geometry_resolver.py`、`structure_reconstruction.py` | 坐标、网格、表头、章节、单位结构 |
| `spread_builder.py`、`logical_table_builder.py`、`spread_pair_analysis.py` | 跨页关系及逻辑表到原物理单元格映射 |
| `physical.py`、`literals.py` | 物理行列视图、单位来源、数字候选与标识符区别 |
| `search_ranking.py`、`evidence_service.py`、`api/evidence.py` | 通用对象排序、按快照分页检索与有界证据读取 |
| `reader.py`、`writer.py`、`revision_service.py` | 当前包、分片、快照、修订与来源解析 |
| `operation_registry.py`、`operation_payloads.py`、`review_orchestrator.py`、`transaction_coordinator.py` | 显式IR局部复核协议与事务；不是每次提取必跑步骤 |
| `quality_router.py`、`evidence_capabilities.py` | 对象可读性、具体质量问题与必要处理能力 |
| `storage/`、`api/document_archive.py` | 作业、包引用、保留、清理和物理结果读取 |

表中`document/`文件均位于`src/esg_document/document/`；OCR和API分别在同包对应目录。物理IR不含条款目标判断。

## ExtractESG/extraction：同一阅读主干（RQ-02—08、10）

均相对`src/esg_extraction/pipeline/`。

| 文件 | 责任 |
|---|---|
| `api.py` | 条款/目标冻结/原生运行/结果/导出HTTP |
| `engine.py` | 组合搜索、阅读、绑定和保存；不另设指标目录 |
| `reading_policy.py` | 局部候选、补页、可选属性额度；命中对象及尝试进度 |
| `retrieval.py`、`selection.py` | 有界候选流与条款正向阅读条件消费 |
| `reading_frame.py`、`decisions.py`、`frames.py` | 紧凑输入、两类引用输出协议和分段 |
| `structure.py`、`period_binding.py`、`binding.py` | 已知列语义、期间、实际数值/引句及字段证据绑定 |
| `enrichment.py` | 已准备证据上的可选属性补充；不生成量值 |
| `statistics.py`、`result_store.py` | 增量计数、结果索引、检查点和分页 |
| `organization.py` | 依冻结条款生成行表/分组/矩阵，不聚合出新测量 |
| `exporting.py`、`delivery.py` | 自包含交接包与交付outbox，接收回执和重试 |
| `ports.py`、`adapters/` | Document/Model/Artifact/Result端口及具体I/O |
| `state.py`、`runtime.py`、`worker.py` | 原生运行持久化、错误/续跑、执行槽及worker入口 |
| `../models/` | 本地Qwen能力、MLX执行、结构约束及失败类别 |

## ExtractESG/pipeline与shared_runtime（RQ-08、09）

`esg_pipeline/report_processing.py`生成准备计划；`extraction.py`冻结会话、连接准备与阅读、取消子任务并续跑；`scheduler.py`拥有排队与调度；`native_dispatch.py`/`worker.py`分派实际worker；`store.py`持久化任务与事件；`progress.py`/`outcomes.py`组合进度和终态。编排不能编造事实或改条款含义。

`esg_runtime/execution_slot.py`拥有共享执行锁；`process.py`/`lifecycle.py`拥有进程生命周期；`model_calls.py`拥有统一调用信封；`release.py`拥有代码指纹/服务身份；`layout.py`只负责组合根路径；`power.py`限制任务期空闲休眠；`trash.py`提供通用生命周期工具。

## ConsoleESG（RQ-09、12）

`backend/src/esg_console/application.py`是文档能力与编排的组合根；`sessions.py`是会话/结果门面；`monitor.py`给列表和浮窗同一安全运行投影；`products.py`组合接收/投影/产品状态。

`frontend/src/app/`路由和外壳；`features/reports`资源；`extractions`创建、列表、详情、分页结果；`clauses`条款导航/编辑；`publication`预览和发布；`runtime`浮窗/队列；`shared`请求、生成类型、资源和基础控件。`scripts/workbench.py`托管五服务，`run_service.sh`定义部署入口，`install_worker.py`部署本地worker包。具体页面及API组合见[前端](07-frontends.md)。

## database（RQ-10、11、13）

`src/esg_database/api.py`接收服务；`ingestion.py`解包；`client.py`调用本模块SQL桥；`projector.py`后台投影；`projection.py`确定性编译观测与比较资格；`selection.py`预览/选源/重述；`products_api.py`产品HTTP；`research.py`精确长表导出；`sources_api.py`/`source_contracts.py`来源生命周期；`http_client.py`向其他模块提供客户端。

`runtime/`连接、RPC、当前模式和显式管理命令；`queries/`事务与查询；`migrations/`不可变SQL历史；`contracts/`生成DTO/类型SDK。数据库部署资产属于该模块，不分散到Plat中。

## PlatESG（RQ-13）

`backend/main.py`组合只读产品服务与静态前端；`products.py`代理有效发布；`evidence_resolver.py`确认来源属于发布后按散列读取PDF；`runtime.py`声明服务身份；`http_prefix.py`限定上游地址。

`frontend/src/App.tsx`、`navigation.ts`组织数据集/观测页面；`components/PublishedData.tsx`展示数据与分页；`api.ts`仅调用只读HTTP；`scripts/run.sh`构建启动8765。

## 开发工具

`tools/export_console_types.py`与`database/scripts/export_contracts.py`生成客户端合同；`tools/build_documentation.py`生成当前接口/源码参考。结构及独立安装检查仍为按需开发工具，输出放`.local/development/`，不会自动形成另一套规范。
