# v2 Construction Progress

## 2026-08-10: Targeted Recall v2 semantic-contract and grouped-evidence upgrade

Constructed:

- Replaced the first-pass `RequirementProfile` behavior with versioned
  `RequirementExecutionSpec v2`: disclosure type, stable concept, required slots,
  aliases, negative signals, units, period/scope policy, cross-dimension relation and
  multiplicity are now executable fields rather than prompt notes.
- Added a versioned ESRS direct-fill execution rule pack. Coverage audit against the
  supplied 43-row workbook resolves 28 conditional rows through explicit policy/action/
  target rules and all 15 quantitative rows through specialized rules. Unknown future
  rows use a visible `generic_local_rule` and produce a preflight warning.
- Added a deterministic `DisclosureCatalog` that groups cells/rows/regions by physical
  table, logical table, paragraph or figure before assessment. Added a separate local
  feature module for numeric values, periods, broad ESG unit families, statement types,
  explicit-zero claims and index-section identification.
- Rebuilt local retrieval into concept, dimension, topic and combined lanes over exact,
  FTS5/BM25 and trigram search, followed by disclosure-group reranking with explicit
  score components and penalties. Optional local embeddings remain recall-only.
- Added independent per-candidate `RequirementVerifier` records with slot coverage,
  missing reasons, cross-tab intersection protection and traceable `FactInstance` IDs.
  Standards indexes can route retrieval but cannot become final disclosure evidence.
- Implemented real multiplicity behavior: single best, table bundle, all instances and
  dimension-grouped answers. Multiple complete disclosure groups and their facts can now
  answer one task without being collapsed to the first hit.
- Added candidate-limit accounting. Pre-limit count, returned count, limit and truncation
  are persisted; a truncated search can return `uncertain` but cannot produce a definitive
  `not_found`.
- Upgraded immutable Targeted packages to v2 with `catalog/disclosures.jsonl`,
  `assessment/verifications.jsonl`, richer selected packets and
  `quality/search-coverage.jsonl`; the reader remains compatible with historical v1 runs.
- Removed the template adapter's fixed source-cell count assumption. Same-format workbooks
  may contain any number of non-empty Data Point rows, while export still writes only L:P.
- Upgraded the separate Targeted frontend to expose the seven-stage chain, compiled slots,
  compiler strategy, search coverage, selected disclosure groups, structured facts,
  candidate score components, rejection reasons and independent verifier decisions.

Local verification completed:

- Added regressions for index-vs-body evidence, turnover-vs-headcount confusion,
  fake cross-tab composition, generic ethics-vs-anti-corruption training, multiple complete
  disclosures, variable task counts, unknown quantitative requirements and candidate-limit
  false negatives.
- Targeted Recall tests pass: 17 tests.
- Full backend suite passes: 132 tests. The only warning is the existing upstream
  Starlette/httpx deprecation warning.
- A temporary zero-cloud local run reused the existing admitted IR and Evidence Inventory.
  It grouped 8,600 Evidence Atoms into 2,841 disclosures, completed all 43 tasks with
  `found=6`, `not_applicable=11`, `uncertain=26`, passed every Targeted Guard check,
  and preserved repeated annual values in dimension-specific facts. Conservative
  uncertainty is intentional where the report or returned candidate set cannot satisfy
  the complete task contract. The temporary run artifacts were removed after verification.
- Python `compileall` passes. The local machine has no `node` binary; macOS JavaScriptCore/
  JXA successfully parses the Targeted frontend module, supplemented by backend API tests
  and static code review.
- No OCR, Document IR rebuild, Qiniu request or other cloud-model job was started by Codex.

Not constructed in this milestone:

- PDF-first Full Harvest, cross-report Evidence Packet service and publication database.
- Publication-grade numeric canonicalization, unit conversion, reporting-boundary
  resolution, concept mapping across standards, model-assisted fact verification and the
  human fact-review workflow.
- Calibrated report benchmark, local reranker/NLI benchmark, cross-run vector cache,
  additional workbook adapters, server/Kodo deployment and distributed workers.
- Automatic cloud fallback for Targeted Recall. The zero-cloud contract remains enforced.

## 2026-08-09: Local-first Evidence Inventory and Targeted Recall vertical slice

Constructed:

- Added a hard Evidence admission gate that validates Package v1 integrity and
  requires both manifest and validation report to declare `can_build_evidence=true`.
- Added immutable `evidence_output/<evd_run_id>/` packages with paragraph, list,
  footnote, table cell/row/region, logical-table row/region, figure and index atoms.
  Every atom keeps exact source text, page/section location, source node IDs, table
  coordinates where relevant, quality flags, source trace and SHA-256 content hash.
- Added portable SQLite FTS5 indexes with exact, BM25 and trigram retrieval lanes.
- Added generic `StandardTaskSpec` and `RequirementProfile` contracts, a versioned
  ESRS English/Chinese rule pack, and a template registry. The initial adapter reads
  the 43-row ESRS trial workbook and exports only `L:P`; the core workflow does not
  contain those 43 row IDs.
- Added deterministic conditional-applicability, table-concept and necessary-dimension
  assessment. `found`, `not_found`, `not_applicable`, `uncertain`, and `system_failed`
  are separate terminal states; missing evidence cannot prove policy/action/target absence.
- Added Targeted Guard for one-result-per-task, selected atom existence, exact quotes,
  grounded positive conclusions, zero cloud calls and system-failure separation.
- Added immutable `targeted_fill_output/<trg_run_id>/` packages containing task profiles,
  query traces, all candidate hits, selected Evidence packets, validation, local-model
  audit, empty cloud-call audit, canonical answers and template-preserving XLSX export.
- Added independent Python workflow, CLI (`evidence build`, `targeted plan/run`) and
  FastAPI endpoints for plan, jobs, results, per-requirement evidence, artifacts and export.
- Added a separate `targeted.html` frontend page with IR admission, dry-run planning,
  mode selection, local capability status, runtime/cost counters, status filtering,
  evidence detail, retrieval lanes, artifacts and export. Existing Document IR views remain.
- Added optional `sentence-transformers` semantic extra with multilingual E5 as the
  default model ID. Automatic download is off; unavailable local semantics is explicitly
  audited and falls back to `local_strict`, never to Qiniu.
- Installed the semantic optional dependencies in the v2 local virtual environment.
  Multilingual E5 model weights were intentionally not downloaded during construction.

Local verification completed:

- Synthetic admitted and rejected Document IR Package v1 fixtures validate the gate.
- Evidence provenance and package SHA-256 integrity tests pass.
- Conditional `not_applicable`, table dimension match, zero-cloud audit, exact Excel
  column preservation, standalone API planning/running/evidence/export and CLI parsing pass.
- Python compileall and frontend JavaScript syntax checks pass.
- Full backend suite passes with 116 tests; the only warning is the existing upstream
  Starlette/httpx deprecation warning.
- No OCR, real Document IR, Qiniu or user report extraction run was started by Codex.

Not constructed in this milestone:

- PDF-first Full Harvest and report-wide coverage ledger.
- Numeric/unit/period/scope normalization, canonical concept mapping, independent fact
  verification, human fact-review workflow and publication database.
