# v2 Construction Progress

## 2026-07-11: OCR-first v2 skeleton and first OCR chain

Constructed:

- Created independent `v2/backend` Python backend.
- Created independent `v2/frontend` static frontend.
- Added `PaddleOcrVlClient` based on the provided API pattern.
- Added OCR workflow:
  - submit job;
  - poll job;
  - download result JSONL;
  - save raw artifacts;
  - save per-page Markdown;
  - download markdown images and output images;
  - write manifest.
- Added local `ocr_output/<run_id>/` artifact structure.
- Added FastAPI endpoints for launching and monitoring OCR jobs.
- Added CLI entrypoint for backend-only use.
- Added startup scripts.

Not constructed yet:

- Evidence atoms and packets.
- ESG extraction.
- Standards/concept mapping.
- Verification and human review.
- Production database.
- Server/Kodo integration.
- Report crawling or report registry, which are explicitly outside v2 extraction ownership.

## 2026-07-11: Document IR construction stage

Constructed:

- Added `document_ir_output/<run_id>/` artifact structure.
- Added Document IR contracts:
  - page;
  - section;
  - block;
  - table;
  - cell;
  - figure;
  - VLM review task;
  - quality report.
- Added `OcrArtifactLoader` to read existing `ocr_output` runs.
- Added PaddleOCR-VL markdown/raw JSONL converter.
- Added local PDF forensics with `pdfplumber`:
  - page count;
  - native text length;
  - native text preview;
  - low native text flags.
- Added `ParserFusion` v0.1:
  - merges OCR candidate and local forensics;
  - attaches quality flags;
  - writes formal `Document IR`.
- Added `OcrQualityRouter`:
  - low OCR text coverage routing;
  - native/OCR conflict routing;
  - table review task routing;
  - figure/chart review task routing.
- Added Qiniu model adapter:
  - `/models`;
  - `/chat/completions`;
  - bearer key from env or request.
- Added optional gated VLM review executor:
  - default off;
  - capped by `ESG_V2_MAX_VLM_REVIEWS_PER_IR_RUN`;
  - writes model result back into `review_tasks.json`;
  - does not publish ESG facts.
- Added CLI:
  - `esg-v2 ir --ocr-run-id <run_id>`.
- Added API:
  - `POST /api/document-ir/jobs`;
  - status, manifest, quality report, review tasks, artifacts.
- Updated frontend:
  - keeps OCR status/artifacts/Markdown preview;
  - adds Document IR launch panel;
  - adds IR manifest;
  - adds quality report;
  - adds VLM review task queue;
  - adds `document_ir_output` artifact and JSON preview.
- Stored local OCR token in ignored `v2/.env.local`.
- Migrated existing local Qiniu env keys into ignored `v2/.env.local` when present.

Still not constructed at that checkpoint (superseded by the 2026-07-12 entry below where noted):

- Proper page rendering and region crop artifacts for VLM review.
- Cell-level table geometry from OCR coordinates.
- Cross-page table graph.
- Evidence Inventory.
- Evidence Packet.
- Full Harvest ESG extraction.
- Targeted Recall.
- Unit normalization and quantitative parser.
- Concept/standard mapping.
- Independent verifier agent.
- Human review UI.
- Production database/publication layer.

Verification performed:

- Installed refreshed backend package with `pdfplumber`.
- `python -m compileall -q src` passed.
- `esg-v2 --help` and `esg-v2 ir --help` passed.
- Built Document IR from a mock OCR run:
  - 1 page;
  - 4 blocks;
  - 1 table;
  - 1 figure;
  - 2 VLM review tasks.
- FastAPI health endpoint passed.
- FastAPI Document IR manifest, quality report, review task, artifact endpoints passed.
- FastAPI `POST /api/document-ir/jobs` background path passed.
- Frontend JavaScript syntax check passed with bundled Node.
- Scanned v2 tracked-style files for real OCR/Qiniu secrets; no real token was found outside ignored `v2/.env.local`.

## 2026-07-12: Document IR v0.2 engineering closure

Constructed:

