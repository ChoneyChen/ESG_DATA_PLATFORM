# 文档入口及维护职责

项目当前事实唯一维护于 [根 README](../README.md)。本页只管理文档类型及入口，不另复制一套项目现状。源码、当前契约与日期化验收优先于旧研究建议。

| 类型 | 入口 | 使用规则 |
|---|---|---|
| 当前项目手册 | [根 README](../README.md) | 新对话及开发首先读取，维护最新范围和验证边界 |
| 更新日志 | [CHANGELOG](CHANGELOG.md) | 按日期倒序的每轮实际变化与入口；结论以链接的日期化记录为准 |
| 当前系统状态 | [2026-10-17系统状态](SYSTEM_STATE_20261017.md) | 服务、端口、本地模型、数据位置、实测质量与速度、测试结果及已知问题；日期化快照 |
| 项目完整交接快照 | [2026-10-16项目交接](PROJECT_HANDOFF_20261016.md) | 项目目标、完整架构、标准与16项范围、数据库/产品、现场状态及下一步；日期化快照，不另维护动态现状 |
| 当前标准系统 | [standards/README](../ExtractESG/standards/README.md) | 作者源、编译、版本、主要/核心条款与唯一入口 |
| 当前数据库合同 | [database/README](../database/README.md) | SQL、导入、审核、发布、运行库及依赖 |
| 当前结构及迁移证据 | [结构迁移](STRUCTURE_MIGRATION.md) | 实际物理职责、旧路径兼容、摘要和回滚 |
| 本轮独立连接审计 | [P4–P7连接审计](P4_P7_INDEPENDENT_LINK_AUDIT.md)、[原文gold](CHINA_OIL_GOLD_AUDIT.md) | 具体合同、历史关联、未应用的补齐方案及真实验收边界 |
| 当前平台/工作台 | [PlatESG](../PlatESG/README.md)、[ConsoleESG](../ConsoleESG/README.md) | 对应产品与操作台的入口与能力边界 |
| 日期化实施审计 | [P0–P3](../ExtractESG/docs/WORKBENCH_P0_P3_IMPLEMENTATION.md)、[平台实施](../ExtractESG/docs/PRIORITY_PLATFORM_IMPLEMENTATION.md) | 结论只适用于记录日期及其验收范围 |
| 16项快速链路实施记录 | [2026-10-17实施记录](FOUNDATION16_FAST_ROUTE_20261017.md) | 定向识别、每表一次调用、确定性绑定、文字层校对、金标准与真实冷启动证据；含未完成项 |
| 16项提取审计与规划 | [2026-10-16运行审计与升级规划](FOUNDATION16_RUN_AUDIT_AND_UPGRADE_PLAN_20261016.md) | 已停止的紫金任务、全运行成本、逐项质量与有界提取方案；方案已由2026-10-17记录实施 |
| 前轮提取实施记录 | [2026-10-14修整记录](EXTRACTION_ROOT_CAUSE_UPGRADE_20261014.md) | 合同驱动路由、每包独立预算及当轮工程验收边界；不能替代最新真实运行结论 |
| 原始升级要求 | [更新要求](../更新要求.md) | 用户要求及规划依据；不是全部已实现的声明 |
| 历史研究与规划 | [系统规划](../ExtractESG/docs/SYSTEM_RESHAPING_PLAN.md)、[平台优先规划](../ExtractESG/docs/ESRS_PRIORITY_PLATFORM_PLAN.md)、[原表目录](../ExtractESG/docs/ESRS_DATAPOINT_CATALOG.md) | 原样保留计划、选择原因及原材料；以当前手册区分落实状态 |
| 其他历史修复记录 | `ExtractESG/docs/`内带日期/阶段的审计、需求及方案 | 不当作最新运行手册，不静默改写历史结论 |
| 本次原件与迁移清单 | `docs/migrations/`、`standards/history/` | 回滚/审计材料；不作为可编辑作者源或生产构建入口 |

历史材料保留原路径，兼顾用户此前给出的文件路径、标准来源引用及相对链接。新当前说明在根docs，既有模块当前手册继续各自单一职责；不复制同一篇当前手册到多个目录。后续完整物理文档归档须先登记全部相对引用和before摘要，与源码迁移一样分批执行，不能为目录整齐破坏历史证据。
