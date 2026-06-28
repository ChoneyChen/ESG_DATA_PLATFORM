# ExtractESG Construction Progress

This file is the project construction ledger. Every meaningful construction
step must add an entry here before or with the implementation change.

Last updated: 2026-06-28

## Ground Rules

- `v0.1` remains reference-only. The active system is the greenfield
  `ExtractESG` architecture.
- Every discussion and implementation step should first be checked against the
  active skeleton: evidence-first, local-first, controlled agents, Qiniu-only
  cloud models in phase 1, candidate-only model output, and review gates before
  publication.
- Do not modify the extraction chain casually. Structural changes must be
  recorded here and checked against `docs/ARCHITECTURE.md` and
  `docs/QINIU_MODEL_STRATEGY.md`.
- Model output is always candidate data until verification and publication gates
  are implemented.
- Cloud model calls must remain evidence-bound. Long reports are split into
  evidence packets; the model receives selected packets, not an uncontrolled
  whole-report prompt.
- Secrets must stay outside Git. Use local `.env`, browser session input, or
  server secret management later.

## Source Documents Checked

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/QINIU_MODEL_STRATEGY.md`
- Current implementation under `src/extract_esg/`

## Current End-to-End Chain

```text
PDF
  -> LocalPdfProcessor
     - pdfplumber native text extraction
     - pdfplumber table candidate detection
     - page quality flags
     - DocumentIR
  -> EvidencePacketBuilder
     - text EvidenceFragments
     - EvidencePackets
  -> QiniuModelRegistry / CloudModelRouter
     - /v1/models refresh
     - account-visible model cache
     - preferred model routing with fallback inference
  -> CloudTaskBuilder
     - structured ESG extraction prompt
     - evidence id constrained JSON schema instruction
  -> QiniuModelAdapter
     - /chat/completions real cloud call
     - raw response, usage, latency capture
  -> parse_structured_disclosures
     - JSON extraction from model response
     - DisclosureObject candidates
     - evidence id validation
  -> SqliteStore
     - reports
     - pages
     - evidence_packets
     - disclosures
     - cloud_task_results
  -> CLI / local console
     - run-local
     - run-cloud
     - report and result browsing
```

## What Is Actually Implemented

| Area | Status | Evidence / Files | Notes |
|---|---:|---|---|
| Greenfield project skeleton | Done | `pyproject.toml`, `src/extract_esg/` | Mainline system exists under `ExtractESG`. |
| Contracts | Done | `contracts/` | Pydantic contracts for document IR, evidence, cloud tasks, extraction objects. |
| Local deterministic PDF extraction | Partial | `document/local_pdf/processor.py` | Uses `pdfplumber` for native text and table candidates. Does not yet crop images or recover complex layout geometry. |
| Evidence packet construction | Partial | `document/packets/builder.py` | Builds page-level text packets from native text. Does not yet group by section, table, topic, or semantic chunk. |
| Qiniu API adapter | Done | `ai/qiniu_adapter.py` | Real `/v1/models` and `/chat/completions` calls work. |
| Model registry and routing | Partial | `ai/qiniu_registry.py`, `ai/model_router.py`, `ai/model_assessment.py` | Model list refresh works. Capability inference is used when Qiniu only returns model IDs. Routing exists for planned task kinds. |
| Structured cloud extraction | Implemented first pass | `document/cloud_tasks/builders.py`, `extraction/cloud_structured.py`, `workflows/report_processing.py` | Real `run-cloud` sends selected evidence packets to Qiniu and parses candidate disclosures. |
| Local baseline extraction | Smoke-only | `extraction/full_harvest.py` | Creates local candidate objects for testing only; not a production extraction method. |
| SQLite persistence | Done for local dev | `persistence/sqlite_store.py` | Stores documents, pages, packets, candidate disclosures, and cloud task results. |
| CLI | Done for current chain | `cli.py` | Supports `inspect-pdf`, `init-db`, `run-local`, `run-cloud`, model registry commands, and `serve`. |
| Local frontend console | Partial | `web/app.py` | Can choose PDF, refresh models, select model options, run local/cloud small-sample extraction, and browse DB results. Needs fuller stage visualization and intermediate artifact browsing. |
| Tests | Partial | `tests/` | Covers contracts, routing, parsing, and local smoke paths. Does not yet mock/verify all workflow branches. |
| GitHub proxy setup | Done locally | repo `.git/config` | Uses `http://127.0.0.1:7897` for this repo. |

