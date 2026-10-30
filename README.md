# ESG Data Platform

当前生产链路：**报告资源 → 条款词典驱动的 PDF 证据 → 单一提取骨干 → 独立原值、映射与组织结果**。Console 已按当前模块重建，审核、发布和新数据库接收器本轮仅规划。

| 模块 | 唯一职责 |
|---|---|
| `ExtractESG/standard/<组>/<条款>/clause.json` | 每条定义、定性/定量、词典、口径、阅读、证据与输出结构 |
| `ExtractESG/contracts` | 公共合同与条款 SDK；不反向导入执行器 |
| `ExtractESG/document` | PDF、文字层、本地 OCR、IR 与物理证据 |
| `ExtractESG/pipeline` | PDF 准备、队列、冻结目标、取消与状态观测 |
| `ExtractESG/extraction` | 单一提取骨干和文档/模型/产物端口 |
| `ExtractESG/shared_runtime` | 本机进程、代码指纹、路径与资源引用保护 |
| `ConsoleESG` | 报告、提取、条款、运行四类父子页面与操作窗口 |
| `PlatESG` / `database` | 既有冻结产品与数据库消费者；不接收本轮新产物 |

条款组只用于展示；16/32 等集合只是编号列表。新增条款信息扩展条款声明。原值、目标口径、报告身份与数值实际身份分别保存，缺失不填零，模型理解不跨条款复用。

```sh
ESG_SKIP_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 ExtractESG/scripts/start.sh
```

Console 为 `http://127.0.0.1:18081/`，文档与编排 API 为 `http://127.0.0.1:18080/docs`，条款与提取 API 为 `http://127.0.0.1:18180/docs`。启动器先构建 Console，再核对三个服务的实际代码身份。条款数据修改由摘要跟踪；执行代码变化要求重启。

首次安装见 [ExtractESG 手册](ExtractESG/README.md)。[目录批量导入](ExtractESG/contracts/examples/report-batch/README.md) 使用 `manifest.json + pdf/`；Console 也支持上传 PDF 并登记身份。

```sh
ExtractESG/.local/python/bin/python ExtractESG/scripts/check_contracts.py
ExtractESG/.local/python/bin/python ExtractESG/scripts/audit_structure.py
ExtractESG/.local/python/bin/python -m pytest ExtractESG/document/tests ExtractESG/extraction/tests -q
npm test --prefix ConsoleESG
```

[架构与接口](docs/ARCHITECTURE.md) · [组织合同](docs/ORGANIZATION.md) · [Console](ConsoleESG/README.md) · [模型边界](docs/MODELS.md) · [本轮全项目审查](docs/PROJECT_AUDIT.md) · [出口方案，仅规划](docs/EXIT_PLAN.md)

条款共 99 个展示组、1,126 条，37 项内容未决明确阻塞；priority32 是 32 个个性化组织样本，其余仍需内容校核。可选状态和执行完成都不等于科学验收或可发布。

原始[升级规划](全链路检查报告与升级规划.md)与[补充要求](更新要求.md)作为需求依据保留。既有 PDF、报告身份和正式/演示数据库不重置、不迁移；`PLATESG_AUTO_PUBLISH=0`。`CrawlESG/` 是保护区，本链路不读取、不修改。
