from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from esg_v2.config import Settings
from esg_v2.document.artifact_loader import OcrArtifactLoader
from esg_v2.document.contracts import DocumentIrBuildRequest, DocumentIrBuildResult
from esg_v2.document.deduplicator import DeterministicEntityDeduplicator
from esg_v2.document.identifier_normalizer import CanonicalIdentifierNormalizer
from esg_v2.document.identity import resolve_document_identity
from esg_v2.document.local_forensics import LocalPdfProcessor
from esg_v2.document.logical_table_builder import LogicalTableBuilder
from esg_v2.document.page_renderer import PageRenderer
from esg_v2.document.paddleocr_converter import PaddleOcrDocumentConverter
from esg_v2.document.parser_fusion import ParserFusion
from esg_v2.document.quality_router import OcrQualityRouter
from esg_v2.document.review_orchestrator import AgentReviewOrchestrator
from esg_v2.document.structure_reconstruction import StructureReconstructor
from esg_v2.document.spread_builder import HorizontalSpreadBuilder
from esg_v2.document.table_graph_builder import TableGraphBuilder
from esg_v2.document.table_geometry_resolver import TableGeometryResolver
from esg_v2.document.validator import DocumentIrValidator
from esg_v2.document.versioning import DocumentIrVersionManager
from esg_v2.document.visual_region_builder import VisualRegionBuilder
from esg_v2.document.visual_semantic_grouper import VisualSemanticGrouper
from esg_v2.document.writer import DocumentIrWriter
from esg_v2.storage.package_layout import (
    DocumentIrPackageLayout,
    create_package_root,
    new_run_id,
    package_dir,
    require_package_dir_name,
    require_run_id,
)
from esg_v2.storage.ir_retention import DocumentIrRetentionManager


LogFn = Callable[[str], None]
TelemetryFn = Callable[[dict[str, Any]], None]


