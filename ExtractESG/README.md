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
python -m extract_esg.cli inspect-pdf "../越秀地产2024可持续发展报告(1).pdf"
python -m unittest discover -s tests
```

Set `QINIU_API_KEY` before running real cloud tasks.

