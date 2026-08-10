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
from esg_v2.storage.package_layout import create_package_root
from esg_v2.storage.package_validator import validate_package
from esg_v2.workflows.ocr_workflow import OcrWorkflow


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
