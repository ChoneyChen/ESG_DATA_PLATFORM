# ExtractESG 中文说明

`ExtractESG` 是 ESG Data Platform 的抽取主线：把本地 ESG/可持续发展报告从 PDF 转换为可追溯、可复核、可按标准要求消费的结构化数据。项目总览、当前能力边界和开发交接请先阅读仓库根目录的 [`../README.md`](../README.md)。

## 目录结构

| 目录 | 职责 |
|---|---|
| `document_ir_extract/` | OCR、Document IR、视觉复核、统一前端和串行任务控制面 |
| `targeted_table_extract/` | 基于固定 IR revision 和标准包执行定向检索、模型填表、校验与结果导出 |
| `standard_packages/` | ESG 标准要求包的源文件、Schema、编译器和已编译版本 |
| `esg_database/` | PostgreSQL/PGlite 结果数据库雏形与迁移脚本 |
| `scripts/` | 本地统一工作台启动脚本 |

## 数据流

```text
PDF
  -> OCR Package
  -> Document IR Revision
  -> Compiled Standard Package + Evidence Selection
  -> Targeted Extraction Result Bundle
  -> Future Publication / Database
```

各阶段使用不可变或版本化合同衔接。下游通过 ID、版本和相对产物路径引用上游，不反向修改 OCR、Document IR 或标准包。

## 本地启动

在仓库根目录执行：

```bash
bash ExtractESG/scripts/start_local_workbenches.sh
```

默认服务：

- 统一前端：`http://127.0.0.1:18081/`
- Document/OCR API：`http://127.0.0.1:18080/docs`
- 定向抽取 API：`http://127.0.0.1:18180/api/docs`

依赖、环境变量和单模块运行方式请分别查看：

- [`document_ir_extract/README.md`](document_ir_extract/README.md)
- [`targeted_table_extract/README.md`](targeted_table_extract/README.md)
- [`standard_packages/README.md`](standard_packages/README.md)
- [`esg_database/README.md`](esg_database/README.md)

## Git 与隐私规则

只提交源代码、测试、文档、Schema、标准源包、编译包和数据库迁移。以下内容必须保留在本地：

- `.env.local`、API Key、Token 和其他密钥；
- PDF 原件及包含报告内容的 OCR/IR/定向抽取结果；
- `.local/`、SQLite/PGlite 数据、模型文件、虚拟环境、缓存和日志；
- 任何包含个人身份、私人路径或内部账号的信息。

提交前至少运行 `git diff --check`，并确认 `git status --ignored` 中的本地产物没有进入暂存区。

## 开发约束

- `CrawlESG/` 是仓库中的独立保护模块，ExtractESG 变更不得修改它。
- OCR、IR 和定向抽取共享单 Worker 队列，避免本机模型并发争用内存。
- 前端用于选择、展示和操作，业务事实以版本化后端合同与产物为准。
- 修改后优先运行单元、合同或假模型测试；真实 OCR/VLM/抽取任务需明确安排。