- A calibrated local ESG benchmark, reranker/NLI decision support, cross-run embedding
  cache, additional framework/template adapters, server/Kodo and distributed workers.
- Cloud fallback for Targeted Recall. The current contract intentionally enforces zero calls.

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
  - reviewer `confirm/correct/abstain` (historical v0.3 protocol; replaced by
    `confirm/propose_patch/abstain` on 2026-07-22);
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

## 2026-07-22: PaddleOCR Transport Isolation

Constructed:

- Diagnosed OCR run `ocr-20260722T080454Z-60f9ecf165a4` as a submission-time
  transport failure before Paddle returned a job ID.
- Confirmed that Python `requests` discovered the macOS proxy at
  `127.0.0.1:7897`; proxied TLS failed with `UNEXPECTED_EOF_WHILE_READING`, while
  the same Paddle endpoint returned HTTP `405` immediately when contacted directly.
- Replaced module-level HTTP calls with a dedicated PaddleOCR `requests.Session`.
- Disabled environment/system proxy inheritance for PaddleOCR by default without
  changing Git proxy settings or the Qiniu model transport.
- Added the explicit `PADDLEOCR_VL_TRUST_ENV_PROXY` opt-in for deployments where
  PaddleOCR genuinely must use an environment proxy.
- Preserved the failed run and its partial `source/request.json` as audit evidence;
  no automatic retry or external model call was performed during diagnosis.

Verification:

- Direct Paddle endpoint connectivity returned HTTP `405`, proving TLS and host
  reachability without submitting a job.
- The same Python runtime reproduced the SSL failure with proxy discovery enabled
  and reached HTTP `405` with `Session.trust_env=False`.
- Backend test coverage increased to 29 tests with an explicit transport-policy
  assertion.

## 2026-07-22: Document IR geometry, routing and agent-contract hardening

Constructed:

- Added page-bound bbox normalization shared by deterministic processors:
  small parser drift is clipped and flagged; materially out-of-page or
  degenerate boxes are rejected.
- Hardened `LocalPdfProcessor` so a pdfplumber table candidate such as the real
  page-10 process graphic cannot enter canonical IR with coordinates extending
  beyond the PDF page.
- Extended Parser Fusion with shape/text/order matching for OCR tables that lack
  geometry. A matched pdfplumber observation now recovers table and wrapper
  geometry, cell geometry where shapes agree, and explicit match scores.
- Added `TableGeometryResolver` with deterministic recovery priority:
  wrapper Block, linked Paddle layout, matched local observation, then compatible
  cell-box union. Unresolved tables remain explicit and route to full-page VLM.
- Replaced the ambiguous reviewer protocol with
  `confirm / propose_patch / abstain` plus per-scope decisions. Legacy `correct`
  responses are normalized only at the adapter boundary.
- Moved patch `before_value` ownership from the model to local canonical state.
  Patch Guard therefore checks an exact system-injected precondition rather than
  trusting model-generated optimistic-lock data.
- Replaced the fixed three-task execution behavior with a blocking-first dynamic
  scheduler and independent global, blocking and optional caps.
- Tightened quality routing. Ordinary diagrams, illustrations and decoration do
  not create page-wide model tasks; missing table geometry, native/OCR conflicts
  and material data visuals remain routed.
- Added semantic section filtering for punctuation-only titles, sentence-like
  headings, repeated short card labels and repeated running chapter headers.
- Added Validator metrics for section density, single-page sections, parentless
  sections and suspicious titles.
- Updated README, active architecture and the project specification so the
  documented skeleton matches the implemented chain.

Verification performed:

- Python `compileall` passed.
- Backend coverage increased to 45 tests; all passed. New regressions cover
  material coordinate overflow, harmless clipping, local table rejection,
  missing-geometry recovery, layout recovery, 300 ordinary diagram pages,
  targeted unresolved-table routing, section-noise suppression, dynamic queue
  priority, system-owned `before_value`, legacy reviewer compatibility,
  non-destructive table expansion, canonical agent-generated cell IDs,
  transient verifier retry, and structured verifier disagreements.
- Rebuilding from the existing 300-page OCR Package, without a new OCR request,
  produced 300 pages, 3,417 blocks, 152 tables and 280 figures. Deterministic
  recovery resolved all 10 previously missing table boxes from semantically
  matched Paddle layouts; table geometry coverage reached 100%.
- The rebuilt package had zero invalid coordinates, table grids, identifiers,
  dangling references, section hierarchy errors, artifact integrity errors, or
  source-trace errors. Source-trace artifact coverage remained 100%.
- Section reconstruction reduced the earlier 600 sections to 535 while rejecting
  75 repeated/noisy heading candidates; suspicious accepted section titles were
  reduced to zero. The conservative density warning remains visible rather than
  being hidden.
- Final routing produced 25 compound page tasks, 20 blocking and 5 optional,
  while suppressing 275 ordinary visual reviews. This replaces the previous
  273-task near-page-wide fan-out and retains sparse image-table checks that the
  earlier 18-task in-memory probe did not yet include.
- Real targeted review exposed and drove fixes for four local protocol defects:
  expansion patches were incorrectly penalized by a length ratio; model-provided
  cell IDs could violate the canonical contract; a second whole-document ID pass
  could invalidate unrelated review scopes; and structured verifier disagreement
  objects failed a string-only payload contract. These paths now have regressions.
- A real reviewer proposal passed the corrected Guard and an independent
  different-family verifier, producing an accepted table correction without any
  direct model mutation of canonical IR. Other real calls also demonstrated that
  verifier disagreement is meaningful and must remain unresolved instead of
  being coerced into acceptance.

Still pending in this construction iteration:

- The final end-to-end run after the last verifier-payload compatibility fix must
  be initiated by the user. Codex must not independently start OCR, Document IR,
  or VLM real-chain tests; it will inspect the user's resulting package read-only.
- Evidence admission is intentionally still closed while routed blocking reviews
  remain unresolved. Evidence Inventory and later ESG extraction stages are out
  of scope for this Document IR hardening iteration.

## 2026-07-23: Table fusion, model resilience and review workbench

Constructed:

- Fixed a real duplicate-table routing defect. Multiple pdfplumber candidates
  contained by one richer OCR table are now retained as local observations when
  their geometry and text are subsumed, instead of becoming independent
  `local_only_table_candidate` objects and unnecessary blocking review tasks.
- Made canonical table quality prefer a non-local OCR/table structure while still
  preserving all local observation evidence and provenance.
- Hardened `set_table_grid` compilation so model or human-approved grids inherit
  compatible existing cell boxes by position/text/span or observation geometry.
  Patch Guard blocks complete geometry erasure and reports partial degradation.
- Added a narrow duplicated-digit OCR-noise classifier to text-retention checks.
  Only paired/repeated numeric artifacts may be discounted; real identifiers,
  stock codes and other material numeric tokens remain mandatory.
- Added Validator metrics for cell bbox coverage and full/partial/no-cell-geometry
  table counts.
