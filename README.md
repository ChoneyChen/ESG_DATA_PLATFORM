# ESG Data Platform

从完整PDF和独立条款目标，生产带原文证据的定量/定性原值，经独立数据库接收、观测投影及显式发布，由Plat展示。

**唯一开发文档入口：[docs/README.md](docs/README.md)。** 人工开发、IDE、终端和AI代理共同维护这套文档。先读[开发指引](AGENTS.md)，按文档目录顺序查阅需求、架构、模块和合同，再同步修改实现与规范。

| 模块 | 责任 |
|---|---|
| ClauseESG | 单条合同、属性、词典、检索与阅读语义 |
| ExtractESG | 共享数据合同、PDF物理证据、提取、队列与运行时 |
| ConsoleESG | 操作前后端及本地应用组合 |
| database | 原值接收、SQL、投影、选源和发布 |
| PlatESG | 已发布产品只读展示与来源读取 |

安装、启动及故障处理见[运维手册](docs/08-operations.md)。制作输入见[条款说明](docs/guides/clause-authoring.md)和[PDF说明](docs/guides/pdf-intake.md)。CrawlESG为保护区，不在本轮维护范围内。
