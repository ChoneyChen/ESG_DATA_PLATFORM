# ESG v2 Architecture

## Boundary

v2 is a greenfield extraction system. It starts from a PDF artifact supplied by
an upstream system and does not own crawling, Kodo indexing, company/year/industry
master data, or the global report registry. Local uploads, paths, and URLs are
development input conveniences only.

The current implementation ends at an immutable, validated `Document IR` revision.
Downstream processing and its intermediate packages are outside this codebase.

## Active Pipeline

```text
PDF artifact
  -> PdfCanvasPreflight
       -> pypdf page-box / rotation / catalog inspection
       -> pypdfium2 all-page renderability validation
       -> optional per-page provider-safe rewrite
  -> OcrProviderRouter (local_first by default)
       -> LocalPaddleOCRVLAdapter (Paddle pipeline + MLX-VLM server)
       -> PaddleOCRVLApiAdapter (explicit or controlled fallback)
  -> immutable ocr_output (raw JSONL + Markdown + images)
  -> PageRenderer supervisor
       -> isolated pypdfium2 worker process
       -> per-page progress and deterministic resource release
       -> timeout/native-crash containment
  -> LocalPdfProcessor (pdfplumber native text/coordinates/table candidates)
  -> PaddleLayoutExtractor (prunedResult.parsing_res_list)
  -> PaddleOcrDocumentConverter
       -> deterministic visible-text normalization
       -> grounded Markdown-fallback reconciliation
       -> Document IR candidate
  -> ParserFusion (OCR/local observations and explicit conflicts)
  -> DeterministicEntityDeduplicator
  -> CanonicalIdentifierNormalizer
  -> TableGeometryResolver (block/layout/local observation/cell-union recovery)
  -> VisualSemanticGrouper
  -> StructureReconstructor (sections/reading order/caption/footnote links)
  -> TableGraphBuilder (cell graph/header/unit/cross-page links)
  -> VisualRegionBuilder (table/figure crops)
  -> HorizontalSpreadBuilder
       -> adjacent binding-edge geometry
       -> pixel-seam continuity
       -> side-by-side composite artifact
       -> SpreadIR candidate
  -> OcrQualityRouter
       -> deterministic pass
       -> one typed task per page/table/figure/spread risk
       -> page-risk absorption into the responsible structured object
       -> blocking and optional queues
  -> AgentReviewOrchestrator (optional)
       -> blocking-first dynamic scheduler
       -> reviewer candidate (confirm/propose_patch/abstain + per-scope decisions)
       -> provider-neutral AtomicPatch
       -> local PatchGuard
       -> independent PatchTransaction groups
       -> candidate revision and before/after diff
       -> verifier from a different model family
       -> at most one feedback-guided repair round
       -> accepted patch application or bounded human fallback
  -> corrected structure/table/crop rebuild
  -> LogicalTableBuilder
  -> DocumentIrValidator
  -> immutable document_ir_output revision
  -> DocumentIrRetentionManager
       -> per-OCR quality promotion
       -> queued-consumer rebasing
       -> rollback-capable superseded-package pruning
```

This is not a strictly linear system. The main dependency direction is forward,
but review creates correction proposals and operators may create a scoped
`DocumentIrRepairRequest`. Repair never mutates an existing snapshot; it produces a
new revision and every consumer must pin the revision it reads.

## Cohesion And Coupling

Each module owns one reason to change:

| Module | Owns | Does not own |
|---|---|---|
| `ocr/` | Paddle job submission, polling, raw artifact download, transport isolation | IR structure or ESG semantics |
| `page_renderer.py` | isolated PDF rasterization, progress, timeout and native-crash containment | OCR or quality decisions |
| `coordinate_mapper.py` | coordinate-system definitions and transforms | page parsing |
| `provenance.py` | canonical URL sanitization and durable remote-reference filtering | raw artifact mutation |
| `local_forensics.py` | deterministic native text/coordinate/table cross-check | primary OCR |
| `table_geometry_resolver.py` | table bbox normalization and deterministic geometry recovery | visual semantic judgment |
| `layout_extractor.py` | version-tolerant Paddle raw layout ingestion | Markdown heuristics or ESG extraction |
| `paddleocr_converter.py` | parser observations to IR candidate | review or publication |
| `text_normalization.py` | canonical visible-text cleanup and grounded fallback reconciliation | semantic summarization |
| `deduplicator.py` | deterministic canonical entity deduplication | semantic review |
| `visual_semantic_grouper.py` | semantic visual regions vs decoration fragments | chart fact extraction |
| `identifier_normalizer.py` | canonical fixed-width entity IDs before routing | provider object identity |
| `structure_reconstruction.py` | document-neutral structural relationships | ESG topic classification |
| `table_graph_builder.py` | cell adjacency, headers, units, cross-page candidates | metric normalization |
| `logical_table_builder.py` | physical-table composition and source-cell-preserving logical grids | physical OCR correction |
| `visual_region_builder.py` | auditable region crops | chart fact publication |
| `spread_builder.py` | adjacent-page spread candidates, seam metrics and composite artifacts | ESG interpretation |
| `quality_router.py` | typed risk reasons, priority, scope isolation and spread-aware suppression | model execution |
| `model_registry.py` | approved/retired models, health, circuit breaker, role ranking | document semantics |
| `review_orchestrator.py` | bounded reviewer/guard/verifier/repair workflow, independent patch transactions and failure fingerprints | unrestricted agent autonomy |
| `patch_guard.py` | patch scope, schema, geometry, table and evidence invariants | visual judgment |
| `revision_service.py` | human decisions, targeted repair and immutable children | in-place mutation |
| `parser_fusion.py` | OCR/local observations and accepted-decision reconciliation | downstream semantics |
| `validator.py` | Document IR readiness gate | human judgment |
| `versioning.py` | immutable revision lineage | report registry |
| `writer.py` / `reader.py` | Package v1 materialization, sharding, portable hydration, legacy reads | business database writes |
| `storage/package_layout.py` | safe paths, run IDs, package layout, atomic root reservation | document semantics |
| `storage/package_validator.py` | entrypoint, file-set, size and SHA-256 verification | semantic readiness judgment |