- Upgraded the platform contract to `document-ir-v0.2`.
- Added canonical top-left PDF-point coordinates plus rendered-image and Paddle input coordinate systems.
- Added deterministic page rendering with `pypdfium2`.
- Added SHA-256 tracked page-image artifacts.
- Expanded `LocalPdfProcessor` with native word coordinates and local table candidates.
- Added version-tolerant Paddle raw layout ingestion from `prunedResult.parsing_res_list`.
- Preserved unknown Paddle fields in `LayoutObjectIR.raw_payload`.
- Kept Markdown only as a flagged coverage fallback when layout content is absent or unmatched.
- Added document-neutral structure reconstruction:
  - reading-order edges;
  - page/section containment;
  - caption and footnote attachment candidates;
  - cross-page paragraph continuation candidates;
  - printed-page-label candidates.
- Added `TableGraphBuilder`:
  - row/column adjacency;
  - header-to-cell relationships;
  - row/column header paths;
  - unit hints;
  - cross-page table candidates.
- Added table and figure region crops linked through artifact IDs.
- Changed quality routing from blanket model use to risk-based review tasks.
- Added provider-neutral `CorrectionPatch` and `ConflictGroup` contracts.
- Added final review reconciliation; divergent VLM content remains `human_required`.
- Added immutable IR revisions and parent-revision lineage.
- Added `DocumentIrValidator` with `ready`, `ready_with_warnings`, `review_required`, and `failed` states.
- Added the `can_build_evidence` gate.
- Expanded artifacts with validation, coordinates, layout, structure edges, patches, conflicts, page bundles, figures, page images, and crops.
- Expanded API endpoints for document, pages, tables, figures, validation, revisions, structure, patches, conflicts, and artifact index.
- Added manifest-only run discovery so CLI-produced runs appear in the frontend.
- Added CLI options for render DPI and parent IR revision.
- Rebuilt the independent frontend as a Document Intelligence workbench with:
  - live pipeline stages;
  - run/revision history;
  - readiness and coverage metrics;
  - page images and bbox overlays;
  - raw layout/object inspection;
  - table grid and graph summaries;
  - visual-region gallery;
  - review tasks, patches, conflicts, and complete artifact browsing.

Verification performed:

- `python -m compileall -q src` passed.
- Three automated tests passed:
  - geometry/layout/table graph/crop integration;
  - immutable parent revision increment;
  - FastAPI intermediate-view endpoints.
- A real two-page PaddleOCR-VL API run completed successfully.
- The real run produced 12 raw layout objects across two pages.
- Real-run page-image coverage: 100%.
- Real-run layout geometry coverage: 100%.
- Real-run figure geometry coverage: 100%.
- Browser QA confirmed page switching, rendered page images, 9 page-one overlays, 3 page-two overlays, review tasks, artifact browsing, and no frontend console errors.
- Real Qiniu review testing exposed repeated 502 errors from local data-URI/single-model calls.
- Added cloud-addressable visual input preference (`https://` / `kodo://`) with local data URI fallback.
- Added deterministic Qiniu vision-model failover for retryable 429/5xx errors.
- A subsequent real review automatically failed over from `qwen2.5-vl-72b-instruct` to `doubao-1.5-vision-pro` and completed successfully.
- The successful visual result became a `human_required` Correction Patch and Conflict Group instead of mutating the figure.
- Skipped review tasks now remain unresolved and block Evidence admission.
- Automated tests increased to six and cover visual input transport, model ordering, review schema normalization, conflict creation, and skipped-task gating.

Still not constructed:

- Human UI action to accept/reject a `CorrectionPatch` and create a child revision.
- Targeted partial rebuild execution from `DocumentIrRepairRequest`; the contract is reserved, while current revisions rebuild the document.
- Character-level Paddle coordinates where the provider does not return them.
- Complete merged-cell geometry when Paddle only returns Markdown table content.
- Evidence Inventory and Evidence Packet.
- ESG Full Harvest and Targeted Recall.
- Unit/value normalization and quantitative fact parsing.
- Concept/standard mapping, independent fact verification, human fact review, and publication database.
- Server/Kodo integration, which remains outside the current local Document IR milestone.

## 2026-07-15: Local one-click launcher

Constructed:

