from __future__ import annotations

from extract_esg.contracts.cloud import CloudTaskRequest
from extract_esg.contracts.evidence import EvidencePacket


class CloudTaskBuilder:
    """Build cloud tasks from evidence packets.

    Task builders keep prompt/version metadata close to the request so model
    outputs remain auditable and replayable.
    """

    def build_structured_extract(
        self,
        packet: EvidencePacket,
        *,
        model_id: str,
        max_chars: int = 6000,
    ) -> CloudTaskRequest:
        evidence_text = "\n\n".join(
            f"[{item.id}] page={item.page_ref.page_index + 1}\n{item.text or ''}"
            for item in packet.primary_evidence + packet.context_evidence
        )[:max_chars]
        return CloudTaskRequest(
            task_type="structured_extract",
            model_id=model_id,
            input_refs=[item.id for item in packet.primary_evidence + packet.context_evidence],
            prompt_version="structured-extract-v0.1",
            schema_name="DisclosureObjectCandidate",
            max_tokens=1200,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are ExtractESG's structured extraction worker. "
                        "Extract ESG disclosure candidates from the provided evidence only. "
                        "Do not invent facts. Return strict JSON only with this shape: "
                        "{\"disclosures\":[{\"raw_label\":\"...\",\"raw_text\":\"...\","
                        "\"disclosure_type\":\"quantitative|qualitative|target|policy|risk|governance|methodology\","
                        "\"concept_candidates\":[\"...\"],\"evidence_ids\":[\"evidence id exactly as provided\"]}],"
                        "\"review_required\":false}. "
                        "Every disclosure must cite evidence_ids from the bracketed ids in the user message. "
                        "If there is no ESG disclosure, return {\"disclosures\":[],\"review_required\":true}."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Evidence packet follows. Extract all ESG disclosures that are explicitly supported.\n\n"
                        f"{evidence_text}"
                    ),
                },
            ],
        )