These are Python module boundaries in a modular monolith. They are not separate
microservices. Worker deployment can be split later without changing contracts.

Local MLX-VLM traffic is a protected loopback transport. The worker preserves any
system proxy needed by other services but writes `NO_PROXY/no_proxy` for
`127.0.0.1`, `localhost`, and `::1`; a real OpenAI-SDK model-list request must reach
the managed server before Paddle CV workers start. This matters on macOS, where
`httpx` can discover system proxy settings even when no proxy environment variable
is visible to the process.

## Coordinate Contract

The canonical coordinate system is top-left PDF points (`72 points = 1 inch`).
Each page may also expose rendered-image pixels and Paddle input pixels. Every
mapped bbox records its coordinate-system ID. Transform scale is stored in
`CoordinateSystem`, so a source region can be replayed on the PDF or page image.

If the original PDF is unavailable, Paddle pixel coordinates are preserved but
the validator prevents the snapshot from claiming full coordinate verification.

All local PDF table candidates are normalized before fusion. Small boundary drift
is clipped and flagged; a materially out-of-page or degenerate candidate is
rejected as a parser error. Missing canonical table geometry is recovered in a
fixed order from its wrapper Block, linked Paddle layout, matched pdfplumber
observation, then the union of compatible cell boxes. An unresolved table is
kept as text/structure observation and routed with the full page image; the
system does not manufacture coordinates.

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

Every Page, Layout Object, Block, Table, Cell, Figure and Spread carries stable
`SourceTrace.artifact_ids`. Build-time local paths are converted to package-relative
paths or durable package URIs before persistence; a local reader may hydrate them for
development tools. Downstream consumers must use artifact IDs plus raw object paths as
the durable provenance contract. Tables parsed from a layout block retain that block's
direct raw object path instead of relying on an indirect Block lookup.

## Review And Correction Contract

Routed visual review uses a provider-neutral boundary. The supported providers are
Qiniu multimodal models and the local `numind/NuExtract3-mlx-4bits` MLX runtime.
Neither provider is the primary OCR parser. Deterministic preflight first closes non-material visual-only Spread
relationships; the default completeness scheduler then admits all remaining
blocking work and unresolved optional review groups in the same run. Queue caps
remain available only for explicitly bounded operating modes.

Review output cannot overwrite parser output. The active `document-ir-v0.12`
contract persists:

- `ReviewPlan`: one typed question, risk statement, evidence checklist, allowed
  operations, automation strategy, explicit human boundary, compiler version and
  immutable contract hash;
- `AgentModelCall`: every successful, failed, timed-out, or invalid model attempt;
- `ReviewerResult`: normalized `confirm`, `propose_patch`, or `abstain` result;
- `AtomicPatch`: minimum local change with explicit before/proposed values and evidence;
- `PatchTransactionIR`: independently guarded and committed patch group with
  failure ownership, retryability and stable fingerprint;
- `GuardResult`: deterministic checks before any model proposal may be considered;
- `CandidateRevision`: target snapshots and machine-readable before/after diff;
- `VerifierResult`: a second-pass judgment plus explicit verification policy and
  model-family-independence flag;
- `FinalReviewDecision`: auto-confirm, auto-correct, defer, reject, or human boundary;
- `ConflictGroup`: remaining source or semantic disagreement.
- `RetiredEntityIR`: full snapshot and evidence trail for a secondary parser
  candidate removed from the canonical layer.
- `SpreadIR`: an auditable logical relationship between two adjacent physical
  pages, including composite evidence, page placements, seam metrics, local
  preflight classification and optional cross-seam entity links.
- `LogicalTableIR`: a source-cell-preserving logical grid composed from one or
  more physical `TableIR` segments.

Review is layered rather than one generic page prompt:

```text
deterministic candidate screening
  -> Router collects final risk/evidence/scope
  -> ReviewPlanCompiler compiles exactly once
  -> OperationRegistry binds operation target/schema/risk/prompt contracts
  -> Scheduler and compact ContextCompiler
  -> capability-aware ModelRunner and ResponseAdapter
  -> object classification when type is uncertain
  -> minimum structure/text proposal when correction is needed
  -> local Patch Guard
  -> TransactionCoordinator
  -> verifier (Qiniu different family / local isolated same-model pass)
  -> ConvergenceEngine feedback and bounded automatic repair
  -> human only for material semantic ambiguity
```

`set_table_grid` uses `TableGridRepairProposal`. A complete proposal carries every
final cell, source-cell mappings, an explicit list of text missing from the parser,
visual evidence references and a repair reason. Guard returns missing source IDs,
their conflicting text and the recommended operation as structured details. It does
not weaken source-text or numeric-token preservation.

Charts use compact `ChartSpec` rather than an unconstrained figure narrative. Figure
context contains the crop, associated table and nearby blocks only. Output budget is
computed from task kind and context complexity, then clamped by each model's declared
vision, structured-output, thinking-control and output-token capabilities. Output
truncation and thinking-budget rejection are request-configuration failures, not
model-health failures; transient transport errors retry within the same task before
cross-model failover.

Obvious small, sparse, non-numeric local-only grids are filtered before routing.
If such a candidate is already canonicalized, a deterministic
`retire_table_candidate` patch may remove it without a cloud call only when the
Guard proves that it came exclusively from the secondary local parser. Its full
snapshot remains under `observations/retired-entities.jsonl`; source text blocks
and raw parser observations are preserved. A `non_table_visual` disposition also
links to an overlapping canonical Figure or materializes an `unknown` Figure at
the same bbox, so removing the false table does not erase the visible object.

