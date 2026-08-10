from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from esg_v2.document.contracts import ArtifactRef, CoordinateSystem
from esg_v2.document.coordinate_mapper import CoordinateMapper
from esg_v2.utils.hashing import sha256_file
from esg_v2.storage.package_layout import page_stem


RenderProgress = Callable[[int, int], None]
RENDER_WORKER_SCHEMA = "esg-pdf-render-worker-v1"


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
    """Runs PDFium outside the API process and reconstructs its typed result."""

    def __init__(self, *, timeout_seconds: float = 1800):
        self.timeout_seconds = max(1.0, timeout_seconds)

    def render(
        self,
        pdf_path: str | None,
        output_dir: Path,
        *,
        dpi: int = 144,
        progress: RenderProgress | None = None,
    ) -> RenderedDocument:
        if not pdf_path:
            return RenderedDocument(Path("."), dpi, [], ["pdf_path_not_provided"])
        path = Path(pdf_path)
        if not path.exists():
            return RenderedDocument(path, dpi, [], ["pdf_path_not_found"])

        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / ".render-result.json"
        status_path = output_dir / ".render-status.json"
        for transient in (result_path, status_path):
            transient.unlink(missing_ok=True)

        command = [
            sys.executable,
            "-m",
            "esg_v2.document.page_renderer",
            "--worker",
            "--pdf",
            str(path.resolve()),
            "--output-dir",
            str(output_dir.resolve()),
            "--dpi",
            str(dpi),
            "--result",
            str(result_path.resolve()),
            "--status",
            str(status_path.resolve()),
        ]
        last_progress: tuple[int, int] | None = None
        started_at = time.monotonic()
        worker_log = ""
        process: subprocess.Popen[str] | None = None
        try:
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                while process.poll() is None:
                    current = self._read_status(status_path)
                    if current and progress:
                        progress_value = (
                            int(current.get("rendered_pages") or 0),
                            int(current.get("total_pages") or 0),
                        )
                        if progress_value[1] > 0 and progress_value != last_progress:
                            progress(*progress_value)
                            last_progress = progress_value
                    if time.monotonic() - started_at > self.timeout_seconds:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                        raise TimeoutError(
                            f"PDF render worker exceeded {self.timeout_seconds:g} seconds"
                        )
                    time.sleep(0.2)
                log_file.flush()
                log_file.seek(0)
                worker_log = log_file.read().strip()
                if process.returncode != 0:
                    signal_text = (
                        f"signal {-process.returncode}"
                        if process.returncode < 0
                        else f"exit code {process.returncode}"
                    )
                    detail = f": {worker_log[-2000:]}" if worker_log else ""
                    raise RuntimeError(
                        f"PDF render worker failed with {signal_text}{detail}"
                    )
            if not result_path.exists():
                raise RuntimeError("PDF render worker produced no result contract")
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            rendered = self._deserialize(path, dpi, payload)
            final_progress = (len(rendered.pages), len(rendered.pages))
            if progress and rendered.pages and last_progress != final_progress:
                progress(*final_progress)
            return rendered
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for transient in (
                result_path,
                status_path,
                result_path.with_name(f"{result_path.name}.tmp"),
                status_path.with_name(f"{status_path.name}.tmp"),
            ):
                transient.unlink(missing_ok=True)

    @staticmethod
    def _read_status(status_path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _deserialize(path: Path, dpi: int, payload: dict[str, Any]) -> RenderedDocument:
        if payload.get("schema_version") != RENDER_WORKER_SCHEMA:
            raise ValueError("Unsupported PDF render worker result schema")
        pages = [
            RenderedPage(
                page_index=int(item["page_index"]),
                width_points=float(item["width_points"]),
                height_points=float(item["height_points"]),
                width_pixels=int(item["width_pixels"]),
                height_pixels=int(item["height_pixels"]),
                image_path=Path(item["image_path"]),
                canonical_coordinate_system=CoordinateSystem.model_validate(
                    item["canonical_coordinate_system"]
                ),
                image_coordinate_system=CoordinateSystem.model_validate(
                    item["image_coordinate_system"]
                ),
                artifact=ArtifactRef.model_validate(item["artifact"]),
            )
            for item in payload.get("pages", [])
        ]
        return RenderedDocument(
            pdf_path=path,
            dpi=dpi,
            pages=pages,
            errors=[str(item) for item in payload.get("errors", [])],
        )


def _render_in_worker(
    pdf_path: Path,
    output_dir: Path,
    *,
    dpi: int,
    result_path: Path,
    status_path: Path,
) -> None:
    try:
        import pypdfium2 as pdfium  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"pypdfium2_unavailable: {exc}") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    payload_pages: list[dict[str, Any]] = []
    errors: list[str] = []
    pdf = None
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
        total_pages = len(pdf)
        _write_json_atomic(
            status_path,
            {"total_pages": total_pages, "rendered_pages": 0},
        )
        scale = dpi / 72.0
        for page_index in range(total_pages):
            page = None
            bitmap = None
            image = None
            try:
                page = pdf[page_index]
                width_points, height_points = page.get_size()
                bitmap = page.render(scale=scale, rotation=0)
                image = bitmap.to_pil()
                width_pixels, height_pixels = image.size
                image_path = output_dir / f"{page_stem(page_index)}.png"
                image.save(image_path, format="PNG", optimize=True)
                canonical = CoordinateMapper.canonical_system(
                    page_index,
                    width_points,
                    height_points,
                )
                image_system = CoordinateMapper.image_system(
                    page_index,
                    width_pixels,
                    height_pixels,
                    dpi,
                    canonical.coordinate_system_id,
                )
                artifact = ArtifactRef(
                    artifact_id=f"artifact-page-image-p{page_index + 1:04d}",
                    kind="page_image",
                    path=str(image_path.resolve()),
                    media_type="image/png",
                    page_index=page_index,
                    width_pixels=width_pixels,
                    height_pixels=height_pixels,
                    sha256=sha256_file(image_path),
                    source="PageRenderer/pypdfium2-isolated-worker",
                )
                rendered_page = RenderedPage(
                    page_index=page_index,
                    width_points=width_points,
                    height_points=height_points,
                    width_pixels=width_pixels,
                    height_pixels=height_pixels,
                    image_path=image_path.resolve(),
                    canonical_coordinate_system=canonical,
                    image_coordinate_system=image_system,
                    artifact=artifact,
                )
                payload_pages.append(_serialize_page(rendered_page))
            except Exception as exc:
                errors.append(f"page_{page_index + 1}_render_failed: {exc}")
            finally:
                if image is not None:
                    image.close()
                if bitmap is not None:
                    bitmap.close()
                if page is not None:
                    page.close()
            _write_json_atomic(
                status_path,
                {
                    "total_pages": total_pages,
                    "rendered_pages": page_index + 1,
                },
            )
    except Exception as exc:
        errors.append(f"pdf_render_open_failed: {exc}")
    finally:
        if pdf is not None:
            pdf.close()

    _write_json_atomic(
        result_path,
        {
            "schema_version": RENDER_WORKER_SCHEMA,
            "pdf_path": str(pdf_path.resolve()),
            "dpi": dpi,
            "pages": payload_pages,
            "errors": errors,
        },
    )


def _serialize_page(page: RenderedPage) -> dict[str, Any]:
    return {
        "page_index": page.page_index,
        "width_points": page.width_points,
        "height_points": page.height_points,
        "width_pixels": page.width_pixels,
        "height_pixels": page.height_pixels,
        "image_path": str(page.image_path),
        "canonical_coordinate_system": page.canonical_coordinate_system.model_dump(
            mode="json"
        ),
        "image_coordinate_system": page.image_coordinate_system.model_dump(
            mode="json"
        ),
        "artifact": page.artifact.model_dump(mode="json"),
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _worker_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dpi", required=True, type=int)
    parser.add_argument("--result", required=True)
    parser.add_argument("--status", required=True)
    args = parser.parse_args()
    if not args.worker:
        parser.error("--worker is required")
    _render_in_worker(
        Path(args.pdf),
        Path(args.output_dir),
        dpi=args.dpi,
        result_path=Path(args.result),
        status_path=Path(args.status),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())
