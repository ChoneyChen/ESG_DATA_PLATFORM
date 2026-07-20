from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    CorrectionPatch,
    DocumentIR,
    DocumentIrRepairRequest,
    FinalReviewDecision,
    PatchDecisionRequest,
    ReviewScopeItem,
    ValidationReport,
    VlmReviewTask,
)
from esg_v2.document.patch_guard import DocumentPatchApplier, PatchGuard
from esg_v2.document.identifiers import sequential_id
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.document.review_orchestrator import AgentReviewOrchestrator
from esg_v2.document.structure_reconstruction import StructureReconstructor
from esg_v2.document.table_graph_builder import TableGraphBuilder
from esg_v2.document.validator import DocumentIrValidator
from esg_v2.document.versioning import DocumentIrVersionManager
from esg_v2.document.visual_region_builder import VisualRegionBuilder
from esg_v2.document.writer import DocumentIrWriter
from esg_v2.storage.package_layout import (
    create_package_root,
    new_run_id,
    package_dir,
    page_stem,
    require_run_id,
)


class DocumentIrRevisionService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.applier = DocumentPatchApplier()

    def decide_patch(self, parent_run_id: str, patch_id: str, request: PatchDecisionRequest) -> dict[str, Any]:
        document, output_dir = self._child(parent_run_id, "human")
        patch = next((item for item in document.atomic_patches if item.patch_id == patch_id), None)
        if patch is None:
            raise KeyError(f"Patch not found: {patch_id}")
        if patch.status != "human_required":
            raise ValueError(f"Patch is not awaiting a human decision: {patch.status}")
        if request.action == "accept":
            self.applier.apply_one(document, patch)
            patch.status = "accepted"
        else:
            patch.status = "rejected"
        self._record_human_action(document, request, target_type="patch", target_id=patch_id)
        self._finish_human_task_if_resolved(document, patch.source_task_id, request)
        return self._finalize(document, output_dir, rebuild=request.action == "accept")

    def decide_task(self, parent_run_id: str, task_id: str, request: PatchDecisionRequest) -> dict[str, Any]:
        document, output_dir = self._child(parent_run_id, "human")
        task = next((item for item in document.review_tasks if item.task_id == task_id), None)
        if task is None:
            raise KeyError(f"Review task not found: {task_id}")
        if task.status != "human_required":
            raise ValueError(f"Review task is not awaiting a human decision: {task.status}")
        unresolved = [
            patch
            for patch in document.atomic_patches
            if patch.source_task_id == task_id and patch.status == "human_required"
        ]
        if unresolved:
            raise ValueError("Resolve the task's atomic patches before closing the task")
        self._record_human_action(document, request, target_type="review_task", target_id=task_id)
        self._resolve_human_task(document, task, request, accepted_patch_ids=[])
        return self._finalize(document, output_dir, rebuild=False)

    def repair(self, request: DocumentIrRepairRequest, *, run_id: str | None = None) -> dict[str, Any]:
        document, output_dir = self._child(request.parent_ir_run_id, "repair", requested_run_id=run_id)
        targets = []
        for target_id in request.target_ids:
            target = PatchGuard._target(document, target_id)
            if target is None:
                raise KeyError(f"Repair target not found: {target_id}")
            targets.append((target_id, target))
        grouped: dict[int, list[tuple[str, Any]]] = {}
        for target_id, target in targets:
            page_index = getattr(target, "page_index", None)
            if page_index is None and hasattr(target, "start_page_index"):
                page_index = target.start_page_index
            if page_index is None:
                raise ValueError(f"Repair target has no page scope: {target_id}")
            grouped.setdefault(int(page_index), []).append((target_id, target))

        review_counter = self._next_review_sequence(document)
        for page_index, page_targets in grouped.items():
            page = next(item for item in document.pages if item.page_index == page_index)
            task_id = f"review-{review_counter:06d}"
            review_counter += 1
            scopes = [
                ReviewScopeItem(
                    target_type=self._target_type(target),
                    target_id=target_id,
                    reason_codes=[request.reason_code],
                    bbox=getattr(target, "bbox", None),
                    blocking=True,
                )
                for target_id, target in page_targets
            ]
            refs = [page.page_image_path, page.markdown_path]
            for _, target in page_targets:
                artifact_id = getattr(target, "crop_artifact_id", None)
                artifact = next((item for item in document.artifacts if item.artifact_id == artifact_id), None)
                if artifact:
                    refs.insert(1, artifact.remote_uri or artifact.path)
            task = VlmReviewTask(
                task_id=task_id,
                task_type="page_compound_review",
                target_type="page",
                target_id=page.page_id,
                page_index=page_index,
                priority="critical",
                blocking=True,
                scope=scopes,
                reason_codes=[request.reason_code],
                prompt_intent="Re-open only the requested Document IR targets and repair them against the source page.",
                input_refs=list(dict.fromkeys(ref for ref in refs if ref)),
            )
            document.review_tasks.append(task)
            page.review_task_ids.append(task_id)
            for _, target in page_targets:
                if hasattr(target, "review_task_ids"):
                    target.review_task_ids.append(task_id)

        document.metadata.source_artifacts["repair_request"] = {
            "parent_ir_run_id": request.parent_ir_run_id,
            "target_ids": request.target_ids,
            "reason_code": request.reason_code,
            "requested_by": request.requested_by,
            "notes": request.notes,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        if request.execute_vlm_reviews:
            document = AgentReviewOrchestrator(self.settings, api_key=request.qiniu_api_key).execute(
                document,
                review_target_ids=request.target_ids,
                max_auto_review_rounds=2,
            )
        return self._finalize(
            document,
            output_dir,
            rebuild=any(patch.status == "accepted" for patch in document.atomic_patches),
        )

    def _child(self, parent_run_id: str, suffix: str, *, requested_run_id: str | None = None) -> tuple[DocumentIR, Path]:
        parent_dir = package_dir(self.settings.document_ir_output_root, parent_run_id)
        reader = DocumentIrPackageReader(parent_dir, ocr_output_root=self.settings.output_root)
        if reader.is_package_v1:
            reader.validate_integrity().require_valid()
        document = reader.load_document(hydrate_local_paths=True)
        revision = DocumentIrVersionManager(self.settings.document_ir_output_root).next_revision(
            document.metadata.ocr_run_id,
            parent_run_id,
        )
        run_id = requested_run_id or self._new_run_id(document.metadata.ocr_run_id, suffix)
        require_run_id(run_id, "ir")
        output_dir = package_dir(self.settings.document_ir_output_root, run_id)
        create_package_root(output_dir)
        if reader.is_package_v1:
            for relative in ("artifacts/page-images", "artifacts/crops"):
                source = parent_dir / relative
                if source.exists():
                    shutil.copytree(source, output_dir / relative)
        else:
            self._copy_legacy_visual_artifacts(document, parent_dir, output_dir)
        document.metadata.run_id = run_id
        document.metadata.ir_revision = revision.revision
        document.metadata.parent_ir_run_id = parent_run_id
        document.metadata.created_at = datetime.now(timezone.utc).isoformat()
        document.metadata.source_artifacts["revision_lineage"] = {
            "parent_ir_run_id": parent_run_id,
            "revision_type": suffix,
        }
        document.readiness = "building"
        document.validation_report = ValidationReport()
        self._rewrite_materialized_paths(
            document,
            parent_dir,
            output_dir,
            legacy_layout=not reader.is_package_v1,
        )
        return document, output_dir

    def _finalize(self, document: DocumentIR, output_dir: Path, *, rebuild: bool) -> dict[str, Any]:
        if rebuild:
            document = StructureReconstructor().reconstruct(document)
            document = TableGraphBuilder().build(document)
            document = VisualRegionBuilder().build(document, output_dir)
        document.correction_patches = [
            CorrectionPatch.model_validate(patch.model_dump(mode="json")) for patch in document.atomic_patches
        ]
        document = DocumentIrValidator().validate(document, expected_page_count=len(document.pages))
        paths = DocumentIrWriter(output_dir).write(document)
        return json.loads(paths["manifest"].read_text(encoding="utf-8"))

    @staticmethod
    def _finish_human_task_if_resolved(document: DocumentIR, task_id: str, request: PatchDecisionRequest) -> None:
        task = next((item for item in document.review_tasks if item.task_id == task_id), None)
        if task is None:
            return
        task_patches = [patch for patch in document.atomic_patches if patch.source_task_id == task_id]
        if any(patch.status == "human_required" for patch in task_patches):
            return
        accepted = [patch.patch_id for patch in task_patches if patch.status == "accepted"]
        DocumentIrRevisionService._resolve_human_task(document, task, request, accepted_patch_ids=accepted)

    @staticmethod
    def _resolve_human_task(document: DocumentIR, task: VlmReviewTask, request: PatchDecisionRequest, *, accepted_patch_ids: list[str]) -> None:
        decision = FinalReviewDecision(
            decision_id=sequential_id(
                "decision",
                (item.decision_id for item in document.final_decisions),
            ),
            task_id=task.task_id,
            outcome="human_resolved",
            blocking_resolved=True,
            accepted_patch_ids=accepted_patch_ids,
            reviewer_result_ids=task.reviewer_result_ids,
            guard_result_ids=task.guard_result_ids,
            verifier_result_ids=task.verifier_result_ids,
            reason=request.notes or f"Human operator resolved the task with action={request.action}.",
            decided_by="human",
        )
        document.final_decisions.append(decision)
        task.final_decision_id = decision.decision_id
        task.status = "reviewed"
        task.result = {"outcome": "human_resolved", "action": request.action, "decision_id": decision.decision_id}
        targets = {task.target_id, *(scope.target_id for scope in task.scope)}
        for conflict in document.conflict_groups:
            if conflict.target_id in targets and conflict.status in {"open", "human_required"}:
                conflict.status = "resolved"
                conflict.resolution = decision.reason

    @staticmethod
    def _record_human_action(document: DocumentIR, request: PatchDecisionRequest, *, target_type: str, target_id: str) -> None:
        actions = document.quality_report.setdefault("human_actions", [])
        actions.append(
            {
                "target_type": target_type,
                "target_id": target_id,
                "action": request.action,
                "decided_by": request.decided_by,
                "notes": request.notes,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    @staticmethod
    def _rewrite_materialized_paths(
        document: DocumentIR,
        parent_dir: Path,
        output_dir: Path,
        *,
        legacy_layout: bool,
    ) -> None:
        table_ids = {item.table_id for item in document.tables}
        legacy_page_indices = {
            Path(page.page_image_path).name: page.page_index
            for page in document.pages
            if page.page_image_path
        }

        def relocate(value: str) -> str:
            if "://" in value:
                return value
            try:
                relative = Path(value).resolve().relative_to(parent_dir.resolve())
            except (OSError, ValueError):
                return value
            if legacy_layout and relative.parts:
                if relative.parts[0] == "page_images":
                    page_index = legacy_page_indices.get(relative.name)
                    filename = (
                        f"{page_stem(page_index)}{relative.suffix.lower()}"
                        if page_index is not None
                        else relative.name
                    )
                    relative = Path("artifacts/page-images", filename)
                elif relative.parts[0] == "crops":
                    kind = "tables" if relative.stem in table_ids else "figures"
                    relative = Path("artifacts/crops", kind, *relative.parts[1:])
            return str(output_dir / relative)

        for page in document.pages:
            if page.page_image_path:
                page.page_image_path = relocate(page.page_image_path)
        for artifact in document.artifacts:
            artifact.path = relocate(artifact.path)
        for task in document.review_tasks:
            task.input_refs = [relocate(ref) for ref in task.input_refs]

    @staticmethod
    def _copy_legacy_visual_artifacts(document: DocumentIR, parent_dir: Path, output_dir: Path) -> None:
        page_images = parent_dir / "page_images"
        if page_images.exists():
            target = output_dir / "artifacts" / "page-images"
            target.mkdir(parents=True, exist_ok=True)
            source_by_name = {item.name: item for item in page_images.iterdir() if item.is_file()}
            for page in document.pages:
                source = source_by_name.get(Path(page.page_image_path or "").name)
                if source:
                    shutil.copy2(source, target / f"{page_stem(page.page_index)}{source.suffix.lower()}")
        crops = parent_dir / "crops"
        if crops.exists():
            table_ids = {item.table_id for item in document.tables}
            for source in crops.glob("*.png"):
                kind = "tables" if source.stem in table_ids else "figures"
                target = output_dir / "artifacts" / "crops" / kind / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    @staticmethod
    def _next_review_sequence(document: DocumentIR) -> int:
        values = []
        for task in document.review_tasks:
            if task.task_id.startswith("review-") and task.task_id[7:].isdigit():
                values.append(int(task.task_id[7:]))
        return max(values, default=0) + 1

    @staticmethod
    def _target_type(target) -> str:
        if hasattr(target, "page_id"):
            return "page"
        if hasattr(target, "block_id"):
            return "block"
        if hasattr(target, "table_id") and not hasattr(target, "cell_id"):
            return "table"
        if hasattr(target, "figure_id"):
            return "figure"
        if hasattr(target, "cell_id"):
            return "cell"
        if hasattr(target, "section_id"):
            return "section"
        raise TypeError("Unsupported repair target type")

    @staticmethod
    def _new_run_id(ocr_run_id: str, suffix: str) -> str:
        return new_run_id("ir")