Every reviewer response must decide every routed scope. `confirm` means the
candidate is materially correct; `propose_patch` means at least one atomic patch
is required; `abstain` means the evidence cannot support a decision. The local
orchestrator injects each patch's exact `before_value` from canonical IR, so a
model cannot guess or rewrite the optimistic-lock precondition. Legacy `correct`
responses are normalized at the adapter boundary and are never part of the new
canonical protocol. The compatibility boundary also normalizes unambiguous
non-contract responses such as `reject_as_nontable` into a typed retirement
proposal. It never treats an unparseable model response as a semantic decision.

The model may never perform an in-place update. A content or structural correction
is applied only when the Guard passes and the configured verifier accepts it. A
pure `confirm` transaction makes no semantic content change; after the local Guard
confirms scope, evidence and required-target coverage, it closes without a redundant
second model call. A rejected/abstained or
Guard-failed candidate gets at most two feedback-guided reviewer repairs. Cloud,
model-format, task-budget, or repeatedly Guard-invalid output becomes `deferred`,
not `human_required`. Optional unresolved visual enrichment does not block
Document IR admission. A blocking semantic disagreement after a Guard-passing
proposal reaches an independent verifier is the primary automatic human boundary.
Reviewer abstention may also reach a person only after the bounded evidence-guided
attempts are exhausted. Repair rounds exclude the preceding reviewer's model
family when another suitable family is available; repeating the same model-family
non-answer is not treated as independent evidence. Repeated Guard fingerprints are
recorded during the bounded run, but become non-retryable only after all three
rounds are exhausted.

A verifier rejection is valid only when it names a concrete evidence disagreement.
An empty `reject` or `abstain` is a model-protocol error retried inside the runner;
it is never promoted into a human semantic disagreement.

One reviewer response may contain several unrelated repairs. They are partitioned
into `PatchTransactionIR` groups by local commit scope. Cells remain with their
physical table. A horizontal spread's classification, cross-seam link and repairs
to the participating physical table segments form one atomic composition
transaction, because the link Guard must evaluate the repaired grids rather than
the unmodified parent snapshot. Unrelated figure or block repairs remain isolated.
A semantic `confirm_spread` is not itself a complete transaction when local geometry
finds one unambiguous cross-seam entity pair. After response adaptation, the local
transaction compiler deterministically appends the required
`link_horizontal_continuation` proposal before Guard execution. This applies equally
to spread-targeted tasks and page-targeted repair tasks whose formal scope contains the
spread, so a correct model confirmation cannot become an unrecoverable local-contract
blocker merely because the Adapter normalized its verdict to `propose_patch`.
A malformed figure patch therefore cannot discard an otherwise valid table repair
from the same response, while a spread cannot partially commit an invalid logical
composition. Failures are classified as
`model_protocol`, `system_contract`, `evidence_missing`,
`verifier_disagreement`, `semantic_ambiguity`, `model_service`, `rate_limit`, or
`repeated_failure`. Tasks additionally use `scheduler_deferred` when they have not
yet consumed an eligible automatic attempt. A stable failure fingerprint includes
the task ID, actual target set, target-operation semantics and failed Guard checks.
Only the same task repeating the same semantic failure becomes
`repeated_failure`; identical generic Guard messages on unrelated pages cannot
poison one another.

Chart output has a deterministic adapter boundary before protocol validation.
`ChartSpecNormalizer` accepts only known structural aliases such as `type`,
`x_categories`, object-valued categories, and `series[].data/values`, then emits the
single canonical `ChartSpec` shape with `series[].points`. Explicit values,
percentages, units, grouping labels, totals, stacking hints, and page/crop evidence
are preserved. Unsupported or data-poor shapes remain unchanged and fail closed at
the Guard; the normalizer does not infer unseen chart semantics.

Object-valued `categories` may also contain complete series objects shaped as
`{name, unit, points}`. The adapter moves those objects into canonical `series`, maps
unknown visual chart labels such as quadrant diagrams to `chart_type=other`, and
preserves the source label in notes. Ambiguous figure-binding patches and empty
captions are discarded as malformed optional proposals before AtomicPatch creation;
they cannot crash the Guard or become system-owned repair blockers.

HTML table materialization is occupancy-aware. When malformed OCR HTML declares a
`colspan` that crosses columns already reserved by an earlier `rowspan`, the parser
retains the cell text and clamps only that conflicting span to the contiguous free
grid. The repair receives `html_col_span_clamped_around_rowspan`; raw OCR HTML remains
unchanged in observations.

The Guard validates ChartSpec structure and visual-evidence resolution independently.
A schema error therefore cannot erase an otherwise valid crop reference. Recognized
aliases that somehow reach the Guard are classified as a local adapter contract
failure, not as a semantic model failure. Terminal repeated-failure fingerprints are
compiled from the task ID, actual target set, target-operation semantics, and Guard
feedback, so unrelated charts never share a terminal retry block.

Before semantic normalization, `ModelJsonObjectDecoder` provides one deliberately
narrow syntax-recovery rule. If an object inside an array is missing only its closing
brace and the next sibling object has already begun, the decoder inserts that single
container delimiter and reruns the standard JSON parser. It never rewrites keys,
strings, numbers, units, or evidence references. Recovered payloads carry an adapter
quality flag and still pass Pydantic validation, Patch Guard, independent verification,
and transactional application. Every other malformed shape fails closed. JSON/protocol
failures are owned by `model_protocol · model`; runtime transport failures remain
`model_service · service`, while internal application exceptions are
`system_contract · system`.

