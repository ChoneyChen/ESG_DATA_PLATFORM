from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from esg_v2.ocr.client import PaddleOcrVlClient
from esg_v2.storage.package_layout import (
    OcrPackageLayout,
    page_stem,
    relative_path,
    write_file_index,
    write_json,
)
from esg_v2.storage.package_validator import validate_package
from esg_v2.utils.hashing import sha256_file
from esg_v2.utils.sanitization import sanitize_payload


class OcrOutputWriter:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.layout = OcrPackageLayout(output_dir)
        self.raw_dir = self.layout.provider_result.parent
        self.pages_dir = self.layout.root / "content" / "pages"
        self.images_dir = self.layout.root / "artifacts" / "images"
        self.output_images_dir = self.layout.root / "artifacts" / "layouts"
        for path in (
            self.layout.source_request.parent,
            self.raw_dir,
            self.pages_dir,
            self.images_dir,
            self.output_images_dir,
            self.layout.root / "observations" / "pages",
            self.layout.file_index.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def write_request(self, payload: Any) -> Path:
        safe = sanitize_payload(payload)
        if isinstance(safe, dict):
            safe = dict(safe)
            if safe.get("token"):
                safe["token"] = "***redacted***"
            file_path = safe.get("file_path")
            if isinstance(file_path, str) and "://" not in file_path:
                safe["file_path"] = f"runtime-upload://{self.output_dir.name}/{Path(file_path).name}"
        return write_json(self.layout.source_request, safe)

    def write_submit_response(self, payload: Any) -> Path:
        return write_json(self.layout.submit_response, payload)

    def append_poll_event(self, payload: Any) -> Path:
        path = self.layout.poll_events
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        return path

    def save_result_jsonl(self, text: str) -> Path:
        self.layout.provider_result.write_text(text, encoding="utf-8")
        return self.layout.provider_result

    def extract_artifacts_from_jsonl(
        self,
        *,
        jsonl_text: str,
        client: PaddleOcrVlClient,
    ) -> tuple[list[Path], list[Path], list[dict[str, Any]], int]:
        markdown_files: list[Path] = []
        downloaded_files: list[Path] = []
        artifact_rows: list[dict[str, Any]] = []
        page_rows: list[dict[str, Any]] = []
        page_index = 0

        for line_number, raw_line in enumerate(jsonl_text.splitlines(), start=1):
            if not raw_line.strip():
                continue
            payload = json.loads(raw_line)
            result = payload.get("result") or {}
            layout_results = result.get("layoutParsingResults") or []
            for result_index, page_result in enumerate(layout_results):
                page_id = page_stem(page_index)
                observation_path = self.layout.page_observation(page_index)
                write_json(
                    observation_path,
                    {
                        "schema_version": "ocr-page-observation-v1",
                        "page_id": page_id,
                        "page_index": page_index,
                        "page_number": page_index + 1,
                        "raw_locator": {
                            "provider_result_path": relative_path(self.output_dir, self.layout.provider_result),
                            "line_number": line_number,
                            "result_index": result_index,
                        },
                        # The exact provider payload remains in provider/result.jsonl.
                        # This derived observation is safe to browse and move between machines.
                        "provider_result": sanitize_payload(page_result),
                    },
                )

                markdown_payload = page_result.get("markdown") or {}
                markdown = str(markdown_payload.get("text") or "")
                markdown_path = self.layout.page_markdown(page_index)
                markdown_images = markdown_payload.get("images") or {}
                for image_sequence, (provider_path, image_url) in enumerate(sorted(markdown_images.items()), start=1):
                    suffix = self._safe_image_suffix(provider_path)
                    local_path = self.layout.page_image_dir(page_index) / f"image-{image_sequence:04d}{suffix}"
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(client.download_bytes(image_url))
                    portable_ref = Path(os.path.relpath(local_path, markdown_path.parent)).as_posix()
                    markdown = markdown.replace(str(provider_path), portable_ref)
                    downloaded_files.append(local_path)
                    artifact_rows.append(
                        self._artifact_row(
                            artifact_id=f"ocr-image-p{page_index + 1:04d}-{image_sequence:04d}",
                            kind="markdown-image",
                            page_index=page_index,
                            path=local_path,
                            provider_ref=str(provider_path),
                        )
                    )

                markdown_path.parent.mkdir(parents=True, exist_ok=True)
                markdown_path.write_text(markdown, encoding="utf-8")
                markdown_files.append(markdown_path)

                output_images = page_result.get("outputImages") or {}
                for layout_sequence, (provider_name, image_url) in enumerate(sorted(output_images.items()), start=1):
                    suffix = self._safe_image_suffix(str(provider_name))
                    if len(output_images) == 1:
                        local_path = self.layout.page_layout_image(page_index, suffix)
                    else:
                        local_path = self.output_images_dir / f"{page_id}-{layout_sequence:04d}{suffix}"
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(client.download_bytes(image_url))
                    downloaded_files.append(local_path)
                    artifact_rows.append(
                        self._artifact_row(
                            artifact_id=f"ocr-layout-p{page_index + 1:04d}-{layout_sequence:04d}",
                            kind="layout-visualization",
                            page_index=page_index,
                            path=local_path,
                            provider_ref=str(provider_name),
                        )
                    )

                page_rows.append(
                    {
                        "page_id": page_id,
                        "page_index": page_index,
                        "page_number": page_index + 1,
                        "observation_path": relative_path(self.output_dir, observation_path),
                        "markdown_path": relative_path(self.output_dir, markdown_path),
                        "image_artifact_ids": [
                            item["artifact_id"] for item in artifact_rows if item["page_index"] == page_index
                        ],
                    }
                )
                page_index += 1

        write_json(
            self.layout.root / "observations" / "pages" / "index.json",
            {"schema_version": "ocr-page-index-v1", "page_count": page_index, "pages": page_rows},
        )
        write_json(
            self.layout.artifact_index,
            {"schema_version": "ocr-artifact-index-v1", "artifact_count": len(artifact_rows), "artifacts": artifact_rows},
        )
        return markdown_files, downloaded_files, artifact_rows, page_index

    def write_manifest(self, manifest: dict[str, Any]) -> Path:
        safe_manifest = sanitize_payload(manifest)
        source = safe_manifest.get("source") if isinstance(safe_manifest, dict) else None
        if isinstance(source, dict) and source.get("kind") == "local-file":
            display_name = source.get("display_name")
            if isinstance(display_name, str):
                source["display_name"] = Path(display_name).name
        payload = {
            "package_type": "ocr-run",
            "package_schema_version": "ocr-package-v1",
            **safe_manifest,
            "entrypoints": {
                "source_request": relative_path(self.output_dir, self.layout.source_request),
                "provider_submit_response": relative_path(self.output_dir, self.layout.submit_response),
                "provider_poll_events": relative_path(self.output_dir, self.layout.poll_events),
                "provider_result": relative_path(self.output_dir, self.layout.provider_result),
                "page_index": "observations/pages/index.json",
                "artifacts": relative_path(self.output_dir, self.layout.artifact_index),
                "integrity": relative_path(self.output_dir, self.layout.file_index),
            },
            "written_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(self.layout.manifest, payload)
        write_file_index(self.output_dir, self.layout.file_index)
        validate_package(
            self.output_dir,
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
        ).require_valid()
        return self.layout.manifest

    def _artifact_row(
        self,
        *,
        artifact_id: str,
        kind: str,
        page_index: int,
        path: Path,
        provider_ref: str,
    ) -> dict[str, Any]:
        return {
            "artifact_id": artifact_id,
            "kind": kind,
            "page_index": page_index,
            "page_number": page_index + 1,
            "path": relative_path(self.output_dir, path),
            "provider_ref": provider_ref,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }

    @staticmethod
    def _safe_image_suffix(value: str) -> str:
        suffix = Path(value.split("?", 1)[0]).suffix.lower()
        return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