- Refreshed the Qiniu multimodal registry against the official model catalog and
  live `/v1/models` response. Removed the invalid
  `bytedance/doubao-seed-2-1-pro` profile, added current Qwen, Doubao, StepFun,
  Kimi and MiniMax candidates, expanded retired IDs, and interleaved model
  families in failover order.
- Split model health into transport, protocol and quota categories with separate
  counters and circuit-breaker thresholds. Invalid JSON/schema no longer consumes
  the two-strike transport circuit.
- Added reviewer protocol repair for non-contract `replace/fix/update` verdicts
  and made explicit retries selectable by `review task id`.
- Added immutable pending-review resume. It creates a child revision, defaults to
  unresolved blocking tasks, and appends the complete model/Guard/verifier audit
  trail without mutating the parent package.
- Added `/human-review/inbox`, which groups tasks into human-required, blocking
  deferred, optional deferred and resolved queues and returns deterministic
  operator guidance, page/crop context, Guard failures and verifier disagreements.
- Rebuilt the frontend review area as an operator workbench with separated queues,
  plain-language diagnosis, synchronized page/table viewing, evidence images,
  structured candidate diffs, per-task/all-blocking retry, patch accept/reject,
  keep-current-IR and targeted-repair actions. Raw JSON remains available under
  the advanced audit view.

Verification policy:

- Backend automated tests: 53 passed. Python compileall, bundled Node JavaScript
  syntax validation, and frontend HTML ID uniqueness checks also passed.
- No OCR job, Document IR real chain, or Qiniu inference is started by Codex.
  The next real run remains user-owned and will be inspected read-only afterward.
- Evidence Inventory, server/Kodo migration and distributed production execution
  remain outside this iteration.

## 2026-07-26: Layered review convergence and human-decision hardening

Constructed:

- Upgraded the active contract to `document-ir-v0.5` and pipeline metadata to
  `document-pipeline-v0.5.0`.
- Added `ReviewPlan`. Every routed task now carries one typed question, current
  risk, evidence checklist, allowed operations, automation strategy, human
  boundary, and bounded context targets.
- Split review behavior into page-text coverage, table-candidate classification,
  table reconstruction, figure binding, figure semantic structure, and generic
  Document IR review. Table-like regions are classified before any grid repair.
- Added deterministic preflight before model-catalog access. Tiny, highly sparse,
  non-numeric local-only label grids can be filtered or retired without a cloud
  call; secondary parser candidates that are filtered retain explicit forensic
  flags.
- Added `retire_table_candidate`, `add_visual_text_block`, and
  `set_figure_legend_text` patch operations with target-type, payload, bbox,
  duplicate, provenance and source-ownership Guard checks.
- Added `RetiredEntityIR` and
  `observations/retired-entities.jsonl`. Canonical collections no longer contain
  a retired table candidate, while the complete source snapshot, disposition,
  evidence and task lineage remain auditable.
- Expanded reviewer protocol normalization for unambiguous non-contract output,
  including non-table rejection aliases, top-level table grids, missing
  single-scope target IDs, structured findings and operation aliases.
- Made abstention repair cross-family: the second bounded reviewer attempt
  excludes the first reviewer's model family, so repeated same-family non-answers
  do not manufacture a human escalation.
- Enforced task-specific operation allowlists in Patch Guard. Repeated malformed,
  out-of-scope or Guard-invalid automation is deferred for system replanning and
  no longer converted into a fake human-content problem.
- Changed native/OCR divergence to a one-sided source-coverage check. OCR that is
  longer because it captured chart labels, bilingual text or image content no
  longer creates a false conflict.
- Tightened visual grouping with two-dimensional proximity. Ordinary side
  paragraphs are not attached to a multi-part visual merely because their
  vertical positions are close; contained labels and nearby headings/captions
  remain linkable.
- Corrected the human-decision state machine. Rejecting the last proposed patch
  no longer silently closes the task. A person must either accept a Guard-passing
  patch, or explicitly choose `keep_current` with written evidence; otherwise
  the task remains open for targeted repair.
- Reworked the independent frontend review workbench around “problem, risk,
  evidence, decision”. It distinguishes human content decisions from automatic
  blocking repair and optional enrichment, presents one plain-language question
  and checklist, labels patch operations in Chinese, and prevents acceptance of
  patches that did not pass Guard.
- Updated the active architecture and README to match the v0.5 implementation.

Verification performed:

- Backend automated tests: 62 passed.
- Python `compileall` passed.
- Frontend JavaScript syntax passed with the bundled Node runtime.
- Frontend HTML contains 74 unique IDs and no duplicate ID.
- New regressions cover local sparse-grid filtering, deterministic retirement
  without a model call, non-contract non-table normalization, non-table visual
  materialization, one-sided OCR/native coverage, visual/paragraph binding,
  cross-family abstention, safe human inbox guidance, and explicit keep-current
  closure after patch rejection.

Not performed or still pending:

- Codex did not start OCR, Document IR, or Qiniu inference. The next real
  end-to-end run remains user-owned and must validate the new review-count
  distribution and real model responses.
- This iteration does not implement Evidence Inventory, ESG fact extraction,
  server/Kodo migration, durable distributed queues, or the publication database.

## 2026-07-26: Document IR v0.6 horizontal-spread and review-contract hardening

Read-only audit of the user-produced v0.5 run:

- The run completed its process but Document IR readiness was `failed`.
- The only structural hard failure was a dangling task/scope reference to retired
  `table-p0041-0003`. The table itself was correctly retired, but the review
  ledger still treated only active canonical entities as valid references.
- Nineteen blocking tasks remained deferred after automatic attempts. The
  dominant failed Guard checks were invalid visual-text geometry/evidence,
  wrong operation target types, targets outside the effective context scope,
  source-retention failures, and blocking page targets that were not counted as
  reviewed when the model correctly patched a child object.
- Pages 10-11, 16-17, 58-59 and 108-109 are horizontal logical spreads. The old
  router reviewed each physical half independently, and in two cases did not
  include the unflagged partner page at all. This made a correct table/page
  reconstruction impossible regardless of reviewer model quality.
- Page 18 is a standalone governance diagram; page 20 includes a small duplicate
  assurance-report preview; pages 23-24 and 42 contain real table/coverage
  questions; pages 28, 32, 119, 155, 228, 251 and 258 are standalone
  infographic/diagram semantic tasks. They must not all be treated as the same
  generic page-text problem.
- The `probable_missing_visual_header_row` rule on page 67 was not sufficiently
  grounded. A narrative first row in a visually encoded table does not prove a
  missing header, so that heuristic was removed as a blocking trigger.

Constructed:

- Upgraded the active schema to `document-ir-v0.6` and pipeline metadata to
  `document-pipeline-v0.6.0`.
- Added formal `SpreadIR`, `SpreadPagePlacement` and `SpreadEntityLink`
  contracts. Physical pages and their page-local coordinates remain unchanged.
- Added `HorizontalSpreadBuilder` after visual-region construction and before
  quality routing. It scans adjacent pages using opposite binding-edge contact,
  vertical object alignment, structural labels, printed-page parity when
  available, and grayscale pixel-seam continuity.
- Added immutable side-by-side spread artifacts under `artifacts/spreads/` and
  canonical spread shards under `canonical/spreads/`.
