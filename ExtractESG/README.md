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
python -m extract_esg.cli select-model vision_ocr
python -m extract_esg.cli select-model structured_extract
```

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

The local console is optional and read-only:

```bash
python -m extract_esg.cli serve --host 127.0.0.1 --port 8765
```

All core functions remain available through CLI and Python APIs without the frontend.