Independent verification does not require one paid call per transaction. One
Verifier call returns exactly one decision per transaction ID; accepted
transactions are committed separately, while rejected or abstained siblings remain
open. Older providers that return only one top-level verdict are supported as a
conservative all-transactions fallback.

Transaction coverage is explicit rather than inferred from a broad writable scope:
a block/table/figure repair covers its parent physical page for routed coverage
accounting; a cell repair covers its physical table; and a horizontal link covers the
linked members. Guard still requires an explicit reviewer scope decision whenever
one of those ancestors is a required target. Only targets covered by accepted
transactions can close conflicts.

The review plan constrains the patch vocabulary. In addition to text, table,
bbox and caption operations, v0.9 supports:

- `retire_table_candidate` for a proven local-only non-table, decoration, or
  duplicate fragment;
- `add_visual_text_block` for visible figure text with a page-local bbox;
- `set_figure_legend_text` for explicit visible legend labels.
- `set_bbox` for evidence-grounded localization of an existing object;
- `upsert_figure_structure` for diagram/chart members without inventing ESG facts;
- `bind_block_to_figure` for binding an existing canonical block as a figure
  element or caption without creating duplicate text;
- `correct_ocr_text` for one visually verified Block/Cell OCR substitution. It
  permits an audited numeric correction but requires tracked visual evidence,
  high reviewer confidence, bounded object-local text and a different-family
  verifier; generic replacement keeps its strict source/numeric preservation;
- `confirm_spread` / `reject_spread` for an explicit adjacent-page decision;
- `link_horizontal_continuation` for same-type entities on opposite member pages.

Every operation has target-type, payload, evidence and preservation checks.
Models cannot invent operations or use a table patch to mutate a figure.
These constraints are evidence boundaries rather than semantic shortcuts. A
spread reviewer may classify the relationship and, within the routed member
pages/entities, also repair blocks, cells, table grids, captions, legends,
visual types, bboxes and horizontal continuation links. Guard rejects
unsupported or destructive changes but does not reduce the VLM to a binary
approval role.

Figure-structure patches use an explicit coordinate boundary. Reviewer context
supplies the canonical Figure bbox in PDF page points and asks for Figure-relative
normalized bboxes in the `0..1` range. The orchestrator deterministically maps
those boxes into page points. For compatibility, an element that fits the recorded
crop pixel dimensions may be mapped from crop-local pixels through the crop's
page-pixel bbox and rendered-page coordinate system. Guard evaluates only the
canonical mapped result and reports per-element localization errors rather than a
generic pass/fail sentence. `visible_text` and `label` are normalized to canonical
element `text`; no visual content is invented by this conversion.

## Native Text Comparison Contract

Native PDF text is a cross-check source, not an unconditional page transcript.
An embedded certificate or assurance-report thumbnail may retain a full
microtext layer even though the page only intends the thumbnail and its caption
to be read at normal scale. `NativeTextComparisonPreprocessor` detects a compact
unknown/illustration Figure containing a dense cluster of sub-3.5-point native
text. Those blocks are excluded only from the page-level native/OCR coverage
denominator.

The raw PDF, native blocks, Figure, crop and coordinates remain unchanged.
Auditable flags are attached to both Page and Figure. A Figure already typed as
`chart` is not filtered by this rule and remains eligible for visual structure
review. Parser Fusion and Quality Router consume the same prepared comparison
view so they cannot disagree about the coverage denominator.

## Horizontal Spread Contract

A spread is a logical reading context, not a new physical PDF page. Detection
runs after table/figure regions exist and before quality routing. A candidate
requires opposite binding-edge contact, vertical alignment, a geometrically matched
same-type entity pair, and structured RGB variation that continues across the seam.
Uniform or near-uniform color on both pages is background continuity, not content
continuity. Printed-page parity is supporting
evidence when available, not the only signal.

```text
physical page N + physical page N+1
  -> deterministic candidate
  -> artifacts/spreads/spread-pNNNN-pMMMM.png
  -> SpreadIR(status=candidate)
  -> local SpreadPreflightClassifier
     -> standalone_pages: terminal local rejection; no model task
     -> visual_continuity: terminal local relationship; no model task
     -> content_crossing: detailed review with composite + both source pages
     -> uncertain: detailed visual classification
  -> confirm_spread | reject_spread | abstain when review is required
  -> optional horizontal entity links for confirmed content continuity
```

The preflight decision is deliberately about information dependency, not visual
beauty. `SpreadPairAnalyzer` normalizes bboxes by physical page height and matches
only vertically compatible same-type entities. A matched table, informative block
or data figure may become `content_crossing`; a matched photo or illustration becomes
`visual_continuity`. Matching table segments with repeated headers and the same column
contract are treated as ordinary vertical continuation. Missing matched entities or
a flat/background-only seam becomes `standalone_pages`. The decision, confidence,
signals and resolution source are persisted on `SpreadIR`. A visual-only spread
remains queryable with its composite artifact but is terminal for review scheduling.

Local preflight runs inside quality routing, after all candidate scopes have been
collected. Only spreads with `requires_detailed_review=true` may absorb member-page
risks into a spread task. This prevents a decorative double-page image from hiding
an unrelated table or page-quality task merely because it touched the binding seam.

`SpreadPagePlacement` records each physical page's pixel offset in the composite.
All Block/Table/Figure bboxes remain page-local PDF-point coordinates. A
confirmed horizontal table relation links two page-local table segments and sets
`continuation_axis=horizontal`; it does not manufacture a cross-page cell bbox or
erase either segment. Downstream retrieval may use the composite as context but
must cite the original page/entity evidence.

`TableIR` remains the physical source-of-truth grid. `LogicalTableBuilder` derives a
separate `LogicalTableIR` with one of three explicit composition modes:

