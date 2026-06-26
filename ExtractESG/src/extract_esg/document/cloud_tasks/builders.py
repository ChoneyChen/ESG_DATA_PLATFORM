from __future__ import annotations

from extract_esg.contracts.cloud import CloudTaskRequest
from extract_esg.contracts.evidence import EvidencePacket


class CloudTaskBuilder:
    """Build cloud tasks from evidence packets.

    Task builders keep prompt/version metadata close to the request so model
    outputs remain auditable and replayable.
    """

    def build_structured_extract(self, packet: EvidencePacket, *, model_id: str) -> CloudTaskRequest:
        evidence_text = "\n\n".join(
            f"[{item.id}] page={item.page_ref.page_index + 1}\n{item.text or ''}"
            for item in packet.primary_evidence + packet.context_evidence
        )
        return CloudTaskRequest(
            task_type="structured_extract",
            model_id=model_id,
            input_refs=[item.id for item in packet.primary_evidence + packet.context_evidence],
            prompt_version="structured-extract-v0.1",
            schema_name="DisclosureObjectCandidate",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract ESG disclosure candidates from provided evidence only. "
                        "Return JSON. If evidence is insufficient, return review_required."
                    ),
                },
                {"role": "user", "content": evidence_text},
            ],
        )

