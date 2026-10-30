# 数据库与冻结产品

本目录拥有既有 SQL 模型、审核、单位换算、发布和公开数据查询。它是已部署数据的消费者，与本轮新的 PDF 提取生产者分开维护。**新事实接收器未建设，现有 Core/Bundle 生产导入与条款包投影入口已退役。** 后续建设方向见 [出口方案](../docs/EXIT_PLAN.md)。

| 对象 | 作用 |
|---|---|
| `migrations/` | 已部署模式的不可变迁移顺序；保留字典、维度、证据和发布血缘 |
| `scripts/frozen-bundle.mjs` | 已有交付收据的冻结格式读取及隔离消费者样本加载；生产导入 CLI 已退役 |
| `scripts/review-decisions.mjs` | 已有 SQL 事实/映射的审核决定；不依赖包解析器 |
| `scripts/publication.mjs` | 已有审核和报告发布、精确单位转换、发布快照与公开查询 |
| `scripts/report-source-facts.mjs` | 既有来源快照查询，不读取当前提取目录 |
| `scripts/scientific-model.mjs` | 已有科学投影与只读对账；不再从当前条款源构造旧包版本 |
| `scripts/extension-derivation.mjs` | 既有显式批准的派生血缘；不提供新的条款定义源 |
| `scripts/unit-conversions*.json` | 冻结换算规则，已登记版本不得删除或重写；当前实现使用 1.5.7 |
| `tests/fixtures/` | 明确虚构或冻结的隔离测试样例，不读取已退役标准目录 |

来源原文、映射、口径确认、正式观测、比较资格、发布成员分别记录。原值保留；正式值使用精确十进制和带摘要的换算规则。字典、规范观测身份、来源及数据集成员按已登记版本冻结。旧迁移和单位表不是运行入口，不能因名称较旧就删除。

公开查询和导出由 [PlatESG](../PlatESG/README.md) 提供。已有事实审核、口径确认、报告发布/撤下继续使用受权限控制的 `/internal/v1/`。新事实、组织视图或任务完成均不会自动进入这些表；`PLATESG_AUTO_PUBLISH=0`。

```sh
npm test --prefix database
```

测试使用临时或内存库。开发 CLI 支持 `db:init / db:status / db:verify`，运行前必须明确库配置；本轮不对任何活动库运行初始化、迁移或重置。`ESG_DATABASE_DATA_DIR` 覆盖独立脚本的存储位置，Plat 使用自己的 `PLATESG_DATA_DIR` 或 PostgreSQL 配置。源码唯一位置为本目录；`ExtractESG/esg_database` 的代码软链接已删除，已有数据目录不会随源码整理迁移。
