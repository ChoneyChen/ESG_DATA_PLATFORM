from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from esg_v2.document.artifact_loader import OcrArtifactLoader
from esg_v2.contracts import OcrJobState, OcrRunRequest
from esg_v2.ocr.client import PaddleOcrVlClient
from esg_v2.ocr.output_writer import OcrOutputWriter
from esg_v2.ocr.pdf_preflight import PdfCanvasPreflight
from esg_v2.storage.package_layout import create_package_root
from esg_v2.storage.package_validator import validate_package
from esg_v2.workflows.ocr_workflow import OcrWorkflow
from esg_v2.config import Settings
from esg_v2.utils.hashing import sha256_file

from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject


class DownloadClient:
    def __init__(self):
        buffer = BytesIO()
        Image.new("RGB", (12, 12), "white").save(buffer, format="JPEG")
        self.image = buffer.getvalue()

    def download_bytes(self, url: str) -> bytes:
        return self.image


def test_paddle_client_ignores_environment_proxy_by_default() -> None:
    direct_client = PaddleOcrVlClient("https://provider.test/jobs", "token")
    proxied_client = PaddleOcrVlClient(
        "https://provider.test/jobs",
        "token",
        trust_environment_proxy=True,
    )

    assert direct_client.session.trust_env is False
    assert proxied_client.session.trust_env is True


def test_ocr_package_uses_portable_paths_and_one_based_page_names(tmp_path: Path) -> None:
    root = tmp_path / "ocr-20260718T120000Z-0123456789ab"
    writer = OcrOutputWriter(root)
    writer.write_request({"file_path": str(tmp_path / "report.pdf"), "token": "secret-token"})
    writer.write_submit_response({"data": {"jobId": "job-1"}})
    writer.append_poll_event({"data": {"state": "done"}})
    page = {
        "prunedResult": {"page_index": 0, "width": 100, "height": 100, "parsing_res_list": []},
        "markdown": {
            "text": '<p>Metric</p><img src="imgs/provider-image.jpg">',
            "images": {
                "imgs/provider-image.jpg": "https://provider.test/image?authorization=secret"
            },
        },
        "outputImages": {"layout_det_res": "https://provider.test/layout"},
    }
    raw = json.dumps({"result": {"layoutParsingResults": [page]}}, ensure_ascii=False) + "\n"
    writer.save_result_jsonl(raw)
    markdown_files, downloaded_files, artifacts, page_count = writer.extract_artifacts_from_jsonl(
        jsonl_text=raw,
        client=DownloadClient(),
    )
    manifest = writer.write_manifest(
        {
            "run_id": root.name,
            "job_id": "job-1",
            "model": "PaddleOCR-VL-1.6",
            "source": {"kind": "local-file", "display_name": "report.pdf", "sha256": "a" * 64},
            "optional_payload": {},
            "page_count": page_count,
            "artifact_count": len(artifacts),
        }
    )

    assert page_count == 1
    assert markdown_files == [root / "content" / "pages" / "page-0001.md"]
    assert len(downloaded_files) == 2
    assert (root / "observations" / "pages" / "page-0001.json").exists()
    assert (root / "artifacts" / "images" / "page-0001" / "image-0001.jpg").exists()
    assert (root / "artifacts" / "layouts" / "page-0001.jpg").exists()
    markdown = markdown_files[0].read_text(encoding="utf-8")
    assert "../../artifacts/images/page-0001/image-0001.jpg" in markdown
    assert writer.layout.provider_result.read_text(encoding="utf-8") == raw
    request_snapshot = json.loads(writer.layout.source_request.read_text(encoding="utf-8"))
    assert request_snapshot["file_path"] == f"runtime-upload://{root.name}/report.pdf"
    assert request_snapshot["token"] == "***redacted***"
    observation = json.loads((root / "observations" / "pages" / "page-0001.json").read_text(encoding="utf-8"))
    assert observation["provider_result"]["markdown"]["images"]["imgs/provider-image.jpg"] == "https://provider.test/image"
    assert "authorization=secret" in writer.layout.provider_result.read_text(encoding="utf-8")
    assert str(tmp_path) not in manifest.read_text(encoding="utf-8")
    assert json.loads(manifest.read_text(encoding="utf-8"))["package_schema_version"] == "ocr-package-v1"
    file_index = json.loads((root / "integrity" / "files.json").read_text(encoding="utf-8"))
    assert any(item["path"] == "manifest.json" for item in file_index["files"])

    loaded = OcrArtifactLoader(tmp_path).load(root.name)
    assert loaded.pages[0].page_index == 0
    assert loaded.pages[0].markdown_path == root / "content" / "pages" / "page-0001.md"
    assert loaded.pages[0].local_markdown_images["imgs/provider-image.jpg"].name == "image-0001.jpg"

    validation = validate_package(
        root,
        expected_type="ocr-run",
        expected_schema="ocr-package-v1",
        required_entrypoints={
            "source_request",
            "provider_submit_response",
            "provider_poll_events",
            "provider_result",
            "page_index",
            "artifacts",
            "integrity",
        },
    )
    assert validation.valid is True


