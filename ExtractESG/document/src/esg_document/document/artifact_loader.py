from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esg_document.storage.package_layout import (
    package_dir,
    page_stem,
    resolve_package_path,
)
from esg_document.storage.package_validator import validate_package


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
    # {mode: "targeted", source_page_count, page_indices, selector} for sparse runs.
    page_coverage: dict[str, Any] | None = None

    @property
    def covered_page_indices(self) -> list[int] | None:
        """Original page indices the OCR run covers; None means every source page."""
        return list(self.page_coverage["page_indices"]) if self.page_coverage else None


def targeted_page_coverage(manifest: dict[str, Any]) -> dict[str, Any] | None:
    """Return the OCR manifest's sparse coverage, or None for whole-document packages."""
    selection = manifest.get("page_selection")
    if not isinstance(selection, dict) or selection.get("mode") != "targeted":
        return None
    page_indices = [int(index) for index in selection.get("page_indices") or []]
    if (
        not page_indices
        or any(left >= right for left, right in zip(page_indices, page_indices[1:]))
        or page_indices[0] < 0
    ):
        raise ValueError(
            "OCR page_selection.page_indices must be a sorted, unique, non-empty list"
        )
    source_page_count = int(selection.get("source_page_count") or 0)
    if source_page_count <= page_indices[-1]:
        raise ValueError(
            "OCR page_selection.source_page_count does not cover its page_indices"
        )
    selector = (
        selection.get("selector") if isinstance(selection.get("selector"), dict) else {}
    )
    coverage = {
        "mode": "targeted",
        "source_page_count": source_page_count,
        "page_indices": page_indices,
        "selector": {
            key: selector[key]
            for key in ("name", "version", "reason", "text_source", "parameters")
            if key in selector
        },
    }
    text_layer = manifest.get("text_layer")
    if isinstance(text_layer, dict):
        # How the covered pages were read: PDF text layer, on-device OCR of
        # empty title regions, or visual OCR.
        coverage["reading"] = {
            "text_layer_pages": len(text_layer.get("text_layer_pages") or []),
            "visual_ocr_pages": len(text_layer.get("visual_ocr_pages") or []),
            "on_device_ocr_pages": len(text_layer.get("on_device_ocr_pages") or []),
            "layout_model": text_layer.get("layout_model"),
        }
    return coverage


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
        raw_jsonl_path = resolve_package_path(
            root, manifest["entrypoints"]["provider_result"]
        )

        artifact_rows = self._load_artifact_rows(root, manifest)
        page_coverage = targeted_page_coverage(manifest)
        pages = self._load_pages(
            root,
            raw_jsonl_path,
            manifest,
            artifact_rows,
            page_indices=page_coverage["page_indices"] if page_coverage else None,
        )
        return OcrRunArtifact(
            run_id=ocr_run_id,
            root_dir=root,
            manifest_path=manifest_path,
            manifest=manifest,
            raw_jsonl_path=raw_jsonl_path,
            pages=pages,
            page_coverage=page_coverage,
        )

    def _load_pages(
        self,
        root: Path,
        raw_jsonl_path: Path,
        manifest: dict[str, Any],
        artifact_rows: list[dict[str, Any]],
        *,
        page_indices: list[int] | None = None,
    ) -> list[OcrPageArtifact]:
        pages: list[OcrPageArtifact] = []
        sequence = 0

        for line_number, raw_line in enumerate(
            raw_jsonl_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            result = payload.get("result") or {}
            layout_results = result.get("layoutParsingResults") or []
            for raw_result_index, raw_result in enumerate(layout_results):
                # The writer used the same position → original page mapping; any
                # disagreement would silently join OCR text to the wrong page.
                if page_indices is not None and sequence >= len(page_indices):
                    raise ValueError(
                        f"OCR result holds more pages than its {len(page_indices)} selected source pages"
                    )
                page_index = (
                    page_indices[sequence] if page_indices is not None else sequence
                )
                markdown = raw_result.get("markdown") or {}
                markdown_text = markdown.get("text") or ""
                markdown_images = markdown.get("images") or {}
                output_images = raw_result.get("outputImages") or {}
                local_markdown_images = self._local_images(
                    root, artifact_rows, page_index, "markdown-image"
                )
                local_output_images = self._local_images(
                    root, artifact_rows, page_index, "layout-visualization"
                )
                markdown_path = (
                    root / "content" / "pages" / f"{page_stem(page_index)}.md"
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
                sequence += 1

        if page_indices is not None and sequence != len(page_indices):
            raise ValueError(
                f"OCR result holds {sequence} pages for {len(page_indices)} selected source pages"
            )
        return pages

    @staticmethod
    def _load_artifact_rows(
        root: Path, manifest: dict[str, Any]
    ) -> list[dict[str, Any]]:
        path = resolve_package_path(root, manifest["entrypoints"]["artifacts"])
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
