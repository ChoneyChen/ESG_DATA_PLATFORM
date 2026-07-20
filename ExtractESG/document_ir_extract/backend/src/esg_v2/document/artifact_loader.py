from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esg_v2.storage.package_layout import package_dir, page_stem, resolve_package_path
from esg_v2.storage.package_validator import validate_package


@dataclass(frozen=True)
class OcrPageArtifact:
    page_index: int
    raw_line_number: int
    raw_result_index: int
    raw_result: dict[str, Any]
    markdown_path: Path | None
    markdown_text: str
    markdown_images: dict[str, str]
    output_images: dict[str, str]
    local_markdown_images: dict[str, Path]
    local_output_images: dict[str, Path]


@dataclass(frozen=True)
class OcrRunArtifact:
    run_id: str
    root_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]
    raw_jsonl_path: Path
    pages: list[OcrPageArtifact]


class OcrArtifactLoader:
    def __init__(self, ocr_output_root: Path):
        self.ocr_output_root = ocr_output_root

    def load(self, ocr_run_id: str) -> OcrRunArtifact:
        root = package_dir(self.ocr_output_root, ocr_run_id)
        if not root.exists():
            raise FileNotFoundError(f"OCR run not found: {ocr_run_id}")

        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"OCR manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        if manifest.get("package_schema_version") == "ocr-package-v1":
            validate_package(
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
            ).require_valid()

        entrypoints = manifest.get("entrypoints") or {}
        raw_entrypoint = entrypoints.get("provider_result") if isinstance(entrypoints, dict) else None
        raw_jsonl_path = resolve_package_path(root, str(raw_entrypoint)) if raw_entrypoint else root / "raw" / "result.jsonl"
        if not raw_jsonl_path.exists() and manifest.get("raw_jsonl_path"):
            raw_jsonl_path = Path(str(manifest["raw_jsonl_path"]))
        if not raw_jsonl_path.exists():
            raise FileNotFoundError(f"OCR result JSONL not found: {raw_jsonl_path}")

        artifact_rows = self._load_artifact_rows(root, manifest)
        pages = self._load_pages(root, raw_jsonl_path, manifest, artifact_rows)
        return OcrRunArtifact(
            run_id=ocr_run_id,
            root_dir=root,
            manifest_path=manifest_path,
            manifest=manifest,
            raw_jsonl_path=raw_jsonl_path,
            pages=pages,
        )

    def _load_pages(
        self,
        root: Path,
        raw_jsonl_path: Path,
        manifest: dict[str, Any],
        artifact_rows: list[dict[str, Any]],
    ) -> list[OcrPageArtifact]:
        pages: list[OcrPageArtifact] = []
        page_index = 0
        is_package_v1 = manifest.get("package_schema_version") == "ocr-package-v1"

        for line_number, raw_line in enumerate(raw_jsonl_path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            result = payload.get("result") or {}
            layout_results = result.get("layoutParsingResults") or []
            for raw_result_index, raw_result in enumerate(layout_results):
                markdown = raw_result.get("markdown") or {}
                markdown_text = markdown.get("text") or ""
                markdown_images = markdown.get("images") or {}
                output_images = raw_result.get("outputImages") or {}
                local_markdown_images = self._local_images(root, artifact_rows, page_index, "markdown-image")
                local_output_images = self._local_images(root, artifact_rows, page_index, "layout-visualization")
                markdown_path = (
                    root / "content" / "pages" / f"{page_stem(page_index)}.md"
                    if is_package_v1
                    else root / "pages" / f"page_{page_index:04d}.md"
                )
                if markdown_path.exists():
                    markdown_text = markdown_path.read_text(encoding="utf-8")
                else:
                    markdown_path = None
                pages.append(
                    OcrPageArtifact(
                        page_index=page_index,
                        raw_line_number=line_number,
                        raw_result_index=raw_result_index,
                        raw_result=raw_result,
                        markdown_path=markdown_path,
                        markdown_text=markdown_text,
                        markdown_images=markdown_images,
                        output_images=output_images,
                        local_markdown_images=local_markdown_images,
                        local_output_images=local_output_images,
                    )
                )
                page_index += 1

        return pages

    @staticmethod
    def _load_artifact_rows(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        entrypoints = manifest.get("entrypoints") or {}
        relative = entrypoints.get("artifacts") if isinstance(entrypoints, dict) else None
        if not relative:
            return []
        path = resolve_package_path(root, str(relative))
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("artifacts") or []) if isinstance(payload, dict) else []

    @staticmethod
    def _local_images(
        root: Path,
        artifact_rows: list[dict[str, Any]],
        page_index: int,
        kind: str,
    ) -> dict[str, Path]:
        result = {}
        for row in artifact_rows:
            if row.get("page_index") != page_index or row.get("kind") != kind:
                continue
            provider_ref = row.get("provider_ref")
            relative = row.get("path")
            if isinstance(provider_ref, str) and isinstance(relative, str):
                result[provider_ref] = resolve_package_path(root, relative)
        return result
