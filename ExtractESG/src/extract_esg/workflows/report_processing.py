from __future__ import annotations

from pathlib import Path

from pydantic import Field

from extract_esg.contracts.base import ContractModel
from extract_esg.contracts.document import DocumentIR
from extract_esg.contracts.evidence import EvidencePacket
from extract_esg.document.local_pdf import LocalPdfProcessor
from extract_esg.document.packets import EvidencePacketBuilder


class ReportProcessingState(ContractModel):
    report_id: str
    document_ir: DocumentIR | None = None
    packets: list[EvidencePacket] = Field(default_factory=list)
    status: str = "created"


class ReportProcessingWorkflow:
    """Synchronous skeleton for the durable workflow to be implemented later."""

    def __init__(
        self,
        local_pdf_processor: LocalPdfProcessor | None = None,
        packet_builder: EvidencePacketBuilder | None = None,
    ) -> None:
        self.local_pdf_processor = local_pdf_processor or LocalPdfProcessor()
        self.packet_builder = packet_builder or EvidencePacketBuilder()

    def prepare_local_evidence(self, artifact_path: str | Path, *, report_id: str | None = None) -> ReportProcessingState:
        document = self.local_pdf_processor.inspect(artifact_path, report_id=report_id)
        packets = self.packet_builder.text_packets(document)
        return ReportProcessingState(
            report_id=document.report_id,
            document_ir=document,
            packets=packets,
            status="local_evidence_prepared",
        )

