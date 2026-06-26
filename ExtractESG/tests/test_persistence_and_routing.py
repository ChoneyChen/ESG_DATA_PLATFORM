from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extract_esg.ai import CloudModelRouter, QiniuModelRegistry
from extract_esg.config import Settings
from extract_esg.contracts.cloud import CloudModelInfo
from extract_esg.persistence import SqliteStore
from extract_esg.workflows import ReportProcessingWorkflow


class PersistenceAndRoutingTests(unittest.TestCase):
    def test_sqlite_store_roundtrip(self) -> None:
        pdf = Path(__file__).resolve().parents[2] / "Reference" / "2606.23050v1.pdf"
        if not pdf.exists():
            self.skipTest("reference PDF not present in this checkout")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "extract_esg.db"
            store = SqliteStore(db_path)
            state = ReportProcessingWorkflow().run_local_pipeline(pdf, report_id="roundtrip", store=store)

            reports = store.list_reports()
            detail = store.get_report("roundtrip")

            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["report_id"], "roundtrip")
            self.assertEqual(reports[0]["page_count"], 14)
            self.assertGreater(reports[0]["packet_count"], 0)
            self.assertEqual(reports[0]["disclosure_count"], len(state.disclosures))
            self.assertIsNotNone(detail)

    def test_router_honors_explicit_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("EXTRACT_ESG_VISION_MODEL=pinned-vision\n", encoding="utf-8")
            settings = Settings.from_env(env)
            registry = QiniuModelRegistry(cache_path=Path(tmp) / "models.json")
            registry.seed(
                [
                    CloudModelInfo(id="pinned-vision", input_modalities=["text", "image"], context_length=128000),
                    CloudModelInfo(id="other", input_modalities=["text", "image"], context_length=262144),
                ]
            )

            choice = CloudModelRouter(registry, settings).choose("vision_ocr")

            self.assertIsNotNone(choice.model)
            assert choice.model is not None
            self.assertEqual(choice.model.id, "pinned-vision")
            self.assertEqual(choice.reason, "explicit_env_model")


if __name__ == "__main__":
    unittest.main()

