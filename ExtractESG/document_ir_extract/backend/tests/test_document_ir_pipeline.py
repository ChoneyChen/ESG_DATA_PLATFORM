from __future__ import annotations

import json
import importlib
import re
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    BlockIR,
    DocumentIR,
    DocumentIRMetadata,
    DocumentIrBuildRequest,
    PageIR,
    SourceTrace,
)
from esg_v2.document.page_renderer import PageRenderer
from esg_v2.document.structure_reconstruction import StructureReconstructor
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.document.validator import DocumentIrValidator
from esg_v2.workflows.ir_workflow import DocumentIrWorkflow


def _write_pdf(path: Path, page_count: int = 2) -> None:
    pages = []
    for index in range(page_count):
        image = Image.new("RGB", (612, 792), "white")
        draw = ImageDraw.Draw(image)
        draw.text((50, 45), f"Sustainability page {index + 1}", fill="black")
        draw.rectangle((50, 180, 560, 400), outline="black", width=2)
        pages.append(image)
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=72)


def _layout_page(page_index: int) -> dict[str, object]:
    table_rows = (
        "| Metric | 2024 | 2025 | Unit |\n"
        "| --- | --- | --- | --- |\n"
        f"| Female employees {page_index} | 42 | 45 | % |"
    )
    return {
        "prunedResult": {
            "page_index": page_index,
            "width": 612,
            "height": 792,
            "parsing_res_list": [
                {"block_id": 1, "block_order": 1, "block_label": "title", "block_content": "Sustainability Performance", "block_bbox": [50, 40, 560, 80]},
                {"block_id": 2, "block_order": 2, "block_label": "text", "block_content": "The Group monitors workforce diversity.", "block_bbox": [50, 100, 560, 150]},
                {"block_id": 3, "block_order": 3, "block_label": "table", "block_content": table_rows, "block_bbox": [50, 180, 560, 400]},
                {"block_id": 4, "block_order": 4, "block_label": "chart", "block_content": "Workforce trend", "block_bbox": [50, 430, 560, 700]},
            ],
        },
        "markdown": {
            "text": f"# Sustainability Performance\n\nThe Group monitors workforce diversity.\n\n{table_rows}\n",
            "images": {},
        },
        "outputImages": {},
    }


def _write_ocr_run(root: Path, run_id: str, pdf_path: Path) -> None:
    run = root / run_id
    (run / "raw").mkdir(parents=True)
    (run / "pages").mkdir()
    payload = {"result": {"layoutParsingResults": [_layout_page(0), _layout_page(1)]}}
    (run / "raw" / "result.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    for index, page in enumerate(payload["result"]["layoutParsingResults"]):
        (run / "pages" / f"page_{index:04d}.md").write_text(page["markdown"]["text"], encoding="utf-8")
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "job_id": "job-test",
                "model": "PaddleOCR-VL-1.6",
                "source": str(pdf_path),
                "page_count": 2,
            }
        ),
        encoding="utf-8",
    )


def test_page_renderer_isolates_pdfium_and_reports_progress(tmp_path: Path) -> None:
    pdf_path = tmp_path / "report.pdf"
    output_dir = tmp_path / "page-images"
    _write_pdf(pdf_path)
    progress: list[tuple[int, int]] = []

    rendered = PageRenderer(timeout_seconds=30).render(
        str(pdf_path),
        output_dir,
        dpi=144,
        progress=lambda current, total: progress.append((current, total)),
    )

    assert len(rendered.pages) == 2
    assert rendered.errors == []
    assert progress[-1] == (2, 2)
    assert not (output_dir / ".render-result.json").exists()
    assert not (output_dir / ".render-status.json").exists()
    assert all(page.artifact.source.endswith("isolated-worker") for page in rendered.pages)


def test_page_renderer_turns_native_worker_crash_into_python_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pdf_path = tmp_path / "report.pdf"
    _write_pdf(pdf_path, page_count=1)

    class CrashedProcess:
        returncode = -11

        @staticmethod
        def poll():
            return -11

    monkeypatch.setattr(
        "esg_v2.document.page_renderer.subprocess.Popen",
        lambda *args, **kwargs: CrashedProcess(),
    )

    with pytest.raises(RuntimeError, match="signal 11"):
        PageRenderer(timeout_seconds=30).render(
            str(pdf_path),
            tmp_path / "page-images",
        )