- Added `v2/scripts/run_all.sh` as a repository-owned frontend/backend launcher.
- Added backend and frontend health checks before opening the operator console.
- Added safe reuse of existing ESG v2 services and explicit rejection of unrelated port owners.
- Added process cleanup for services started by the launcher.
- Added ignored runtime logs under `v2/.local/logs/`.
- Added the macOS desktop entry `启动ESG-v2前后端.command`.

This launcher changes no OCR, Document IR, review, or validation behavior. The backend
and frontend can still be started and used independently.

Verification performed:

- Shell syntax checks passed for both launcher layers.
- Backend `/health` returned the expected ESG v2 service identity.
- Frontend returned the Document IR Console page.
- A second launcher invocation reused both running services without duplicating them.
- `Ctrl-C` stopped launcher-owned processes cleanly and released ports 18080 and 18081.

## 2026-07-15: Document IR v0.3 deterministic and agent-review closure

Constructed:

- Upgraded the active contract to `document-ir-v0.3` while retaining v0.2 read compatibility.
- Added deterministic HTML table parsing with `rowspan`, `colspan`, embedded-image and header preservation.
- Added canonical Block/Table/Figure deduplication while preserving raw Paddle layout observations.
- Added bbox-IoU image binding and semantic visual grouping so decoration/icon fragments are not canonical figures.
- Expanded `pdfplumber` fusion to retain table and cell observations instead of only table counts.
- Improved section hierarchy, printed page labels, reading order, Table Graph and cross-page table links.
- Replaced separate blanket review items with one compound task per risk-bearing page:
  - explicit scope items;
  - critical/high/normal/low priority;
  - blocking vs optional risks;
  - page plus key crop inputs.
- Added Qiniu model registry:
  - live `/models` refresh;
  - approved current model profiles;
  - retired-model denylist;
  - reviewer/verifier role ranking;
  - in-process health counters, circuit breaker and family-aware failover.
- Added bounded `AgentReviewOrchestrator`:
  - reviewer `confirm/correct/abstain`;
  - every model attempt retained as `AgentModelCall`;
  - provider-neutral `ReviewerResult` and `AtomicPatch`;
  - deterministic `PatchGuard`;
  - candidate target snapshots and before/after diff;
  - verifier from a different model family;
  - at most one feedback-guided automatic repair;
  - explicit `FinalReviewDecision`.
- Added constrained local patch compilation:
  - block/cell text replacement;
  - full table grid replacement with complete rectangular coverage checks;
  - bounded `insert_table_row` that shifts and preserves existing cells locally;
  - bbox, page label, visual type, caption, continuation, merge and split operations;
  - target-scope, before-value, numeric, geometry and table invariants.
- Added the `auto_review_pending` state. API/model failures are deferred automation, not human review.
- Restricted `human_required` to blocking semantic disagreement after bounded automatic repair.
- Added immutable human patch/task decisions and targeted `DocumentIrRepairRequest` child revisions.
- Added new materialized artifacts:
  - `model_calls.json`;
  - `reviewer_results.json`;
  - `atomic_patches.json`;
  - `guard_results.json`;
  - `verifier_results.json`;
  - `final_decisions.json`;
  - `candidate_revisions.json`;
  - per-task `reviews/<task_id>.json` bundles.
- Added API views for every new review artifact, model health, human decisions and targeted repair.
- Expanded the frontend without removing prior OCR/page/layout/table/figure/artifact views:
  - ten-stage pipeline;
  - model registry and health cards;
  - full per-task model timeline;
  - candidate before/after diff;
  - Guard, verifier and final-decision views;
  - human patch actions and targeted repair form.

Real verification performed:

- Rebuilt the real six-page report through the deterministic chain:
  - 6 physical pages and printed labels 62-67;
  - 74 raw layout observations with 100% geometry;
  - 59 canonical blocks and 9 sections;
  - 6 canonical table segments and one cross-page table link;
  - 5 semantic figures;
  - 6 page-compound review tasks, with only 2 currently classified as blocking.
- Live Qiniu `/models` returned 72 entries and all three approved current candidates.
- A first real full review retained six failed/invalid model attempts and correctly produced only deferred decisions; human-review count stayed zero.
- After JPEG/context/token tuning, a real targeted page review produced usable structured responses:
  - Qwen reviewer success in about 26.5 seconds;
  - Kimi fallback success in about 68.5 seconds after Qwen/Doubao timeouts;
  - two bounded reviewer rounds were retained;
  - both proposed table rewrites failed deterministic grid/before-value guards;
  - no parser content was changed;
  - the blocking task correctly ended at `human_required` after the automatic repair limit.
