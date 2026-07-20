from __future__ import annotations

from pathlib import Path
from typing import Callable

from esg_v2.config import Settings
from esg_v2.document.artifact_loader import OcrArtifactLoader
from esg_v2.document.contracts import DocumentIrBuildRequest, DocumentIrBuildResult
from esg_v2.document.deduplicator import DeterministicEntityDeduplicator
from esg_v2.document.identifier_normalizer import CanonicalIdentifierNormalizer
from esg_v2.document.local_forensics import LocalPdfProcessor
from esg_v2.document.page_renderer import PageRenderer
from esg_v2.document.paddleocr_converter import PaddleOcrDocumentConverter
from esg_v2.document.parser_fusion import ParserFusion
from esg_v2.document.quality_router import OcrQualityRouter
from esg_v2.document.review_orchestrator import AgentReviewOrchestrator
from esg_v2.document.structure_reconstruction import StructureReconstructor
from esg_v2.document.table_graph_builder import TableGraphBuilder
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


LogFn = Callable[[str], None]


class DocumentIrWorkflow:
    def __init__(self, settings: Settings):
        self.settings = settings

    def run(self, request: DocumentIrBuildRequest, *, run_id: str | None = None, log: LogFn | None = None) -> DocumentIrBuildResult:
        run_id = run_id or request.run_id or self._new_run_id(request.ocr_run_id)
        require_run_id(run_id, "ir")
        require_package_dir_name(request.ocr_run_id)
        if request.parent_ir_run_id:
            require_package_dir_name(request.parent_ir_run_id)
        output_dir = package_dir(self.settings.document_ir_output_root, run_id)
        create_package_root(output_dir)
        package = DocumentIrPackageLayout(output_dir)
        version_manager = DocumentIrVersionManager(self.settings.document_ir_output_root)
        revision = version_manager.next_revision(request.ocr_run_id, request.parent_ir_run_id)

        self._log(log, f"1/10 Loading immutable OCR artifacts: {request.ocr_run_id}")
        artifact = OcrArtifactLoader(self.settings.output_root).load(request.ocr_run_id)
        pdf_path = request.pdf_path or self._infer_pdf_path(artifact)

        self._log(log, f"2/10 Rendering PDF pages at {request.render_dpi} DPI")
        rendered = PageRenderer().render(pdf_path, package.root / "artifacts" / "page-images", dpi=request.render_dpi)

        self._log(log, "3/10 Running deterministic local PDF forensics")
        local_forensics = LocalPdfProcessor().analyze(pdf_path, rendered)

        self._log(log, "4/10 Parsing PaddleOCR raw layout and building the IR candidate")
        document = PaddleOcrDocumentConverter().convert(
            artifact,
            run_id=run_id,
            pdf_path=pdf_path,
            rendered=rendered,
            ir_revision=revision.revision,
            parent_ir_run_id=revision.parent_ir_run_id,
        )

        self._log(log, "5/10 Reconstructing reading order, sections, captions, and footnotes")
        fusion = ParserFusion()
        document = fusion.fuse(document, local_forensics=local_forensics)
        document = DeterministicEntityDeduplicator().deduplicate(document)
        document = VisualSemanticGrouper().group(document)
        document = CanonicalIdentifierNormalizer().normalize(document)
        document = StructureReconstructor().reconstruct(document)

        self._log(log, "6/10 Building table graphs and cross-page table links")
        document = TableGraphBuilder().build(document)

        self._log(log, "7/10 Building visual regions and auditable crop artifacts")
        document = VisualRegionBuilder().build(document, output_dir)

        self._log(log, "8/10 Routing page-level quality risks and optional Qiniu agent reviews")
        document = OcrQualityRouter().route(document, local_forensics).document
        if request.execute_vlm_reviews:
            document = AgentReviewOrchestrator(self.settings, api_key=request.qiniu_api_key).execute(
                document,
                review_target_ids=request.review_target_ids,
                max_auto_review_rounds=request.max_auto_review_rounds,
                log=log,
            )

        self._log(log, "9/10 Rebuilding corrected structure and reconciling accepted review decisions")
        if request.execute_vlm_reviews and any(patch.status == "accepted" for patch in document.atomic_patches):
            document = StructureReconstructor().reconstruct(document)
            document = TableGraphBuilder().build(document)
            document = VisualRegionBuilder().build(document, output_dir)
        document = fusion.reconcile_reviews(document)

        self._log(log, "10/10 Validating Document IR readiness and writing immutable artifacts")
        expected_page_count = artifact.manifest.get("page_count")
        document = DocumentIrValidator().validate(
            document,
            expected_page_count=int(expected_page_count) if isinstance(expected_page_count, (int, float, str)) and str(expected_page_count).isdigit() else None,
        )
        paths = DocumentIrWriter(output_dir).write(document)

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
            review_task_count=len(document.review_tasks),
            readiness=document.readiness,
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