- `vertical_stack`: segments append logical rows;
- `horizontal_append_columns`: a right-page segment adds new logical columns;
- `horizontal_continue_last_column`: a one-column right-page segment continues the
  left segment's final logical column across the binding seam.

Every `LogicalCellIR` retains its ordered `source_cell_ids`. Logical text is derived
from those cells and never replaces their page-local coordinates or OCR traces.
For `horizontal_continue_last_column`, unequal source row counts are not guessed:
the logical table is marked `review_required`, Patch Guard rejects spread
confirmation with `horizontal_table_composition_resolved`, and the reviewer must
first repair the physical grid. Validator checks complete logical-grid coverage,
source mapping consistency and only the intentional final-column overlap.
Reviewer context includes the exact left/right physical dimensions, the likely
composition mode and an action recipe. A link emitted against either physical
table is deterministically retargeted to the formal `SpreadIR`; the tables remain
the link members. Guard feedback names the source/target table IDs and the required
page-local grid dimensions for the next bounded repair round.

Candidate member pages do not also receive independent half-page review tasks.
Their page/table/figure risks are folded into the spread review as formal
`ReviewScopeItem` values. Member entities are writable only through the task's
bounded target set. If the model
rejects the spread, that same typed task must still decide the underlying
standalone-page risks. Every spread proposal must contain one explicit
classification operation, so a child text patch cannot accidentally close the
relationship question.

A spread task is blocking only when one of its member pages or seam entities
already carries a blocking text, table or material-visual risk. A visual-only
relationship is resolved locally as `visual_continuity` and never enters the model
queue. A semantically uncertain but non-blocking relation remains an explicit
optional task, rather than a default task for every detected double-page design.

Up to three bounded reviewer rounds attempt the decision, with different-family
verification for every accepted correction. Guard prevents a physical page
from joining two confirmed spreads and validates that horizontal links join
geometrically matched same-type entities on opposite member pages. A later candidate
that overlaps a page already committed to a confirmed spread is rejected locally
before any model call. Human fallback shows the composite
and both source pages and offers only `confirm_spread` or `reject_spread`; an
unclear case remains open.

Deferred automation is resumable. A retry selects existing `review task id`
values, creates an immutable child revision, and appends new model calls, Guards,
verifier results and final decisions without rewriting the parent package.
By default only unresolved blocking tasks are resumed; optional tasks require an
explicit task selection or `include_optional=true`.

Resume is checkpoint-aware rather than task-replay-only. Each task persists its
lifetime execution count, logical Reviewer/Verifier call counts, last run/time and
one of `reviewer_pending`, `verifier_pending`, `repair_pending` or `complete`.
A Guard-passing transaction whose Verifier was unavailable remains `guard_passed`.
The next child revision verifies that exact candidate first, including compatible
older transactions that were marked `rejected` only because a service/rate-limit
failure interrupted verification. It does not spend another Reviewer call unless
the independent Verifier rejects the candidate or required scopes remain unresolved.

Provider limits are coordinated above individual model failover. `RPM` is a
model-scoped short-term condition: the runner records it separately from transport
health, honors `Retry-After` when supplied, applies bounded exponential backoff and
retries inside the same logical task. `TPD` is an account/API-key-scope daily limit:
the first response opens a persisted Provider circuit and stops all remaining task
HTTP calls in that run. A later retry request checks the circuit before reserving a
child package, so a known TPD block cannot create another empty revision. The state
stores only a credential fingerprint, never the API key.

Every retry revision writes `document-ir-review-retry-result-v1` into its quality
report and manifest. It records selected/resolved/remaining tasks, status
transitions, new model-call failure categories, checkpoint resumes and tasks skipped
by the Provider circuit. A job being `done` therefore means the retry process ended;
the result summary separately proves whether any review task actually converged.

Scheduler preflight groups are scheduling metadata, not a claim that unrelated
tasks share one model response. Verifier checkpoints run before new Reviewer work.
Homogeneous multi-target inference may be added only where one malformed response
cannot couple unrelated transactions; Guard and Verifier decisions remain per
transaction regardless of transport batching.

An optional enhancement has two honest resolution paths. It may run through the
same Reviewer, Guard and Verifier chain, or an operator may create an immutable
child revision with `accept_current_nonmaterial`. An operator note is optional and,
when supplied, remains in the audit record. The latter
sets the related conflict disposition to `accepted_nonmaterial_difference`; it
does not promote a candidate spread, chart structure or visual relation to a
verified canonical fact. Blocking tasks can never use this action.

Human patch decisions preserve the same invariant: only a Guard-passing patch may
be accepted. Rejecting a patch rejects that proposal but does not prove that the
current IR is correct, so it does not close the task. Closure without an accepted
patch requires an explicit `keep_current` click; the evidence note is optional.
Otherwise the operator starts a targeted repair revision.

Table correction has deterministic compiler operations. For example,
`insert_table_row` accepts only an insertion index and one complete non-overlapping
row; local code shifts every existing cell and rebuilds the grid. Models do not
rewrite the preserved table merely to add a missing visual header. Full-grid
replacement additionally checks rectangular coverage, retained text volume, and
numeric tokens before independent visual verification.

`set_table_grid` is also a local geometry-aware compiler operation. It preserves
existing cell boxes by position or unique text, can inherit compatible observation
boxes, and can union covered source-cell boxes for spans. Patch Guard blocks a
replacement that would erase all existing cell geometry. Repeated duplicated-digit
OCR noise may be ignored only by an explicit noise classifier; real codes and
other numeric tokens still must be preserved.

Native/OCR coverage routing is intentionally one-sided. It routes when OCR is
materially shorter than reliable native text and has low source-character recall.
An OCR result that is longer because it includes chart labels, bilingual text, or
image content is not a divergence by itself.

