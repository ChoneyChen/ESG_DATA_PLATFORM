from __future__ import annotations

from extract_esg.contracts.evidence import EvidencePacket


class TargetedRecallAgent:
    version = "targeted-recall-0.1"

    def filter_packets(self, packets: list[EvidencePacket], keywords: list[str]) -> list[EvidencePacket]:
        lowered = [keyword.lower() for keyword in keywords]
        selected = []
        for packet in packets:
            text = "\n".join(fragment.text or "" for fragment in packet.primary_evidence).lower()
            if any(keyword in text for keyword in lowered):
                selected.append(packet)
        return selected