## Verified Real Runs

| Date | Command / Path | Result |
|---|---|---|
| 2026-06-28 | `refresh-models` with local Qiniu key | Qiniu returned 66 account-visible models. |
| 2026-06-28 | Small `/chat/completions` smoke call | `qwen-turbo` and `deepseek/deepseek-v4-flash` returned valid responses. |
| 2026-06-28 | `run-cloud` on `越秀地产2024可持续发展报告(1).pdf` | 300 pages, 293 packets, 2 packets sent to cloud, 2 cloud results, 7 candidate disclosures parsed and saved. |

## API Model Call Points

The planned API model points still match the original architecture, but only
one of them is currently implemented as a real chain.

| Step | Planned? | Implemented? | Current Model Use |
|---|---:|---:|---|
| Page / section classification | Yes | Not yet | Planned route only. Should classify pages before expensive extraction. |
| Scanned-page OCR / visual reading | Yes | Not yet | Planned route only. No image rendering or VLM OCR call is wired yet. |
| Image/table structure extraction | Yes | Not yet | Planned route only. No crop generation or table-cell VLM extraction yet. |
| Full-harvest structured ESG extraction | Yes | First pass done | `run-cloud` calls Qiniu `/chat/completions` on selected evidence packets. |
| Qualitative claim compression | Yes | Not yet | Planned route only. Current structured extraction may produce qualitative candidates, but no dedicated compression agent exists. |
| Whole-report long-context synthesis | Yes | Not yet | Planned route only. |
| ESG standard mapping review | Yes | Not yet | Planned route only. No HKEX/ESRS/GRI/ISSB clause library or mapper is wired. |
| Independent text/evidence verification | Yes | Not yet | Only local evidence-id subset check exists. No independent model verifier yet. |
| Independent visual verification | Yes | Not yet | Planned route only. |

## Local-First / Model-When-Needed Policy

Current best architecture is not "call a model at every step." The intended
policy is:

```text
local deterministic method first
  -> quality/risk/ambiguity gate
  -> cloud model only when justified
  -> candidate result
  -> deterministic validation and independent review
  -> human review/publication later
```

Examples:

- page classification starts with local headings, TOC, keywords, and page
  quality flags; model classification is used when local signals are ambiguous
  or control expensive downstream work;
- OCR/VLM is skipped when native text is good and used only for scanned,
  image-only, or visually inconsistent pages;
- visual table extraction is skipped when deterministic table extraction is
  good enough and used only for image tables or uncertain header/unit paths;
- structured ESG extraction needs model support for semantic recall, but still
  operates only on evidence packets;
- standard mapping must start from local standard-library/dictionary/vector
  candidates, then use a model for semantic judgement;
- verification must be independent from the generation call for high-risk facts.

Composite model calls are allowed only when they share the same evidence and do
not remove review boundaries. Acceptable composites include page classification
plus routing flags, visual OCR plus layout notes, table structure plus raw
numeric candidates, and packet extraction plus qualitative claim candidates.
Forbidden composites include extraction plus self-verification, model-only
standard retrieval plus final mapping, and model output plus publication.

## Non-Model Tools Actually Used

| Tool / Technique | Used Now? | Purpose | Gap |
|---|---:|---|---|
| `pdfplumber` | Yes | Native PDF text extraction and table candidate detection. | Needs richer block coordinates, table cell extraction, crop rendering, and reading-order checks. |
| `sqlite3` | Yes | Local development DB. | Server version should move to PostgreSQL behind same persistence boundary. |
| Pydantic | Yes | Typed contracts. | Need more schemas for observations, mappings, standards, review states. |
| Deterministic evidence IDs | Yes | Traceability from model output to evidence packet. | Need stable region/table/cell IDs, not just page text fragment IDs. |
| JSON parser / schema-tolerant parser | Yes | Parse model response into candidate disclosures. | Needs stricter schema validation, repair attempts, and error audit records. |
| Local HTTP frontend | Yes | Simple local console. | Needs full workflow visualization, intermediate artifacts, and review UI. |

