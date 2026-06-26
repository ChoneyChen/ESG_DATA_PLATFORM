from __future__ import annotations

from extract_esg.contracts.base import SourceTrace
from extract_esg.contracts.evidence import EvidencePacket
from extract_esg.contracts.extraction import DisclosureObject


class FullHarvestAgent:
    """Local placeholder for PDF-first disclosure discovery.

    Real extraction should call cloud structured models through CloudTaskBuilder
    and QiniuModelAdapter. This local fallback only creates auditable candidates
    for smoke tests and deterministic development.
    """

    version = "full-harvest-0.1"

    def run_local_baseline(self, packet: EvidencePacket) -> list[DisclosureObject]:
        text = "\n".join(fragment.text or "" for fragment in packet.primary_evidence)
        if not text.strip():
            return []
        trace = SourceTrace(run_id=packet.id, producer="FullHarvestAgent", version=self.version)
        return [
            DisclosureObject(
                report_id=packet.report_id,
                raw_label="unclassified_esg_disclosure_candidate",
                raw_text=text[:4000],
                evidence_ids=[fragment.id for fragment in packet.primary_evidence],
                disclosure_type="qualitative",
                source=trace,
                quality_flags=["local_baseline_only", "requires_cloud_extraction"],
            )
        ]