Figure classification uses an explicit Document IR taxonomy: `unknown`, `chart`,
`diagram`, `illustration`, `photo`, `icon`, `decoration`, and `composite`. A model
cannot invent a type outside that contract, and absence of a visible caption is
represented by no patch rather than an empty caption value.

Visual transport is also adapted. Qiniu calls prefer cloud-addressable
`https://` or `kodo://` references; local image data URIs are development fallback
only. The model router intersects a local approved profile list with Qiniu
`GET /v1/models`, then interleaves model families in the failover order. The
2026-07-23 approved multimodal profiles are `qwen/qwen3.5-plus`,
`doubao-seed-2.0-pro`, `qwen3.5-397b-a17b`,
`stepfun/step-3.7-flash`, `moonshotai/kimi-k2.6`,
`moonshotai/kimi-k2.5`, `doubao-seed-2.0-mini`,
`minimax/minimax-m3`, and `moonshotai/kimi-k3`; only IDs returned by the live
catalog are eligible.

Local NuExtract3 receives local page, crop and spread paths directly. A provider
factory supplies the adapter, registry, visual resolver, health policy and verifier
policy to the same `ModelRunner`; orchestration above that boundary is unchanged.
The MLX adapter uses a backend-owned persistent subprocess so model weights are loaded
once per backend lifetime rather than once per review task. The worker translates
NuExtract's template-constrained response into the same OpenAI-compatible envelope
consumed by `ReviewResponseAdapter`. Both providers therefore produce the same
`ReviewerPayload`, `AtomicPatch`, `GuardResult`, `VerifierResult`, transaction and
immutable package locations.

Qiniu uses `different_model_family` verification. A single local NuExtract3 instance
cannot honestly provide model-family independence, so it uses
`same_model_secondary_verification`: a fresh verifier prompt and isolated context run
through the same Guard and transaction checks. `VerifierResult`, `AgentModelCall` and
`quality_report.review_provider` persist this distinction. A second local model can be
added later at the provider registry boundary without changing the review kernel.

Health state is split into transport, protocol and quota categories. Two
consecutive transport failures open a 15-minute transport circuit; three
consecutive malformed/invalid responses open a 5-minute protocol circuit.
Account-wide quota errors stop failover immediately for the current request.
A successful response resets both transient circuits. Every failed attempt and
its category remains auditable. Runtime health is atomically persisted at
`document_ir_output/.state/model-health.json`, so backend restarts do not erase
active circuit windows or failure counters. This mutable runtime file contains
only model-health metadata; no token, document text or raw model response is
stored there. Full calls remain in the immutable revision review ledger.

## Readiness Gate

Every snapshot has one status:

- `ready`: structural checks pass and no unresolved review remains;
- `ready_with_warnings`: usable by downstream consumers with explicit non-blocking warnings;
- `auto_review_pending`: one or more retryable required tasks await automation;
- `repair_required`: a structural error, non-retryable system failure, or
  system-blocked conflict requires chain repair rather than semantic judgment;
- `review_required`: a bounded evidence-backed semantic disagreement needs a person;
- `failed`: page coverage or another blocking invariant failed.

`validation_report.json` exposes `can_build_evidence` as a stable Document IR quality
gate. A consumer must not accept a snapshot when this value is false.

The gate validates more than page counts and review status. It deterministically
checks global and internal ID uniqueness, reciprocal entity references, contiguous
page indices, coordinate-system and bbox validity, complete non-overlapping table
grids, section hierarchy/range containment, local artifact existence and SHA-256,
source-trace artifact coverage, and absence of credential-bearing remote URLs.
It also reports table-cell coordinate coverage, tables with full/partial/no cell
geometry, section density, single-page ratio, parentless ratio and suspicious title
count. Logical tables add complete-grid, source-cell mapping, composition-mode and
row-alignment checks. Punctuation-only, sentence-like and repeated short card labels
are suppressed before section creation; remaining semantic anomalies are exposed by
the readiness report rather than hidden by a syntactically valid tree.

## Adapter Rule

The parser remains behind a replaceable adapter boundary:

```text
DocumentParserAdapter
  -> OcrProviderRouter
       -> LocalPaddleOCRVLAdapter
       -> PaddleOCRVLApiAdapter
```

Both adapters emit the same canonical `layoutParsingResults` envelope and use the
same `OcrOutputWriter`. Provider selection is recorded in `ocr_provider` and
`provider_route`; it never changes `Document IR` or downstream consumer contracts.
`local_first` attempts the local adapter first. API fallback requires both an explicit
task allowance and a configured request/environment token; `local_paddleocr` never
calls the cloud.

The isolated local worker emits structured page progress plus a liveness heartbeat
while Paddle is blocked inside a slow page. A provider-side watchdog measures completed
page progress rather than stdout activity: the default ten-minute no-progress threshold
terminates a stalled local attempt and lets the existing `local_first` router decide
whether API fallback is permitted. This watchdog is separate from the two-hour whole-job
deadline. Both local and API providers publish the same progress contract to native job
state and the unified task dock.

## Backend Independence

The backend remains usable without the frontend:

- CLI: `esg-v2 ocr --file ... --provider local_first|local_paddleocr|paddle_api`
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
- Every initial build, targeted repair and review-retry job uses the same mutable
  `document-ir-runtime-telemetry-v1` state. It records stage timing and model-call
  counters for operator observability; the immutable `AgentModelCall` collection
  remains the final audit source after a revision is written.
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
  `candidate-000001`, `transaction-000001`, `logical-table-000001`,
  `decision-000001`, and `conflict-000001`.
- JSON object collections that are read independently use one object per `.json`
  file. Append-only event/ledger collections use `.jsonl`.
