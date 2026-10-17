# 数据库合同与审核发布

迁移SQL、候选导入、审核、发布、单位转换和冻结数据版本的代码唯一维护于本目录。旧 `ExtractESG/esg_database/{migrations,scripts,tests,examples}`及包文件为兼容链接。历史默认运行库仍为 `ExtractESG/esg_database/.local/pglite`；源码迁移不初始化、移动或替换数据。

正式方言是PostgreSQL SQL，本地开发使用PGlite。精确数值、主体、期间、边界、方法及类型化维度共同表达观测身份，保留原值及来源。事实审核、映射审核、发布、当前比较口径分别表达。批准及outbox同事务，发布幂等重试；冻结版本保持可复现。

额外表行独立存入中立来源快照，补充属性经明确选择后随测量口径及发布来源冻结。详见 [科学数据存储链路](../docs/SCIENTIFIC_DATA_STORAGE.md)，包括实际表、事务、工作台读取与产品展示合同。

| 位置 | 内容 |
|---|---|
| `migrations/` | 表/约束、候选/审核/outbox、规范观测及公开视图 |
| `scripts/import-bundle.mjs` | 中性事实、标准投影、候选导入/审核、幂等及来源保护 |
| `scripts/publication.mjs` | 单位、身份、发布门槛及数据集版本 |
| `scripts/db.mjs` | 本地库/迁移CLI |
| `scripts/unit-conversions.1.2.0.json` | 当前精确比例及显式单位规则；原 `unit-conversions.v1.json` 保留旧1.1.0内容 |
| `tests/` | 隔离数据库的事务、发布及来源验证 |

HTTP及产品读取由 [PlatESG后端](../PlatESG/README.md)集成；产品平台与独立工作台分别呈现。本目录不复制HTTP业务服务。

本目录使用Node.js20+和pnpm：

```bash
pnpm install
pnpm test
pnpm db:init
pnpm db:status
```

`ESG_DATABASE_DATA_DIR`覆盖运行库。依赖实体统一维护于 `database/node_modules`，旧路径是兼容链接；PostgreSQL驱动固定为 `pg 8.23.1`，连接由服务配置选择，参见 [node-postgres官方说明](https://node-postgres.com/features/connecting)。原包/lock及安装元数据留有迁移原件，运行数据未移动。`db:demo`、`db:reset`及导入/审核会修改所选库，操作须确认配置；测试使用隔离或内存库。

实际活动存储由PlatESG配置决定，旧默认路径不等于活动库。来源删除须通过全部已登记存储引用核查；未知或不可访问时失败关闭，归档保留引用。迁移依据见 [结构迁移记录](../docs/STRUCTURE_MIGRATION.md)。迁移前手册原样保存在迁移审计目录。
