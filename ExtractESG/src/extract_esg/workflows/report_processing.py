from __future__ import annotations

from pathlib import Path

from pydantic import Field

from extract_esg.contracts.base import ContractModel
from extract_esg.contracts.document import DocumentIR
from extract_esg.contracts.evidence import EvidencePacket
from extract_esg.contracts.extraction import DisclosureObject
from extract_esg.document.local_pdf import LocalPdfProcessor
from extract_esg.document.packets import EvidencePacketBuilder
from extract_esg.extraction import FullHarvestAgent
from extract_esg.persistence import SqliteStore


class ReportProcessingState(ContractModel):
    report_id: str
    document_ir: DocumentIR | None = None
    packets: list[EvidencePacket] = Field(default_factory=list)
    disclosures: list[DisclosureObject] = Field(default_factory=list)
    status: str = "created"


class ReportProcessingWorkflow:
    """Synchronous skeleton for the durable workflow to be implemented later."""

    def __init__(
        self,
        local_pdf_processor: LocalPdfProcessor | None = None,
        packet_builder: EvidencePacketBuilder | None = None,
        full_harvest_agent: FullHarvestAgent | None = None,
    ) -> None:
        self.local_pdf_processor = local_pdf_processor or LocalPdfProcessor()
        self.packet_builder = packet_builder or EvidencePacketBuilder()
        self.full_harvest_agent = full_harvest_agent or FullHarvestAgent()

    def prepare_local_evidence(self, artifact_path: str | Path, *, report_id: str | None = None) -> ReportProcessingState:
        document = self.local_pdf_processor.inspect(artifact_path, report_id=report_id)
        packets = self.packet_builder.text_packets(document)
        return ReportProcessingState(
            report_id=document.report_id,
            document_ir=document,
            packets=packets,
            status="local_evidence_prepared",
        )

    def run_local_pipeline(
        self,
        artifact_path: str | Path,
        *,
        report_id: str | None = None,
        store: SqliteStore | None = None,
    ) -> ReportProcessingState:
        state = self.prepare_local_evidence(artifact_path, report_id=report_id)
        disclosures: list[DisclosureObject] = []
        for packet in state.packets:
            disclosures.extend(self.full_harvest_agent.run_local_baseline(packet))

        state = state.model_copy(update={"disclosures": disclosures, "status": "local_pipeline_completed"})
        if store and state.document_ir:
            store.save_document(state.document_ir)
            store.save_packets(state.packets)
            store.save_disclosures(state.disclosures)
        return state