- `integrity/files.json` records every package file except the integrity index
  itself: relative path, byte size, media type, and SHA-256. The writer then
  executes package validation against the manifest, entrypoints, file set,
  sizes, and hashes before declaring the write successful. Semantic artifact IDs
  remain separate from physical package-file integrity records.
- A completed package is immutable while it exists. Corrections create a new Document
  IR revision with `parent_ir_run_id`; packages are never edited in place. After a
  self-contained successor is validated and promoted, the best-only retention policy
  may transactionally remove superseded package roots and their local job state.
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

Run identity is deliberately separate from document identity and revision lineage:

```text
document_id = doc-sha256-<full 64-character PDF SHA-256>
lineage_id  = irl-<24 lowercase hex derived from the root IR run>
```

- `document_id` is content-addressed. Renaming, moving, or OCR-processing the exact
  same PDF again does not change it. Two files with the same human filename but
  different bytes cannot collide.
- `ocr_run_id` identifies one OCR provider attempt, not a report.
- A parentless IR build starts a new `lineage_id` at `ir_revision=1`, even when the
  same OCR run or same PDF already has another IR build. This prevents independent
  rebuilds from appearing as false descendants.
- Targeted repair, review retry, and human decisions preserve the parent's
  `lineage_id` and allocate the next revision within that lineage. The explicit
  `parent_ir_run_id` remains the authoritative ancestry edge.
- `document_label` is a non-authoritative display label. `external_document_id` is
  an optional pass-through reference to an upstream report registry; this subsystem
  does not infer issuer, reporting year, jurisdiction, or report type from filenames.

Before page rendering, the workflow compares the local PDF SHA-256 with the immutable
OCR source hash when that hash is available and rejects mismatches. The derived catalog
API groups OCR attempts and retained IR revisions by `document_id` without creating a
mutable second source of truth. Existing packages remain immutable; missing v0.11
identity fields are derived at read time from source hashes and parent chains.

New writes reject non-conforming run IDs and unsafe package directory names.
Historical pre-v1 names remain readable through compatibility readers, but they
cannot be used to create a new Package v1 run.

### OCR Package v1

```text
ocr_output/<ocr_run_id>/
  manifest.json
  source/
    request.json
    preflight.json
    provider-input.pdf              # present only after normalization
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

For API runs, `provider/result.jsonl` remains the exact PaddleOCR-VL response. For
local runs it is a lossless adapter envelope with the same `prunedResult`, `markdown`
and `outputImages` fields, while provider-owned source images remain under
`provider/local-resources/`. Per-page
observations and portable Markdown are derived views. `artifacts/index.json`
maps provider image references to stable local artifact IDs and relative paths.
`source/request.json` replaces a local upload path with `runtime-upload://...`,
redacts tokens, and sanitizes credential-bearing URL queries. Derived page
observations are sanitized; exact provider files stay unchanged for audit.
The Paddle client uses a dedicated HTTP session and ignores environment/system
proxy discovery by default. Deployments that require an explicit proxy may opt
in with `PADDLEOCR_VL_TRUST_ENV_PROXY=true`.

### PDF canvas preflight and provider input

The original PDF is the immutable identity and provenance artifact. Before a
local file is submitted, `PdfCanvasPreflight` inspects every page's MediaBox,
CropBox, rotation, box origin and displayed dimensions, checks document-level
incremental updates and AcroForm state, and renders every page through
`pypdfium2`. This is an input adapter, not a new parser.

When provider-risk features are present, the adapter writes a provider-only PDF.
Each page is independently normalized: rotation is transferred to page content,
the displayed canvas is uniformly scaled to the configured maximum edge, and a
fresh PDF structure is written. The operation never crops, stretches, merges or
reorders pages. Mixed page sizes remain mixed in the same order; only the scale
needed by each page changes.

`source/preflight.json` records the complete page transform and its inverse.
The OCR manifest continues to use the original PDF hash, so `document_id` is
stable across passthrough and normalized OCR attempts. Downstream Document IR
renders the original PDF. Existing coordinate conversion maps provider page
coordinates by the provider/source page-size ratio into canonical original-PDF
points, preserving evidence replay. A failed provider job still exposes the
preflight, submission payload response, poll history and provider job ID.

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
    logical-tables/index.json
    logical-tables/logical-table-000001.json
    figures/index.json
    figures/figure-p0001-0001.json
    spreads/index.json
    spreads/spread-p0010-p0011.json
    relations/structure-edges.jsonl
    coordinates/systems.json
  observations/
    paddle-layout/page-0001.json
    local-pdf/forensics.json
    retired-entities.jsonl
  artifacts/
    index.json
    page-images/page-0001.png
    crops/tables/table-p0001-0001.png
    crops/figures/figure-p0001-0001.png
    spreads/spread-p0010-p0011.png
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
    patches/patch-transactions.jsonl
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

Canonical page/table/logical-table/figure shards are the authoritative semantic data for
Document IR consumers. `exports/document-ir.snapshot.json` is a compatibility and
debug export that can be regenerated and must not become a second source of truth.
Parser observations, retired candidate snapshots, review ledgers, and quality
reports remain in the same
revision package for auditability, but they are not canonical document content.

`document-ir-identifiers-v1` is a validation contract, not a naming convention
left to developer discipline. New workflow output is rejected when canonical
page/object/cell/section IDs, review ledger IDs, coordinate-system IDs, artifact
IDs, structure-edge IDs, or table-edge IDs do not match their declared grammar.
The package reader can still open historical flat runs and relocates old page
images when a child revision is created.

No API token is written to any package artifact.

### Local artifact inventory and dependency-aware cleanup

OCR Package v1 and Document IR Package v1 remain immutable while they exist. The
storage management layer is separate from `OcrWorkflow`, `DocumentIrWorkflow`, and
the read-only `DocumentIrCatalog`. It derives an inventory from package manifests,
local job state, and upload copies; it is not a mutable report registry and never
becomes a second source of document truth.

