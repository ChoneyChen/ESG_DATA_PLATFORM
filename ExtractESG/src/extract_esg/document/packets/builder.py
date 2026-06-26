from __future__ import annotations

from extract_esg.contracts.base import SourceTrace
from extract_esg.contracts.document import DocumentIR
from extract_esg.contracts.evidence import EvidenceFragment, EvidencePacket


class EvidencePacketBuilder:
    version = "evidence-packet-builder-0.1"

    def text_packets(self, document: DocumentIR, *, task_type: str = "full_harvest") -> list[EvidencePacket]:
        packets: list[EvidencePacket] = []
        trace = SourceTrace(run_id=document.report_id, producer="EvidencePacketBuilder", version=self.version)
        for page in document.pages:
            fragments = [
                EvidenceFragment(
                    report_id=document.report_id,
                    page_ref=block.page_ref,
                    kind="text_block",
                    text=block.text,
                    bbox=block.bbox,
                    source=trace,
                )
                for block in page.text_blocks
                if block.text.strip()
            ]
            if not fragments:
                continue
            packets.append(
                EvidencePacket(
                    report_id=document.report_id,
                    task_type=task_type,  # type: ignore[arg-type]
                    primary_evidence=fragments,
                )
            )
        return packets

