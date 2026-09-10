from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

from esg_v2.utils.hashing import sha256_file


@dataclass(frozen=True)
class PreparedPdfInput:
    source_path: Path
    provider_path: Path
    source_sha256: str
    provider_sha256: str
    transformed: bool
    report: dict[str, Any]


class PdfCanvasPreflight:
    """Validate a PDF and build a provider-safe, geometry-preserving copy when needed."""

    def __init__(
        self,
        *,
        max_canvas_points: float = 1200.0,
        max_file_bytes: int = 100 * 1024 * 1024,
        max_pages: int = 1000,
        normalization_enabled: bool = True,
    ):
        if max_canvas_points <= 0:
            raise ValueError("max_canvas_points must be positive")
        self.max_canvas_points = float(max_canvas_points)
        self.max_file_bytes = int(max_file_bytes)
        self.max_pages = int(max_pages)
        self.normalization_enabled = normalization_enabled

    def prepare(self, source_path: str | Path, provider_output_path: str | Path) -> PreparedPdfInput:
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"PDF file not found: {source}")
        if source.stat().st_size > self.max_file_bytes:
            raise ValueError(
                "Local PDF exceeds the configured PaddleOCR-VL upload limit: "
                f"{source.stat().st_size} > {self.max_file_bytes} bytes"
            )

        source_sha = sha256_file(source)
        reader = PdfReader(str(source), strict=False)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF is not supported by the OCR input preflight")
        page_count = len(reader.pages)
        if page_count < 1:
            raise ValueError("PDF contains no pages")
        if page_count > self.max_pages:
            raise ValueError(f"PDF has {page_count} pages; configured provider limit is {self.max_pages}")

        raw_bytes = source.read_bytes()
        eof_markers = raw_bytes.count(b"%%EOF")
        has_acroform = bool(reader.root_object.get("/AcroForm"))
        pages = [self._inspect_page(page, index) for index, page in enumerate(reader.pages)]
        self._validate_rendering(source, expected_pages=page_count)

        reasons = self._normalization_reasons(pages, eof_markers=eof_markers, has_acroform=has_acroform)
        transformed = self.normalization_enabled and bool(reasons)
        provider_path = Path(provider_output_path).resolve() if transformed else source
        if transformed:
            provider_path.parent.mkdir(parents=True, exist_ok=True)
            pages = self._write_provider_pdf(reader, pages, provider_path)
            self._validate_rendering(provider_path, expected_pages=page_count)
        provider_sha = sha256_file(provider_path)

        report = {
            "schema_version": "ocr-pdf-canvas-preflight-v1",
            "status": "normalized" if transformed else "passthrough",
            "normalization_enabled": self.normalization_enabled,
            "strategy": "per_page_uniform_scale_and_structural_rewrite" if transformed else "original_pdf",
            "reasons": reasons,
            "limits": {
                "max_canvas_points": self.max_canvas_points,
                "max_file_bytes": self.max_file_bytes,
                "max_pages": self.max_pages,
            },
            "source": {
                "display_name": source.name,
                "sha256": source_sha,
                "size_bytes": source.stat().st_size,
                "page_count": page_count,
                "encrypted": False,
                "eof_marker_count": eof_markers,
                "incremental_update_count": max(0, eof_markers - 1),
                "has_acroform": has_acroform,
                "local_render_validation": "passed",
            },
            "provider_input": {
                "reference": "source/provider-input.pdf" if transformed else "original-upload",
                "sha256": provider_sha,
                "size_bytes": provider_path.stat().st_size,
                "transformed": transformed,
                "local_render_validation": "passed",
            },
            "pages": pages,
        }
        return PreparedPdfInput(source, provider_path, source_sha, provider_sha, transformed, report)

    def _normalization_reasons(
        self,
        pages: list[dict[str, Any]],
        *,
        eof_markers: int,
        has_acroform: bool,
    ) -> list[str]:
        reasons: list[str] = []
        if any(
            max(
                item["source_canvas"]["display_width_points"],
                item["source_canvas"]["display_height_points"],
            )
            > self.max_canvas_points
            for item in pages
        ):
            reasons.append("oversized_page_canvas")
        canvas_shapes = {
            (
                round(float(item["source_canvas"]["display_width_points"]), 3),
                round(float(item["source_canvas"]["display_height_points"]), 3),
            )
            for item in pages
        }
        if len(canvas_shapes) > 1:
            reasons.append("mixed_page_canvas_sizes")
        if any(int(item["source_canvas"]["rotation_degrees"]) % 360 for item in pages):
            reasons.append("rotated_page_canvas")
        if any(bool(item["source_canvas"]["nonzero_box_origin"]) for item in pages):
            reasons.append("nonzero_page_box_origin")
        if any(bool(item["source_canvas"]["page_box_mismatch"]) for item in pages):
            reasons.append("noncanonical_page_boxes")
        if eof_markers > 1:
            reasons.append("incremental_pdf_updates")
        if has_acroform:
            reasons.append("interactive_form_catalog")
        return reasons

    def _write_provider_pdf(
        self,
        reader: PdfReader,
        page_rows: list[dict[str, Any]],
        output_path: Path,
    ) -> list[dict[str, Any]]:
        writer = PdfWriter()
        normalized_rows: list[dict[str, Any]] = []
        for index, source_page in enumerate(reader.pages):
            writer.add_page(source_page)
            page = writer.pages[-1]
            source_row = page_rows[index]
            rotation = int(page.get("/Rotate", 0) or 0) % 360
            if rotation:
                page.transfer_rotation_to_content()
            crop = page.cropbox
            crop_left = float(crop.left)
            crop_bottom = float(crop.bottom)
            crop_width = float(crop.width)
            crop_height = float(crop.height)
            if abs(crop_left) > 1e-6 or abs(crop_bottom) > 1e-6:
                page.add_transformation(Transformation().translate(tx=-crop_left, ty=-crop_bottom))
            canonical_box = RectangleObject((0, 0, crop_width, crop_height))
            page.mediabox = canonical_box
            page.cropbox = RectangleObject(canonical_box)
            page.trimbox = RectangleObject(canonical_box)
            page.bleedbox = RectangleObject(canonical_box)
            page.artbox = RectangleObject(canonical_box)
            display_width = float(source_row["source_canvas"]["display_width_points"])
            display_height = float(source_row["source_canvas"]["display_height_points"])
            scale = min(1.0, self.max_canvas_points / max(display_width, display_height))
            if scale < 1.0:
                page.scale_by(scale)
            provider_width = float(page.cropbox.width)
            provider_height = float(page.cropbox.height)
            normalized = dict(source_row)
            normalized["provider_canvas"] = {
                "width_points": round(provider_width, 6),
                "height_points": round(provider_height, 6),
                "rotation_degrees": int(page.get("/Rotate", 0) or 0) % 360,
            }
            normalized["source_to_provider"] = {
                "uniform_scale": round(scale, 12),
                "rotation_transferred_to_content": bool(rotation),
                "provider_to_source_scale_x": round(display_width / provider_width, 12),
                "provider_to_source_scale_y": round(display_height / provider_height, 12),
                "preserves_aspect_ratio": True,
                "cropped": False,
            }
            normalized_rows.append(normalized)
        if reader.metadata:
            writer.add_metadata({str(key): str(value) for key, value in reader.metadata.items() if value is not None})
        with output_path.open("wb") as handle:
            writer.write(handle)
        return normalized_rows

    @staticmethod
    def _inspect_page(page: Any, page_index: int) -> dict[str, Any]:
        media = page.mediabox
        crop = page.cropbox
        width = float(crop.width)
        height = float(crop.height)
        if width <= 0 or height <= 0:
            raise ValueError(f"PDF page {page_index + 1} has an invalid page box: {width} x {height}")
        rotation = int(page.get("/Rotate", 0) or 0) % 360
        if rotation not in {0, 90, 180, 270}:
            raise ValueError(f"PDF page {page_index + 1} has unsupported rotation: {rotation}")
        display_width, display_height = (height, width) if rotation in {90, 270} else (width, height)
        origins = [float(media.left), float(media.bottom), float(crop.left), float(crop.bottom)]
        page_box_mismatch = any(
            abs(left - right) > 1e-6
            for left, right in zip(
                (float(media.left), float(media.bottom), float(media.right), float(media.top)),
                (float(crop.left), float(crop.bottom), float(crop.right), float(crop.top)),
            )
        )
        return {
            "page_index": page_index,
            "page_number": page_index + 1,
            "source_canvas": {
                "media_box": [float(media.left), float(media.bottom), float(media.right), float(media.top)],
                "crop_box": [float(crop.left), float(crop.bottom), float(crop.right), float(crop.top)],
                "display_width_points": round(display_width, 6),
                "display_height_points": round(display_height, 6),
                "rotation_degrees": rotation,
                "nonzero_box_origin": any(abs(value) > 1e-6 for value in origins),
                "page_box_mismatch": page_box_mismatch,
            },
            "provider_canvas": {
                "width_points": round(display_width, 6),
                "height_points": round(display_height, 6),
                "rotation_degrees": rotation,
            },
            "source_to_provider": {
                "uniform_scale": 1.0,
                "rotation_transferred_to_content": False,
                "provider_to_source_scale_x": 1.0,
                "provider_to_source_scale_y": 1.0,
                "preserves_aspect_ratio": True,
                "cropped": False,
            },
        }

    @staticmethod
    def _validate_rendering(path: Path, *, expected_pages: int) -> None:
        try:
            document = pdfium.PdfDocument(str(path))
        except Exception as exc:
            raise ValueError(f"PDF cannot be opened by the local page renderer: {exc}") from exc
        if len(document) != expected_pages:
            raise ValueError(f"PDF page count changed during preflight: {len(document)} != {expected_pages}")
        failures = []
        for page_index in range(len(document)):
            page = None
            bitmap = None
            try:
                page = document[page_index]
                width, height = page.get_size()
                scale = max(0.02, min(0.2, 160.0 / max(float(width), float(height))))
                bitmap = page.render(scale=scale)
                if bitmap.width < 1 or bitmap.height < 1:
                    raise ValueError("empty rendered bitmap")
            except Exception as exc:
                failures.append(f"page {page_index + 1}: {exc}")
            finally:
                if bitmap is not None:
                    bitmap.close()
                if page is not None:
                    page.close()
        document.close()
        if failures:
            raise ValueError("PDF contains locally unrenderable pages: " + "; ".join(failures[:10]))