- Added one pair-focused `horizontal_page_spread` review task. Its inputs start
  with the composite and then include both source pages. Candidate member-page
  risks are folded into the pair task instead of creating independent half-page
  reviews.
- Made spread review risk-aware: candidates carrying an existing blocking
  page/table/material-visual issue use the blocking queue; layout-only candidates
  are optional enrichment and cannot exhaust the blocking budget or prevent
  Evidence admission.
- Added typed `confirm_spread`, `reject_spread` and
  `link_horizontal_continuation` patches. Guard requires an explicit spread
  classification, prevents conflicting confirmed memberships, and allows links
  only between same-type entities on opposite member pages.
- Horizontal table segments now retain separate page-local cells/bboxes while
  recording `continuation_axis=horizontal` and reciprocal continuation IDs.
- Added spread-aware validation, identifier patterns, Package v1 entrypoints,
  counts, provenance and artifact integrity checks. Old v0.5 Package v1 readers
  remain compatible.
- Retired entity IDs are now valid audit-ledger reference targets, fixing the
  page-41 dangling-reference failure without restoring the retired table to the
  canonical table collection.
- Changed quality routing from one compound task per risk-bearing page to typed
  object tasks. A page-coverage issue explained by a blocking table/figure is
  carried as `page_text_coverage_support` on that object instead of duplicating
  a whole-page task.
- Review context now sends visible semantic text instead of tag-heavy raw page
  Markdown/HTML, and uses bounded target inventories rather than cutting a JSON
  object in the middle.
- Added a deterministic native-text comparison preprocessor for embedded
  document thumbnails. Dense sub-3.5-point text inside a compact unknown visual
  remains in raw evidence but is excluded from page-level OCR coverage checks.
  Against the existing page-20 snapshot, the comparison length changes from
  1831 raw characters to 307 page-level characters, removes the false open
  native/OCR conflict, and creates no review task.
- Patch Guard now accepts explicit per-scope review decisions as coverage while
  still validating every proposed patch. Context objects are legal bounded
  targets instead of visible-but-unpatchable prompt decoration.
- Reviewer normalization now corrects declared target types from canonical IDs,
  maps figure-targeted visual text to its physical page, resolves evidence refs
  to tracked artifacts, normalizes visual block types, and derives a bbox only
  from a uniquely identified visual object.
- Figure semantic reviews can add page-local visual-text blocks. Figure binding,
  table reconstruction and page coverage retain separate questions and allowed
  operation sets.
- Spread review keeps a broad but bounded VLM repair surface: after classifying
  the relationship, the model may repair member blocks, cells, table grids,
  captions, legends, visual types, bboxes and cross-seam links. Guard enforces
  evidence and preservation constraints rather than replacing visual judgment.
- Added spread-specific frontend evidence ordering: composite first, then left
  and right physical pages, then local crops. Human fallback offers explicit
  “confirm horizontal spread” and “confirm standalone pages” actions; unclear
  cases remain open.
- Revision children copy spread artifacts, hydrate their paths, and preserve
  immutable parent lineage.

Verification performed:

- Backend automated tests: 67 passed.
- Python compileall passed.
- Frontend JavaScript syntax passed.
- New regression tests cover deterministic spread construction, composite
  dimensions, one pair-focused task, spread Guard admission, horizontal table
  linking, embedded-document microtext filtering, and chart non-suppression.
- A read-only heuristic audit against the existing v0.5 snapshot retained all
  four user-identified page pairs after pixel-seam filtering. It did not write a
  revision or call a model.

Not performed or still pending:

- Codex did not start OCR, Document IR, or Qiniu inference. The next real-chain
  run remains user-owned.
- The existing v0.5 output remains immutable and failed; the fixes apply to the
  next v0.6 run or child revision.
- Evidence Inventory, ESG fact extraction, server/Kodo migration, distributed
  queues and publication remain outside this iteration.

## 2026-07-27: Document IR v0.7 blocker-root-cause and transaction hardening

Read-only audit of the latest user-produced v0.6 child revision:

- Three blocking tasks remained: the page 16-17 horizontal spread, page 18
  governance diagram, and page 28 infographic. Twenty-three optional tasks were
  budget-deferred and did not represent human decisions.
- Page 16-17 is a true horizontal spread. Its left physical segment is a
  two-column table and its right physical segment is the continuation of the
  left table's final column. Treating `2 + 1` as three logical columns would be
  structurally valid JSON but semantically wrong.
- The spread reviewer tried to classify the spread, repair the grid, link the
  physical tables, and retire a canonical OCR table in one response. The old
  all-or-nothing Guard correctly rejected the destructive retirement, but also
  discarded valid sibling work.
- Page 18 and page 28 contained useful canonical blocks that were visible in
  context but outside the old writable scope. The model therefore attempted
  duplicate visual text or unsupported deletion instead of localizing and
  structuring the existing objects.
- Repeated retries preserved the same failure shape because failure ownership,
  retryability, and stable fingerprints were not first-class task data.
- The audit was read-only. No existing OCR or Document IR package was changed,
  and no model call was issued.

Constructed:

- Upgraded the active schema to `document-ir-v0.7` and pipeline metadata to
  `document-pipeline-v0.7.0`; v0.6 remains readable.
- Added deterministic visible-text normalization for OCR blocks/cells, including
  presentation-only TeX superscript numbers adjacent to Chinese text.
- Added grounded Markdown-fallback retirement. A bbox-less fallback block is
  removed from canonical content only when same-page grounded blocks from the
  same source observation fully represent it; its snapshot and evidence remain
  under retired observations.
- Added `LogicalCellIR` and expanded `LogicalTableIR` with complete logical grids,
  ordered `source_cell_ids`, explicit composition mode, physical segments, and
  auditable source traces.
- Implemented `vertical_stack`, `horizontal_append_columns`, and
  `horizontal_continue_last_column`. The last mode combines the right physical
  one-column segment with the left table's last logical column instead of
  inventing a third column.
- Added row-alignment gating for horizontal final-column continuation. A row
  mismatch sets `review_required`; Patch Guard blocks spread confirmation until
  a physical-grid repair and Validator checks complete logical-grid/source-cell
  consistency. Intentional overlap is legal only in the final logical column.
- Changed spread risk folding from prompt-only reason text into formal
  `ReviewScopeItem` values. Blocking member-page risks are required decision
  targets, and bounded member blocks/tables/figures are genuinely writable.
- Expanded page-text review context to include localized canonical objects.
  Existing visible text without coordinates can use `set_bbox`; diagram and
  infographic structure can use evidence-bounded `upsert_figure_structure`
  without inventing ESG facts.
- Added independent `PatchTransactionIR` groups. Guard-invalid sibling
  transactions no longer roll back a valid, independently verifiable repair.
- Made transaction coverage explicit across object ancestry: a block/table/figure
  repair can resolve an explicitly reviewed parent-page risk, a cell resolves
  its physical table, and a horizontal-link transaction covers its linked
  members. Conflict closure now uses accepted transaction coverage instead of
  closing every mutable object in the task.
