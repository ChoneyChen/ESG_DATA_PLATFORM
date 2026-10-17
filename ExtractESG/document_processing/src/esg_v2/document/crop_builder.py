from __future__ import annotations

from pathlib import Path

from esg_v2.document.contracts import ArtifactRef, BoundingBox, CoordinateSystem
from esg_v2.document.coordinate_mapper import CoordinateMapper
from esg_v2.utils.hashing import sha256_file


class CropBuilder:
    def build(
        self,
        *,
        target_id: str,
        page_index: int,
        bbox: BoundingBox,
        page_image_path: str,
        canonical_system: CoordinateSystem,
        image_system: CoordinateSystem,
        output_dir: Path,
        padding_pixels: int = 12,
    ) -> ArtifactRef | None:
        try:
            from PIL import Image  # type: ignore

            image = Image.open(page_image_path)
            left, top, right, bottom = CoordinateMapper.canonical_to_pixels(bbox, canonical_system, image_system)
            left = max(0, left - padding_pixels)
            top = max(0, top - padding_pixels)
            right = min(image.width, right + padding_pixels)
            bottom = min(image.height, bottom + padding_pixels)
            if right <= left or bottom <= top:
                return None
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_target = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in target_id)
            path = output_dir / f"{safe_target}.png"
            image.crop((left, top, right, bottom)).save(path, format="PNG", optimize=True)
            return ArtifactRef(
                artifact_id=f"artifact-crop-{safe_target}",
                kind="region_crop",
                path=str(path.resolve()),
                media_type="image/png",
                page_index=page_index,
                bbox=bbox,
                width_pixels=right - left,
                height_pixels=bottom - top,
                page_pixel_bbox=BoundingBox(
                    x0=left,
                    y0=top,
                    x1=right,
                    y1=bottom,
                    unit="pixels",
                    origin="top_left",
                    coordinate_system_id=image_system.coordinate_system_id,
                ),
                sha256=sha256_file(path),
                source="CropBuilder",
            )
        except Exception:
            return None
