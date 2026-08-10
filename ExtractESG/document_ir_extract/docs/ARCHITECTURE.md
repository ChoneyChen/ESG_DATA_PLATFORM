# ESG v2 Architecture

## Boundary

v2 is a greenfield extraction system. It starts from a PDF artifact supplied by
an upstream system and does not own crawling, Kodo indexing, company/year/industry
master data, or the global report registry. Local uploads, paths, and URLs are
development input conveniences only.

The current implementation includes a hardened downstream Targeted Recall path: an
admitted, versioned `Document IR` produces an immutable deterministic `Evidence Inventory`,
then a standard task workbook is compiled into executable disclosure contracts and assessed
with local grouped retrieval and an independent deterministic verifier. It does not yet
implement PDF-first Full Harvest, publication-grade normalization, model-assisted fact
verification, human fact review, or publication.

## Active Pipeline

```text
PDF artifact
  -> PaddleOCR-VL API adapter
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
  -> Evidence admission gate (`can_build_evidence=true`)
  -> EvidenceInventoryBuilder
       -> block/list/footnote/figure atoms
       -> physical and logical table cell/row/region atoms
       -> source node, page, section, bbox and content hash
  -> TemplateAdapterRegistry
       -> workbook-specific parser/export adapter
       -> generic versioned RequirementExecutionSpec v2
       -> conditional, specialized or explicit generic-local compilation strategy
       -> required semantic slots and multiplicity policy
  -> DisclosureCatalog
       -> physical/logical table, paragraph and figure groups
       -> deterministic number, period, unit, statement and index features
  -> LocalEvidenceIndex
       -> exact substring lane
       -> SQLite FTS5/BM25 lane
       -> SQLite trigram lane
       -> optional local embedding lane
  -> HybridLocalRetriever
       -> concept, dimension, topic and combined lanes
       -> RRF + disclosure-group structural reranking
       -> explicit candidate-limit trace
  -> RequirementVerifier
       -> per-candidate slot coverage and fact instances
       -> cross-tab intersection and index-evidence guards
  -> TargetedAssessor
       -> compare all returned groups
       -> apply multiplicity and conditional applicability policies
       -> abstain when required slots or search coverage are incomplete
  -> TargetedGuard (grounding, exact quote, slot completeness, facts, zero cloud)
  -> immutable targeted_fill_output package
```

This is not a strictly linear system. The main dependency direction is forward,
but review creates correction proposals and later Evidence/extraction/verification
may create a `DocumentIrRepairRequest`. Repair never mutates an existing snapshot;
it produces a new revision and downstream consumers must pin the revision they read.

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
| `parser_fusion.py` | OCR/local observations and accepted-decision reconciliation | Evidence or facts |
| `validator.py` | readiness and Evidence-entry gate | human judgment |
| `versioning.py` | immutable revision lineage | report registry |
| `writer.py` / `reader.py` | Package v1 materialization, sharding, portable hydration, legacy reads | business database writes |
| `storage/package_layout.py` | safe paths, run IDs, package layout, atomic root reservation | document semantics |
| `storage/package_validator.py` | entrypoint, file-set, size and SHA-256 verification | semantic readiness judgment |
| `evidence/` | deterministic Evidence Atom construction, package IO and local FTS index | standard interpretation or publication |
| `standards/` | RequirementExecutionSpec v2, versioned rule packs, generic fallback and task compilation | report retrieval |
| `templates/` | workbook detection, blue-column ingestion and allowed-cell export | extraction decisions |
| `targeted/catalog.py` | group Evidence Atoms into report disclosure units | requirement interpretation |
| `targeted/features.py` | deterministic number, period, unit-family, statement and index parsing | retrieval or final decisions |
| `targeted/retrieval.py` | role-separated local recall and disclosure-group reranking | final disclosure judgment |
| `targeted/verification.py` | per-group slot coverage, cross-dimension checks and FactInstance proposals | workbook IO |
| `targeted/assessment.py` | applicability, multiplicity, convergence and bounded deterministic answers | cloud prompting or workbook IO |
| `targeted/package.py` | immutable result/audit package and canonical JSONL | report registry |
| `targeted/workflow.py` | stage orchestration and explicit local-semantic fallback | OCR/IR mutation |

These are Python module boundaries in a modular monolith. They are not separate
microservices. Worker deployment can be split later without changing contracts.

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

Qiniu remains the VLM/LLM provider for routed model tasks. It is not the primary
OCR parser. Deterministic preflight first closes non-material visual-only Spread
relationships; the default completeness scheduler then admits all remaining
blocking work and unresolved optional review groups in the same run. Queue caps
remain available only for explicitly bounded operating modes.

Review output cannot overwrite parser output. The active `document-ir-v0.11`
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
- `VerifierResult`: independent judgment from a different model family;
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
  -> independent different-family verifier
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

The model may never perform an in-place update. A correction is applied only when
the Guard passes and the independent verifier accepts it. A rejected/abstained or
Guard-failed candidate gets at most two feedback-guided reviewer repairs. Cloud,
model-format, task-budget, or repeatedly Guard-invalid output becomes `deferred`,
not `human_required`. Optional unresolved visual enrichment does not block
Evidence admission. A blocking semantic disagreement after a Guard-passing
proposal reaches an independent verifier is the primary automatic human boundary.
Reviewer abstention may also reach a person only after the bounded evidence-guided
attempts are exhausted. Repair rounds exclude the preceding reviewer's model
family when another suitable family is available; repeating the same model-family
non-answer is not treated as independent evidence. Repeated Guard fingerprints are
recorded during the bounded run, but become non-retryable only after all three
rounds are exhausted.

