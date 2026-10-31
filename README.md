# ESG Data Platform

当前链路：**报告资源 → 条款搜索 → PDF物理证据 → 两类原值 → 独立数据库接收 → 观测投影 → 显式发布 → Plat只读展示**。接收不等于审核或发布。

| 模块 | 所有权 |
|---|---|
| `ClauseESG/` | 一条一整体：定义、定性/定量、词典、读取、用途、证据、展示及验收声明 |
| `ExtractESG/contracts/` | 两类原值、字段证据、报告资源、运行和交接 DTO；独立轻量 SDK |
| `ExtractESG/document/` | 原生文字、布局、本地 OCR、IR、证据对象 |
| `ExtractESG/extraction/` | 检索、局部阅读、原值绑定、目标关系与输出；不写条款专用词典 |
| `ExtractESG/pipeline/` | PDF 准备、冻结输入、队列、取消和运行状态 |
| `ExtractESG/shared_runtime/` | 调用信封、部署指纹与本机组合配置；独立基础包 |
| `ConsoleESG/frontend/`、`backend/` | 对象导航、结果表和常驻运行窗；后端安全展示投影与应用组合 |
| `database/` | 原值接收、SQL、精确数值、观测、来源、发布与撤下；连接和迁移由本模块拥有 |
| `PlatESG/frontend/`、`backend/` | 已发布产品的只读HTTP与页面；按来源散列取回PDF |

```sh
bash ConsoleESG/scripts/install_local.sh
ESG_SKIP_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 ConsoleESG/scripts/start.sh
```

本地服务：Console 18081、文档/Console 组合 API 18080、提取 18180、独立数据库18280、Plat8765，由同一控制器管理。启动器不初始化或迁移数据库，使用已配置的独立原值库。

业务数据集中在 `$ESG_DATA_ROOT`（默认 `.local/data`）；报告、文档证据、提取结果、控制记录、日志和数据库分别存放。原PDF独立保管，新运行固定当前输入与合同；旧字典/旧运行兼容读取已删除。模型理解不跨条款/运行复用，物理 OCR/IR 可按内容和配置复用。

[当前升级与逐项验收](docs/implementation/2026-10-31/README.md) · [架构](docs/architecture/README.md) · [目录与运维](docs/guides/local-operation.md) · [文档目录](docs/README.md)

2026-10-31：当前完整PDF新运行得到51条三年原值并已接收、投影，九个空气值逐项核对；其他报告与全文档复核继续进行。源码、隔离测试、实际部署及效果边界见上述实施记录。制作说明：[独立条款](docs/guides/clause-authoring.md)、[PDF整理导入](docs/guides/pdf-intake.md)。

1,126个独立声明：281定量、832定性、13非执行结构描述；37项已逐条归类，当前零技术阻塞。99展示组、16/32等集合仅组织ID，不产生专用路线；技术就绪与科学样本验收分开。自动发布关闭；CrawlESG保护区不接入。

桌面入口更新：运行 `ExtractESG/.local/python/bin/python ConsoleESG/scripts/install_desktop_launcher.py`。双击“启动ESG统一工作台.command”后台启动并打开Console；同一入口支持 `status --json`、`stop`、`restart`。关闭终端不停止服务。

模型worker使用离线安装的合同/条款/运行时与提取应用包，不拼接兄弟源码路径；代码指纹按实际安装包内容计算。安装入口会更新所配置MLX Python中的本项目包，不安装或替换第三方模型依赖。队列与worker共用运行时执行锁；worker不能绕过队列直接启动模型。