## Non-Model Tools Not Yet Implemented

| Tool / Capability | Status | Why It Matters |
|---|---:|---|
| OCR engine / page image renderer | Not implemented | Needed for scanned reports and visual table extraction. |
| Page/region crop artifact store | Not implemented | Needed to make VLM answers replayable from exact visual evidence. |
| Table graph / cell-level table extraction | Not implemented | Needed for robust quantitative ESG metrics, headers, units, and dimensions. |
| Synonym dictionary | Not implemented | Needed to map company-specific terms to canonical ESG concepts without relying only on LLMs. |
| ESG standard clause library | Not implemented | Needed for HKEX, ESRS, GRI, ISSB mapping. |
| Vector index / semantic retrieval | Not implemented | Needed for standards lookup, similar disclosure matching, and recall over large corpora. |
| Unit normalization engine | Not implemented | Needed for comparable metrics and database-grade quantitative facts. |
| Deterministic numeric parser | Not implemented | Needed to split raw text into value, unit, period, boundary, and dimensions. |
| Local-cloud fusion logic | Stub only | Needed to reconcile pdfplumber, OCR, table extraction, and model claims. |
| Independent verifier agent | Not implemented | Needed before publication. |
| Human review workflow | Not implemented | Needed for approval/rejection and audit trail. |
| Publication database layer | Not implemented | Current SQLite stores candidates, not approved published facts. |
| Server deployment | Not implemented | Planned future stack is ECS + self-hosted PostgreSQL + existing Qiniu Kodo. |

## Current Frontend State

Implemented:

- PDF path or upload input.
- Qiniu API key input for browser session.
- Qiniu model list refresh.
- Per-step model dropdowns.
- Local extraction button.
- Real cloud extraction button.
- `start_packet` and `max_packets` controls.
- Report list with packet, disclosure, and cloud-result counts.
- Raw report detail JSON display.

Not implemented yet:

- Full chain diagram grouped by stage.
- Clickable intermediate artifacts.
- Page list with quality flags.
- Evidence packet browser.
- Cloud task browser with prompt, input refs, usage, latency, raw response, parsed response.
- Disclosure browser grouped by type and evidence.
- Model-call budget/cost preview.
- Visual review of page crops or table crops.
- Mapping/verification/publication review panels.

## Required Next Frontend Upgrade

The next frontend construction should not change the extraction chain. It should
only expose current state more clearly.

Required view blocks:

1. `PDF Intake`
   - artifact path
   - sha256
   - page count
   - parser version
2. `Local PDF Evidence`
   - page list
   - native text length
   - quality flags
   - table candidate count
3. `Evidence Packets`
   - packet id
   - task type
   - page refs
   - evidence fragment ids
   - text preview
4. `Cloud Model Tasks`
   - request id
   - model id
   - prompt version
   - schema name
   - input evidence refs
   - usage and latency
   - parsed response
5. `Candidate Disclosures`
   - disclosure id
   - type
   - label
   - raw text
   - concept candidates
   - evidence ids
   - quality flags
6. `Not Built Yet`
   - show disabled planned stages: OCR, table graph, synonym dictionary,
     vector retrieval, standards mapping, independent verification, human review,
     publication.

## Construction Log

### 2026-06-28: Current-state audit and progress ledger

Constructed in this entry:

- Added this construction progress ledger.
- Audited current docs and implementation.
- Recorded which pieces are real, partial, or not implemented.
- Recorded next frontend upgrade requirements.

Not constructed in this entry:

- No extraction-chain code changes.
- No frontend redesign yet.
- No new OCR/table/vector/synonym/standard-mapping implementation.

### 2026-06-28: Model gating and composite-call policy

Constructed in this entry:

- Confirmed the local-first/model-when-needed strategy is consistent with the
  active ExtractESG skeleton.
- Updated architecture documentation with model use policy and composite-call
  boundaries.
- Updated Qiniu model strategy with local-first gates and allowed/forbidden
  composite calls.
- Recorded that every discussion and implementation step should check back
  against the active skeleton.

Not constructed in this entry:

- No extraction-chain code changes.
- No new model calls.
- No frontend redesign.
- No OCR/table/vector/synonym/standard-mapping implementation.
