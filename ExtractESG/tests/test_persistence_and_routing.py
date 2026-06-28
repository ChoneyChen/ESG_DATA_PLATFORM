from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extract_esg.ai import CloudModelRouter, QiniuModelRegistry, api_model_assessment_payload
from extract_esg.config import Settings
from extract_esg.contracts.base import SourceTrace
from extract_esg.contracts.cloud import CloudModelInfo, CloudTaskResult
from extract_esg.extraction import parse_structured_disclosures
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

    def test_model_assessment_covers_api_tasks(self) -> None:
        payload = api_model_assessment_payload()
        keys = {item["key"] for item in payload}
        self.assertIn("vision_ocr", keys)
        self.assertIn("structured_extract", keys)
        self.assertIn("mapping_review", keys)
        self.assertIn("verification", keys)
        for item in payload:
            self.assertTrue(item["default_model"]["name"])
            self.assertGreaterEqual(len(item["alternatives"]), 1)

    def test_registry_infers_capabilities_from_model_id(self) -> None:
        model = QiniuModelRegistry._parse_model({"id": "qwen2.5-vl-72b-instruct", "object": "model"})
        self.assertIn("image", model.input_modalities)
        self.assertTrue(model.supports_schema_output)
        self.assertGreaterEqual(model.context_length or 0, 32768)

    def test_parse_structured_cloud_disclosures(self) -> None:
        result = CloudTaskResult(
            request_id="cloud_task_test",
            model_id="qwen-turbo",
            raw_response={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"disclosures":[{"raw_label":"GHG emissions","raw_text":"2024 emissions were 12 tCO2e",'
                                '"disclosure_type":"quantitative","concept_candidates":["emissions"],"evidence_ids":["ev_1"]}]}'
                            )
                        }
                    }
                ]
            },
            source=SourceTrace(run_id="cloud_task_test", producer="test", version="test"),
        )

        disclosures, parsed = parse_structured_disclosures(result, report_id="r1", allowed_evidence_ids={"ev_1"})

        self.assertEqual(len(disclosures), 1)
        self.assertEqual(disclosures[0].disclosure_type, "quantitative")
        self.assertEqual(disclosures[0].evidence_ids, ["ev_1"])
        self.assertIn("disclosures", parsed)


if __name__ == "__main__":
    unittest.main()
