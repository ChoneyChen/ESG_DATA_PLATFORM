# ExtractESG 中文说明

`ExtractESG` 是 ESG Data Platform 的抽取主线：把本地 ESG/可持续发展报告从 PDF 转换为可追溯、可复核、可按标准要求消费的结构化数据。项目总览、当前能力边界和开发交接请先阅读仓库根目录的 [`../README.md`](../README.md)。

2026-10-04：标准包已区分包内“核心条款”与产品“主要条款”。49项主要条款原编号完整保留，当前E1–E3的16项可执行配置接通候选导入、独立审核、规范发布与[PlatESG独立平台](../PlatESG/README.md)。事实与映射分别审核，符合发布条件后自动进入正式数据版本。实现、连接方式与验收边界见平台实施记录（已归档到 Git 历史）。

最终目标是一条口径明确的报告事实及其多标准映射，不是每个条款一个非空格。
2026-10-02 系统升级已接入报告 Session、模型角色路由、跨标准中性来源事实、逻辑表阅读、按缺口补证与内部计算依赖。普通 API/CLI 使用同一队列，结果可通过候选导入器接续现有数据库。
架构依据见 系统重塑方案（已归档到 Git 历史），实施、测试和真实报告验收边界见 升级实施报告（已归档到 Git 历史）。

## 目录结构

| 目录 | 职责 |
|---|---|
| `document_ir_extract/` | OCR、Document IR、视觉复核、统一前端和串行任务控制面 |
| `targeted_table_extract/` | 基于固定 IR revision 和标准包执行定向检索、模型填表、校验与结果导出 |
| `standard_packages/` | ESG 标准要求包的源文件、Schema、编译器和已编译版本 |
| `esg_database/` | PostgreSQL/PGlite 候选、审核、规范发布数据库与迁移脚本 |
| `scripts/` | 本地统一工作台启动脚本 |

## 数据流

```text
PDF
  -> OCR Package
  -> Document IR Revision
  -> ReportSession + Neutral SourceFact + Standard Projections
  -> Targeted Extraction Result Bundles
  -> Candidate Database + Independent Review
  -> Curated Observations + Sealed Dataset
  -> PlatESG Display / Comparison / Evidence / Export
```

各阶段使用不可变或版本化合同衔接。下游通过 ID、版本和相对产物路径引用上游，不反向修改 OCR、Document IR 或标准包。

## 本地启动

在仓库根目录执行：

```bash
zsh ExtractESG/scripts/start_local_workbenches.sh
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
- 正式操作使用 OCR、IR 和定向抽取的单 Worker 队列；旧接口和默认 CLI 均为统一队列适配；worker 内部命令负责实际工作流。
- 前端用于选择、展示和操作，业务事实以版本化后端合同与产物为准。
- 修改后优先运行单元、合同或假模型测试；真实 OCR/VLM/抽取任务需明确安排。


2026-10-22标准包阶段：原表1,126个独立原编号全部编译为99包（2.1.1/2.1.2 draft），其中41包512条为人工撰写，58包614条按EFRAG IG 3机械生成、待逐条复核；核心条款与平台49项主要条款分开，平台字典只把人工撰写的16项标为就绪。详见[建设记录及接口](docs/ESRS_STANDARD_PACKAGE_BUILD.md)。
