# PDF → 条款原值

| 目录 | 职责 |
|---|---|
| `standard/` | 每条独立声明；展示组和选择集合只组织编号 |
| `contracts/src/` | 条款 SDK、报告资源、两类值、事实/映射/覆盖、证据端口、运行及结果合同 |
| `document/src/esg_document` | 文字层、页面布局、本地 OCR、IR、物理对象与证据 API |
| `pipeline/src/esg_pipeline` | 冻结输入、PDF 准备、统一队列、隔离进程、取消与状态投影 |
| `extraction/src/esg_extraction` | 单一条款提取骨干；文档、模型、产物通过端口连接 |
| `shared_runtime/src/esg_runtime` | 布局、进程身份、发布代码指纹与引用保护 |
| `scripts/` | 启动、单服务运行、合同生成和结构审查 |

一份 `pyproject.toml` 安装源码，不使用源码软链接。开发运行时位于 `.local/python`；本地 MLX 模型运行时通过 `ESG_MODEL_PYTHON` 配置。启动器不初始化或发布数据库。

```sh
ESG_SKIP_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 scripts/start.sh
.local/python/bin/python scripts/check_contracts.py
.local/python/bin/python scripts/audit_structure.py
.local/python/bin/python -m pytest -q
```

需要安装时先创建 Python 3.11+ 虚拟环境，再 `python -m pip install -e '.[dev]'`；模型依赖按本机配置单独安装。安装本身不下载模型、不调用推理服务。

[全链路架构](../docs/ARCHITECTURE.md)、[组织合同](../docs/ORGANIZATION.md)、[Console](../ConsoleESG/README.md)、[本轮审查](../docs/PROJECT_AUDIT.md)。历史文档证据只保留其原数据位置；新检出使用 `document/.local`。原 PDF 与报告身份由报告资源模块登记，跨服务只传资源引用与对象合同。