def test_full_document_ir_pipeline_builds_geometry_graphs_and_artifacts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "report.pdf"
    _write_pdf(pdf_path)
    ocr_root = tmp_path / "ocr_output"
    ir_root = tmp_path / "document_ir_output"
    _write_ocr_run(ocr_root, "ocr-layout", pdf_path)
    settings = Settings(output_root=ocr_root, document_ir_output_root=ir_root, upload_root=tmp_path / "uploads")

    result = DocumentIrWorkflow(settings).run(
        DocumentIrBuildRequest(ocr_run_id="ocr-layout", pdf_path=str(pdf_path), render_dpi=144),
        run_id="ir-20260718T010101Z-000000000001",
    )
    document = json.loads(result.document_ir_path.read_text(encoding="utf-8"))

    assert document["schema_version"] == "document-ir-v0.11"
    assert len(document["pages"]) == 2
    assert len(document["layout_objects"]) == 8
    assert all(page["page_image_path"] for page in document["pages"])
    assert all(item["bbox"]["unit"] == "points" for item in document["layout_objects"])
    assert len(document["coordinate_systems"]) == 6
    assert len(document["tables"]) == 2
    assert len(document["logical_tables"]) == 1
    assert document["tables"][0]["continues_to_table_id"] == document["tables"][1]["table_id"]
    assert document["tables"][1]["continues_from_table_id"] == document["tables"][0]["table_id"]
    assert all(table["graph_edges"] for table in document["tables"])
    assert all(table["crop_artifact_id"] for table in document["tables"])
    assert all(figure["crop_artifact_id"] for figure in document["figures"])
    assert document["validation_report"]["checks"]["page_render_coverage_complete"] is True
    assert document["validation_report"]["checks"]["all_layout_geometry_available"] is True
    assert document["readiness"] == "auto_review_pending"
    assert (result.output_dir / "review" / "calls" / "model-calls.jsonl").exists()
    assert (result.output_dir / "review" / "decisions" / "final-decisions.jsonl").exists()
    assert (result.output_dir / "artifacts" / "page-images" / "page-0001.png").exists()
    assert (result.output_dir / "artifacts" / "crops" / "tables" / f"{document['tables'][0]['table_id']}.png").exists()
    assert (result.output_dir / "canonical" / "document.json").exists()
    assert (result.output_dir / "canonical" / "pages" / "page-0001.json").exists()
    assert (result.output_dir / "canonical" / "spreads" / "index.json").exists()
    assert (result.output_dir / "canonical" / "logical-tables" / "index.json").exists()
    assert (result.output_dir / "integrity" / "files.json").exists()
    assert all(re.fullmatch(r"block-p\d{4}-\d{4}", item["block_id"]) for item in document["blocks"])
    assert all(re.fullmatch(r"table-p\d{4}-\d{4}", item["table_id"]) for item in document["tables"])
    manifest_text = result.manifest_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in manifest_text
    assert str(tmp_path) not in result.document_ir_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in (result.output_dir / "canonical" / "document.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["package_schema_version"] == "document-ir-package-v1"
    assert manifest["entrypoints"]["canonical_document"] == "canonical/document.json"
    assert manifest["entrypoints"]["spreads"] == "canonical/spreads/index.json"
    assert manifest["entrypoints"]["logical_tables"] == "canonical/logical-tables/index.json"


def test_ir_revision_increments_from_parent(tmp_path: Path) -> None:
    pdf_path = tmp_path / "report.pdf"
    _write_pdf(pdf_path)
    ocr_root = tmp_path / "ocr_output"
    ir_root = tmp_path / "document_ir_output"
    _write_ocr_run(ocr_root, "ocr-layout", pdf_path)
    settings = Settings(output_root=ocr_root, document_ir_output_root=ir_root, upload_root=tmp_path / "uploads")
    workflow = DocumentIrWorkflow(settings)
    first_run_id = "ir-20260718T010101Z-000000000002"
    second_run_id = "ir-20260718T010101Z-000000000003"
    first = workflow.run(DocumentIrBuildRequest(ocr_run_id="ocr-layout", pdf_path=str(pdf_path)), run_id=first_run_id)
    second = workflow.run(
        DocumentIrBuildRequest(ocr_run_id="ocr-layout", pdf_path=str(pdf_path), parent_ir_run_id=first_run_id),
        run_id=second_run_id,
    )
    first_manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    second_manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
    assert first_manifest["ir_revision"] == 1
    assert second_manifest["ir_revision"] == 2
    assert second_manifest["parent_ir_run_id"] == first_run_id


def test_document_ir_api_exposes_intermediate_views(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "report.pdf"
    _write_pdf(pdf_path)
    ocr_root = tmp_path / "ocr_output"
    ir_root = tmp_path / "document_ir_output"
    upload_root = tmp_path / "uploads"
    _write_ocr_run(ocr_root, "ocr-layout", pdf_path)
    monkeypatch.setenv("ESG_V2_OCR_OUTPUT_DIR", str(ocr_root))
    monkeypatch.setenv("ESG_V2_DOCUMENT_IR_OUTPUT_DIR", str(ir_root))
    monkeypatch.setenv("ESG_V2_UPLOAD_DIR", str(upload_root))
    monkeypatch.setenv("ESG_V2_OCR_JOB_STATE_DIR", str(tmp_path / "jobs" / "ocr"))
    monkeypatch.setenv("ESG_V2_DOCUMENT_IR_JOB_STATE_DIR", str(tmp_path / "jobs" / "document-ir"))

    import esg_v2.api.main as api_main

    api_main = importlib.reload(api_main)
    client = TestClient(api_main.app)
    response = client.post(
        "/api/document-ir/jobs",
        json={"ocr_run_id": "ocr-layout", "pdf_path": str(pdf_path), "render_dpi": 144},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    for _ in range(100):
        state = client.get(f"/api/document-ir/jobs/{run_id}").json()
        if state["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert state["status"] == "done", state
    assert client.get(f"/api/document-ir/jobs/{run_id}/manifest").status_code == 200
    assert client.get(f"/api/document-ir/jobs/{run_id}/validation-report").json()["readiness"] == "auto_review_pending"
    assert len(client.get(f"/api/document-ir/jobs/{run_id}/pages").json()) == 2
    assert client.get(f"/api/document-ir/jobs/{run_id}/pages/0").json()["layout_objects"]
    tables = client.get(f"/api/document-ir/jobs/{run_id}/tables").json()
    assert len(tables) == 2
    assert client.get(f"/api/document-ir/jobs/{run_id}/tables/{tables[0]['table_id']}").status_code == 200
    logical_tables = client.get(f"/api/document-ir/jobs/{run_id}/logical-tables").json()
    assert len(logical_tables) == 1
    assert (
        client.get(
            f"/api/document-ir/jobs/{run_id}/logical-tables/{logical_tables[0]['logical_table_id']}"
        ).status_code
        == 200
    )
    tasks = client.get(f"/api/document-ir/jobs/{run_id}/review-tasks").json()
    inbox = client.get(f"/api/document-ir/jobs/{run_id}/human-review/inbox").json()
    assert inbox["schema_version"] == "document-ir-human-review-inbox-v1"
    assert sum(inbox["counts"].values()) == len(tasks)
    assert inbox["groups"]["blocking_deferred"][0]["guidance"]["recommended_action"]
    for artifact_endpoint in (
        "model-calls",
        "reviewer-results",
        "atomic-patches",
        "patch-transactions",
        "guard-results",
        "verifier-results",
        "final-decisions",
        "candidate-revisions",
    ):
        assert client.get(f"/api/document-ir/jobs/{run_id}/{artifact_endpoint}").status_code == 200
    assert client.get(f"/api/document-ir/jobs/{run_id}/reviews/{tasks[0]['task_id']}").status_code == 200

    queued: dict[str, object] = {}

    class NoopThread:
        def __init__(self, *, target, args, daemon):
            queued.update({"target": target, "args": args, "daemon": daemon})

        def start(self):
            queued["started"] = True

    monkeypatch.setattr(api_main.threading, "Thread", NoopThread)
    retry = client.post(
        f"/api/document-ir/jobs/{run_id}/reviews/retry",
        json={"task_ids": [tasks[0]["task_id"]], "requested_by": "api-test"},
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
    assert queued["started"] is True
    assert queued["args"][1] == run_id

    duplicate = client.post(
        "/api/document-ir/jobs",
        json={"ocr_run_id": "ocr-layout", "pdf_path": str(pdf_path), "run_id": run_id},
    )
    assert duplicate.status_code == 409
    assert client.get("/api/document-ir/revisions/ocr-layout").json()[0]["ir_revision"] == 1


def test_nested_section_ranges_cover_all_descendant_pages() -> None:
    trace = SourceTrace(parser="test")
    pages = [
        PageIR(
            page_id=f"page-{index + 1:04d}",
            page_index=index,
            page_number=index + 1,
            source_trace=trace,
        )
        for index in range(4)
    ]
    blocks = [
        BlockIR(
            block_id="chapter",
            page_index=0,
            order=0,
            block_type="header",
            text="第1章 氣候智慧",
            source_trace=trace,
        ),
        BlockIR(
            block_id="risk-management",
            page_index=1,
            order=0,
            block_type="heading",
            text="1.2.2 風險管理",
            source_trace=trace,
        ),
        BlockIR(
            block_id="scenario-analysis",
            page_index=2,
            order=0,
            block_type="heading",
            text="情境分析",
            source_trace=trace,
        ),
        BlockIR(
            block_id="risk-identification",
            page_index=3,
            order=0,
            block_type="heading",
            text="風險識別",
            source_trace=trace,
        ),
    ]
    document = StructureReconstructor().reconstruct(
        DocumentIR(
            metadata=DocumentIRMetadata(run_id="ir-sections", ocr_run_id="ocr-sections"),
            pages=pages,
            blocks=blocks,
        )
    )
    sections = {section.title: section for section in document.sections}

    assert sections["第1章 氣候智慧"].end_page_index == 3
    assert sections["1.2.2 風險管理"].end_page_index == 3
    assert sections["情境分析"].parent_section_id == sections["1.2.2 風險管理"].section_id
    assert sections["風險識別"].parent_section_id == sections["1.2.2 風險管理"].section_id
    document = DocumentIrValidator().validate(document, expected_page_count=4)
    assert document.validation_report.checks["section_hierarchy_valid"] is True

    sections["第1章 氣候智慧"].end_page_index = 1
    document = DocumentIrValidator().validate(document, expected_page_count=4)
    assert document.readiness == "failed"
    assert document.validation_report.checks["section_hierarchy_valid"] is False


def test_canonical_ir_uses_artifact_ids_and_redacts_signed_urls(tmp_path: Path) -> None:
    pdf_path = tmp_path / "report.pdf"
    _write_pdf(pdf_path)
    ocr_root = tmp_path / "ocr_output"
    ir_root = tmp_path / "document_ir_output"
    _write_ocr_run(ocr_root, "ocr-provenance", pdf_path)
    run_dir = ocr_root / "ocr-provenance"
    signed_url = "https://paddle.example.test/result.json?authorization=bce-auth-v1%2Ftemporary"
    raw_path = run_dir / "raw" / "result.jsonl"
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    first_page = raw_payload["result"]["layoutParsingResults"][0]
    first_page["prunedResult"]["parsing_res_list"][0]["asset_url"] = signed_url
    first_page["outputImages"] = {"layout_det_res": signed_url}
    raw_path.write_text(json.dumps(raw_payload) + "\n", encoding="utf-8")
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["result_json_url"] = signed_url
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    settings = Settings(output_root=ocr_root, document_ir_output_root=ir_root, upload_root=tmp_path / "uploads")

    result = DocumentIrWorkflow(settings).run(
        DocumentIrBuildRequest(ocr_run_id="ocr-provenance", pdf_path=str(pdf_path)),
        run_id="ir-20260718T010101Z-000000000004",
    )
    document = DocumentIrPackageReader(result.output_dir, ocr_output_root=ocr_root).load_document(
        hydrate_local_paths=True
    )
    serialized = result.document_ir_path.read_text(encoding="utf-8")

    assert "authorization=" not in serialized
    assert "authorization=" in raw_path.read_text(encoding="utf-8")
    assert document.pages[0].remote_image_urls == []
    assert all(item.source_trace.artifact_ids for item in document.layout_objects)
    assert all(item.source_trace.artifact_ids for item in document.blocks)
    assert all(item.source_trace.artifact_ids for item in document.tables)
    assert all(cell.source_trace.artifact_ids for table in document.tables for cell in table.cells)
    assert all(item.source_trace.artifact_ids for item in document.figures)
    assert all(item.source_trace.raw_object_path for item in document.tables)
    assert document.validation_report.checks["source_trace_integrity_valid"] is True
    assert document.validation_report.checks["sensitive_remote_urls_absent"] is True
    assert document.validation_report.metrics["source_trace_artifact_coverage"] == 1.0


def test_validator_blocks_corrupted_references_grids_and_artifacts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "report.pdf"
    _write_pdf(pdf_path)
    ocr_root = tmp_path / "ocr_output"
    ir_root = tmp_path / "document_ir_output"
    _write_ocr_run(ocr_root, "ocr-corruption", pdf_path)
    settings = Settings(output_root=ocr_root, document_ir_output_root=ir_root, upload_root=tmp_path / "uploads")
    result = DocumentIrWorkflow(settings).run(
        DocumentIrBuildRequest(ocr_run_id="ocr-corruption", pdf_path=str(pdf_path)),
        run_id="ir-20260718T010101Z-000000000005",
    )
    document = DocumentIrPackageReader(result.output_dir, ocr_output_root=ocr_root).load_document(
        hydrate_local_paths=True
    )
    document.structure_edges[0].target_id = "missing-entity"
    document.tables[0].cells[0].row_index = document.tables[0].row_count
    local_artifact = next(item for item in document.artifacts if "://" not in item.path)
    local_artifact.sha256 = "0" * 64

    document = DocumentIrValidator().validate(document, expected_page_count=2)

    assert document.readiness == "failed"
    assert document.validation_report.checks["entity_references_valid"] is False
    assert document.validation_report.checks["table_grids_valid"] is False
    assert document.validation_report.checks["artifact_integrity_valid"] is False
    assert document.validation_report.metrics["dangling_reference_count"] >= 1
    assert document.validation_report.metrics["invalid_table_grid_count"] >= 1
    assert document.validation_report.metrics["artifact_integrity_error_count"] == 1
