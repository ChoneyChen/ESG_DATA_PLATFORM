# 文档入口

项目当前事实唯一维护于 [根 README](../README.md)。本页只列当前有效的文档；历史实施报告已删除，
需要追溯时用 `git log --all -- <原路径>` 取回，不再占据工作树。

## 当前文档

| 文档 | 内容 |
|---|---|
| [根 README](../README.md) | 系统定位、Console 与 Extract 的关系、数据流、目录、测试与约定；新对话先读这一份 |
| [全链路检查报告与升级规划](../全链路检查报告与升级规划.md) | 2026-10-28 只读架构研究：模型、检索、条款、科学数据与 Console 的分阶段升级 |
| [阶段 0 真实基线](PHASE0_BASELINE.md) | 固定报告/正反例、全字段与模型对照、自动审核、资源与七阶段实施入口 |
| [阶段 1 科学合同](PHASE1_SCIENTIFIC_CONTRACT.md) | 标准包驱动平台字典、foundation 规格与审核归一；候选字典与真实回放 |
| [阶段 2 中立事实](PHASE2_NEUTRAL_FACTS.md) | 全期 SourceFact、不可变解释版本、字段证据与公开 Observation |
| [阶段 3 增量证据](PHASE3_INCREMENTAL_EVIDENCE.md) | 共享文字层、合同选页、报告级中立缓存与覆盖决策 |
| [更新日志](CHANGELOG.md) | 按日期倒序的每轮实际变化 |
| [系统状态](SYSTEM_STATE.md) | 服务、端口、数据位置、实测质量、测试结果与已知问题 |
| [全链路体检](AUDIT_2026-10-23.md) | 2026-10-23 的未完成/待测试/待修复清单与逐轮修复台账 |
| [定量条款审核](EXTRACTION_QUANTITATIVE_AUDIT_2026-10-24.md) | 29 份结果中未完全通过的原因分类与子表/区间/表外属性增强 |
| [数据生产工作台](PRODUCTION_WORKBENCH_20261010.md) | 日常操作、权限、恢复与本地模型边界 |
| [科学数据存储](SCIENTIFIC_DATA_STORAGE.md) | 事实、测量口径、版本与发布的存储合同 |
| [结构迁移](STRUCTURE_MIGRATION.md) | 物理职责、旧路径兼容与回滚依据 |

## 模块手册（各自单一职责）

| 模块 | 入口 | 角色 |
|---|---|---|
| 操作台 | [ConsoleESG/README](../ConsoleESG/README.md) | 唯一人工界面：报告、提取、审核、发布 |
| 文档读取 | [document_ir_extract/README](../ExtractESG/document_ir_extract/README.md) | PDF→OCR→DocumentIR 服务与合同（无自带前端） |
| 定向提取 | [targeted_table_extract/README](../ExtractESG/targeted_table_extract/README.md) | 条款投影、事实与成果包 |
| 标准系统 | [ExtractESG/standards/README](../ExtractESG/standards/README.md) | 作者源、编译、发布与版本不可变 |
| 数据库合同 | [database/README](../database/README.md) | 存储、审核与发布 SQL |
| 产品网站 | [PlatESG/README](../PlatESG/README.md) | 公开 `/v1`、内部接口与发布库 |

## 标准与来源材料

| 材料 | 入口 |
|---|---|
| 原表数据点目录 | [ESRS_DATAPOINT_CATALOG](../ExtractESG/docs/ESRS_DATAPOINT_CATALOG.md) |
| 标准包建设 | [ESRS_STANDARD_PACKAGE_BUILD](../ExtractESG/docs/ESRS_STANDARD_PACKAGE_BUILD.md) |
| 结构证据审阅合同 | [IR_REVIEW_REFERENCE_STATE_CONTRACT](../ExtractESG/docs/IR_REVIEW_REFERENCE_STATE_CONTRACT.md) |
| 报告输入与任务模型 | [REPORT_INPUT_AND_TASK_MODELS](../ExtractESG/docs/REPORT_INPUT_AND_TASK_MODELS.md) |
| 原始需求 | [更新要求](../更新要求.md) |

`docs/migrations/` 保存迁移前的原件副本，只读，不作为可编辑来源。