One reviewer response may contain several unrelated repairs. They are partitioned
into `PatchTransactionIR` groups by local commit scope. Cells remain with their
physical table. A horizontal spread's classification, cross-seam link and repairs
to the participating physical table segments form one atomic composition
transaction, because the link Guard must evaluate the repaired grids rather than
the unmodified parent snapshot. Unrelated figure or block repairs remain isolated.
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
requires opposite binding-edge contact, vertical alignment, a structural signal,
and non-empty pixel continuity at the seam. Printed-page parity is supporting
evidence when available, not the only signal.

```text
physical page N + physical page N+1
  -> deterministic candidate
  -> artifacts/spreads/spread-pNNNN-pMMMM.png
  -> SpreadIR(status=candidate)
  -> local SpreadPreflightClassifier
     -> visual_continuity: terminal local relationship; no model task
     -> content_crossing: detailed review with composite + both source pages
     -> uncertain: detailed visual classification
  -> confirm_spread | reject_spread | abstain when review is required
  -> optional horizontal entity links for confirmed content continuity
```

The preflight decision is deliberately about information dependency, not visual
beauty. It inspects the canonical seam members already produced by layout parsing:
tables or informative objects on both sides mean `content_crossing`; figures on
both sides without cross-seam text/table semantics mean `visual_continuity`; mixed
or insufficient evidence stays `uncertain`. The decision, confidence, signals and
resolution source are persisted on `SpreadIR`. A visual-only spread remains
queryable with its composite artifact but is terminal for review scheduling.

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
same-type entities on opposite member pages. Human fallback shows the composite
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
child revision with `accept_current_nonmaterial` plus a written reason. The latter
sets the related conflict disposition to `accepted_nonmaterial_difference`; it
does not promote a candidate spread, chart structure or visual relation to a
verified canonical fact. Blocking tasks can never use this action.

Human patch decisions preserve the same invariant: only a Guard-passing patch may
be accepted. Rejecting a patch rejects that proposal but does not prove that the
current IR is correct, so it does not close the task. Closure without an accepted
patch requires an explicit `keep_current` decision with a written evidence note;
otherwise the operator starts a targeted repair revision.

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
- `ready_with_warnings`: usable by Evidence with explicit non-blocking warnings;
- `auto_review_pending`: one or more retryable required tasks await automation;
- `repair_required`: a structural error, non-retryable system failure, or
  system-blocked conflict requires chain repair rather than semantic judgment;
- `review_required`: a bounded evidence-backed semantic disagreement needs a person;
- `failed`: page coverage or another blocking invariant failed.

`validation_report.json` exposes `can_build_evidence`. Evidence Inventory must not
consume a snapshot when this value is false.

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
evd-YYYYMMDDTHHMMSSZ-<12 lowercase hex>
trg-YYYYMMDDTHHMMSSZ-<12 lowercase hex>
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
The Paddle client uses a dedicated HTTP session and ignores environment/system
proxy discovery by default. Deployments that require an explicit proxy may opt
in with `PADDLEOCR_VL_TRUST_ENV_PROXY=true`.

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

Canonical page/table/logical-table/figure shards are the authoritative semantic data used by
Evidence Inventory. `exports/document-ir.snapshot.json` is a compatibility and
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

Evidence Inventory may start only when the manifest resolves a successful
`quality/validation-report.json` with `can_build_evidence=true`. It reads canonical
collections through the manifest and stores `ir_run_id`, `ir_revision`, and
`source_node_ids`; it must not scan package directories or depend on local paths.

### Evidence Inventory Package v1

`EvidenceAtom` is retrieval-oriented but not ESG-classified. Each atom stores exact
source text, normalized search text, page/section location, optional bbox, physical
and logical table coordinates, source node IDs, quality flags, source trace and a
content hash. The builder never calls a model and never mutates Document IR.

### Targeted Recall Package v2

The task workbook is compiled into generic `RequirementExecutionSpec v2` records before
retrieval. Every contract declares its disclosure type, concept, required slots, aliases,
unit families, period/scope policy and multiplicity. A matching specialized rule is used
when available. Unknown same-format requirements receive an explicit `generic_local_rule`
and a preflight warning instead of silent hard-coded failure. The extraction core therefore
contains no ESRS row IDs; another workbook shape adds an adapter and another standard adds
a versioned rule pack.

Evidence Atoms are first grouped into `DisclosureGroup` objects. A table and all of its
cell/row atoms are assessed as one unit, preventing isolated cells from being mistaken for
complete disclosures. Local feature tools identify numeric tokens, years, unit families,
statement types, explicit-zero claims and index-like sections. Retrieval is high recall;
`RequirementVerifier` is the separate precision boundary. It records every candidate's
slot coverage, reasons and proposed `FactInstance` values. Cross-tab requirements must show
their dimensions in a real intersection, not merely in unrelated rows.

`local_strict` is always available and uses exact, BM25 and trigram lanes.
`local_semantic` may add a local embedding backend; failure to load it is a recorded
fallback to `local_strict`, never a cloud escalation. Embeddings only increase recall.
The final answer still requires deterministic applicability/table checks and exact Evidence
quotes. `single_best`, `all_instances`, `group_by_dimension`, and `table_bundle` policies
control whether one or several complete groups are preserved. If the candidate limit is
reached, the system may abstain but cannot claim `not_found`. The five terminal states are
`found`, `not_found`, `not_applicable`, `uncertain`, and `system_failed`; silence never proves
a conditional absence.

The package stores `catalog/disclosures.jsonl`, `assessment/verifications.jsonl`, and
`quality/search-coverage.jsonl` in addition to canonical answers and selected packets.
XLSX export preserves the template and updates only adapter-authorized cells.
`audit/cloud-calls.jsonl` must remain empty under the current zero-cloud policy, and
Targeted Guard rejects export otherwise.

No API token is written to any package artifact.
