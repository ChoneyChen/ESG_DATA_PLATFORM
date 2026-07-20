from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from esg_v2.document.contracts import ArtifactRef, CoordinateSystem
from esg_v2.document.coordinate_mapper import CoordinateMapper
from esg_v2.utils.hashing import sha256_file
from esg_v2.storage.package_layout import page_stem


@dataclass(frozen=True)
class RenderedPage:
    page_index: int
    width_points: float
    height_points: float
    width_pixels: int
    height_pixels: int
    image_path: Path
    canonical_coordinate_system: CoordinateSystem
    image_coordinate_system: CoordinateSystem
    artifact: ArtifactRef


@dataclass(frozen=True)
class RenderedDocument:
    pdf_path: Path
    dpi: int
    pages: list[RenderedPage]
    errors: list[str]


class PageRenderer:
    def render(self, pdf_path: str | None, output_dir: Path, *, dpi: int = 144) -> RenderedDocument:
        if not pdf_path:
            return RenderedDocument(Path("."), dpi, [], ["pdf_path_not_provided"])
        path = Path(pdf_path)
        if not path.exists():
            return RenderedDocument(path, dpi, [], ["pdf_path_not_found"])

        try:
            import pypdfium2 as pdfium  # type: ignore
        except Exception as exc:
            return RenderedDocument(path, dpi, [], [f"pypdfium2_unavailable: {exc}"])

        output_dir.mkdir(parents=True, exist_ok=True)
        pages: list[RenderedPage] = []
        errors: list[str] = []
        pdf = None
        try:
            pdf = pdfium.PdfDocument(str(path))
            scale = dpi / 72.0
            for page_index in range(len(pdf)):
                try:
                    page = pdf[page_index]
                    width_points, height_points = page.get_size()
                    bitmap = page.render(scale=scale, rotation=0)
                    image = bitmap.to_pil()
                    image_path = output_dir / f"{page_stem(page_index)}.png"
                    image.save(image_path, format="PNG", optimize=True)
                    canonical = CoordinateMapper.canonical_system(page_index, width_points, height_points)
                    image_system = CoordinateMapper.image_system(
                        page_index,
                        image.width,
                        image.height,
                        dpi,
                        canonical.coordinate_system_id,
                    )
                    artifact = ArtifactRef(
                        artifact_id=f"artifact-page-image-p{page_index + 1:04d}",
                        kind="page_image",
                        path=str(image_path.resolve()),
                        media_type="image/png",
                        page_index=page_index,
                        sha256=sha256_file(image_path),
                        source="PageRenderer/pypdfium2",
                    )
                    pages.append(
                        RenderedPage(
                            page_index=page_index,
                            width_points=width_points,
                            height_points=height_points,
                            width_pixels=image.width,
                            height_pixels=image.height,
                            image_path=image_path.resolve(),
                            canonical_coordinate_system=canonical,
                            image_coordinate_system=image_system,
                            artifact=artifact,
                        )
                    )
                except Exception as exc:
                    errors.append(f"page_{page_index + 1}_render_failed: {exc}")
        except Exception as exc:
            errors.append(f"pdf_render_open_failed: {exc}")
        finally:
            if pdf is not None:
                pdf.close()
        return RenderedDocument(path, dpi, pages, errors)
