# ESG v2 Architecture

## Boundary

v2 is a greenfield extraction system. It starts from a PDF artifact supplied by
an upstream system and does not own crawling, Kodo indexing, company/year/industry
master data, or the global report registry. Local uploads, paths, and URLs are
development input conveniences only.

The current implementation ends at a validated, versioned `Document IR` snapshot.
It does not yet build Evidence Inventory or extract ESG facts.

## Active Pipeline

```text
PDF artifact
  -> PaddleOCR-VL API adapter
  -> immutable ocr_output (raw JSONL + Markdown + images)
  -> PageRenderer (pypdfium2)
  -> LocalPdfProcessor (pdfplumber native text/coordinates/table candidates)
  -> PaddleLayoutExtractor (prunedResult.parsing_res_list)
  -> PaddleOcrDocumentConverter (Document IR candidate)
  -> ParserFusion (OCR/local observations and explicit conflicts)
  -> DeterministicEntityDeduplicator
  -> VisualSemanticGrouper
  -> StructureReconstructor (sections/reading order/caption/footnote links)
  -> TableGraphBuilder (cell graph/header/unit/cross-page links)
  -> VisualRegionBuilder (table/figure crops)
  -> OcrQualityRouter
       -> deterministic pass
       -> one compound task per risk-bearing page
       -> blocking and optional scopes
  -> AgentReviewOrchestrator (optional)
       -> reviewer candidate (confirm/correct/abstain)
       -> provider-neutral AtomicPatch
       -> local PatchGuard
       -> candidate revision and before/after diff
       -> verifier from a different model family
       -> at most one feedback-guided repair round
       -> accepted patch application or bounded human fallback
  -> corrected structure/table/crop rebuild
  -> DocumentIrValidator
  -> immutable document_ir_output revision
```

This is not a strictly linear system. The main dependency direction is forward,
but review creates correction proposals and later Evidence/extraction/verification
may create a `DocumentIrRepairRequest`. Repair never mutates an existing snapshot;
it produces a new revision and downstream consumers must pin the revision they read.

## Cohesion And Coupling

Each module owns one reason to change:

| Module | Owns | Does not own |
|---|---|---|
| `ocr/` | Paddle job submission, polling, raw artifact download | IR structure or ESG semantics |
| `page_renderer.py` | PDF page rasterization | OCR or quality decisions |
| `coordinate_mapper.py` | coordinate-system definitions and transforms | page parsing |
| `provenance.py` | canonical URL sanitization and durable remote-reference filtering | raw artifact mutation |
| `local_forensics.py` | deterministic native text/coordinate/table cross-check | primary OCR |
| `layout_extractor.py` | version-tolerant Paddle raw layout ingestion | Markdown heuristics or ESG extraction |
| `paddleocr_converter.py` | parser observations to IR candidate | review or publication |
| `deduplicator.py` | deterministic canonical entity deduplication | semantic review |
| `visual_semantic_grouper.py` | semantic visual regions vs decoration fragments | chart fact extraction |
| `identifier_normalizer.py` | canonical fixed-width entity IDs before routing | provider object identity |
| `structure_reconstruction.py` | document-neutral structural relationships | ESG topic classification |
| `table_graph_builder.py` | cell adjacency, headers, units, cross-page candidates | metric normalization |
| `visual_region_builder.py` | auditable region crops | chart fact publication |
| `quality_router.py` | page-compound risk reasons, priority and blocking scope | model execution |
| `model_registry.py` | approved/retired models, health, circuit breaker, role ranking | document semantics |
| `review_orchestrator.py` | bounded reviewer/guard/verifier/repair workflow | unrestricted agent autonomy |
| `patch_guard.py` | patch scope, schema, geometry, table and evidence invariants | visual judgment |
| `revision_service.py` | human decisions, targeted repair and immutable children | in-place mutation |
| `parser_fusion.py` | OCR/local observations and accepted-decision reconciliation | Evidence or facts |
| `validator.py` | readiness and Evidence-entry gate | human judgment |
| `versioning.py` | immutable revision lineage | report registry |
| `writer.py` / `reader.py` | Package v1 materialization, sharding, portable hydration, legacy reads | business database writes |
| `storage/package_layout.py` | safe paths, run IDs, package layout, atomic root reservation | document semantics |
| `storage/package_validator.py` | entrypoint, file-set, size and SHA-256 verification | semantic readiness judgment |