- Extended one Verifier call to return one decision per transaction ID. Accepted
  transactions commit independently; rejected/abstained siblings remain open.
  Legacy top-level-only verifier output remains a conservative all-transactions
  fallback, so this does not multiply API calls merely to obtain isolation.
- Added task/transaction failure class, owner, retryability, stable fingerprint,
  and repeated-failure stopping. Human review is reserved for bounded semantic
  ambiguity; missing evidence, local contract failures, and repeated invalid
  automation remain system work.
- Added the `repair_required` readiness state between retryable
  `auto_review_pending` and semantic `review_required`. System-blocked conflicts
  and non-retryable tasks can no longer masquerade as human content questions.
- Added explicit non-material conflict disposition so optional source
  differences can close without becoming false blockers.
- Persisted model circuit health atomically at
  `document_ir_output/.state/model-health.json`. It stores only model runtime
  metadata, never credentials, report text, or raw replies.
- Added Package v1 logical-table shards and
  `review/patches/patch-transactions.jsonl`, plus reader/API endpoints for
  logical tables and patch transactions.
- Added a frontend physical/logical table view, system-repair queue,
  failure-owner/fingerprint guidance, transaction timeline, and per-transaction
  Verifier decisions. Static asset versions were bumped so the browser does not
  retain the older workbench.
- Updated README and architecture contracts to match the implementation.

Verification performed:

- Backend automated tests: 83 passed.
- Python `compileall` passed.
- Frontend JavaScript syntax passed with the bundled Node runtime.
- Frontend HTML contains 75 unique IDs and no duplicate ID.
- New regressions cover page-28 OCR token normalization, fallback retirement,
  formal spread scopes, exact writable page context, real-shape two-column plus
  one-column continuation, row-alignment Guard failure and repair, logical-cell
  source mapping, illegal non-final-column overlap, independent Guard
  transactions, independent Verifier transaction decisions, persistent model
  health, parent-page coverage, accepted-only conflict closure, readiness
  separation, human inbox routing, and Package/API reads.

Not performed or still pending:

- Codex did not start OCR, Document IR, or Qiniu inference. The next real-chain
  run and the evaluation of its new blocker distribution remain user-owned.
- No historical OCR or IR output was rewritten or deleted in this iteration.
- Evidence Inventory, ESG fact extraction, standard mapping, publication,
  server/Kodo migration, durable distributed task queues, and multi-worker
  orchestration remain outside the current Document IR scope.

## 2026-07-27: Document IR v0.8 real-result failure isolation and composition repair

Read-only audit of the user-produced v0.7 parent and retry child:

- The child revision completed all 300 pages and retained healthy core structure:
  full page-image, layout, physical-table and Figure geometry coverage; no
  duplicate IDs, dangling references, invalid coordinates, invalid grids,
  artifact-integrity errors, source-trace errors or credential-bearing URLs.
- It produced 2,916 blocks, 127 physical tables, 15 logical tables, 335 figures,
  25 spread candidates and 46 review tasks. Fifteen of 22 blocking tasks were
  already auto-resolved.
- Seven blocking tasks remained. The page 16-17 true horizontal spread was still
  retryable because its proposed physical grids did not produce row-aligned
  `horizontal_continue_last_column` composition.
- Six tasks were incorrectly shown as non-retryable system work: page 24, page
  51, two page-89 charts, page 119 and page 155. Five unrelated targets shared
  the exact fingerprint `07d77b6ec6b7c4a13aa5`, proving the old fingerprint
  classified generic Guard wording globally instead of the actual task and
  target semantics.
- Page 24 and page 155 reviewer outputs used the clear alias `needs_repair`.
  The old compatibility layer did not recognize that scope decision, so valid
  child repairs could not satisfy the required parent-page decision.
- Page 51, page 89, page 119 and page 155 figure proposals used
  `visible_text` and crop-local pixel coordinates while labelling the boxes as
  page points. The old Guard correctly rejected out-of-Figure geometry, but its
  generic message did not tell the repair round which element or coordinate
  space was wrong.
- Page 16-17 exposed a transaction-boundary defect. Spread confirmation/link and
  the two physical-table grid repairs were guarded as sibling transactions
  against the unchanged parent document. A link transaction therefore could not
  observe a valid sibling grid repair, and partial acceptance could create a
  misleading composition state.
- Twenty-four non-blocking enhancements remained deferred: 20 layout-only spread
  reviews and four optional chart reviews. They did not block Evidence and were
  not human decisions; they merely had not consumed an optional model budget.
- The child remained `repair_required` with `can_build_evidence=false` because
  of the seven unresolved blocking tasks, not because of the optional queue.

Constructed:

- Upgraded the active schema to `document-ir-v0.8` and pipeline metadata to
  `document-pipeline-v0.8.0`; Package readers retain v0.7 compatibility.
- Rebuilt Guard failure fingerprints from task ID, transaction target set,
  target-operation semantics and failed checks. Repetition is now searched only
  inside the same task. Unrelated pages and figures cannot make one another
  non-retryable.
- Added `needs_repair`, `requires_repair` and `repair` reviewer aliases. A clear
  repair decision remains `propose_patch` even when the first response omitted
  its patch; legacy patchless `correct` remains a confirmation for backward
  compatibility.
- Made horizontal spread composition atomic. Classification, cross-seam link and
  repairs to participating physical tables/cells are planned as one transaction;
  unrelated blocks and figures remain independent.
- Tightened the spread prompt: preserve both page-local physical tables, never
  copy right-page content into the left table, never retire a valid partner as a
  zero-row grid, and align rows before accepting last-column continuation.
- Added Figure coordinate contracts to reviewer context. The preferred output is
  a Figure-normalized `0..1` bbox; the local orchestrator maps it to canonical PDF
  points. Crop dimensions and its rendered-page pixel bbox support deterministic
  legacy crop-pixel conversion. `visible_text`/`label` normalize to `text`.
- Replaced generic Figure Guard text with element-level failures for missing or
  duplicate IDs, missing visible text, unsupported role, invalid/degenerate/
  out-of-target bbox, invalid block binding and relation/evidence errors.
- Added crop/page/spread artifact pixel dimensions and page-pixel crop location
  to the v0.8 artifact contract, enabling deterministic visual-coordinate
  conversion without another model call.
- Added an audited non-blocking resolution path. The frontend can run all
  eligible optional tasks or create an immutable
  `accept_current_nonmaterial` child revision with a written note. Blocking
  tasks cannot use this action, and it never marks candidate semantics verified.
- Added frontend per-task guidance/actions and bumped static asset versions.

Verification performed:

- Python `compileall` passed.
- Targeted review/Guard/revision/spread suite: 57 passed.
- Full backend suite: 89 passed, with one upstream Starlette/httpx deprecation
  warning and no test failures.
- Regression coverage now includes cross-task fingerprint collision isolation,
  true same-task repeated-failure stopping, `needs_repair` normalization,
  atomic spread composition planning, Figure normalized and legacy crop-pixel
  mapping, `visible_text` compatibility, immutable optional closure and blocking
  action rejection.
