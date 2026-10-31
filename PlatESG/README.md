# PlatESG · 已发布数据

Plat 是独立数据库发布产品的只读前后端。没有旧企业/指标字典、SQL worker、审核写入口或冻结旧数据适配；旧 `/v1`、`/internal/v1`、`/review` 接口返回410。

前端按「数据集 → 数据表 → 单条观测」组织，提供返回、全部期间/条款筛选、完整游标分页、精确原值、实际属性、物理证据、来源PDF及CSV/Parquet长表。缺比较轴的已发布事实仍可展示，比较资格另行标注。

后端 `/v2/datasets` 及子接口通过HTTP消费独立数据库，不读取Extract或数据库目录。PDF先验证其属于有效发布，再经文档服务的散列端口读取并复核完整字节。撤下后的成员及PDF不能通过本平台公开读取。

| 目录 | 职责 |
|---|---|
| `backend` | 发布读代理、资源解析、就绪与发布客户端版本检查 |
| `frontend` | 当前数据集/观测/定义/来源页面 |
| `scripts/run.sh` | 构建并启动本机8765；不读取凭据文件或初始化数据库 |
| `tests` | 当前HTTP/来源边界隔离测试 |

`ESG_DATABASE_PRODUCTS_URL`（默认18280）配置数据库服务；`ESG_DOCUMENT_BACKEND_URL`（默认18080）配置PDF解析。两者均使用无凭据的显式服务地址。服务离线时明确不可用，不回退旧数据。

```sh
npm --prefix PlatESG/frontend ci
npm --prefix PlatESG/frontend test
npm --prefix PlatESG/frontend run build
PlatESG/scripts/run.sh
```

科研导出包含六份规范长表、定义及类型清单；Decimal精度不足时明确失败，不转浮点或悄悄截断。发布控制归Console与数据库，Plat无自动发布行为。源码验证、实际服务部署及真实报告核验分别记录在[本轮实施](../docs/implementation/2026-10-31/README.md)。