- Backend automated tests: 13 passed.
- Frontend JavaScript syntax check passed with the bundled Node runtime.
- A second targeted real review completed the full acceptance path on `fig-0003-0002`:
  - Qwen reviewer proposed one atomic `set_visual_type` patch;
  - the local Guard accepted the scoped `unknown -> illustration` change;
  - Doubao, from a different model family, independently accepted it at 0.98 confidence;
  - the patch was applied, the candidate diff was retained, and the task ended at `auto_corrected`;
  - all unselected page tasks remained explicitly deferred, so the targeted run correctly stayed `auto_review_pending` rather than claiming report-wide readiness.
- Expanded the visual taxonomy with `illustration` and added a Guard rule that rejects empty caption patches.
- Added whole-grid text-retention and numeric-token checks for table replacement patches.
- Backend automated tests: 15 passed, including the new visual-type and empty-caption Guard cases.
- Browser QA confirmed that the console now:
  - auto-opens the newest IR revision;
  - displays the approved and retired model registry;
  - shows the Qwen reviewer, local Guard, Doubao verifier and final decision in one task timeline;
  - renders the accepted `unknown -> illustration` candidate diff;
  - preserves every earlier OCR, page, layout, table, figure and artifact view.
- Versioned the frontend static assets so local browser caches cannot silently keep an older console.

Still not constructed:

- Persistent cross-process model-health storage; current circuit state is process-local.
- Durable production job queue and distributed worker execution.
- Kodo upload/CDN materialization for page and crop inputs; local development uses compressed JPEG data URIs.
- Production authentication, authorization and operator identity management.
- A formally human-labeled, independently adjudicated validation program and production error-rate calibration.
- Character-level Paddle coordinates where the provider does not return them.
- Evidence Inventory, ESG fact extraction, standards mapping, fact verification and publication database.
- Crawling, report registry and upstream Kodo file indexing, which remain outside v2 ownership.

## 2026-07-16: Document IR model-patch isolation and entity resolution fix

Constructed:

- Fixed the real `set_table_grid target is not a table` failure at the Document IR agent-review boundary.
- Corrected target lookup to resolve only canonical primary IDs. A Block's `table_id` or `figure_id` association can no longer shadow the referenced Table or Figure entity.
- Added deterministic declared-type versus canonical-entity-type checks for every model-proposed patch.
- Made malformed model payload validation exception-safe; bad values become blocking Guard checks instead of workflow exceptions.
- Added isolated full-batch patch application validation before verifier admission or mutation of the canonical Document IR.
- Isolated unexpected per-task agent errors so optional cloud review cannot terminate deterministic Document IR construction.
- Kept Guard-rejected patches as `guard_failed`; they no longer appear as human-acceptable patches after bounded repair fails.
- Added canonical `target_type` to model candidate context to reduce target ambiguity.
- Added globally unique targetable-entity-ID validation and the `duplicate_entity_id_count` metric.
- Made reviewer output budgets task-aware: table/cell tasks receive enough output space for complete rectangular grids while non-table tasks retain a smaller budget.
- Added account-wide Qiniu TPD/quota detection. Account-level 429 responses stop cross-model failover immediately and remain deferred automation.

Verification performed:

- Backend automated tests: 20 passed.
- Added regressions for wrong declared target types, malformed table payloads, Block-to-Table reference shadowing, safe unresolved review behavior, and Qiniu account-wide rate-limit short-circuiting.
- Reused the real six-page OCR run `ocr-968396d90f72`; no second PaddleOCR call was needed.
- The first post-fix full real run completed as `done` and wrote all Document IR artifacts: 6 pages, 59 blocks, 6 tables, 5 figures, 6 review tasks, page/layout/table/figure JSON, crops, model calls, Guards, decisions, and validation.
- That run retained 9 real cloud attempts and completed despite Qwen/Doubao timeouts and truncated Kimi JSON; no model or patch error terminated the workflow.
- Replayed the original real Qwen patch that previously crashed. The corrected resolver returned `TableIR` for `tbl-0004-0001`, and the complete deterministic Guard passed.
- A targeted revision completed as `done` with `entity_ids_globally_unique: true`; Qiniu returned an external account-level TPD limit, so the review correctly remained `auto_review_pending` rather than becoming a false success or system failure.

