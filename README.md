# ESG Data Platform

当前链路：**报告资源 → 条款搜索 → PDF 物理证据 → 单一提取骨干 → 独立原值 → 封存导出 → 独立数据库接收**。接收不等于审核或发布。

| 模块 | 所有权 |
|---|---|
| `ClauseESG/` | 一条一整体：定义、定性/定量、词典、读取、用途、证据、展示及验收声明 |
| `ExtractESG/contracts/` | 两类原值、字段证据、报告资源、运行和交接 DTO；独立轻量 SDK |
| `ExtractESG/document/` | 原生文字、布局、本地 OCR、IR、证据对象 |
| `ExtractESG/extraction/` | 检索、局部阅读、原值绑定、目标关系与输出；不写条款专用词典 |
| `ExtractESG/pipeline/` | PDF 准备、冻结输入、队列、取消和运行状态 |
| `ExtractESG/shared_runtime/` | 调用信封、部署指纹与本机组合配置；独立基础包 |
| `ConsoleESG/frontend/`、`backend/` | 对象导航、结果表和常驻运行窗；后端安全展示投影与应用组合 |
| `database/` | 原值接收、SQL、精确数值、证据与冻结产品；连接和迁移由本模块拥有 |
| `PlatESG/frontend/`、`backend/` | 已发布数据产品及权限接口；通过文档证据端口定位原文 |

```sh
bash ExtractESG/scripts/install_local.sh
ESG_SKIP_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 ExtractESG/scripts/start.sh
```

本地服务：Console 18081、文档/Console 组合 API 18080、提取 18180、独立原值接收 18280。Plat 8765 独立维护。启动器不初始化或迁移数据库，使用已配置的独立原值库。

业务数据集中在 `$ESG_DATA_ROOT`（默认 `.local/data`）；报告、文档证据、提取结果、控制记录、日志和数据库分别存放。原 PDF 不变，旧运行按冻结版本读取。模型理解不跨条款/运行复用，物理 OCR/IR 可按内容和配置复用。

[当前升级与逐项验收](docs/implementation/2026-10-30/README.md) · [架构](docs/architecture/README.md) · [目录与运维](docs/guides/local-operation.md) · [文档目录](docs/README.md)

1,126 条声明、99 展示组、273 定量、853 定性；37 条内容未决阻塞。16/32 等集合只有编号，不产生专用路线。技术就绪、科学样本验收、可比较、可发布分别表达。`PLATESG_AUTO_PUBLISH=0`；CrawlESG 是保护区，本链路不接入。

桌面入口更新：运行 `ExtractESG/.local/python/bin/python ExtractESG/scripts/install_desktop_launcher.py`。双击“启动ESG统一工作台.command”后台启动并打开Console；同一入口支持 `status --json`、`stop`、`restart`。关闭终端不停止服务。