The ownership tree is:

```text
document_id
  ocr_run_id
    ocr_output/<ocr_run_id>/
    .local/jobs/ocr/<ocr_run_id>/
    .local/uploads/<ocr_run_id>/
    ir_run_id
      document_ir_output/<ir_run_id>/
      .local/jobs/document-ir/<ir_run_id>/
      child ir_run_id ...
```

`GET /api/storage/inventory` exposes this tree with package/state health,
readiness, file counts, byte size, ancestry, and cleanup eligibility. Deletion is a
two-phase operation: `POST /api/storage/deletion-plans` compiles the dependency
closure and impact preview; `POST /api/storage/deletion-plans/{plan_id}/execute`
rechecks the same fingerprint before moving owned directories into a cleanup-local
trash transaction. The UI requires one explicit confirmation click but no typed
phrase. Failed moves are rolled back; successful transactions write an
audit event outside the deleted package and purge temporary trash.

Running jobs are protected. An OCR run with surviving IR consumers and an IR revision
with surviving children are blocked unless cascade is explicitly enabled. Independent
downstream extraction services are not silently inspected or mutated, so plans that
delete IR carry `downstream_dependencies_unverified` until a dependency provider is
connected.

Best-only IR retention is a separate automatic path from operator cleanup. It runs only
after the new package writer and integrity validation complete. Candidate quality is
ordered by Evidence readiness, blocking/error issues, unresolved blocking and optional
reviews, readiness state, human closure and revision recency. A winning package is
self-contained, so its superseded parent package and job state can be moved into a
transaction-local trash root and purged without rewriting the winner. Queued repair,
review-retry and targeted-extraction payloads are rebased first. A revision referenced
by a running task is protected and recorded for deferred cleanup. The current pointer
and append-only cleanup audit live outside immutable packages under
`.local/storage-cleanup/`.

The frontend mirrors this responsibility split through six business views: report
assets, OCR task creation, Document IR task creation, IR review/inspection, targeted
extraction, and extraction results. The report asset view is the primary cleanup
surface; raw package viewers remain read-only.

## Unified local pipeline control plane

The local product shell has one frontend but does not merge workflow ownership. The
Document backend owns a small control plane composed of `PipelineQueueStore`,
`PipelineScheduler`, `RuntimeSecretVault`, and provider-neutral task hooks. OCR,
Document IR build/repair/review-retry, and targeted extraction run as isolated child
processes under one persistent FIFO queue. Only queued tasks are reorderable. Cancelling
a running task terminates its process group before the next task can start.
The single-worker invariant limits execution concurrency only. Producers can enqueue
additional OCR, IR, review/repair, and targeted tasks while another task is running;
form submission locks are request-scoped and never mirror task lifetime.

SQLite stores task order, lifecycle, progress, worker PID and events. Runtime API keys
remain in memory or deployment environment variables and are never written into the
queue database. Backend restart marks an active task `interrupted`; native OCR/IR/targeted
job state receives the same terminal meaning. Business artifacts remain in their
existing immutable package roots, so the scheduler is replaceable without changing any
OCR, Document IR, Evidence Inventory or Result Bundle contract.

`ReportAssetCatalog` is similarly separate from document semantics. It indexes PDFs in
the dedicated workspace `/pdf` directory by SHA-256 and derives successful OCR history
from immutable OCR manifests. The catalog never contains extracted text or facts.

The unified frontend is a presentation composition of six views: report assets, OCR,
Document IR, IR review/inspection, targeted extraction, and extraction results. The
bottom task dock is the only global runtime surface. The targeted backend remains an
independent service and can still be used through its own API/CLI.

Workflow input selection is version-aware and view-scoped. The Document IR view reads
the complete OCR package catalog and explicitly selects one immutable OCR run; browsing
an OCR result elsewhere never mutates that selection. The targeted extraction view
independently reads the complete Document IR revision catalog and accepts only an
explicitly selected Evidence-ready revision. Both selectors expose the report label,
year, page count, provider or schema, revision/readiness, timestamp and full run ID.
The standard-package and metric selectors are separate inputs. Metric selections are
keyed by `package_id@package_version` and survive switching among package cards. A
cross-standard submission is compiled into one queue task per selected package through
the atomic batch enqueue API. Those tasks share the selected IR and execution profile,
but each native targeted job still owns exactly one immutable standard-package snapshot
and one Result Bundle. The frontend never merges elements or digests from different
standard packages into one extraction contract. These selectors are presentation
adapters over catalog APIs: they do not rewrite OCR/IR artifacts or create hidden state
in another business view.

The IR review view begins with a read-only unresolved-task worklist. It selects the
retained latest revision independently for each available lineage, then lists only unresolved
`human_required`, non-retryable system-blocked, retryable blocking, and optional tasks.
Superseded parent packages are removed after promotion and therefore cannot duplicate
the current operator worklist. Selecting a row pins its IR
run and Review Task, then opens the existing evidence, Guard, Verifier, patch and human
decision cockpit; the worklist never creates a second review state or mutates a package.
`GET /api/document-ir/review-worklist` performs this latest-lineage projection in the
Document backend and returns compact task, target, status and progress metadata only.
Full IR objects, evidence crops and audit trails remain lazily loaded after the operator
opens one row, avoiding one full-package request per report during catalog refresh.

The normal Document backend launcher does not enable Uvicorn source reload. A source
reload replaces the process that owns the persistent scheduler and therefore converts
its active task to `interrupted`; it can also invalidate queued work during development
startup recovery. `ESG_V2_DEV_RELOAD=1` is an explicit development-only opt-in and must
not be used while real OCR, IR review or extraction tasks are in flight.
