# 软件开发文档

维护日期：2026-11-15。实现基线：`db2b6d53`及本轮文档整理；本日期不是生产服务健康检查时间。

这是一套持续维护的当前规范。旧审计、阶段规划、实施记录、截图和多处“当前状态”退出工作树，历史在Git中。代码行为、要求与剩余工作分开表达，不以规划代替实现。

## 查阅顺序

| 顺序 | 文档 | 回答的问题 |
|---|---|---|
| 1 | [产品与需求](01-requirements.md) | 最终交付什么；什么是可用原值 |
| 2 | [系统架构](02-architecture.md) | 模块怎样合作；每个决定归谁 |
| 3 | [模块设计](03-modules.md) | 应改哪个目录、入口与组件 |
| 4 | [合同与接口](04-contracts.md) | 字段、版本、交接与兼容 |
| 5 | [PDF与提取执行](05-execution.md) | 定位、OCR、模型、补读、队列 |
| 6 | [数据与发布](06-data.md) | 存储、接收、比较、发布与清理 |
| 7 | [前后端设计](07-frontends.md) | Console/Plat对象、页面和操作 |
| 8 | [部署与运维](08-operations.md) | 安装、配置、启动、排障及恢复 |
| 9 | [开发与维护](09-development.md) | 开发前读什么，同步改什么，如何完成 |
| 10 | [质量与工作清单](10-quality-and-work.md) | 真实基线、缺口、下一步与优先级 |

操作材料：[构造条款](guides/clause-authoring.md)、[组织PDF](guides/pdf-intake.md)。

代码事实索引：[全部HTTP操作](reference/http-api.md)、[文件级源码索引](reference/source-map.md)。由`ExtractESG/.local/python/bin/python tools/build_documentation.py`生成；修改源码后重建，不手写第二套清单。

## 同步维护

- 稳定的RQ编号只在需求文档定义；模块责任只在架构和模块设计定义，其余章节引用。
- 精确类型由合同源码、生成Schema及运行服务OpenAPI维护，文档解释含义和使用过程。
- 本次修改涉及哪项责任，就更新对应章节；[开发影响表](09-development.md)给出路径。小修改写原段落，不追加一篇报告。
- 真实运行证据留在运行产物或`.local/development/`；规范只保留结论、版本、运行ID和必要边界。
- [变更记录](CHANGELOG.md)简要记录新文档体系建立后的变化，不恢复旧平行文档树。

所有环境从根`AGENTS.md`进入本目录。文档不是运行配置；JSON合同、SQL迁移与业务资产仍由各所属模块维护。
