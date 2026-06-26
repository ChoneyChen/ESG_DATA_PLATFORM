# Qiniu Model Strategy

The model list is dynamic. ExtractESG must sync capabilities from Qiniu rather than hard-code model IDs.

## Capability Buckets

| Bucket | Required Fields |
|---|---|
| Vision/File Recognition | `input_modalities` contains `image` or model supports file/PDF recognition |
| Long Context | `context_length >= 128000` |
| Structured Output | `schema_output.supported = true` |
| Tool/Agent Use | `function_calling.supported = true` |
| Reasoning Review | `reasoning.supported = true` |

## Routing Principles

- Use low-cost/fast models for page classification and ESG relevance filtering.
- Use vision-capable models for scanned pages and image-only tables.
- Use structured-output models for disclosure candidates and claim extraction.
- Use reasoning-capable models for standard mapping and conflict review.
- Use a different model or prompt version for independent verification when cost allows.

## Current Default Model Matrix

The names below are planning defaults, not hard-coded permanent IDs. The live
runtime should still call `/v1/models`, cache the account-visible registry, and
match the selected model by capability and registry hint before a paid request.

| API task | When it is called | Capability requirement | Default model | Backups |
|---|---|---|---|---|
| Page / section classification | After local page packets are built | Fast text understanding, low cost, stable JSON labels | DeepSeek-V4-Flash | Doubao Seed 2.0 Mini; Qwen3 Next 80B A3B Instruct; Qwen-Turbo |
| Scanned-page OCR and visual reading | Native PDF text is empty, garbled, or visually incomplete | Image input, OCR, layout/reading order, table/footnote awareness | Qwen/Qwen3.5-Plus | Qwen VL-MAX-2025-01-25; Moonshotai/Kimi-K2.5; Minimax/Minimax-M3 |
| Image/table structure extraction | Table is an image, spans pages, or local cell detection is weak | Image input, table reasoning, strict JSON, unit/header preservation | Qwen/Qwen3.5-Plus | Qwen VL-MAX-2025-01-25; Moonshotai/Kimi-K2.5; Minimax/Minimax-M3 |
| Full-harvest structured ESG extraction | Evidence packets need candidate disclosure objects | Structured output, long context, low hallucination, bilingual ESG language | Doubao Seed 2.0 Lite | DeepSeek-V4-Flash; Qwen3 Next 80B A3B Instruct; Qwen/Qwen3.5-Plus |
| Qualitative claim compression | Policies, targets, risk controls, and governance narratives need canonical summaries | Faithful summarization, long context, source-claim preservation | DeepSeek-V4-Flash | Doubao Seed 2.0 Lite; Qwen3 Next 80B A3B Instruct; Moonshotai/Kimi-K2.5 |
| Whole-report long-context synthesis | Report-level inventory or cross-page consistency checks | 1M-class context preferred, cost control, evidence referencing | DeepSeek-V4-Flash | DeepSeek-V4-Pro; Z-AI/GLM-5.2; Minimax/Minimax-M3 |
| ESG standard mapping review | Candidate disclosure must map to HKEX, ESRS, GRI, ISSB, or internal concepts | Deep reasoning, long standard context, multi-label judgement | DeepSeek-V4-Pro | Z-AI/GLM 5; Minimax/Minimax-M2.7; Qwen3 Max |
| Independent text/evidence verification | Candidate facts, units, summaries, or mappings need audit | Different model family, reasoning, schema checking, contradiction detection | Z-AI/GLM 5 | DeepSeek-V4-Pro; Minimax/Minimax-M2.7; Qwen3 Next 80B A3B Thinking |
| Independent visual verification | OCR/table/chart-derived fact affects the database | Image input, visual reasoning, different model family | Moonshotai/Kimi-K2.5 | Qwen/Qwen3.5-Plus; Minimax/Minimax-M3; Qwen VL-MAX-2025-01-25 |

## Agent Shape

ExtractESG's "agents" are controlled workflow workers, not free-form desktop
agents like Codex. Each agent has a narrow role, typed input contracts, typed
output contracts, model routing rules, and persistence/audit obligations.

- `Document Agent`: local PDF inspection, page artifacts, quality flags.
- `Evidence Agent`: page/table/section evidence packet construction.
- `Vision Agent`: cloud OCR and image-table extraction only when local evidence
  flags require it.
- `Harvest Agent`: full-harvest candidate disclosure extraction from evidence
  packets.
- `Normalization Agent`: units, dimensions, reporting scope, period, and entity
  normalization.
- `Mapping Agent`: maps disclosure candidates to canonical concepts and external
  standard clauses.
- `Verification Agent`: independent evidence-only audit; it can approve, reject,
  or require human review.
- `Publication Agent`: writes only approved facts to the publication-facing
  database layer.

The orchestrator is a durable state machine. It decides which agent runs next,
which model route is allowed, and whether a result is publishable. Individual
agents do not browse files, mutate schema, or invent workflow steps by
themselves.

## Required Audit Metadata

Every cloud task stores:

- model ID and registry snapshot
- prompt version and schema version
- input evidence IDs
- raw and parsed response
- latency, usage, estimated cost
- quality flags and schema repair attempts
