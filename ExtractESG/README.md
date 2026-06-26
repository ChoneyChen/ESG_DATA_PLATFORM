# ExtractESG

Evidence-first ESG report extraction pipeline.

This is the greenfield extraction service for ESG Evidence Hub. It follows the current project architecture:

```text
Local deterministic PDF processing
  -> Qiniu cloud model API
  -> Local-cloud fusion
  -> Evidence packets
  -> Full harvest / targeted recall
  -> normalization, mapping, verification, review, publication
```

## Non-negotiable Constraints

- Do not self-host OCR, VLM, or LLM models in phase 1.
- All model calls go through the Qiniu AI gateway adapter.
- Model outputs are candidates only. They never write directly to published facts.
- Every extracted fact must reference local evidence: page, text block, crop, table candidate, or another replayable evidence object.
- Long-context model calls must operate on evidence packets, not unbounded whole-report prompts.

## Main Components

| Area | Purpose |
|---|---|
| `ai/` | Qiniu model registry, routing, and API adapter |
| `contracts/` | Pydantic contracts for document IR, evidence, cloud tasks, and ESG extraction |
| `document/local_pdf/` | Local deterministic PDF text, page, coordinate, and render preparation |
| `document/cloud_tasks/` | Typed cloud vision/text task builders |
| `document/fusion/` | Local-cloud result fusion and conflict recording |
| `document/packets/` | Evidence packet construction for extraction agents |
| `extraction/` | Full harvest and targeted recall agent boundaries |
| `validation/` | Evidence verifier and quality gates |
| `workflows/` | Durable workflow shape and state transitions |
| `benchmark/` | Qiniu model/input-granularity benchmark harness |

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m extract_esg.cli init-db
python -m extract_esg.cli run-local "../越秀地产2024可持续发展报告(1).pdf"
python -m extract_esg.cli list-reports
python -m extract_esg.cli model-assessment
python -m extract_esg.cli serve
```

Set `QINIU_API_KEY` before running real cloud tasks.

## API Key and Model Selection

Put credentials and optional model pins in `.env`:

```bash
QINIU_API_KEY=your_api_key
QINIU_BASE_URL=https://api.qnaigc.com/v1

# Optional. Leave blank to auto-select by capability from /v1/models.
EXTRACT_ESG_TEXT_MODEL=
EXTRACT_ESG_VISION_MODEL=
EXTRACT_ESG_REASONING_MODEL=
EXTRACT_ESG_FAST_MODEL=
```

Model registry commands:

```bash
python -m extract_esg.cli refresh-models
python -m extract_esg.cli list-cached-models
python -m extract_esg.cli model-assessment
python -m extract_esg.cli select-model vision_ocr
python -m extract_esg.cli select-model structured_extract
```

`model-assessment` prints the current planning defaults and backups for every
API model step. The live runtime should still confirm account-visible model IDs
through Qiniu `/v1/models` before a paid cloud call.

## Current Local Database

Phase 1 uses SQLite for local development:

```text
.local/extract_esg.db
```

The schema stores reports, pages, evidence packets, candidate disclosures, and cloud task results. It is intentionally isolated behind `SqliteStore` so server deployment can replace it with PostgreSQL repositories later.

## API Model Call Points

Cloud models are expected in these blocks:

| Block | Model Capability |
|---|---|
| Page classification | fast text/vision model |
| Scanned page OCR | Qiniu vision/file recognition model |
| Image-only table interpretation | Qiniu vision + structured output model |
| Full harvest extraction | structured output model |
| Qualitative claim extraction | long-context structured model |
| Requirement mapping | reasoning/long-context model |
| Independent verification | separate model or prompt version |

The default `run-local` command does not call cloud models. It prepares local evidence, evidence packets, and local baseline candidates for offline smoke testing.

## Optional Local Console

The local console is optional. It can choose a PDF, accept a Qiniu API key for
the current browser session, refresh the Qiniu model list, select per-step model
defaults, visualize the local/API-planned workflow, run the local evidence to
SQLite chain, and browse database results:

```bash
python -m extract_esg.cli serve --host 127.0.0.1 --port 18765
```

All core functions remain available through CLI and Python APIs without the frontend.

---

# ExtractESG 中文说明

ExtractESG 是一个“证据优先”的 ESG 报告抽取管线。它是 ESG Evidence Hub 的全新主线抽取服务，不继承旧版实验架构。

当前架构遵循：

```text
本地确定性 PDF 处理
  -> 七牛云模型 API
  -> 本地结果与云模型结果融合
  -> 证据包
  -> 全量收割 / 定向召回
  -> 归一化、标准映射、验证、复核、发布