class DocumentIrWorkflow:
    def __init__(self, settings: Settings):
        self.settings = settings

    def run(
        self,
        request: DocumentIrBuildRequest,
        *,
        run_id: str | None = None,
        log: LogFn | None = None,
        telemetry: TelemetryFn | None = None,
    ) -> DocumentIrBuildResult:
        run_id = run_id or request.run_id or self._new_run_id(request.ocr_run_id)
        require_run_id(run_id, "ir")
        require_package_dir_name(request.ocr_run_id)
        if request.parent_ir_run_id:
            require_package_dir_name(request.parent_ir_run_id)
        output_dir = package_dir(self.settings.document_ir_output_root, run_id)

        self._stage(log, telemetry, 1, f"Loading immutable OCR artifacts: {request.ocr_run_id}")
        artifact = OcrArtifactLoader(self.settings.output_root).load(request.ocr_run_id)
        pdf_path = request.pdf_path or self._infer_pdf_path(artifact)
        identity = resolve_document_identity(
            ocr_run_id=request.ocr_run_id,
            ocr_manifest=artifact.manifest,
            pdf_path=pdf_path,
            document_label=request.document_label,
        )
        version_manager = DocumentIrVersionManager(self.settings.document_ir_output_root)
        revision = version_manager.next_revision(
            request.ocr_run_id,
            request.parent_ir_run_id,
            document_id=identity.document_id,
            root_ir_run_id=run_id,
        )
        create_package_root(output_dir)
        package = DocumentIrPackageLayout(output_dir)

        render_message = f"2/11 Rendering PDF pages at {request.render_dpi} DPI"
        self._stage(log, telemetry, 2, f"Rendering PDF pages at {request.render_dpi} DPI")
        last_render_progress = -1

        def render_progress(rendered_pages: int, total_pages: int) -> None:
            nonlocal last_render_progress
            if (
                rendered_pages == total_pages
                or rendered_pages == 1
                or rendered_pages - last_render_progress >= 10
            ):
                last_render_progress = rendered_pages
                self._log(
                    log,
                    f"{render_message} ({rendered_pages}/{total_pages})",
                )

        rendered = PageRenderer(
            timeout_seconds=self.settings.pdf_render_timeout_seconds,
        ).render(
            pdf_path,
            package.root / "artifacts" / "page-images",
            dpi=request.render_dpi,
            progress=render_progress,
        )

        self._stage(log, telemetry, 3, "Running deterministic local PDF forensics")
        local_forensics = LocalPdfProcessor().analyze(pdf_path, rendered)

        self._stage(log, telemetry, 4, "Parsing PaddleOCR raw layout and building the IR candidate")
        document = PaddleOcrDocumentConverter().convert(
            artifact,
            run_id=run_id,
            pdf_path=pdf_path,
            rendered=rendered,
            document_id=identity.document_id,
            document_label=identity.document_label,
            external_document_id=request.external_document_id,
            lineage_id=revision.lineage_id,
            ir_revision=revision.revision,
            parent_ir_run_id=revision.parent_ir_run_id,
        )

        self._stage(log, telemetry, 5, "Reconstructing reading order, sections, captions, and footnotes")
        fusion = ParserFusion()
        document = fusion.fuse(document, local_forensics=local_forensics)
        document = DeterministicEntityDeduplicator().deduplicate(document)
        document = CanonicalIdentifierNormalizer().normalize(document)
        document = TableGeometryResolver().resolve(document, local_forensics)
        document = VisualSemanticGrouper().group(document)
        document = StructureReconstructor().reconstruct(document)

        self._stage(log, telemetry, 6, "Building table graphs and cross-page table links")
        document = TableGraphBuilder().build(document)
        document = LogicalTableBuilder().build(document)

        self._stage(log, telemetry, 7, "Building visual regions and auditable crop artifacts")
        document = VisualRegionBuilder().build(document, output_dir)

        self._stage(log, telemetry, 8, "Detecting horizontal page spreads and building composite evidence")
        document = HorizontalSpreadBuilder().build(document, output_dir)

        self._stage(
            log,
            telemetry,
            9,
            f"Routing typed quality risks and optional {request.review_provider} agent reviews",
        )
        document = OcrQualityRouter().route(document, local_forensics).document
        if request.execute_vlm_reviews:
            document = AgentReviewOrchestrator(
                self.settings,
                provider=request.review_provider,
                api_key=request.qiniu_api_key,
                telemetry=telemetry,
            ).execute(
                document,
                review_target_ids=request.review_target_ids,
                max_auto_review_rounds=request.max_auto_review_rounds,
                log=log,
            )

        self._stage(log, telemetry, 10, "Rebuilding corrected structure and reconciling accepted review decisions")
        if request.execute_vlm_reviews and any(patch.status == "accepted" for patch in document.atomic_patches):
            document = TableGeometryResolver().resolve(document, local_forensics)
            document = StructureReconstructor().reconstruct(document)
            document = TableGraphBuilder().build(document)
            document = LogicalTableBuilder().build(document)
            document = VisualRegionBuilder().build(document, output_dir)
        document = fusion.reconcile_reviews(document)

        self._stage(log, telemetry, 11, "Validating Document IR readiness and writing immutable artifacts")
        expected_page_count = artifact.manifest.get("page_count")
        document = DocumentIrValidator().validate(
            document,
            expected_page_count=int(expected_page_count) if isinstance(expected_page_count, (int, float, str)) and str(expected_page_count).isdigit() else None,
        )
        paths = DocumentIrWriter(output_dir).write(document)
        retention = DocumentIrRetentionManager(
            ir_output_root=self.settings.document_ir_output_root,
            ir_state_root=self.settings.document_ir_job_state_root,
            cleanup_root=self.settings.storage_cleanup_root,
            pipeline_queue_db=self.settings.pipeline_queue_db,
            enabled=self.settings.document_ir_best_only_retention,
        ).reconcile_safely(run_id)
        if retention.get("action") == "cleanup_failed":
            self._log(log, f"Document IR retention cleanup failed: {retention.get('error')}")
        elif retention.get("pruned_run_ids"):
            self._log(
                log,
                f"Retained best Document IR {retention['retained_run_id']}; pruned "
                f"{len(retention['pruned_run_ids'])} superseded revision(s)",
            )

        return DocumentIrBuildResult(
            run_id=run_id,
            ocr_run_id=request.ocr_run_id,
            output_dir=output_dir,
            manifest_path=paths["manifest"],
            document_ir_path=paths["document_ir"],
            quality_report_path=paths["quality_report"],
            validation_report_path=paths["validation_report"],
            review_tasks_path=paths["review_tasks"],
            page_count=len(document.pages),
            block_count=len(document.blocks),
            table_count=len(document.tables),
            figure_count=len(document.figures),
            spread_count=len(document.spreads),
            review_task_count=len(document.review_tasks),
            readiness=document.readiness,
            retention=retention,
        )

    @staticmethod
    def _new_run_id(ocr_run_id: str) -> str:
        return new_run_id("ir")

    def _infer_pdf_path(self, artifact) -> str | None:
        source = artifact.manifest.get("source")
        if isinstance(source, str) and not source.startswith(("http://", "https://")):
            path = Path(source)
            if path.exists():
                return str(path.resolve())
        if isinstance(source, dict):
            display_name = source.get("display_name")
            if isinstance(display_name, str) and display_name:
                uploaded = self.settings.upload_root / artifact.run_id / Path(display_name).name
                if uploaded.exists():
                    return str(uploaded.resolve())
        return None

    @staticmethod
    def _log(log: LogFn | None, message: str) -> None:
        if log:
            log(message)

    @classmethod
    def _stage(
        cls,
        log: LogFn | None,
        telemetry: TelemetryFn | None,
        index: int,
        name: str,
    ) -> None:
        if telemetry:
            telemetry({"event": "stage_started", "index": index, "total": 11, "name": name})
        cls._log(log, f"{index}/11 {name}")
