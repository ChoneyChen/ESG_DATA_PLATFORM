from __future__ import annotations

from pathlib import Path

from pydantic import Field

from extract_esg.ai import CloudModelRouter, QiniuModelAdapter, QiniuModelRegistry
from extract_esg.config import Settings
from extract_esg.contracts.cloud import CloudTaskResult
from extract_esg.contracts.base import ContractModel
from extract_esg.contracts.document import DocumentIR
from extract_esg.contracts.evidence import EvidencePacket
from extract_esg.contracts.extraction import DisclosureObject
from extract_esg.document.cloud_tasks import CloudTaskBuilder
from extract_esg.document.local_pdf import LocalPdfProcessor
from extract_esg.document.packets import EvidencePacketBuilder
from extract_esg.extraction import FullHarvestAgent, parse_structured_disclosures
from extract_esg.persistence import SqliteStore


class ReportProcessingState(ContractModel):
    report_id: str
    document_ir: DocumentIR | None = None
    packets: list[EvidencePacket] = Field(default_factory=list)
    disclosures: list[DisclosureObject] = Field(default_factory=list)
    cloud_results: list[CloudTaskResult] = Field(default_factory=list)
    status: str = "created"


class ReportProcessingWorkflow:
    """Synchronous skeleton for the durable workflow to be implemented later."""

    def __init__(
        self,
        local_pdf_processor: LocalPdfProcessor | None = None,
        packet_builder: EvidencePacketBuilder | None = None,
        full_harvest_agent: FullHarvestAgent | None = None,
        cloud_task_builder: CloudTaskBuilder | None = None,
    ) -> None:
        self.local_pdf_processor = local_pdf_processor or LocalPdfProcessor()
        self.packet_builder = packet_builder or EvidencePacketBuilder()
        self.full_harvest_agent = full_harvest_agent or FullHarvestAgent()
        self.cloud_task_builder = cloud_task_builder or CloudTaskBuilder()

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

    def run_cloud_pipeline(
        self,
        artifact_path: str | Path,
        *,
        report_id: str | None = None,
        store: SqliteStore | None = None,
        settings: Settings | None = None,
        model_id: str | None = None,
        start_packet: int = 0,
        max_packets: int = 2,
        max_chars_per_packet: int = 6000,
    ) -> ReportProcessingState:
        settings = settings or Settings.from_env()
        state = self.prepare_local_evidence(artifact_path, report_id=report_id)
        selected_model = model_id or self._choose_structured_model(settings)
        adapter = QiniuModelAdapter(settings)

        cloud_results: list[CloudTaskResult] = []
        disclosures: list[DisclosureObject] = []
        packets = state.packets[start_packet : start_packet + max_packets]
        if store and state.document_ir:
            store.save_document(state.document_ir)
            store.save_packets(state.packets)

        for packet in packets:
            task = self.cloud_task_builder.build_structured_extract(
                packet,
                model_id=selected_model,
                max_chars=max_chars_per_packet,
            )
            result = adapter.chat_completions(task)
            allowed_evidence_ids = set(task.input_refs)
            parsed_disclosures, parsed_payload = parse_structured_disclosures(
                result,
                report_id=state.report_id,
                allowed_evidence_ids=allowed_evidence_ids,
            )
            result = result.model_copy(update={"parsed_response": parsed_payload})
            cloud_results.append(result)
            disclosures.extend(parsed_disclosures)
            if store:
                store.save_cloud_result(result, report_id=state.report_id, task_type=task.task_type)

        state = state.model_copy(
            update={
                "disclosures": disclosures,
                "cloud_results": cloud_results,
                "status": "cloud_pipeline_completed",
            }
        )
        if store:
            store.save_disclosures(state.disclosures)
        return state

    def _choose_structured_model(self, settings: Settings) -> str:
        registry = QiniuModelRegistry(cache_path=settings.model_cache, adapter=QiniuModelAdapter(settings))
        models = registry.load_cache()
        if not models and settings.qiniu_api_key:
            models = registry.refresh()
        choice = CloudModelRouter(registry, settings).choose("structured_extract")
        if choice.model:
            return choice.model.id
        available = {model.id for model in models}
        for model_id in (
            "deepseek/deepseek-v4-flash",
            "qwen-turbo",
            "qwen3-next-80b-a3b-instruct",
            "qwen/qwen3.5-plus",
        ):
            if not available or model_id in available:
                return model_id
        raise RuntimeError("No usable Qiniu structured extraction model is available.")