These are Python module boundaries in a modular monolith. They are not separate
microservices. Worker deployment can be split later without changing contracts.

## Coordinate Contract

The canonical coordinate system is top-left PDF points (`72 points = 1 inch`).
Each page may also expose rendered-image pixels and Paddle input pixels. Every
mapped bbox records its coordinate-system ID. Transform scale is stored in
`CoordinateSystem`, so a source region can be replayed on the PDF or page image.

If the original PDF is unavailable, Paddle pixel coordinates are preserved but
the validator prevents the snapshot from claiming full coordinate verification.

## Raw Layout Contract

PaddleOCR-VL is read from `prunedResult.parsing_res_list`, including:

- `block_id` and `block_order`;
- `block_label`;
- `block_content`;
- `block_bbox` and polygon points;
- page width and height.

Known fields are normalized, while each raw object's structure and non-sensitive
values are preserved in `LayoutObjectIR.raw_payload`. Credential-bearing URL query
parameters are removed from canonical IR; the exact provider payload remains in the
immutable `artifact-ocr-raw` object referenced by `SourceTrace.raw_object_path`.
If raw layout is missing, Markdown fallback is kept with an explicit
`markdown_fallback_without_layout_geometry` quality flag.

Every Page, Layout Object, Block, Table, Cell and Figure carries stable
`SourceTrace.artifact_ids`. Build-time local paths are converted to package-relative
paths or durable package URIs before persistence; a local reader may hydrate them for
development tools. Downstream consumers must use artifact IDs plus raw object paths as
the durable provenance contract. Tables parsed from a layout block retain that block's
direct raw object path instead of relying on an indirect Block lookup.

## Review And Correction Contract

Qiniu remains the VLM/LLM provider for routed model tasks. It is not the primary
OCR parser. VLM execution is off by default and capped by
`ESG_V2_MAX_VLM_REVIEWS_PER_IR_RUN`.

Review output cannot overwrite parser output. The active `document-ir-v0.4` contract persists:

- `AgentModelCall`: every successful, failed, timed-out, or invalid model attempt;
- `ReviewerResult`: normalized `confirm`, `correct`, or `abstain` result;
- `AtomicPatch`: minimum local change with explicit before/proposed values and evidence;
- `GuardResult`: deterministic checks before any model proposal may be considered;
- `CandidateRevision`: target snapshots and machine-readable before/after diff;
- `VerifierResult`: independent judgment from a different model family;
- `FinalReviewDecision`: auto-confirm, auto-correct, defer, reject, or human boundary;
- `ConflictGroup`: remaining source or semantic disagreement.

The model may never perform an in-place update. A correction is applied only when
the Guard passes and the independent verifier accepts it. A rejected/abstained or
Guard-failed candidate gets at most one feedback-guided reviewer repair. Cloud or
model-format failure becomes `deferred`, not `human_required`. Optional unresolved
visual enrichment does not block Evidence admission. A blocking semantic
disagreement after the bounded repair round is the only automatic human boundary.

Table correction has deterministic compiler operations. For example,
`insert_table_row` accepts only an insertion index and one complete non-overlapping
row; local code shifts every existing cell and rebuilds the grid. Models do not
rewrite the preserved table merely to add a missing visual header. Full-grid
replacement additionally checks rectangular coverage, retained text volume, and
numeric tokens before independent visual verification.

Figure classification uses an explicit Document IR taxonomy: `unknown`, `chart`,
`diagram`, `illustration`, `photo`, `icon`, `decoration`, and `composite`. A model
cannot invent a type outside that contract, and absence of a visible caption is
represented by no patch rather than an empty caption value.