def test_ocr_package_integrity_detects_tampering(tmp_path: Path) -> None:
    root = tmp_path / "ocr-20260718T120000Z-0123456789ab"
    writer = OcrOutputWriter(root)
    writer.write_request({"file_path": "report.pdf"})
    writer.write_submit_response({"data": {"jobId": "job-1"}})
    writer.append_poll_event({"data": {"state": "done"}})
    writer.save_result_jsonl('{"result":{"layoutParsingResults":[]}}\n')
    writer.extract_artifacts_from_jsonl(
        jsonl_text='{ "result": {"layoutParsingResults": []} }\n',
        client=DownloadClient(),
    )
    writer.write_manifest(
        {
            "run_id": root.name,
            "job_id": "job-1",
            "model": "PaddleOCR-VL-1.6",
            "source": {"kind": "local-file", "display_name": "report.pdf", "sha256": "a" * 64},
            "optional_payload": {},
            "page_count": 0,
            "artifact_count": 0,
        }
    )
    writer.layout.source_request.write_text('{"tampered":true}', encoding="utf-8")

    validation = validate_package(
        root,
        expected_type="ocr-run",
        expected_schema="ocr-package-v1",
        required_entrypoints={"source_request", "provider_result", "integrity"},
    )

    assert validation.valid is False
    assert any("sha256 mismatch" in error for error in validation.errors)


def test_package_root_reservation_prevents_overwriting_an_existing_run(tmp_path: Path) -> None:
    root = tmp_path / "ocr-20260718T120000Z-0123456789ab"
    create_package_root(root)
    (root / "sentinel.txt").write_text("immutable", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_package_root(root)

    assert (root / "sentinel.txt").read_text(encoding="utf-8") == "immutable"


def test_ocr_request_snapshot_sanitizes_signed_source_urls() -> None:
    request = OcrRunRequest(
        file_url="https://provider.test/report.pdf?authorization=temporary-secret",
        token="api-secret",
    )

    snapshot = OcrWorkflow._redacted_request(
        request,
        "ocr-20260718T120000Z-0123456789ab",
    )

    assert snapshot["file_url"] == "https://provider.test/report.pdf"
    assert snapshot["token"] == "***redacted***"

    state = OcrJobState(
        run_id="ocr-20260718T120000Z-0123456789ab",
        status="done",
        message="done",
        output_dir=Path("/tmp/ocr-output"),
        result_json_url="https://provider.test/result.json?authorization=temporary-secret",
    )
    assert state.result_json_url == "https://provider.test/result.json"


def _write_canvas_pdf(path: Path, pages: list[tuple[float, float, int]]) -> None:
    writer = PdfWriter()
    for width, height, rotation in pages:
        page = writer.add_blank_page(width=width, height=height)
        if rotation:
            page.rotate(rotation)
    with path.open("wb") as handle:
        writer.write(handle)


def test_pdf_canvas_preflight_keeps_standard_pdf_as_original_input(tmp_path: Path) -> None:
    source = tmp_path / "standard.pdf"
    provider = tmp_path / "provider.pdf"
    _write_canvas_pdf(source, [(595, 842, 0), (595, 842, 0)])

    prepared = PdfCanvasPreflight(max_canvas_points=1200).prepare(source, provider)

    assert prepared.transformed is False
    assert prepared.provider_path == source.resolve()
    assert prepared.report["status"] == "passthrough"
    assert prepared.report["pages"][0]["source_to_provider"]["uniform_scale"] == 1.0
    assert not provider.exists()


def test_pdf_canvas_preflight_normalizes_large_mixed_and_rotated_pages_without_cropping(tmp_path: Path) -> None:
    source = tmp_path / "mixed.pdf"
    provider = tmp_path / "provider.pdf"
    _write_canvas_pdf(source, [(1920, 1080, 0), (600, 800, 0), (792, 612, 90)])

    prepared = PdfCanvasPreflight(max_canvas_points=1200).prepare(source, provider)
    provider_reader = PdfReader(provider)

    assert prepared.transformed is True
    assert prepared.source_sha256 == sha256_file(source)
    assert prepared.provider_sha256 == sha256_file(provider)
    assert prepared.source_sha256 != prepared.provider_sha256
    assert prepared.report["reasons"] == [
        "oversized_page_canvas",
        "mixed_page_canvas_sizes",
        "rotated_page_canvas",
    ]
    assert len(provider_reader.pages) == 3
    assert max(float(provider_reader.pages[0].cropbox.width), float(provider_reader.pages[0].cropbox.height)) == 1200
    assert prepared.report["pages"][0]["source_to_provider"] == {
        "uniform_scale": 0.625,
        "rotation_transferred_to_content": False,
        "provider_to_source_scale_x": 1.6,
        "provider_to_source_scale_y": 1.6,
        "preserves_aspect_ratio": True,
        "cropped": False,
    }
    assert prepared.report["pages"][2]["provider_canvas"]["rotation_degrees"] == 0
    assert prepared.report["pages"][2]["source_to_provider"]["rotation_transferred_to_content"] is True


def test_pdf_canvas_preflight_rejects_encrypted_pdf_before_cloud_submission(tmp_path: Path) -> None:
    source = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("secret")
    with source.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ValueError, match="Encrypted PDF"):
        PdfCanvasPreflight().prepare(source, tmp_path / "provider.pdf")


