# 独立数据库模块

本模块拥有原值接收、精确数值存储、观测投影、来源选择、发布与撤下。当前只支持 `extraction-export-1.1`，`facts` 是原值权威；没有旧字典、旧SQL体系、冻结旧接口或历史数据库适配。

| 目录 | 职责 |
|---|---|
| `src/esg_database` | 接收/产品HTTP、DTO、确定性投影、科研导出、独立HTTP客户端 |
| `runtime` | 当前SQL worker、连接与显式管理命令 |
| `queries` | 接收、完整分页、投影、发布及来源保护事务 |
| `migrations` | 当前不可变模式：0019原值、0020产品、0021来源生命周期 |
| `contracts` | 从数据库DTO生成的JSON Schema及TypeScript类型SDK |
| `tests` | 临时PGlite与HTTP边界测试；不打开活动库 |

服务18280自动接收、返回幂等回执并后台投影。缺主体、期间、单位、边界不删除真实原值；比较资格单独计算。全年份和未知期间均保留。发布必须显式执行，`PLATESG_AUTO_PUBLISH=0`。数据库不调用提取模型，也不扫描生产者目录。

新库必须显式指定目标后初始化；启动不迁移、不接管旧模式：

```sh
ESG_DATABASE_DATA_DIR=/absolute/new/capture database/scripts/run-node.sh database/runtime/manage.mjs init --profile capture
npm --prefix database test
```

`ESG_DATABASE_DATA_DIR` 为本地PGlite位置；`ESG_DATABASE_URL` 为外部PostgreSQL连接配置，禁止输出其敏感内容。默认工作台数据在 `$ESG_DATA_ROOT/databases/capture`。服务部署需安装SDK wheel及本模块Node runtime、queries、migrations和依赖；`ESG_DATABASE_MODULE_ROOT` 指向这些资产。Console与Plat只消费HTTP客户端及类型合同，不打开SQL文件。

PDF来源保护属于数据库独立接口。已接收原值引用的PDF不可直接删除；IR/OCR是可重建缓存，来源ID只作血缘，不形成对生产者目录的依赖。清理预留与接收串行，清除后的迟到交付会明确拒绝。

[交接](../docs/architecture/delivery.md) · [产品接口](../docs/architecture/products.md) · [当前实施](../docs/implementation/2026-10-31/README.md)
