from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    registry_hints: tuple[str, ...]
    why: str


@dataclass(frozen=True)
class ApiModelAssessment:
    key: str
    task_kind: str
    label: str
    pipeline_stage: str
    api_required_when: str
    capability_needs: tuple[str, ...]
    default_model: ModelCandidate
    alternatives: tuple[ModelCandidate, ...]
    invocation_policy: str
    review_policy: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["options"] = [asdict(self.default_model), *[asdict(item) for item in self.alternatives]]
        return payload


API_MODEL_ASSESSMENTS: tuple[ApiModelAssessment, ...] = (
    ApiModelAssessment(
        key="page_classification",
        task_kind="page_classification",
        label="Page / section classification",
        pipeline_stage="Document understanding",
        api_required_when="The local parser has prepared page evidence and the workflow needs ESG topic, table, appendix, and standard-reference labels.",
        capability_needs=(
            "fast text understanding",
            "low cost",
            "stable JSON labels",
            "at least page-packet context",
        ),
        default_model=ModelCandidate(
            name="DeepSeek-V4-Flash",
            registry_hints=("DeepSeek-V4-Flash", "deepseek/deepseek-v4-flash"),
            why="Best default for cheap high-throughput classification; it is listed with high TPS and 1M-context positioning.",
        ),
        alternatives=(
            ModelCandidate(
                name="Doubao Seed 2.0 Mini",
                registry_hints=("Doubao Seed 2.0 Mini", "doubao-seed-2-0-mini"),
                why="Good when latency and cost dominate; supports 256K context and multimodal understanding.",
            ),
            ModelCandidate(
                name="Qwen3 Next 80B A3B Instruct",
                registry_hints=("Qwen3 Next 80B A3B Instruct", "qwen3-next-80b-a3b-instruct"),
                why="Good fallback when strict instruction following and final-answer stability matter more than raw speed.",
            ),
            ModelCandidate(
                name="Qwen-Turbo",
                registry_hints=("Qwen-Turbo", "qwen-turbo"),
                why="Reasonable light classifier if available in the account's model scope.",
            ),
        ),
        invocation_policy="Batch page packets; temperature <= 0.2; output only controlled labels plus evidence packet ids.",
        review_policy="No human review required unless classification controls later expensive vision calls or standard mapping.",
    ),
    ApiModelAssessment(
        key="vision_ocr",
        task_kind="vision_ocr",
        label="Scanned-page OCR and visual reading",
        pipeline_stage="Document parsing",
        api_required_when="Native PDF text is empty, garbled, visually inconsistent, or a page is image-only.",
        capability_needs=(
            "image input",
            "fine-grained OCR",
            "layout and reading-order recovery",
            "table and footnote awareness",
        ),
        default_model=ModelCandidate(
            name="Qwen/Qwen3.5-Plus",
            registry_hints=("Qwen/Qwen3.5-Plus", "qwen3.5-plus", "qwen/qwen3.5-plus"),
            why="Best current default for general visual-language extraction because it is listed as a native VLM series with strong text and multimodal performance.",
        ),
        alternatives=(
            ModelCandidate(
                name="Qwen VL-MAX-2025-01-25",
                registry_hints=("Qwen VL-MAX-2025-01-25", "qwen-vl-max"),
                why="Specialized fallback for fine-grained image parsing, content recognition, and visual reasoning.",
            ),
            ModelCandidate(
                name="Moonshotai/Kimi-K2.5",
                registry_hints=("Moonshotai/Kimi-K2.5", "kimi-k2.5", "moonshotai/kimi-k2.5"),
                why="Good multimodal fallback when the page mixes diagrams, dense text, and long reasoning.",
            ),
            ModelCandidate(
                name="Minimax/Minimax-M3",
                registry_hints=("Minimax/Minimax-M3", "minimax-m3", "minimax/minimax-m3"),
                why="Good fallback for long multimodal contexts and multi-step visual work.",
            ),
        ),
        invocation_policy="Call only for pages or regions flagged by local evidence quality checks; always store page/image refs and model output.",
        review_policy="Visual OCR output stays candidate evidence until fused with local text or independently verified.",
    ),
    ApiModelAssessment(
        key="table_vision_extract",
        task_kind="table_vision_extract",
        label="Image/table structure extraction",
        pipeline_stage="Document parsing",
        api_required_when="A table is embedded as an image, spans pages, or local cell detection cannot recover row/column headers.",
        capability_needs=(
            "image input",
            "table structure reasoning",
            "strict JSON output",
            "unit and header-path preservation",
        ),
        default_model=ModelCandidate(
            name="Qwen/Qwen3.5-Plus",
            registry_hints=("Qwen/Qwen3.5-Plus", "qwen3.5-plus", "qwen/qwen3.5-plus"),
            why="Best first choice for image-plus-text table reconstruction when the account exposes structured/multimodal support.",
        ),
        alternatives=(
            ModelCandidate(
                name="Qwen VL-MAX-2025-01-25",
                registry_hints=("Qwen VL-MAX-2025-01-25", "qwen-vl-max"),
                why="Strong visual parser for detailed image recognition and visual logic.",
            ),
            ModelCandidate(
                name="Moonshotai/Kimi-K2.5",
                registry_hints=("Moonshotai/Kimi-K2.5", "kimi-k2.5", "moonshotai/kimi-k2.5"),
                why="Useful for dense mixed visual/text tables that require longer reasoning.",
            ),
            ModelCandidate(
                name="Minimax/Minimax-M3",
                registry_hints=("Minimax/Minimax-M3", "minimax-m3", "minimax/minimax-m3"),
                why="Useful when many page images or long table continuations need one multimodal context.",
            ),
        ),
        invocation_policy="Pass cropped table images plus local table candidates; require cell values, row headers, column headers, units, and evidence refs.",
        review_policy="Always route high-value tables to verification because unit/header mistakes are database-polluting errors.",
    ),
    ApiModelAssessment(
        key="structured_extract",
        task_kind="structured_extract",
        label="Full-harvest structured ESG extraction",
        pipeline_stage="Extraction",
        api_required_when="Evidence packets are ready and the system needs candidate quantitative or qualitative disclosure objects.",
        capability_needs=(
            "structured output",
            "long context",
            "low hallucination",
            "schema repair tolerance",
            "Chinese and English ESG language",
        ),
        default_model=ModelCandidate(
            name="Doubao Seed 2.0 Lite",
            registry_hints=("Doubao Seed 2.0 Lite", "doubao-seed-2-0-lite"),
            why="Best extraction default because it is positioned for unstructured information processing, multi-source fusion, and high-fidelity structured output.",
        ),
        alternatives=(
            ModelCandidate(
                name="DeepSeek-V4-Flash",
                registry_hints=("DeepSeek-V4-Flash", "deepseek/deepseek-v4-flash"),
                why="Best cheaper long-context fallback for large packet batches.",
            ),
            ModelCandidate(
                name="Qwen3 Next 80B A3B Instruct",
                registry_hints=("Qwen3 Next 80B A3B Instruct", "qwen3-next-80b-a3b-instruct"),
                why="Good when final-answer format stability matters and explicit reasoning traces are not desired.",
            ),
            ModelCandidate(
                name="Qwen/Qwen3.5-Plus",
                registry_hints=("Qwen/Qwen3.5-Plus", "qwen3.5-plus", "qwen/qwen3.5-plus"),
                why="Good if extraction must combine OCR-derived visual context with text packets.",
            ),
        ),
        invocation_policy="Use schema-constrained prompts; emit candidates with exact evidence ids, raw text spans, units, boundaries, and uncertainty flags.",
        review_policy="Every candidate remains unpublished until verifier and mapping gates pass.",
    ),
    ApiModelAssessment(
        key="qualitative_summarization",
        task_kind="qualitative_summarization",
        label="Qualitative claim compression",
        pipeline_stage="Extraction",
        api_required_when="Policies, targets, risk controls, governance mechanisms, and narrative ESG claims need compressed canonical summaries.",
        capability_needs=(
            "faithful summarization",
            "long context",
            "entity and scope preservation",
            "no unsupported inference",
        ),
        default_model=ModelCandidate(
            name="DeepSeek-V4-Flash",
            registry_hints=("DeepSeek-V4-Flash", "deepseek/deepseek-v4-flash"),
            why="Best cost/performance default for long narrative compression and large-volume reports.",
        ),
        alternatives=(
            ModelCandidate(
                name="Doubao Seed 2.0 Lite",
                registry_hints=("Doubao Seed 2.0 Lite", "doubao-seed-2-0-lite"),
                why="Good when narrative compression should directly emit structured summary fields.",
            ),
            ModelCandidate(
                name="Qwen3 Next 80B A3B Instruct",
                registry_hints=("Qwen3 Next 80B A3B Instruct", "qwen3-next-80b-a3b-instruct"),
                why="Good final-answer model with stable instruction following.",
            ),
            ModelCandidate(
                name="Moonshotai/Kimi-K2.5",
                registry_hints=("Moonshotai/Kimi-K2.5", "kimi-k2.5", "moonshotai/kimi-k2.5"),
                why="Good for long bilingual narratives that include images or charts.",
            ),
        ),
        invocation_policy="Summaries must include source claim ids and must not invent performance conclusions.",
        review_policy="Verifier checks whether every summary sentence is entailed by cited evidence.",
    ),
    ApiModelAssessment(
        key="long_context_understanding",
        task_kind="long_context_understanding",
        label="Whole-report long-context synthesis",
        pipeline_stage="Analysis",
        api_required_when="The system needs report-level consistency checks, cross-page continuity, or broad ESG topic inventory.",
        capability_needs=(
            "1M-class context preferred",
            "multi-step synthesis",
            "cost control for whole reports",
            "stable evidence referencing",
        ),
        default_model=ModelCandidate(
            name="DeepSeek-V4-Flash",
            registry_hints=("DeepSeek-V4-Flash", "deepseek/deepseek-v4-flash"),
            why="Best default for whole-report passes because it combines 1M-context positioning, high throughput, and low price.",
        ),
        alternatives=(
            ModelCandidate(
                name="DeepSeek-V4-Pro",
                registry_hints=("DeepSeek-V4-Pro", "deepseek-v4-pro", "deepseek/deepseek-v4-pro"),
                why="Use when long-context synthesis is high stakes and can tolerate higher cost.",
            ),
            ModelCandidate(
                name="Z-AI/GLM-5.2",
                registry_hints=("Z-AI/GLM-5.2", "glm-5.2", "z-ai/glm-5.2"),
                why="Good long-task fallback when available; listed with 1M context positioning.",
            ),
            ModelCandidate(
                name="Minimax/Minimax-M3",
                registry_hints=("Minimax/Minimax-M3", "minimax-m3", "minimax/minimax-m3"),
                why="Good multimodal 1M-context fallback for long multi-step analysis.",
            ),
        ),
        invocation_policy="Use sparingly for report-level passes; avoid replacing packet-level evidence extraction.",
        review_policy="Report-level summaries are advisory and cannot create database facts without packet evidence.",
    ),
    ApiModelAssessment(
        key="mapping_review",
        task_kind="mapping_review",
        label="ESG standard mapping review",
        pipeline_stage="Mapping",
        api_required_when="A disclosure candidate must be mapped to HKEX, ESRS, GRI, ISSB, or internal canonical concepts.",
        capability_needs=(
            "deep reasoning",
            "long standard text context",
            "multi-label judgement",
            "semantic similarity with conflict handling",
        ),
        default_model=ModelCandidate(
            name="DeepSeek-V4-Pro",
            registry_hints=("DeepSeek-V4-Pro", "deepseek-v4-pro", "deepseek/deepseek-v4-pro"),
            why="Best default for standards mapping because it is positioned for advanced reasoning, long-context synthesis, and agentic multi-step work.",
        ),
        alternatives=(
            ModelCandidate(
                name="Z-AI/GLM 5",
                registry_hints=("Z-AI/GLM 5", "glm-5", "z-ai/glm-5"),
                why="Strong alternative for complex long-horizon agentic engineering style reasoning.",
            ),
            ModelCandidate(
                name="Minimax/Minimax-M2.7",
                registry_hints=("Minimax/Minimax-M2.7", "minimax-m2.7", "minimax/minimax-m2.7"),
                why="Good alternative for autonomous planning, execution, and optimization style review.",
            ),
            ModelCandidate(
                name="Qwen3 Max",
                registry_hints=("Qwen3 Max", "qwen3-max"),
                why="Good alternative for complex agent tasks and tool-call oriented standard lookup.",
            ),
        ),
        invocation_policy="Use standard snippets plus candidate evidence; require matched clause ids, mismatch reasons, and confidence.",
        review_policy="High-confidence mappings can pass to publication; ambiguous mappings require human review.",
    ),
    ApiModelAssessment(
        key="verification",
        task_kind="verification",
        label="Independent text/evidence verification",
        pipeline_stage="Review",
        api_required_when="A candidate disclosure, unit normalization, summary, or mapping needs independent audit before publication.",
        capability_needs=(
            "independent model family",
            "deep reasoning",
            "schema checking",
            "contradiction detection",
            "evidence-only judgement",
        ),
        default_model=ModelCandidate(
            name="Z-AI/GLM 5",
            registry_hints=("Z-AI/GLM 5", "glm-5", "z-ai/glm-5"),
            why="Best default verifier because it is a different family from the recommended extraction defaults and is positioned for complex agentic work.",
        ),
        alternatives=(
            ModelCandidate(
                name="DeepSeek-V4-Pro",
                registry_hints=("DeepSeek-V4-Pro", "deepseek-v4-pro", "deepseek/deepseek-v4-pro"),
                why="Use when DeepSeek was not the extractor or when review is exceptionally high stakes.",
            ),
            ModelCandidate(
                name="Minimax/Minimax-M2.7",
                registry_hints=("Minimax/Minimax-M2.7", "minimax-m2.7", "minimax/minimax-m2.7"),
                why="Good alternative for multi-step audit and self-correction style review.",
            ),
            ModelCandidate(
                name="Qwen3 Next 80B A3B Thinking",
                registry_hints=("Qwen3 Next 80B A3B Thinking", "qwen3-next-80b-a3b-thinking"),
                why="Good reasoning fallback when visible reasoning-mode behavior helps debug verification failures.",
            ),
        ),
        invocation_policy="Never verify with the same exact model and prompt that produced the candidate when alternatives are available.",
        review_policy="Verifier can approve, reject, or mark needs-human-review; it cannot silently repair source facts.",
    ),
    ApiModelAssessment(
        key="visual_verification",
        task_kind="visual_verification",
        label="Independent visual verification",
        pipeline_stage="Review",
        api_required_when="A scanned-page OCR result, image table, chart-derived number, or visual layout decision affects a database fact.",
        capability_needs=(
            "image input",
            "visual reasoning",
            "independent model family",
            "evidence-only judgement",
        ),
        default_model=ModelCandidate(
            name="Moonshotai/Kimi-K2.5",
            registry_hints=("Moonshotai/Kimi-K2.5", "kimi-k2.5", "moonshotai/kimi-k2.5"),
            why="Best visual verifier because it is multimodal and provides a different-family check against Qwen-based OCR defaults.",
        ),
        alternatives=(
            ModelCandidate(
                name="Qwen/Qwen3.5-Plus",
                registry_hints=("Qwen/Qwen3.5-Plus", "qwen3.5-plus", "qwen/qwen3.5-plus"),
                why="Use when the extractor was not Qwen or when Qwen gives the best account-level visual capability.",
            ),
            ModelCandidate(
                name="Minimax/Minimax-M3",
                registry_hints=("Minimax/Minimax-M3", "minimax-m3", "minimax/minimax-m3"),
                why="Good long multimodal verification fallback.",
            ),
            ModelCandidate(
                name="Qwen VL-MAX-2025-01-25",
                registry_hints=("Qwen VL-MAX-2025-01-25", "qwen-vl-max"),
                why="Good specialist fallback for fine-grained image parsing checks.",
            ),
        ),
        invocation_policy="Compare visual evidence against the extracted candidate; ask for pass/fail/uncertain and exact conflicting pixels/regions.",
        review_policy="Any visual verification uncertainty on a numeric fact becomes human-review required.",
    ),
)


def api_model_assessment_payload() -> list[dict[str, Any]]:
    return [item.to_dict() for item in API_MODEL_ASSESSMENTS]