Still not constructed or externally blocked:

- A fresh independent verifier acceptance on the repaired table patch could not be executed after the Qiniu account reached its TPD limit. It can resume from a new targeted revision after quota recovery; the deterministic replay and all local tests already pass.
- Persistent cross-process model health and a durable distributed task queue remain future server-stage work.
- Evidence Inventory and all later ESG extraction, mapping, verification, and publication stages remain outside the current Document IR milestone.

## 2026-07-16: Document IR hierarchy, provenance and integrity closure

Constructed:

- Bumped the build identifier to `document-pipeline-v0.3.1` without changing the `document-ir-v0.3` field schema.
- Corrected hierarchy-aware section ranges:
  - a section now ends at the next section of the same or higher level;
  - parent sections cover all descendant pages;
  - direct block membership remains attached to the most specific section.
- Added canonical provenance sanitization:
  - credential-bearing `authorization`, signature, token and credential URL queries are excluded from canonical IR;
  - exact provider responses remain unchanged in immutable OCR raw artifacts;
  - sanitized raw-layout objects retain an explicit quality flag and source note.
- Added stable source artifact references for Page, Layout Object, Block, Table, Cell and Figure entities.
- Added direct raw-object lineage for tables parsed from Paddle layout blocks.
- Preserved pdfplumber contribution lineage through `artifact-source-pdf` when local table observations or geometry participate.
- Expanded `DocumentIrValidator` with deterministic checks for:
  - contiguous page indices and internal ID uniqueness;
  - forward and inverse entity references;
  - reciprocal cross-page table continuation links;
  - coordinate-system references, bbox bounds and page ownership;
  - rectangular table coverage, merged-cell overlap, spans and graph endpoints;
  - section parent/child reciprocity, block ownership and range containment;
  - local artifact existence and SHA-256 integrity;
  - source-trace artifact coverage;
  - credential-bearing URL leakage.
- Added explicit integrity checks and metrics to `validation_report.json`; any structural integrity failure now prevents Evidence admission.

Verification performed:

- Backend automated tests increased from 20 to 23 and all passed.
- Added regressions for nested section ranges, signed-URL sanitization, full source artifact coverage, and deliberate reference/grid/hash corruption.
- Rebuilt the real six-page OCR result in a temporary output directory without calling OCR or Qiniu models.
- Real-sample hierarchy now closes the chapter and `1.2.2 風險管理` section on physical page index 5 instead of index 1.
- Real-sample integrity results:
  - 293/293 Page/Layout/Block/Table/Cell/Figure entities have source artifact IDs;
  - 6/6 tables have direct Paddle raw-object paths;
  - zero dangling references, invalid coordinates, invalid table grids, invalid section ranges, artifact errors or source-trace errors;
  - source-trace artifact coverage is 100%;
  - zero credential-bearing remote URLs remain in canonical IR.

Intentionally not changed in this closure:

- OCR recognition quality and model-review outcomes.
- Evidence Inventory or any later ESG extraction stage.
- Kodo, server migration, distributed queues, production authentication or deployment behavior.
- Historical immutable Document IR outputs; the corrected contract applies to newly built revisions.

## 2026-07-18: OCR and Document IR Product Package v1

Constructed:

- Replaced loose output-file conventions with two immutable product packages:
  `ocr-package-v1` and `document-ir-package-v1`.
- Made root `manifest.json` the only machine entrypoint. All declared entrypoints
  and indexes are package-relative and safely resolved inside the package root.
- Separated runtime state from artifacts under `.local/jobs/ocr` and
  `.local/jobs/document-ir`.
- Standardized UTC run IDs:
  `ocr-YYYYMMDDTHHMMSSZ-<12 hex>` and
  `ir-YYYYMMDDTHHMMSSZ-<12 hex>`.
- Enforced safe package directory names to prevent `..`, slash, and path-escape
  behavior in workflows, loaders, revision services, APIs, and local job state.