```

## 不可违反的约束

- 第一阶段不自部署 OCR、VLM 或 LLM 模型。
- 所有模型调用都通过七牛 AI 网关适配器。
- 模型输出只作为候选结果，不能直接写入已发布事实。
- 每一条抽取事实都必须能追溯到本地证据，例如页码、文本块、截图区域、表格候选或其他可回放证据对象。
- 长文本模型调用必须基于证据包，而不是把整份报告无限制地塞进 prompt。

## 主要模块

| 模块 | 作用 |
|---|---|
| `ai/` | 七牛模型注册表、能力路由、API 适配器 |
| `contracts/` | 文档 IR、证据、云任务、ESG 抽取结果的 Pydantic 契约 |
| `document/local_pdf/` | 本地确定性 PDF 文本、页、坐标和渲染准备 |
| `document/cloud_tasks/` | 类型化的云端视觉/文本任务构建 |
| `document/fusion/` | 本地结果与云模型结果融合，并记录冲突 |
| `document/packets/` | 为抽取 agent 构造可回放证据包 |
| `extraction/` | 全量收割与定向召回 agent 边界 |
| `validation/` | 证据验证器和质量门禁 |
| `workflows/` | 可持久化工作流形态与状态流转 |
| `benchmark/` | 七牛模型与输入粒度的评测框架 |

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m extract_esg.cli init-db
python -m extract_esg.cli run-local "../越秀地产2024可持续发展报告(1).pdf"
python -m extract_esg.cli list-reports
python -m extract_esg.cli model-assessment
python -m extract_esg.cli serve
```

真实调用云模型前，需要配置 `QINIU_API_KEY`。

## API Key 与模型选择

把密钥和可选的模型固定配置放在 `.env` 中：

```bash
QINIU_API_KEY=your_api_key
QINIU_BASE_URL=https://api.qnaigc.com/v1

# 可选。留空时，系统会根据 /v1/models 返回的能力信息自动选择。
EXTRACT_ESG_TEXT_MODEL=
EXTRACT_ESG_VISION_MODEL=
EXTRACT_ESG_REASONING_MODEL=
EXTRACT_ESG_FAST_MODEL=
```

模型注册表相关命令：

```bash
python -m extract_esg.cli refresh-models
python -m extract_esg.cli list-cached-models
python -m extract_esg.cli model-assessment
python -m extract_esg.cli select-model vision_ocr
python -m extract_esg.cli select-model structured_extract
```

`model-assessment` 会输出当前每个 API 步骤的默认推荐模型和备选模型。真实付费调用前，运行时仍应通过七牛 `/v1/models` 确认当前账号可见的模型 ID。

## 当前本地数据库

第一阶段本地开发使用 SQLite：

```text
.local/extract_esg.db
```

数据库会保存报告、页面、证据包、候选披露项和云模型任务结果。数据库访问被隔离在 `SqliteStore` 后面，后续服务器部署时可以替换为 PostgreSQL repository。

## 需要调用 API 模型的位置

云模型预计用于以下模块：

| 模块 | 模型能力 |
|---|---|
| 页面分类 | 快速文本/视觉模型 |
| 扫描页 OCR | 七牛视觉或文件识别模型 |
| 图片表格理解 | 七牛视觉 + 结构化输出模型 |
| 全量 ESG 披露抽取 | 结构化输出模型 |
| 定性内容抽取与压缩 | 长上下文结构化模型 |
| 披露要求映射 | 推理/长上下文模型 |
| 独立审核 | 不同模型或不同 prompt 版本 |

默认的 `run-local` 命令不会调用云模型。它只会准备本地证据、证据包和本地 baseline 候选结果，用于离线烟测。

## 可选本地控制台

本地控制台是可选功能。它可以选择 PDF、在当前浏览器会话中填写七牛 API Key、刷新七牛模型列表、选择每个步骤的默认模型、可视化本地/API 规划流程、运行本地证据到 SQLite 的链路，并查看数据库结果：

```bash
python -m extract_esg.cli serve --host 127.0.0.1 --port 18765
```

即使不用前端，所有核心功能仍然可以通过 CLI 和 Python API 直接调用。
