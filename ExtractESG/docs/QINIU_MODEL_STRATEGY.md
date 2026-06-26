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

## Required Audit Metadata

Every cloud task stores:

- model ID and registry snapshot
- prompt version and schema version
- input evidence IDs
- raw and parsed response
- latency, usage, estimated cost
- quality flags and schema repair attempts