Visual transport is also adapted. Qiniu calls prefer cloud-addressable
`https://` or `kodo://` references; local image data URIs are development fallback
only. The model router maintains an ordered candidate list and may fail over on
429/5xx responses. Every failed model attempt is retained in the review result.

## Readiness Gate

Every snapshot has one status:

- `ready`: structural checks pass and no unresolved review remains;
- `ready_with_warnings`: usable by Evidence with explicit non-blocking warnings;
- `auto_review_pending`: one or more required routed tasks are pending/deferred;
- `review_required`: a structural error or bounded semantic disagreement needs a person;
- `failed`: page coverage or another blocking invariant failed.

`validation_report.json` exposes `can_build_evidence`. Evidence Inventory must not
consume a snapshot when this value is false.

The gate validates more than page counts and review status. It deterministically
checks global and internal ID uniqueness, reciprocal entity references, contiguous
page indices, coordinate-system and bbox validity, complete non-overlapping table
grids, section hierarchy/range containment, local artifact existence and SHA-256,
source-trace artifact coverage, and absence of credential-bearing remote URLs.

## Adapter Rule

The parser remains behind a replaceable adapter boundary:

```text
DocumentParserAdapter
  -> PaddleOCRVLApiAdapter now
  -> LocalPaddleOCRVLAdapter later
```

Moving to local PaddleOCR-VL changes the adapter deployment, not `Document IR` or
downstream Evidence contracts.

## Backend Independence

The backend remains usable without the frontend:

- CLI: `esg-v2 ocr --file ...`
- CLI: `esg-v2 ir --ocr-run-id ... --pdf ... --render-dpi 144`
- API: `POST /api/ocr/jobs`
- API: `POST /api/document-ir/jobs`
- Python: `OcrWorkflow.run(...)`
- Python: `DocumentIrWorkflow.run(...)`

The frontend is an operator console and artifact browser only.

## Product Artifact Package Contract

OCR and Document IR outputs are immutable, self-describing packages rather than
loose collections of debug files. The package contract is independent from the
frontend, local filesystem location, Kodo key prefix, and deployment topology.

### Common package rules

- `manifest.json` is the only package entrypoint and machine-readable table of
  contents. Consumers must resolve files through its relative entrypoints.
- Package manifests and indexes never contain local absolute paths or credentials.
- Runtime job state and logs live under `.local/jobs/` and `.local/logs/`; they are
  not part of an immutable package.
- All platform-generated names use lowercase ASCII kebab-case. Entity files use
  the exact entity ID as the filename.
- Physical page numbering in IDs and filenames is one-based and four digits:
  `page-0001`. JSON retains zero-based `page_index` and one-based `page_number`.
- Page-local objects use a page-qualified, fixed-width sequence:
  `block-p0001-0001`, `layout-p0001-0001`, `table-p0001-0001`, and
  `figure-p0001-0001`.
- Table cells use their stable anchor coordinate:
  `cell-p0001-t0001-r0001-c0001`. Workflow/audit records use a type prefix and
  six-digit package-local sequence: `review-000001`, `model-call-000001`,
  `reviewer-000001`, `guard-000001`, `verifier-000001`, `patch-000001`,
  `candidate-000001`, `decision-000001`, and `conflict-000001`.
- JSON object collections that are read independently use one object per `.json`
  file. Append-only event/ledger collections use `.jsonl`.
- `integrity/files.json` records every package file except the integrity index
  itself: relative path, byte size, media type, and SHA-256. The writer then
  executes package validation against the manifest, entrypoints, file set,
  sizes, and hashes before declaring the write successful. Semantic artifact IDs
  remain separate from physical package-file integrity records.
- A completed package is immutable. Corrections create a new Document IR revision
  with `parent_ir_run_id`; old packages are never edited in place.