- Frontend JavaScript parsed successfully with the macOS JavaScript runtime.
  The HTML contains 76 unique IDs, no duplicate IDs, and every static
  `$("#...")` reference has a matching element. No real browser workflow was
  launched.

Not performed or still pending:

- Codex did not start OCR, Document IR or Qiniu inference. The next real-chain
  run remains user-owned and is required to evaluate v0.8 model behavior.
- OCR quality itself, Evidence Inventory and later ESG extraction/mapping/
  publication stages were not changed.
- Server/Kodo migration and distributed execution remain outside this local
  Document IR iteration.

## 2026-07-27: PDFium render-worker crash containment

Incident audit:

- User run `ir-20260727T144313Z-6e5860f75b8e` appeared stuck at
  `2/11 Rendering PDF pages at 144 DPI`.
- All 300 page PNGs were actually written between 22:43:14 and 22:43:57.
  Therefore the document was not stuck on a difficult page and page rendering
  itself took about 43 seconds.
- The FastAPI worker PID 2394 then crashed with `SIGSEGV 11`. The macOS crash
  report located the fault in PDFium's `CPDF_DocPageData` destructor while the
  Document IR job was running in an in-process background thread.
- The uvicorn reloader retained a defunct worker and no replacement API worker
  became responsive. Because native crashes bypass Python exceptions, the
  persisted job state remained `running` at stage 2 forever.

Constructed:

- Moved pypdfium2 rasterization into a supervised child process. A native PDFium
  crash can no longer terminate the FastAPI process.
- Explicitly closes each PIL image, PdfBitmap and PdfPage in dependency order
  before closing PdfDocument.
- Added an atomic worker result/status contract and per-page progress polling.
  The frontend state now advances as
  `2/11 Rendering PDF pages at 144 DPI (N/total)`.
- Added a configurable 30-minute default deadline through
  `ESG_V2_PDF_RENDER_TIMEOUT_SECONDS`. Timeout terminates, then force-kills an
  unresponsive worker and becomes a normal task failure.
- Converts child signal exits such as `SIGSEGV 11` into a Python exception that
  the existing job boundary persists as `failed`, with no permanent running
  state.
- Removes transient render result/status files on success, failure, timeout or
  cancellation.
- Upgraded local pypdfium2 from 5.11.0 to 5.12.1 and constrained the dependency
  to the supported 5.x line.

Verification performed:

- Isolated two-page render completed with typed page artifacts, progress and no
  transient worker files.
- A simulated worker `SIGSEGV 11` was contained and surfaced as an ordinary
  Python failure.
- Document IR pipeline/API regression suite passed.
- Full backend suite: 91 passed, with one upstream Starlette/httpx deprecation
  warning and no failures.
- Codex did not run the user's 300-page OCR/Document IR/VLM chain.

Cleanup:

- The crashed backend process group and stale job are terminated after this
  repair.
- Only the failed run's `document_ir_output` and local Document IR job state are
  removed. OCR packages and persisted model health remain untouched.

## 2026-08-04: Document IR v0.9 typed visual repair and blocker convergence

Read-only audit of the latest user-produced parent and child revisions:

- The latest child retained 46 review tasks: 20 blocking tasks auto-resolved,
  two blocking tasks remained model-owned `repeated_failure`, 23 optional tasks
  were budget-deferred, and one optional task was closed as non-material.
- `review-000007` correctly identified pages 16-17 as a horizontal spread, but
  the left 11x2 and right 8x1 physical tables could not form
  `horizontal_continue_last_column`. One model linked the right table instead of
  the SpreadIR; another destructively rebuilt the left table. The old feedback
  named the row mismatch but did not give an executable 11x1 repair recipe.
- `review-000039` correctly read page 119 OCR errors, including footer text and
  page number `77 -> 117`. The old generic text Guard then rejected the valid
  correction because it was designed to preserve existing source characters and
  numeric tokens. Caption proposals also used unsupported semantic aliases or
  guessed out-of-page boxes.
- Validation also reported two invalid coordinate references on page 251: visual
  blocks retained the temporary `figure-p0251-0001-normalized` identifier instead
  of the page PDF-point coordinate system. The current normalizer already maps
  Figure-relative and legacy crop-pixel boxes to canonical page points before
  Guard/application; this path was rechecked and its regression now asserts the
  final coordinate-system ID as well as numeric coordinates.
- Nineteen unresolved optional tasks were layout-only spread candidates. They
  remain auditable candidate relationships and do not block Evidence. Four were
  Figure binding enhancements; `review-000032` was stale because another
  independently verified correction had already reviewed and structured its
  Figure.

Constructed:

- Upgraded the active schema to `document-ir-v0.9` and pipeline metadata to
  `document-pipeline-v0.9.0`; Package v1 readers retain v0.6-v0.8 compatibility.
- Added `correct_ocr_text`, a Block/Cell-only OCR correction operation. It is
  bounded by tracked visual evidence, reviewer confidence >= 0.85, object-local
  size, exact before-value locking and a mandatory independent verifier. Numeric
  substitutions are explicit audit warnings. Generic replacement retains the old
  source-character and numeric-preservation checks.
- Added `bind_block_to_figure`, allowing a VLM to bind an existing canonical
  block as a Figure element or caption. Guard requires same-page geometry,
  supported role/relation, tracked evidence and confidence >= 0.80. Accepted
  binding updates Figure membership/relations without duplicating visible text.
- Expanded page, Figure and spread ReviewPlans with OCR correction, caption and
  existing-block binding choices. Figure tasks now receive nearby canonical
  blocks as real context and bounded mutable targets.
- Normalized spread classification/link operations to the formal SpreadIR even
  when a model mistakenly names a participating TableIR. Reviewer context now
  includes exact physical table dimensions, likely composition mode, atomic
  operation order and a concrete repair recipe such as preserving the left 11x2
  table while rebuilding the right segment as a complete 11x1 page-local grid.
- Converted `figure_caption` / `table_caption` visual-text proposals into typed
  `set_caption` patches when the referenced object is known. Remaining aliases
  normalize to the canonical `caption` block type.
- Increased only complex blocking review plans to three bounded reviewer rounds.
  Repeated fingerprints remain visible during repair but become non-retryable
  only after the final round; each accepted proposal still requires a
  different-family verifier.
- Recomputed canonical page text after accepted local corrections so PageIR text
  cannot remain stale after a Block, Cell, table, caption, legend or Figure change.
- Added deterministic supersession closure: an optional Figure-binding or exact
  spread task already covered by another independently verified correction is
  auto-resolved without a duplicate model call.
- Updated frontend retry requests to the three-round bounded contract and bumped
  the static asset version. Historical immutable output packages were not edited.
- Added frontend stale-run invalidation: when an active Document IR run no longer
  exists in the backend history, the workbench now clears its cached document,
  artifacts, review state and progress instead of continuing to display a deleted
  run.
- Per the user's pre-test cleanup request, removed generated Document IR packages
  and local Document IR job states, then restarted the local services to clear the
  backend's in-memory job cache. OCR outputs, OCR job states and persisted model
  health were preserved.

Verification performed:

- Python `compileall` passed.
- Targeted Guard/review/spread/revision/pipeline suite: 70 passed.
- Full backend suite: 96 passed, with one upstream Starlette/httpx deprecation
  warning and no failures.
- Regressions cover numeric OCR correction without weakening generic replacement,
  Figure binding without duplicate blocks, caption normalization, wrong-target
  spread-link normalization, 11x2/8x1 composition diagnostics, bounded repeated
  failure behavior and superseded optional-task closure.

Not performed or still pending:

- Codex did not start OCR, Document IR or Qiniu inference. The next real-chain
  run remains user-owned and must confirm that the two known false blockers close.
- A valid system may still produce `repair_required` when visual evidence is
  unavailable, all model services fail, or every bounded proposal is unsafe. The
  v0.9 objective is zero known contract-induced blockers, not false automatic
  acceptance.
- Evidence Inventory, ESG extraction/mapping/publication, server/Kodo migration
  and distributed execution remain outside this iteration.

## 2026-08-05: Document IR v0.10 review-kernel convergence

Constructed:

- Replaced mutable two-stage plan attachment with a single `ReviewPlanCompiler`.
  Router now supplies the complete scope once; plans carry `compiler_version` and
  `contract_hash`, and executor preflight rejects missing or changed plans before a
  cloud call.
- Added `OperationRegistry` as the runtime operation source for target types,
  payload contracts, risk levels and prompt fragments. Patch risk and operation
  target validation now consume the registry.
- Split the review runtime into `ReviewScheduler`, `ReviewContextCompiler`,
  `ModelRunner`, `ReviewResponseAdapter`, `TransactionCoordinator` and
  `ConvergenceEngine`. The old orchestrator coordinates these components and no
  longer regenerates plans.
- Made Spread classification, page-local table repair and cross-seam links one
  composition transaction. Plan preflight catches writable-scope contradictions
  before Qiniu is called.
- Added `TableGridRepairProposal` with source-cell mappings, missing-text inventory,
  visual evidence and structured Guard feedback. Existing source-text, numeric and
  atomic-application Guards remain active.
- Added compact `ChartSpec`; chart tasks no longer request generic Figure structure.
  Figure context is limited to its crop relationship, associated tables and nearby
  blocks.
- Added model capability declarations for vision, JSON output, thinking control,
  minimum/maximum output budget and same-model transient retries. `finish_reason=length`
  and thinking-budget rejection are request-configuration failures and do not poison
  persistent model health.
- Enabled completeness mode by default. Twenty optional Spread tasks and three
  optional Figure tasks form explicit preflight groups and are all scheduled;
  `scheduler_deferred=0` unless an operator deliberately enables a hard bound.
- Upgraded the active schema to `document-ir-v0.10` and pipeline metadata to
  `document-pipeline-v0.10.0`; readers retain older revision support.

Local verification:

- Added regressions derived from the latest three failed objects: pages 16-17
  11x2/8x1 Spread repair transaction, page 24 destructive table remap, and page 89
  chart output truncation with same-task budget expansion.
- Added transient Qiniu-service retry and 20+3 completeness scheduler regressions.
- Full backend suite: 102 passed, with one upstream Starlette/httpx deprecation
  warning and no failures. Python `compileall` passed. No JavaScript runtime is
  installed locally, so the small frontend rendering change was not checked with
  `node --check`.
- No OCR job, Document IR real-chain job or Qiniu inference was started by Codex.
  The next real-chain acceptance remains user-owned.

Acceptance boundary for the next user run:

- zero known contract-owned blockers;
- zero default optional scheduler backlog;
- transient cloud failures consumed inside the current task;
- `can_build_evidence=true` when visual evidence is readable and Qiniu is available.
  Only provider-wide unavailability or genuinely indeterminate visual evidence may
  remain unresolved.

## 2026-08-05: Document IR v0.11 spread preflight and live runtime telemetry

Evidence from the latest immutable user run:

- The final audit contained 350 model-call audit records: 256 verifier and 94
  reviewer records. Of these, 220 were `invalid_response`, 120 succeeded and 10
  failed at the service layer. Summed provider latency was 7,445.2 seconds; token
  usage recorded by the provider was 4,344,486 total tokens.
- The dominant avoidable failure was protocol mismatch, not unreadable evidence:
  97 verifier responses used top-level `approve`, and many per-transaction results
  used `decision=accept` without the canonical `verdict`/confidence fields. These
  valid semantic answers were rejected and retried across model families.
- The report produced 25 horizontal Spread candidates, while only a small subset
  contained a table or information-bearing object crossing the binding seam. Most
  candidates represented a background image or decorative layout continuing across
  two physical pages, so completeness mode paid for unnecessary detailed reviews.

Constructed:

- Upgraded the active contract to `document-ir-v0.11` and pipeline metadata to
  `document-pipeline-v0.11.0`. Package v1 readers retain v0.6-v0.10 compatibility;
  historical output packages were not rewritten.
- Added `SpreadPreflightClassifier` between Spread detection and quality-task
  compilation. It uses already canonicalized Block/Table/Figure seam members to
  classify each candidate as `content_crossing`, `visual_continuity` or `uncertain`.
  Pure visual continuity is terminal locally and does not create a Qiniu task.
- Extended `SpreadIR` with `classification`, `content_dependency`,
  `requires_detailed_review`, `resolution_source`, `preflight_confidence` and
  `preflight_signals`. The spread index and frontend expose the same fields.
- Limited spread scope absorption to relationships that truly require detailed
  review. A visual-only Spread therefore cannot hide an unrelated page, table or
  figure quality risk; that risk continues through its own typed route.
- Added verifier response normalization before Pydantic validation. Common aliases
  such as `approve`, `confirm`, `pass` and per-transaction `decision=accept` are
  converted to the canonical accept/reject/abstain protocol, with bounded confidence
  and disagreement normalization. This removes the known protocol retry loop without
  weakening Guard or independent-verifier requirements.
- Added persistent per-job runtime telemetry in `.local/jobs`: run and stage
  timestamps, logical model calls, actual HTTP attempts, completed attempts, retries,
  success/failure/protocol counts, model/role/failure-category totals, latency, usage
  and the currently active model request. Request-configuration failures before HTTP
  are still audited but are not falsely counted as network requests.
- Applied the same runtime contract to initial Document IR builds, Targeted Repair
  revisions and review-retry revisions. Their stage counts may differ, while model
  request semantics and frontend counters remain identical.
- Added a frontend runtime panel that updates during the run and shows total/current
  stage duration, logical calls, real HTTP requests, same-model retries, successful
  and invalid responses, and the active task/model/role/round. Added a separate
  Spread routing panel showing every candidate's local classification, review need,
  preflight signals and composite image.
- Kept the execution architecture focused: no extra cloud batch-classifier Agent was
  added. The deterministic classifier resolves the large homogeneous visual-only
  set; the existing scoped VLM review remains responsible for the few genuinely
  semantic or uncertain cases.

Offline acceptance evidence:

- Replayed the classifier in memory against the immutable
  `ir-20260804T171639Z-dc537d11150c` canonical snapshot, resetting only in-memory
  spread decision fields to approximate pre-review state. No artifact was modified
  and no cloud call was made.