- Standardized one-based, fixed-width filenames and canonical IDs:
  `page-0001`, `block-p0001-0001`, `layout-p0001-0001`,
  `table-p0001-0001`, `figure-p0001-0001`, and
  `cell-p0001-t0001-r0001-c0001`.
- Standardized package-local review/audit IDs with six digits for tasks, model
  calls, reviewer/guard/verifier results, patches, candidates, decisions, and
  conflicts.
- Added `document-ir-identifiers-v1` validation for canonical objects, review
  ledgers, coordinate systems, artifacts, structure edges, and table edges.
- Added `integrity/files.json` with relative path, media type, byte size, and
  SHA-256 for every other package file.
- Added a package validator that rejects missing entrypoints, unsafe paths,
  manifest/directory ID mismatch, unindexed or stale files, and size/hash changes.
- Added atomic run-directory reservation so rerunning an existing ID cannot
  overwrite an immutable OCR or Document IR package.
- Added exclusive local job-state creation and API conflict handling so
  concurrent submissions with the same run ID return `409` instead of replacing
  queued or completed state.
- Made OCR `source/request.json` portable and credential-safe:
  local paths become `runtime-upload://...`, tokens are redacted, and sensitive
  URL queries are removed.
- Sanitized credential-bearing OCR result URLs before they enter persisted local
  job state or API projections; exact provider polling evidence remains only in
  the provider layer. Historical mutable state snapshots were sanitized as well.
- Kept `provider/result.jsonl` byte-for-byte unchanged while sanitizing the
  derived per-page observation JSON and rewriting Markdown image references to
  package-relative files.
- Split Document IR into canonical, observations, artifacts, quality, review,
  integrity, and exports layers. `canonical/document.json` is the semantic root;
  `exports/document-ir.snapshot.json` is compatibility-only.
- Added Package v1 readers while retaining legacy flat OCR/IR read compatibility.
- Fixed legacy revision migration so old `page_images/page_0001.png` becomes
  `artifacts/page-images/page-0001.png` in a new child package.
- Grouped all OCR and Document IR artifacts in the independent frontend by
  package layer without removing page, layout, table, figure, review, diff, or
  raw-file views.
- Isolated frontend backend contexts: switching Backend URL clears old state,
  cancels polling, and discards late responses from the previous backend.

Verification performed:

- Backend: 28 automated tests passed; only the upstream Starlette/httpx
  deprecation warning remains.
- The Document IR API integration test verifies that resubmitting a completed
  run ID is rejected with `409`.
- Python compileall and bundled Node JavaScript syntax checks passed.
- Rebuilt the existing real six-page OCR result without calling OCR or Qiniu:
  6 pages, 9 sections, 59 blocks, 74 layout objects, 6 tables, 5 figures,
  183 structure edges, 25 semantic artifacts, and 6 review tasks.
- The real Package v1 contained 67 hash-indexed files; all entrypoints, byte
  sizes, and SHA-256 values validated successfully.
- Real-sample portability scan found zero local absolute paths in JSON, JSONL,
  or Markdown package artifacts.
- Real-sample strict identifier validation returned zero errors.
- Browser QA against an isolated Package v1 backend showed 68 visible files
  including `integrity/files.json`, eight clear package groups, all six pages,
  all six tables, five figures, six review tasks, and a working
  `canonical/document.json` preview.
- Browser QA found no console errors and confirmed that all artifact URLs and
  state switched cleanly between independent backend addresses.
- OCR Package v1 was verified with a representative provider payload, including
  raw JSONL preservation, signed-URL sanitization in derived observations,
  portable Markdown images, file hashes, and deliberate tamper detection.

Not constructed or intentionally deferred:

- No new PaddleOCR cloud job was submitted in this packaging iteration; the OCR
  API client and workflow behavior were not changed, and package writing was
  covered without incurring another external call.
- Existing historical output directories remain immutable and keep their old
  flat layout. New runs use Package v1; compatibility readers bridge both forms.
- Evidence Inventory, Full Harvest, Targeted Recall, normalization, standards
  mapping, fact verification, fact-level human review, and publication remain
  future stages after a revision reaches `can_build_evidence=true`.
- Kodo materialization, durable distributed queues, production identity/access,
  and server deployment remain future infrastructure work.
