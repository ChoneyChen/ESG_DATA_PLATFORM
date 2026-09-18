from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    AtomicPatch,
    CorrectionPatch,
    DocumentIR,
    DocumentIrRepairRequest,
    FinalReviewDecision,
    PatchDecisionRequest,
    ReviewRetryRequest,
    ReviewScopeItem,
    ValidationReport,
    VlmReviewTask,
)
from esg_v2.document.patch_guard import DocumentPatchApplier, PatchGuard
from esg_v2.document.identifiers import sequential_id
from esg_v2.document.logical_table_builder import LogicalTableBuilder
from esg_v2.document.reader import DocumentIrPackageReader
from esg_v2.document.review_orchestrator import AgentReviewOrchestrator
from esg_v2.document.review_plan_compiler import ReviewPlanCompiler
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
from esg_v2.storage.ir_retention import DocumentIrRetentionManager


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
            guard_passed = any(
                guard.passed and patch.patch_id in guard.patch_ids
                for guard in document.guard_results
            )
            if not guard_passed:
                raise ValueError("A human cannot accept a patch that did not pass Patch Guard")
            self.applier.apply_one(document, patch)
            patch.status = "accepted"
        elif request.action == "reject":
            patch.status = "rejected"
        else:
            raise ValueError("Patch decisions support only accept or reject")
        self._record_human_action(document, request, target_type="patch", target_id=patch_id)
        self._finish_human_task_if_resolved(document, patch.source_task_id, request)
        return self._finalize(document, output_dir, rebuild=request.action == "accept")

    def decide_task(self, parent_run_id: str, task_id: str, request: PatchDecisionRequest) -> dict[str, Any]:
        document, output_dir = self._child(parent_run_id, "human")
        task = next((item for item in document.review_tasks if item.task_id == task_id), None)
        if task is None:
            raise KeyError(f"Review task not found: {task_id}")
        if request.action == "continue_limited":
            if not request.notes or not request.notes.strip():
                raise ValueError("Limited continuation requires an explicit reason")
            self._record_human_action(document, request, target_type="review_task", target_id=task_id)
            # Keep the unresolved task and failed patches truthful. This action
            # publishes a revision with bounded evidence availability, not approval.
            document.quality_report.setdefault("limited_continuation_requests", []).append({
                "task_id": task_id, "decided_by": request.decided_by, "notes": request.notes,
            })
            return self._finalize(document, output_dir, rebuild=False)
        if request.action == "accept_current_nonmaterial":
            if task.blocking:
                raise ValueError("Only a non-blocking enhancement may be accepted as non-material")
            if task.status not in {"pending", "queued", "deferred", "failed", "skipped"}:
                raise ValueError(f"Optional review task is not open: {task.status}")
            self._record_human_action(
                document,
                request,
                target_type="review_task",
                target_id=task_id,
            )
            self._resolve_human_task(
                document,
                task,
                request,
                accepted_patch_ids=[],
            )
            return self._finalize(document, output_dir, rebuild=False)
        if task.status != "human_required":
            raise ValueError(f"Review task is not awaiting a human decision: {task.status}")
        target = PatchGuard._target(document, task.target_id)
        if request.action in {"confirm_spread", "reject_spread"}:
            if self._target_type(target) != "spread":
                raise ValueError("Spread decisions are valid only for a SpreadIR review task")
            operation = request.action
            patch = AtomicPatch(
                patch_id=sequential_id("patch", (item.patch_id for item in document.atomic_patches)),
                source_task_id=task.task_id,
                target_type="spread",
                target_id=task.target_id,
                operation=operation,
                proposed_value={"reading_direction": "left_to_right"},
                evidence_refs=list(task.input_refs),
                confidence=1.0,
                risk_level="low",
                rationale=request.notes or f"Human operator selected {request.action}.",
            )
            patch.before_value = PatchGuard._current_value(patch, target)
            patches = [patch]
            if operation == "confirm_spread":
                inferred = AgentReviewOrchestrator._unique_spread_link_proposal(
                    document,
                    task,
                    confidence=1.0,
                )
                if inferred is not None:
                    link_patch = AtomicPatch(
                        patch_id=sequential_id(
                            "patch",
                            (
                                item.patch_id
                                for item in [*document.atomic_patches, *patches]
                            ),
                        ),
                        source_task_id=task.task_id,
                        target_type=inferred.target_type,
                        target_id=inferred.target_id,
                        operation=inferred.operation,
                        proposed_value=inferred.proposed_value,
                        evidence_refs=inferred.evidence_refs,
                        confidence=inferred.confidence,
                        risk_level="low",
                        rationale=inferred.rationale,
                    )
                    link_patch.before_value = PatchGuard._current_value(link_patch, target)
                    patches.append(link_patch)
            document.atomic_patches.extend(patches)
            guard = PatchGuard().evaluate(
                document,
                task.task_id,
                patches,
                allowed_target_ids={task.target_id},
                required_target_ids={task.target_id},
                allowed_operations={item.operation for item in patches},
                required_any_operations={"confirm_spread", "reject_spread"},
            )
            guard.requires_independent_verifier = False
            document.guard_results.append(guard)
            task.guard_result_ids.append(guard.guard_result_id)
            if not guard.passed:
                for item in patches:
                    item.status = "guard_failed"
                raise ValueError("Human spread decision did not pass the structural guard")
            self.applier.apply(document, patches)
            self._record_human_action(document, request, target_type="review_task", target_id=task_id)
            self._resolve_human_task(
                document,
                task,
                request,
                accepted_patch_ids=[item.patch_id for item in patches],
            )
            return self._finalize(document, output_dir, rebuild=False)
        if request.action != "keep_current":
            raise ValueError("A review task can be closed only with keep_current or a typed spread decision")
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

    def repair(
        self,
        request: DocumentIrRepairRequest,
        *,
        run_id: str | None = None,
        telemetry: Callable[[dict[str, Any]], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        self._stage(telemetry, log, 1, 4, "Loading parent and creating immutable repair revision")
        document, output_dir = self._child(request.parent_ir_run_id, "repair", requested_run_id=run_id)
        self._stage(telemetry, log, 2, 4, "Compiling targeted repair plans")
        targets = []
        for target_id in request.target_ids:
            target = PatchGuard._target(document, target_id)
            if target is None:
                raise KeyError(f"Repair target not found: {target_id}")
            targets.append((target_id, target))
        grouped: dict[int, list[tuple[str, Any]]] = {}
        for target_id, target in targets:
            page_index = getattr(target, "page_index", None)
            if page_index is None and hasattr(target, "page_indices") and target.page_indices:
                page_index = target.page_indices[0]
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
                composite_artifact_id = getattr(target, "composite_artifact_id", None)
                composite_artifact = next(
                    (item for item in document.artifacts if item.artifact_id == composite_artifact_id),
                    None,
                )
                if composite_artifact:
                    refs.insert(0, composite_artifact.remote_uri or composite_artifact.path)
                for member_index in getattr(target, "page_indices", []):
                    member_page = next(
                        (item for item in document.pages if item.page_index == member_index),
                        None,
                    )
                    if member_page and member_page.page_image_path:
                        refs.append(member_page.page_image_path)
                artifact_id = getattr(target, "crop_artifact_id", None)
                artifact = next((item for item in document.artifacts if item.artifact_id == artifact_id), None)
                if artifact:
                    refs.insert(1, artifact.remote_uri or artifact.path)
            primary_scope = scopes[0] if len(scopes) == 1 else None
            task_type = {
                "table": "table_structure_review",
                "figure": "figure_chart_review",
                "page": "page_compound_review",
                "spread": "horizontal_spread_review",
            }.get(primary_scope.target_type if primary_scope else "", "low_confidence_region_review")
            task = VlmReviewTask(
                task_id=task_id,
                task_type=task_type,
                target_type=primary_scope.target_type if primary_scope else "page",
                target_id=primary_scope.target_id if primary_scope else page.page_id,
                page_index=page_index,
                priority="critical",
                blocking=True,
                scope=scopes,
                reason_codes=[request.reason_code],
                prompt_intent="Re-open only the requested Document IR targets and repair them against the source page.",
                input_refs=list(dict.fromkeys(ref for ref in refs if ref)),
            )
            ReviewPlanCompiler().compile(document, task)
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
            "review_provider": request.review_provider,
        }
        if request.execute_vlm_reviews:
            self._stage(
                telemetry,
                log,
                3,
                4,
                f"Running targeted {request.review_provider} agent review",
            )
            document = AgentReviewOrchestrator(
                self.settings,
                provider=request.review_provider,
                api_key=request.qiniu_api_key,
                telemetry=telemetry,
            ).execute(
                document,
                review_target_ids=request.target_ids,
                max_auto_review_rounds=3,
                log=log,
            )
        else:
            self._stage(telemetry, log, 3, 4, "Targeted review disabled; preserving compiled tasks")
        self._stage(telemetry, log, 4, 4, "Validating and writing immutable repair revision")
        return self._finalize(
            document,
            output_dir,
            rebuild=any(patch.status == "accepted" for patch in document.atomic_patches),
        )

    def resume_reviews(
        self,
        parent_run_id: str,
        request: ReviewRetryRequest,
        *,
        run_id: str | None = None,
        telemetry: Callable[[dict[str, Any]], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        self._stage(telemetry, log, 1, 3, "Loading parent and selecting unresolved review tasks")
        document, output_dir = self._child(parent_run_id, "review-retry", requested_run_id=run_id)
        unresolved_statuses = {"pending", "queued", "deferred", "failed", "skipped"}
        # Older immutable revisions retain the retryability decision made by a
        # previous transaction compiler. Re-evaluate this known compiler failure
        # in the child revision before selecting work to resume.
        for task in document.review_tasks:
            if (
                task.status in unresolved_statuses
                and task.failure_class == "system_contract"
                and (task.result or {}).get("reason")
                == "required_review_targets_incomplete_after_guard_confirmation"
            ):
                task.retryable = True
        unresolved = [
            task
            for task in document.review_tasks
            if task.status in unresolved_statuses and task.retryable
        ]
        by_id = {task.task_id: task for task in unresolved}
        if request.task_ids:
            missing = sorted(set(request.task_ids) - set(by_id))
            if missing:
                raise KeyError(f"Review tasks are not retryable: {', '.join(missing)}")
            selected = [by_id[task_id] for task_id in dict.fromkeys(request.task_ids)]
        else:
            selected = [task for task in unresolved if task.blocking or request.include_optional]
        if not selected:
            raise ValueError("No unresolved review tasks match the retry request")

        selected_ids = [task.task_id for task in selected]
        before_state = {
            task.task_id: {
                "status": task.status,
                "execution_count": task.execution_count,
                "reviewer_call_count": task.reviewer_call_count,
                "verifier_call_count": task.verifier_call_count,
            }
            for task in selected
        }
        model_call_count_before = len(document.model_calls)
        document.metadata.source_artifacts["review_retry_request"] = {
            "parent_ir_run_id": parent_run_id,
            "task_ids": selected_ids,
            "include_optional": request.include_optional,
            "max_auto_review_rounds": request.max_auto_review_rounds,
            "requested_by": request.requested_by,
            "notes": request.notes,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "review_provider": request.review_provider,
        }
        document.quality_report.setdefault("review_retry_history", []).append(
            document.metadata.source_artifacts["review_retry_request"]
        )
        self._stage(
            telemetry,
            log,
            2,
            3,
            f"Running selected {request.review_provider} review tasks",
        )
        document = AgentReviewOrchestrator(
            self.settings,
            provider=request.review_provider,
            api_key=request.qiniu_api_key,
            telemetry=telemetry,
        ).execute(
            document,
            review_target_ids=selected_ids,
            max_auto_review_rounds=request.max_auto_review_rounds,
            log=log,
        )
        selected_after = [task for task in document.review_tasks if task.task_id in set(selected_ids)]
        resolved_statuses = {"auto_resolved", "reviewed", "done"}
        new_model_calls = document.model_calls[model_call_count_before:]
        failure_categories: dict[str, int] = {}
        for call in new_model_calls:
            failure_categories[call.failure_category] = failure_categories.get(call.failure_category, 0) + 1
        resolved_count = sum(1 for task in selected_after if task.status in resolved_statuses)
        remaining_count = len(selected_after) - resolved_count
        checkpoint_resumed_count = sum(
            1
            for task in selected_after
            if task.verifier_call_count > int(before_state[task.task_id]["verifier_call_count"])
            and task.reviewer_call_count == int(before_state[task.task_id]["reviewer_call_count"])
        )
        provider_short_circuited_count = sum(
            1
            for task in selected_after
            if bool((task.result or {}).get("short_circuited"))
        )
        provider_limit_triggered = bool(
            (document.quality_report.get("provider_rate_limit") or {}).get("run_short_circuited")
        )
        retry_result = {
            "schema_version": "document-ir-review-retry-result-v1",
            "parent_ir_run_id": parent_run_id,
            "child_ir_run_id": document.metadata.run_id,
            "selected_task_ids": selected_ids,
            "selected_count": len(selected_ids),
            "resolved_count": resolved_count,
            "remaining_count": remaining_count,
            "blocking_remaining_count": sum(
                1 for task in selected_after if task.blocking and task.status not in resolved_statuses
            ),
            "optional_remaining_count": sum(
                1 for task in selected_after if not task.blocking and task.status not in resolved_statuses
            ),
            "new_model_call_count": len(new_model_calls),
            "new_model_call_failure_categories": failure_categories,
            "provider_short_circuited_count": provider_short_circuited_count,
            "provider_limit_triggered": provider_limit_triggered,
            "checkpoint_resumed_count": checkpoint_resumed_count,
            "status_transitions": {
                task.task_id: {
                    "before": before_state[task.task_id]["status"],
                    "after": task.status,
                    "resume_stage": task.resume_stage,
                    "execution_count": task.execution_count,
                    "reviewer_call_count": task.reviewer_call_count,
                    "verifier_call_count": task.verifier_call_count,
                }
                for task in selected_after
            },
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        document.quality_report["review_retry_result"] = retry_result
        self._stage(telemetry, log, 3, 3, "Rebuilding and writing immutable review revision")
        manifest = self._finalize(
            document,
            output_dir,
            rebuild=any(
                patch.status == "accepted" and patch.source_task_id in selected_ids
                for patch in document.atomic_patches
            ),
        )
        manifest["review_retry_result"] = retry_result
        return manifest

    @staticmethod
    def _stage(
        telemetry: Callable[[dict[str, Any]], None] | None,
        log: Callable[[str], None] | None,
        index: int,
        total: int,
        name: str,
    ) -> None:
        if telemetry:
            telemetry({"event": "stage_started", "index": index, "total": total, "name": name})
        if log:
            log(f"{index}/{total} {name}")

    def _child(self, parent_run_id: str, suffix: str, *, requested_run_id: str | None = None) -> tuple[DocumentIR, Path]:
        parent_dir = package_dir(self.settings.document_ir_output_root, parent_run_id)
        reader = DocumentIrPackageReader(parent_dir, ocr_output_root=self.settings.output_root)
        if reader.is_package_v1:
            reader.validate_integrity().require_valid()
        document = reader.load_document(hydrate_local_paths=True)
        revision = DocumentIrVersionManager(self.settings.document_ir_output_root).next_revision(
            document.metadata.ocr_run_id,
            parent_run_id,
            document_id=document.metadata.document_id,
            root_ir_run_id=requested_run_id,
        )
        run_id = requested_run_id or self._new_run_id(document.metadata.ocr_run_id, suffix)
        require_run_id(run_id, "ir")
        output_dir = package_dir(self.settings.document_ir_output_root, run_id)
        create_package_root(output_dir)
        if reader.is_package_v1:
            for relative in ("artifacts/page-images", "artifacts/crops", "artifacts/spreads"):
                source = parent_dir / relative
                if source.exists():
                    shutil.copytree(source, output_dir / relative)
        else:
            self._copy_legacy_visual_artifacts(document, parent_dir, output_dir)
        document.metadata.run_id = run_id
        document.metadata.document_id = revision.document_id
        document.metadata.lineage_id = revision.lineage_id
        document.metadata.ir_revision = revision.revision
        document.metadata.parent_ir_run_id = parent_run_id
        document.metadata.created_at = datetime.now(timezone.utc).isoformat()
        document.schema_version = "document-ir-v0.12"
        document.metadata.pipeline_version = "document-pipeline-v0.12.1"
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
            document = LogicalTableBuilder().build(document)
            document = VisualRegionBuilder().build(document, output_dir)
        document.correction_patches = [
            CorrectionPatch.model_validate(patch.model_dump(mode="json")) for patch in document.atomic_patches
        ]
        document = DocumentIrValidator().validate(document, expected_page_count=len(document.pages))
        paths = DocumentIrWriter(output_dir).write(document)
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["retention"] = DocumentIrRetentionManager(
            ir_output_root=self.settings.document_ir_output_root,
            ir_state_root=self.settings.document_ir_job_state_root,
            cleanup_root=self.settings.storage_cleanup_root,
            pipeline_queue_db=self.settings.pipeline_queue_db,
            enabled=self.settings.document_ir_best_only_retention,
        ).reconcile_safely(document.metadata.run_id)
        return manifest

    @staticmethod
    def _finish_human_task_if_resolved(document: DocumentIR, task_id: str, request: PatchDecisionRequest) -> None:
        task = next((item for item in document.review_tasks if item.task_id == task_id), None)
        if task is None:
            return
        task_patches = [patch for patch in document.atomic_patches if patch.source_task_id == task_id]
        if any(patch.status == "human_required" for patch in task_patches):
            return
        accepted = [patch.patch_id for patch in task_patches if patch.status == "accepted"]
        if not accepted:
            return
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
        task.failure_class = "none"
        task.failure_owner = "none"
        task.retryable = False
        task.failure_fingerprint = None
        task.resume_stage = "complete"
        targets = set(
            task.review_plan.mutable_target_ids
            if task.review_plan
            else [task.target_id, *(scope.target_id for scope in task.scope)]
        )
        for conflict in document.conflict_groups:
            if conflict.target_id in targets and conflict.status in {"open", "human_required"}:
                conflict.status = "resolved"
                conflict.routing_disposition = (
                    "accepted_nonmaterial_difference"
                    if request.action == "accept_current_nonmaterial"
                    else "deterministically_resolved"
                )
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
        if hasattr(target, "spread_id"):
            return "spread"
        if hasattr(target, "cell_id"):
            return "cell"
        if hasattr(target, "section_id"):
            return "section"
        raise TypeError("Unsupported repair target type")

    @staticmethod
    def _new_run_id(ocr_run_id: str, suffix: str) -> str:
        return new_run_id("ir")