- Of 25 candidates, 19 became local `visual_continuity`, four remained explicit
  `content_crossing`, and two remained `uncertain`. Only six therefore require
  detailed visual review, a 76% reduction in spread review objects. The true
  page 16-17 table spread remains in the detailed path, as do page 4-5 and page
  10-11 where local structure alone cannot safely decide.

Local verification:

- Added regressions for content-bearing versus visual-only Spread routing, verifier
  alias normalization and runtime model-attempt accounting.
- Full backend suite: 105 passed, with one upstream Starlette/httpx deprecation
  warning and no failures. Python `compileall` passed.
- The running local backend health endpoint and the served frontend v0.11 asset URLs
  were checked. Existing historical job state correctly has no retroactive telemetry.
- Codex did not start OCR, Document IR or Qiniu inference. The next real-chain run
  remains user-owned and must validate the achieved call-count and elapsed-time
  reduction under live provider behavior.

Current acceptance target for the next user run:

- visual-only double-page layouts terminate locally and create zero model attempts;
- true content-crossing and uncertain pairs remain visible and reviewable;
- verifier aliases do not trigger cross-model protocol retries;
- frontend `真实 HTTP 请求` matches new `model-calls.jsonl` HTTP attempts;
- elapsed time and current model request remain visible throughout the run;
- unresolved items arise only from real evidence ambiguity or provider availability,
  not default scheduling or known response aliases.

## 2026-08-05: Provider-aware retry convergence and Verifier checkpoint resume

Evidence from the latest immutable user retry:

- The retry child selected all 22 blocking deferred tasks and issued 34 HTTP
  attempts, but resolved zero tasks. Twenty-two responses were account-wide TPD
  limits and twelve were RPM limits, so unchanged queue counts were real outcomes,
  not stale frontend state.
- The only successful parent Reviewer correctly found a truncated text block and
  missing figure caption, but returned `after_value`/`reason`. The adapter discarded
  `after_value`, and Guard therefore received empty correction values. Three other
  Guard-passing transactions then lost their useful checkpoint when Verifier hit TPD.

Constructed:

- Added `ProviderRateLimitCoordinator` with persisted API-key fingerprint scope,
  account-wide TPD circuit, model-scoped RPM cooldown, bounded backoff, request
  pacing, `Retry-After` parsing and request-ID retention. No credential is persisted.
- A first TPD response now stops the whole review run. Remaining selected tasks are
  deferred without HTTP and record `short_circuited=true`; a known TPD block rejects
  a manual retry before an empty child revision is reserved. TPD during `/models`
  catalog refresh opens the same circuit.
- Split model-call audit categories into `rate_limit_tpd` and `rate_limit_rpm`.
  RPM remains same-task retryable and no longer contaminates generic transport health.
- Added task lifetime state: execution count, Reviewer/Verifier logical call counts,
  last run/time and explicit resume stage. Verifier-pending tasks are scheduled before
  new Reviewer work.
- Preserved Guard-passing patches, candidates and transactions when Verifier is
  unavailable. Retry resumes the exact Verifier checkpoint, including compatible
  legacy service-rejected transactions, and invokes Reviewer again only after a
  semantic rejection or incomplete required scope.
- Hardened response aliases for `after_value`, `after`, `corrected_text`,
  `visual_evidence_refs`, operation-valued scope decisions, and `reason`/`rationale`.
  The page-18 response shape is now a regression fixture.
- Added `document-ir-review-retry-result-v1` to quality report and manifest. It
  records selected/resolved/remaining counts, per-task transitions, new failure
  categories, Provider short-circuit skips and Verifier checkpoint resumes.
- The frontend keeps retry controls locked until the child job actually terminates,
  displays the parent-child retry delta, exposes task resume/call counters, and
  explains an active TPD circuit before another retry is attempted. A temporary
  state-poll failure now retries automatically instead of leaving the controls
  permanently locked; changing backend context or resetting the IR view also clears
  stale retry state.
- Clarified that scheduler preflight groups are grouping metadata, not one shared
  multi-task model response. Independent Guard and Verifier transaction boundaries
  remain mandatory.

Local verification boundary:

- Full backend suite: 112 passed, with one upstream Starlette/httpx deprecation
  warning and no failures. Python `compileall` and frontend JavaScript syntax checks
  passed.
- No OCR job, Document IR real-chain job or Qiniu inference was started by Codex.
- The user remains responsible for the next real-chain run; its immutable result is
  the acceptance evidence for live quota behavior and queue convergence.

## 2026-08-09: Delegated offline review completion and review-contract hardening

Delegated execution boundary:

- The user explicitly requested Codex to replace the unavailable Qiniu visual models
  for the latest immutable parent `ir-20260809T122534Z-3334dc47798d` and complete its
  open review inventory. No OCR job was rerun and no Qiniu request was made.
- Codex supplied bounded Reviewer candidates and an independent second-pass Verifier
  through the existing `ReviewPlan -> AtomicPatch -> PatchGuard -> PatchTransaction ->
  Verifier -> Apply -> Rebuild -> Validator` path. The parent package remains unchanged.
- A full in-memory rehearsal was required to reach 29/29 reviewed tasks, zero new Guard
  failures, zero open conflicts and zero logical tables in `review_required` before an
  immutable child revision could be written.

Reusable chain fixes constructed from the rehearsal:

- Fixed `ReviewResponseAdapter` table-contract completion so an unchanged blank cell is
  never invented as a `source_cell_id`. Only non-empty source text may be auto-mapped;
  this removes the prior Adapter/Guard contradiction on sparse visual tables.
- Reviewer repair rounds still prefer a different model family, but now fall back to the
  only available Reviewer family when no alternative exists. This fallback is Reviewer-
  only; independent Verifier family separation remains strict.
- Tightened the spread operation contract: `confirm_spread` means an information-bearing
  horizontal composition, not merely a continuous background image. Decorative-only
  continuity must use `reject_spread`, preventing fabricated entity links and unnecessary
  downstream visual work.
- Added regressions for blank-cell source mapping, single-family Reviewer repair fallback
  and decorative-only spread semantics.

Immutable output and Evidence admission:

- Wrote child revision `ir-20260809T143952Z-1b1d2739b0f2`, revision 4, with explicit
  `codex_offline_delegated` provenance and zero Qiniu calls.
- All 29 review tasks are resolved; active blocking conflicts, human-required items,
  optional unresolved items and logical-table review requirements are all zero.
- Reconstructed page 16-17 as an 11x2 logical risk table, page 42-43 as a 27x14
  materiality/standards table, and page 108-109 as an 8x5 action/performance/target table.
- Manifest admission is `readiness=ready_with_warnings` and `can_build_evidence=true`.
  Validator produced zero errors. The three remaining warnings are global non-blocking
  coverage diagnostics: four blocks without geometry, 1209/4047 cells with geometry,
  and section over-segmentation risk. Cell-geometry coverage improved from 25.00% in the
  parent to 29.87% in the child.

Local verification:

- Full backend suite: 119 passed, with one upstream Starlette/httpx deprecation warning
  and no failures.
- Package integrity validation passed for the immutable child revision.
