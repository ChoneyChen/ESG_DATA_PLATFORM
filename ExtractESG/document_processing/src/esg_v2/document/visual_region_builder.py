from __future__ import annotations

from pathlib import Path

from esg_v2.document.contracts import DocumentIR
from esg_v2.document.crop_builder import CropBuilder
from esg_v2.storage.package_layout import DocumentIrPackageLayout


class VisualRegionBuilder:
    def __init__(self, crop_builder: CropBuilder | None = None):
        self.crop_builder = crop_builder or CropBuilder()

    def build(self, document: DocumentIR, output_dir: Path) -> DocumentIR:
        document.artifacts = [artifact for artifact in document.artifacts if artifact.kind != "region_crop"]
        pages = {page.page_index: page for page in document.pages}
        systems = {system.coordinate_system_id: system for system in document.coordinate_systems}
        package = DocumentIrPackageLayout(output_dir)
        for target in [*document.tables, *document.figures]:
            target.crop_artifact_id = None
            page = pages.get(target.page_index)
            if not page or not page.page_image_path or not target.bbox:
                if "visual_crop_unavailable" not in target.quality_flags:
                    target.quality_flags.append("visual_crop_unavailable")
                continue
            canonical = next((systems[item] for item in page.coordinate_system_ids if item in systems and systems[item].name == "canonical_pdf_points"), None)
            image_system = next((systems[item] for item in page.coordinate_system_ids if item in systems and systems[item].name == "rendered_page_pixels"), None)
            if not canonical or not image_system:
                target.quality_flags.append("coordinate_mapping_unavailable")
                continue
            target_id = target.table_id if hasattr(target, "table_id") else target.figure_id
            crop_kind = "tables" if hasattr(target, "table_id") else "figures"
            crop = self.crop_builder.build(
                target_id=target_id,
                page_index=target.page_index,
                bbox=target.bbox,
                page_image_path=page.page_image_path,
                canonical_system=canonical,
                image_system=image_system,
                output_dir=package.root / "artifacts" / "crops" / crop_kind,
            )
            if crop:
                target.crop_artifact_id = crop.artifact_id
                document.artifacts.append(crop)
            else:
                target.quality_flags.append("visual_crop_failed")
        return document
