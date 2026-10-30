# 独立数据库模块

本模块拥有连接适配、SQL迁移、查询、原值接收器、审核及已发布产品。数据库不读取提取产物目录；新交接用 `extraction-export-1.1`（兼容冻结1.0），facts是原值权威，旧冻结数据消费者保留独立适配。

| 目录 | 用途 |
|---|---|
| `src/esg_database` | Python客户端、封存包解码、独立接收HTTP应用 |
| `runtime` | Node SQL worker、连接和显式管理 |
| `queries` | 原值接收及产品查询；旧收据适配位于frozen |
| `migrations` | 不可变SQL迁移；platform子目录保留原迁移编号和登记 |
| `catalog` | 已部署产品字典及冻结合同；不是当前条款作者源 |
| `scripts` | 精确换算、审核、发布、科学表和管理工具 |
| `tests` | 内存或临时库验证，不读正式/演示库 |

新接收器18280只接收原值，保存精确numeric、原字串、字段属性和证据，返回幂等回执；接收不表示可比较或可发布。`capture_worker.mjs`只装配接收/查询，不加载旧字典与发布路由；冻结产品使用独立worker。启动不迁移/登记字典；`runtime/manage.mjs init --profile capture`是显式初始化入口，须指定目标库，安装capture模式即可。桌面配置原值库为 `.local/data/databases/capture`，与Plat正式/演示库独立。

Python wheel提供SDK；运行服务还需Node runtime、queries、migrations和本模块Node依赖。`ESG_DATABASE_MODULE_ROOT`指定完整部署资产根，`ESG_DATABASE_WORKER`可单独指定worker。`ESG_DATABASE_URL`配置PostgreSQL，`ESG_DATABASE_DATA_DIR`配置本地PGlite；capture模式忽略旧PLATESG库与审核凭据变量，旧变量仅供显式冻结产品配置使用。跨模块不复制SQL或通过相对源码导入连接。

```sh
npm --prefix database test
```

[交接合同和接口](../docs/architecture/delivery.md) · [运行与目录](../docs/guides/local-operation.md) · [升级证据](../docs/implementation/2026-10-30/README.md)

旧字典1.5.0字节不变，已批准产品继续按其冻结含义读取。正式/演示库本轮不迁移、不重置；`PLATESG_AUTO_PUBLISH=0`。