def test_pdf_canvas_preflight_canonicalizes_nonzero_and_mismatched_page_boxes(tmp_path: Path) -> None:
    source = tmp_path / "offset-box.pdf"
    provider = tmp_path / "provider.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=800, height=1000)
    page.cropbox = RectangleObject((100, 150, 700, 950))
    with source.open("wb") as handle:
        writer.write(handle)

    prepared = PdfCanvasPreflight(max_canvas_points=1200).prepare(source, provider)
    provider_page = PdfReader(provider).pages[0]

    assert prepared.report["reasons"] == [
        "nonzero_page_box_origin",
        "noncanonical_page_boxes",
    ]
    assert [float(value) for value in provider_page.mediabox] == [0.0, 0.0, 600.0, 800.0]
    assert [float(value) for value in provider_page.cropbox] == [0.0, 0.0, 600.0, 800.0]
    assert prepared.report["pages"][0]["source_to_provider"]["cropped"] is False


def test_ocr_workflow_submits_normalized_provider_pdf_but_keeps_original_source_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "large-canvas.pdf"
    _write_canvas_pdf(source, [(1920, 1080, 0), (600, 800, 0)])
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def submit(self, **kwargs):
            captured["submitted_file"] = kwargs["file_path"]
            return {"data": {"jobId": "job-normalized"}}

        def get_job(self, job_id: str):
            return {
                "data": {
                    "jobId": job_id,
                    "state": "done",
                    "extractProgress": {"extractedPages": 2},
                    "resultUrl": {"jsonUrl": "https://provider.test/result.json"},
                }
            }

        def download_text(self, url: str) -> str:
            pages = [
                {"markdown": {"text": f"page {index + 1}", "images": {}}, "outputImages": {}}
                for index in range(2)
            ]
            return json.dumps({"result": {"layoutParsingResults": pages}}) + "\n"

        def download_bytes(self, url: str) -> bytes:
            raise AssertionError("No images are expected in this fixture")

    monkeypatch.setattr("esg_v2.ocr.providers.PaddleOcrVlClient", FakeClient)
    settings = Settings(
        output_root=tmp_path / "ocr-output",
        document_ir_output_root=tmp_path / "ir-output",
        upload_root=tmp_path / "uploads",
        paddle_token="token",
        ocr_provider_max_canvas_points=1200,
    )
    run_id = "ocr-20260823T040101Z-000000000001"

    result = OcrWorkflow(settings).run(
        OcrRunRequest(file_path=str(source), ocr_provider="paddle_api"),
        run_id=run_id,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert Path(str(captured["submitted_file"])).name == "provider-input.pdf"
    assert manifest["source"]["sha256"] == sha256_file(source)
    assert manifest["provider_input"]["sha256"] != manifest["source"]["sha256"]
    assert manifest["pdf_preflight"]["transformed"] is True
    assert manifest["entrypoints"]["source_preflight"] == "source/preflight.json"
    assert manifest["entrypoints"]["provider_input"] == "source/provider-input.pdf"
    assert manifest["ocr_provider"] == "paddle_api"
    assert manifest["provider_route"]["requested"] == "paddle_api"
    assert result.page_count == 2
