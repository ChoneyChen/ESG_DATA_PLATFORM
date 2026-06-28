# ExtractESG Architecture

ExtractESG is intentionally built around the current deployment constraint: no self-hosted OCR/VLM/LLM models in phase 1.

## Runtime Shape

```text
PDF artifact
  -> LocalPdfProcessor
       native text, coordinates, page geometry, table candidates
  -> EvidencePacketBuilder
       replayable task inputs
  -> QiniuModelRegistry / CloudModelRouter
       model selection by capability, not hard-coded names
  -> QiniuModelAdapter
       /v1/models, /v1/chat/completions, /v1/messages later
  -> LocalCloudFusion
       conflict flags and quality scoring
  -> Extraction / Mapping / Verification
       candidates only until review and publication gates
```

## Controlled Agent Architecture

ExtractESG uses a controlled multi-agent workflow, not an open-ended Codex-like
agent that freely decides tools and edits system state.

In this project an "agent" means a bounded worker:

- it has one role, such as document parsing, evidence construction, extraction,
  mapping, verification, or publication;
- it receives typed inputs such as `DocumentIR`, `EvidencePacket`, or
  `DisclosureObject`;
- it emits typed outputs with source traces and quality flags;
- it can call only the cloud model route assigned to that task;
- it cannot publish a fact unless the workflow's review gates allow it.

The orchestrator is the actual state machine. It owns task ordering, retry
policy, model selection, persistence, and publishability decisions. This keeps
AI useful without letting it silently rewrite the database logic.

## Model Use Policy

The architecture is local-first and evidence-first. A model call is not the
default action; it is triggered when deterministic evidence quality, semantic
ambiguity, visual complexity, or review risk justifies the cost.

Use local deterministic methods first for:

- file hashing, page counting, and report identity;
- native PDF text extraction;
- table candidate detection;
- evidence IDs and packet construction;
- dictionary, standard library, and retrieval candidate generation;
- numeric parsing and unit normalization;
- database writes and review state transitions.

Use cloud models for:

- ambiguous page and section classification;
- scanned or image-only page reading;
- complex visual table understanding;
- full-harvest ESG disclosure candidate generation;
- qualitative claim compression;
- standard-mapping judgement over supplied candidate clauses;
- independent text or visual verification.

The orchestrator must record why a model was called or skipped. Later frontend
views should expose that decision so users can see whether a stage was handled
locally, sent to a model, skipped, or blocked by missing infrastructure.

## Composite Model Calls

Some adjacent model tasks may share one call when they read the same evidence
and produce separable typed outputs. This is a cost optimization, not a license
to collapse the architecture.

Allowed composites:

- page triage: page/section classification plus extraction-worthiness and
  OCR/table-routing flags;
- visual page recovery: OCR text plus visual layout notes for the same page or
  region;
- visual table pass: table structure plus raw value/unit/period candidates from
  the same table crop;
- packet extraction: disclosure candidates plus qualitative claim candidates
  plus initial concept candidates from the same evidence packet;
- report inventory: whole-report topic inventory plus consistency warnings,
  as advisory analysis only.

Do not combine:

- candidate generation and independent verification for the same fact;
- standard clause retrieval and final mapping when the model has not been given
  explicit candidate clauses from the local standard library or retrieval layer;
- model output and publication;
- visual extraction and text-only verification when the fact depends on pixels;
- unrelated packets merely to reduce call count when doing so weakens evidence
  traceability.

## Why Local Evidence Still Matters

Qiniu cloud models may read PDF files or rendered page images, but the platform still needs local evidence anchors:

- page index and printed label
- native text where available
- character/block/region coordinates where available
- page and region crops
- table line/cell candidates
- model invocation metadata

Any cloud output without a replay path remains a candidate and cannot be published.

## Phase 1 Engineering Priorities

1. Local PDF evidence readiness.
2. Qiniu model registry and capability-based routing.
3. Cloud model benchmark harness.
4. Evidence packet quality and cost controls.
5. Human review and publication gates.
