# 文档入口

项目当前事实唯一维护于 [根 README](../README.md)。本页只列当前有效的文档；历史实施报告已删除，
需要追溯时用 `git log --all -- <原路径>` 取回，不再占据工作树。

## 当前文档

| 文档 | 内容 |
|---|---|
| [根 README](../README.md) | 项目范围、架构、运行方式与验证边界；新对话先读这一份 |
| [更新日志](CHANGELOG.md) | 按日期倒序的每轮实际变化，以及被删除文档的结论摘要 |
| [系统状态](SYSTEM_STATE.md) | 服务、端口、本地模型、数据位置、实测质量、测试结果与已知问题 |
| [数据生产工作台](PRODUCTION_WORKBENCH_20261010.md) | 工作台操作、权限、恢复与本地模型边界 |
| [科学数据存储](SCIENTIFIC_DATA_STORAGE.md) | 事实、测量口径、版本与发布的存储合同 |
| [结构迁移](STRUCTURE_MIGRATION.md) | 物理职责、旧路径兼容与回滚依据 |

## 模块手册（各自单一职责）

| 模块 | 入口 |
|---|---|
| 标准系统 | [ExtractESG/standards/README](../ExtractESG/standards/README.md) |
| 数据库合同 | [database/README](../database/README.md) |
| 产品网站 | [PlatESG/README](../PlatESG/README.md) |
| 操作台 | [ConsoleESG/README](../ConsoleESG/README.md) |
| 文档读取 | [document_ir_extract/README](../ExtractESG/document_ir_extract/README.md) |
| 定向提取 | [targeted_table_extract/README](../ExtractESG/targeted_table_extract/README.md) |

## 标准与来源材料

| 材料 | 入口 |
|---|---|
| 原表数据点目录 | [ESRS_DATAPOINT_CATALOG](../ExtractESG/docs/ESRS_DATAPOINT_CATALOG.md) |
| 标准包建设 | [ESRS_STANDARD_PACKAGE_BUILD](../ExtractESG/docs/ESRS_STANDARD_PACKAGE_BUILD.md) |
| 结构证据审阅合同 | [IR_REVIEW_REFERENCE_STATE_CONTRACT](../ExtractESG/docs/IR_REVIEW_REFERENCE_STATE_CONTRACT.md) |
| 报告输入与任务模型 | [REPORT_INPUT_AND_TASK_MODELS](../ExtractESG/docs/REPORT_INPUT_AND_TASK_MODELS.md) |
| 原始需求 | [更新要求](../更新要求.md) |

`docs/migrations/` 保存迁移前的原件副本，只读，不作为可编辑来源。
