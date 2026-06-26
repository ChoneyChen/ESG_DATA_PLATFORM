from __future__ import annotations

from extract_esg.contracts.evidence import EvidencePacket
from extract_esg.contracts.extraction import DisclosureObject


class EvidenceVerifier:
    version = "evidence-verifier-0.1"

    def has_supporting_evidence(self, disclosure: DisclosureObject, packet: EvidencePacket) -> bool:
        available = {fragment.id for fragment in packet.primary_evidence + packet.context_evidence}
        return bool(disclosure.evidence_ids) and set(disclosure.evidence_ids).issubset(available)

