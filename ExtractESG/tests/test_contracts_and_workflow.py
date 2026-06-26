from __future__ import annotations

import unittest
from pathlib import Path

from extract_esg.ai.qiniu_registry import QiniuModelRegistry
from extract_esg.contracts.cloud import CloudModelInfo
from extract_esg.contracts.evidence import EvidencePacket
from extract_esg.validation import EvidenceVerifier
from extract_esg.workflows import ReportProcessingWorkflow


class ContractAndWorkflowTests(unittest.TestCase):
    def test_qiniu_registry_selects_by_capability(self) -> None:
        registry = QiniuModelRegistry(cache_path=Path("/tmp/nonexistent-qiniu-model-cache.json"))
        registry.seed(
            [
                CloudModelInfo(id="text-small", input_modalities=["text"], context_length=32000),
                CloudModelInfo(
                    id="vision-structured-long",
                    input_modalities=["text", "image"],
                    context_length=262144,
                    supports_schema_output=True,
                ),
            ]
        )

        selected = registry.select(requires_image=True, requires_schema=True, min_context=128000)

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.id, "vision-structured-long")

    def test_workflow_prepares_local_evidence_for_reference_pdf(self) -> None:
        pdf = Path(__file__).resolve().parents[2] / "Reference" / "2606.23050v1.pdf"
        if not pdf.exists():
            self.skipTest("reference PDF not present in this checkout")

        state = ReportProcessingWorkflow().prepare_local_evidence(pdf, report_id="uocr-paper")

        self.assertEqual(state.status, "local_evidence_prepared")
        self.assertIsNotNone(state.document_ir)
        assert state.document_ir is not None
        self.assertEqual(len(state.document_ir.pages), 14)
        self.assertGreater(len(state.packets), 0)

    def test_local_baseline_disclosure_is_evidence_supported(self) -> None:
        pdf = Path(__file__).resolve().parents[2] / "Reference" / "2606.23050v1.pdf"
        if not pdf.exists():
            self.skipTest("reference PDF not present in this checkout")

        state = ReportProcessingWorkflow().prepare_local_evidence(pdf, report_id="uocr-paper")
        packet: EvidencePacket = state.packets[0]
        from extract_esg.extraction.full_harvest import FullHarvestAgent

        disclosures = FullHarvestAgent().run_local_baseline(packet)

        self.assertTrue(disclosures)
        self.assertTrue(EvidenceVerifier().has_supporting_evidence(disclosures[0], packet))


if __name__ == "__main__":
    unittest.main()