- A workflow atomically reserves a previously nonexistent run directory before
  writing. Reusing an existing run ID fails instead of overwriting any file.
- Runtime job-state creation is exclusive as well. API submissions that reuse a
  reserved run ID return HTTP `409`; only background progress updates may update
  the state created for that run.

Run IDs are globally unique and sortable:

```text
ocr-YYYYMMDDTHHMMSSZ-<12 lowercase hex>
ir-YYYYMMDDTHHMMSSZ-<12 lowercase hex>
```

The manifest, rather than parsing the run ID, carries the source OCR run, revision,
parent revision, schema, pipeline, and readiness semantics.

New writes reject non-conforming run IDs and unsafe package directory names.
Historical pre-v1 names remain readable through compatibility readers, but they
cannot be used to create a new Package v1 run.

### OCR Package v1

```text
ocr_output/<ocr_run_id>/
  manifest.json
  source/
    request.json
  provider/
    submit-response.json
    poll-events.jsonl
    result.jsonl
  observations/
    pages/index.json
    pages/page-0001.json
  content/
    pages/page-0001.md
  artifacts/
    index.json
    images/page-0001/image-0001.jpg
    layouts/page-0001.jpg
  integrity/
    files.json
```

`provider/result.jsonl` remains the exact PaddleOCR-VL response. Per-page
observations and portable Markdown are derived views. `artifacts/index.json`
maps provider image references to stable local artifact IDs and relative paths.
`source/request.json` replaces a local upload path with `runtime-upload://...`,
redacts tokens, and sanitizes credential-bearing URL queries. Derived page
observations are sanitized; exact provider files stay unchanged for audit.

### Document IR Package v1

```text
document_ir_output/<ir_run_id>/
  manifest.json
  canonical/
    document.json
    pages/index.json
    pages/page-0001.json
    tables/index.json
    tables/table-p0001-0001.json
    figures/index.json
    figures/figure-p0001-0001.json
    relations/structure-edges.jsonl
    coordinates/systems.json
  observations/
    paddle-layout/page-0001.json
    local-pdf/forensics.json
  artifacts/
    index.json
    page-images/page-0001.png
    crops/tables/table-p0001-0001.png
    crops/figures/figure-p0001-0001.png
  quality/
    quality-report.json
    validation-report.json
  review/
    index.json
    tasks/review-000001.json
    calls/model-calls.jsonl
    results/reviewer-results.jsonl
    results/guard-results.jsonl
    results/verifier-results.jsonl
    patches/atomic-patches.jsonl
    patches/correction-patches.jsonl
    candidates/candidate-000001.json
    decisions/final-decisions.jsonl
    conflicts/conflict-groups.jsonl
  integrity/
    files.json
  exports/
    document-ir.snapshot.json
```

The package has two deliberately different primary files:

- `manifest.json` describes package identity, entrypoints, counts, readiness,
  lineage, and integrity locations.
- `canonical/document.json` is the semantic document root. It contains document
  metadata, section hierarchy, collection references, and stable object indexes.

Canonical page/table/figure shards are the authoritative semantic data used by
Evidence Inventory. `exports/document-ir.snapshot.json` is a compatibility and
debug export that can be regenerated and must not become a second source of truth.
Parser observations, review ledgers, and quality reports remain in the same
revision package for auditability, but they are not canonical document content.

`document-ir-identifiers-v1` is a validation contract, not a naming convention
left to developer discipline. New workflow output is rejected when canonical
page/object/cell/section IDs, review ledger IDs, coordinate-system IDs, artifact
IDs, structure-edge IDs, or table-edge IDs do not match their declared grammar.
The package reader can still open historical flat runs and relocates old page
images when a child revision is created.

Evidence Inventory may start only when the manifest resolves a successful
`quality/validation-report.json` with `can_build_evidence=true`. It reads canonical
collections through the manifest and stores `ir_run_id`, `ir_revision`, and
`source_node_ids`; it must not scan package directories or depend on local paths.

No API token is written to any package artifact.
