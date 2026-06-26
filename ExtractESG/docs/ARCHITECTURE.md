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

