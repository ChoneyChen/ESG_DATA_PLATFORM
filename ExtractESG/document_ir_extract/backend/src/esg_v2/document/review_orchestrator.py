from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    AgentModelCall,
    AtomicPatch,
    AtomicPatchProposal,
    CandidateRevision,
    ConflictGroup,
    CorrectionPatch,
    DocumentIR,
    FinalReviewDecision,
    ReviewerPayload,
    ReviewerResult,
    VerifierPayload,
    VerifierResult,
    VlmReviewTask,
)
from esg_v2.document.patch_guard import DocumentPatchApplier, PatchGuard
from esg_v2.document.identifiers import sequential_id
from esg_v2.models.contracts import CloudChatRequest, CloudChatResult
from esg_v2.models.model_registry import ModelHealthRegistry, ModelProfile, QiniuModelRegistry
from esg_v2.models.qiniu_adapter import QiniuApiError, QiniuModelAdapter
from esg_v2.models.vision_input import QiniuVisionInputResolver


LogFn = Callable[[str], None]
PayloadT = TypeVar("PayloadT", bound=BaseModel)


class ReviewExecutionUnavailable(RuntimeError):
    pass


class AgentReviewOrchestrator:
    """Runs bounded visual review without allowing a model to mutate Document IR directly."""

    def __init__(self, settings: Settings, *, api_key: str | None = None):
        self.settings = settings
        self.adapter = QiniuModelAdapter(settings, api_key=api_key)
        self.registry = QiniuModelRegistry(settings, self.adapter)
        self.input_resolver = QiniuVisionInputResolver()
        self.guard = PatchGuard()
        self.applier = DocumentPatchApplier()

    def execute(
        self,
        document: DocumentIR,
        *,
        review_target_ids: list[str] | None = None,
        max_auto_review_rounds: int = 2,
        log: LogFn | None = None,
    ) -> DocumentIR:
        selected_targets = set(review_target_ids or [])
        try:
            catalog = self.registry.refresh()
        except Exception as exc:
            catalog = {"catalog_error": str(exc), "health": ModelHealthRegistry.snapshot()}
            for task in document.review_tasks:
                if self._selected(task, selected_targets):
                    self._defer(document, task, f"model_catalog_unavailable: {exc}")
            document.quality_report["model_registry"] = catalog
            self._finish_report(document)
            return document

        ordered = sorted(
            (
                task
                for task in document.review_tasks
                if self._selected(task, selected_targets)
                and task.status in {"pending", "queued", "deferred", "failed", "skipped"}
            ),
            key=lambda task: (
                not task.blocking,
                {"critical": 0, "high": 1, "normal": 2, "low": 3}.get(task.priority, 4),
                task.page_index,
            ),
        )
        task_limit = max(0, self.settings.max_vlm_reviews_per_ir_run)
        for index, task in enumerate(ordered):
            if index >= task_limit:
                self._defer(document, task, "review_task_budget_deferred")
                continue
            try:
                self._run_task(
                    document,
                    task,
                    max_rounds=min(max_auto_review_rounds, task.max_attempts),
                    log=log,
                )
            except Exception as exc:
                reason = f"agent_review_internal_error: {type(exc).__name__}: {exc}"
                if log:
                    log(f"Agent review {task.task_id} isolated a non-fatal error: {reason}")
                document.quality_report.setdefault("agent_review_internal_errors", []).append(
                    {"task_id": task.task_id, "error": reason}
                )
                self._defer(document, task, reason, errors=[reason])

        for task in document.review_tasks:
            if task.status == "pending" and selected_targets and not self._selected(task, selected_targets):
                self._defer(document, task, "outside_targeted_review_scope")
        document.quality_report["model_registry"] = self.registry.catalog_snapshot()
        self._finish_report(document)
        document.correction_patches = [
            CorrectionPatch.model_validate(patch.model_dump(mode="json")) for patch in document.atomic_patches
        ]
        return document

    def _run_task(self, document: DocumentIR, task: VlmReviewTask, *, max_rounds: int, log: LogFn | None) -> None:
        task.status = "running"
        images = self.input_resolver.resolve(task.input_refs, limit=2)
        if not images:
            self._defer(document, task, "no_visual_input_available")
            return

        feedback: list[str] = []
        infrastructure_errors: list[str] = []
        for round_index in range(1, max_rounds + 1):
            task.attempt_count = round_index
            if log:
                log(f"Agent review {task.task_id}: reviewer round {round_index}/{max_rounds}")
            try:
                reviewer_payload, cloud_result, profile = self._call_model(
                    document,
                    task,
                    role="reviewer",
                    round_index=round_index,
                    images=images,
                    context=self._reviewer_context(document, task, feedback),
                    payload_type=ReviewerPayload,
                )
            except ReviewExecutionUnavailable as exc:
                infrastructure_errors.append(str(exc))
                self._defer(document, task, str(exc), errors=infrastructure_errors)
                return

            reviewer_result, patches = self._record_reviewer(
                document,
                task,
                round_index,
                reviewer_payload,
                cloud_result,
                profile,
            )
            if reviewer_payload.verdict == "abstain":
                feedback = [reviewer_payload.abstain_reason or "Reviewer abstained without a reason."]
                if round_index < max_rounds:
                    continue
                self._semantic_unresolved(document, task, "reviewer_abstained_after_repair", reviewer_result)
                return

            allowed_targets = {task.target_id, *(scope.target_id for scope in task.scope)}
            scoped_table_ids = {scope.target_id for scope in task.scope if scope.target_type == "table"}
            allowed_targets.update(
                cell.cell_id
                for table in document.tables
                if table.table_id in scoped_table_ids
                for cell in table.cells
            )
            required_targets = {scope.target_id for scope in task.scope if scope.blocking}
            guard = self.guard.evaluate(
                document,
                task.task_id,
                patches,
                allowed_target_ids=allowed_targets,
                required_target_ids=required_targets,
            )
            document.guard_results.append(guard)
            task.guard_result_ids.append(guard.guard_result_id)
            if not guard.passed:
                for patch in patches:
                    patch.status = "guard_failed"
                feedback = [check.message for check in guard.checks if not check.passed]
                self._record_candidate(document, task, patches, status="guard_failed")
                if round_index < max_rounds:
                    continue
                self._semantic_unresolved(document, task, "patch_guard_failed_after_repair", reviewer_result)
                return

            for patch in patches:
                patch.status = "guard_passed"
            candidate = self._record_candidate(document, task, patches, status="proposed")

            if log:
                log(f"Agent review {task.task_id}: independent verifier round {round_index}")
            try:
                verifier_payload, verifier_cloud, verifier_profile = self._call_model(
                    document,
                    task,
                    role="verifier",
                    round_index=round_index,
                    images=images,
                    context=self._verifier_context(document, task, reviewer_result, patches),
                    payload_type=VerifierPayload,
                    exclude_family=profile.family,
                )
            except ReviewExecutionUnavailable as exc:
                candidate.status = "rejected"
                infrastructure_errors.append(str(exc))
                self._defer(document, task, str(exc), errors=infrastructure_errors)
                return

            verifier = VerifierResult(
                verifier_result_id=sequential_id(
                    "verifier",
                    (item.verifier_result_id for item in document.verifier_results),
                ),
                task_id=task.task_id,
                model_id=verifier_profile.model_id,
                model_family=verifier_profile.family,
                reviewer_result_id=reviewer_result.reviewer_result_id,
                patch_ids=[patch.patch_id for patch in patches],
                verdict=verifier_payload.verdict,
                disagreements=verifier_payload.disagreements,
                confidence=verifier_payload.confidence,
                usage=verifier_cloud.usage,
                latency_ms=verifier_cloud.latency_ms,
                raw_response=verifier_cloud.raw_response,
            )
            document.verifier_results.append(verifier)
            task.verifier_result_ids.append(verifier.verifier_result_id)

            if verifier.verdict == "accept":
                candidate.status = "verified"
                self.applier.apply(document, patches)
                candidate.status = "accepted"
                self._resolve_task(document, task, reviewer_result, guard.guard_result_id, verifier)
                return

            candidate.status = "rejected"
            for patch in patches:
                patch.status = "rejected"
            feedback = verifier.disagreements or [f"Independent verifier returned {verifier.verdict}."]
            if round_index < max_rounds:
                continue
            self._semantic_unresolved(
                document,
                task,
                "independent_verifier_disagreed_after_repair",
                reviewer_result,
                verifier=verifier,
            )
            return

    def _call_model(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        *,
        role: str,
        round_index: int,
        images: list[str],
        context: str,
        payload_type: type[PayloadT],
        exclude_family: str | None = None,
    ) -> tuple[PayloadT, CloudChatResult, ModelProfile]:
        profiles = self.registry.candidates(role, exclude_family=exclude_family)
        if not profiles:
            raise ReviewExecutionUnavailable(f"no_healthy_{role}_model_available")
        errors = []
        for profile in profiles:
            request = self._build_request(task, profile, role, round_index, images, context)
            result: CloudChatResult | None = None
            try:
                result = self.adapter.chat_completions(request)
                payload_dict = self._message_json(result.raw_response)
                if role == "reviewer":
                    payload_dict = self._normalize_reviewer_payload(document, payload_dict)
                payload = payload_type.model_validate(payload_dict)
                ModelHealthRegistry.record_success(profile.model_id, result.latency_ms)
                document.model_calls.append(
                    AgentModelCall(
                        call_id=sequential_id("model-call", (item.call_id for item in document.model_calls)),
                        task_id=task.task_id,
                        role=role,
                        round_index=round_index,
                        model_id=profile.model_id,
                        model_family=profile.family,
                        status="succeeded",
                        request_summary=self._request_summary(task, images, context),
                        response_payload=payload_dict,
                        raw_response=result.raw_response,
                        usage=result.usage,
                        latency_ms=result.latency_ms,
                    )
                )
                return payload, result, profile
            except QiniuApiError as exc:
                ModelHealthRegistry.record_failure(profile.model_id, str(exc), retryable=exc.retryable)
                error = str(exc)[:2000]
                errors.append(f"{profile.model_id}: {error}")
                document.model_calls.append(
                    AgentModelCall(
                        call_id=sequential_id("model-call", (item.call_id for item in document.model_calls)),
                        task_id=task.task_id,
                        role=role,
                        round_index=round_index,
                        model_id=profile.model_id,
                        model_family=profile.family,
                        status="failed",
                        request_summary=self._request_summary(task, images, context),
                        error=error,
                    )
                )
                if exc.account_wide_rate_limit:
                    raise ReviewExecutionUnavailable(f"qiniu_account_rate_limited: {error}") from exc
            except (ValueError, ValidationError, TypeError) as exc:
                ModelHealthRegistry.record_failure(profile.model_id, f"invalid_response: {exc}")
                error = str(exc)[:2000]
                errors.append(f"{profile.model_id}: invalid_response: {error}")
                document.model_calls.append(
                    AgentModelCall(
                        call_id=sequential_id("model-call", (item.call_id for item in document.model_calls)),
                        task_id=task.task_id,
                        role=role,
                        round_index=round_index,
                        model_id=profile.model_id,
                        model_family=profile.family,
                        status="invalid_response",
                        request_summary=self._request_summary(task, images, context),
                        raw_response=result.raw_response if result else None,
                        usage=result.usage if result else {},
                        latency_ms=result.latency_ms if result else None,
                        error=error,
                    )
                )
        raise ReviewExecutionUnavailable(f"all_{role}_models_failed: {' | '.join(errors)}")

    def _record_reviewer(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        round_index: int,
        payload: ReviewerPayload,
        cloud_result: CloudChatResult,
        profile: ModelProfile,
    ) -> tuple[ReviewerResult, list[AtomicPatch]]:
        reviewer_id = sequential_id(
            "reviewer",
            (item.reviewer_result_id for item in document.reviewer_results),
        )
        proposals = payload.patches
        if payload.verdict == "confirm":
            scope = task.scope or []
            proposals = [
                AtomicPatchProposal(
                    target_type=item.target_type,
                    target_id=item.target_id,
                    operation="confirm",
                    evidence_refs=task.input_refs,
                    rationale="Reviewer confirmed the routed candidate against the supplied visual evidence.",
                    confidence=payload.confidence,
                )
                for item in scope
            ]
            if not proposals:
                proposals = [
                    AtomicPatchProposal(
                        target_type=task.target_type,
                        target_id=task.target_id,
                        operation="confirm",
                        evidence_refs=task.input_refs,
                        confidence=payload.confidence,
                    )
                ]

        patches = []
        for index, proposal in enumerate(proposals, start=1):
            patches.append(
                AtomicPatch(
                    patch_id=sequential_id(
                        "patch",
                        (item.patch_id for item in [*document.atomic_patches, *patches]),
                    ),
                    source_task_id=task.task_id,
                    source_reviewer_result_id=reviewer_id,
                    target_type=proposal.target_type,
                    target_id=proposal.target_id,
                    operation=proposal.operation,
                    field_path=proposal.field_path,
                    before_value=proposal.before_value,
                    proposed_value=proposal.proposed_value,
                    evidence_refs=proposal.evidence_refs or task.input_refs,
                    confidence=proposal.confidence,
                    risk_level=self._risk(proposal.operation),
                    rationale=proposal.rationale,
                )
            )
        document.atomic_patches.extend(patches)
        reviewer = ReviewerResult(
            reviewer_result_id=reviewer_id,
            task_id=task.task_id,
            model_id=profile.model_id,
            model_family=profile.family,
            attempt=round_index,
            verdict=payload.verdict,
            findings=payload.findings,
            patch_ids=[patch.patch_id for patch in patches],
            confidence=payload.confidence,
            abstain_reason=payload.abstain_reason,
            quality_flags=payload.quality_flags,
            usage=cloud_result.usage,
            latency_ms=cloud_result.latency_ms,
            raw_response=cloud_result.raw_response,
        )
        document.reviewer_results.append(reviewer)
        task.reviewer_result_ids.append(reviewer.reviewer_result_id)
        task.model_name = profile.model_id
        return reviewer, patches

    def _record_candidate(self, document: DocumentIR, task: VlmReviewTask, patches: list[AtomicPatch], *, status: str) -> CandidateRevision:
        target_ids = list(dict.fromkeys(patch.target_id for patch in patches))
        before = self.applier.snapshot(document, target_ids)
        after = deepcopy(before)
        if status != "guard_failed":
            candidate_document = document.model_copy(deep=True)
            candidate_patches = [patch.model_copy(deep=True) for patch in patches]
            self.applier.apply(candidate_document, candidate_patches)
            after = self.applier.snapshot(candidate_document, target_ids)
        candidate = CandidateRevision(
            candidate_id=sequential_id(
                "candidate",
                (item.candidate_id for item in document.candidate_revisions),
            ),
            task_id=task.task_id,
            based_on_run_id=document.metadata.run_id,
            patch_ids=[patch.patch_id for patch in patches],
            target_snapshots=before,
            before_snapshots=before,
            after_snapshots=after,
            diffs=self._diff_snapshots(before, after),
            status=status,
        )
        document.candidate_revisions.append(candidate)
        return candidate

    def _resolve_task(self, document: DocumentIR, task: VlmReviewTask, reviewer: ReviewerResult, guard_id: str, verifier: VerifierResult) -> None:
        accepted = [patch for patch in document.atomic_patches if patch.patch_id in verifier.patch_ids]
        corrected = any(patch.operation != "confirm" for patch in accepted)
        decision = FinalReviewDecision(
            decision_id=sequential_id(
                "decision",
                (item.decision_id for item in document.final_decisions),
            ),
            task_id=task.task_id,
            outcome="auto_corrected" if corrected else "auto_confirmed",
            blocking_resolved=True,
            accepted_patch_ids=[patch.patch_id for patch in accepted],
            reviewer_result_ids=[reviewer.reviewer_result_id],
            guard_result_ids=[guard_id],
            verifier_result_ids=[verifier.verifier_result_id],
            reason="Reviewer proposal passed deterministic guards and an independent model-family verifier.",
            decided_by="agent_consensus",
        )
        document.final_decisions.append(decision)
        task.final_decision_id = decision.decision_id
        task.status = "auto_resolved"
        task.result = {"outcome": decision.outcome, "decision_id": decision.decision_id}
        resolved_targets = {task.target_id, *(scope.target_id for scope in task.scope)}
        for conflict in document.conflict_groups:
            if conflict.target_id in resolved_targets and conflict.status in {"open", "human_required"}:
                conflict.status = "auto_resolved"
                conflict.resolution = decision.reason

    def _semantic_unresolved(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        reason: str,
        reviewer: ReviewerResult,
        *,
        verifier: VerifierResult | None = None,
    ) -> None:
        if task.blocking:
            outcome = "human_required"
            task.status = "human_required"
            current_patch_ids = set(reviewer.patch_ids)
            for patch in document.atomic_patches:
                if patch.patch_id in current_patch_ids and not reason.startswith("patch_guard_failed"):
                    patch.status = "human_required"
            conflict = next((item for item in document.conflict_groups if item.target_id == task.target_id), None)
            if conflict is None:
                document.conflict_groups.append(
                    ConflictGroup(
                        conflict_id=sequential_id(
                            "conflict",
                            (item.conflict_id for item in document.conflict_groups),
                        ),
                        target_id=task.target_id,
                        conflict_type=reason,
                        observation_refs=[reviewer.reviewer_result_id, *([verifier.verifier_result_id] if verifier else [])],
                        patch_ids=reviewer.patch_ids,
                        blocking=True,
                        status="human_required",
                    )
                )
            else:
                conflict.status = "human_required"
                conflict.resolution = reason
        else:
            outcome = "rejected"
            task.status = "auto_resolved"
        decision = FinalReviewDecision(
            decision_id=sequential_id(
                "decision",
                (item.decision_id for item in document.final_decisions),
            ),
            task_id=task.task_id,
            outcome=outcome,
            blocking_resolved=not task.blocking,
            accepted_patch_ids=[],
            reviewer_result_ids=[reviewer.reviewer_result_id],
            guard_result_ids=list(task.guard_result_ids),
            verifier_result_ids=[verifier.verifier_result_id] if verifier else [],
            reason=reason,
            decided_by="agent_consensus",
        )
        document.final_decisions.append(decision)
        task.final_decision_id = decision.decision_id
        task.result = {"outcome": outcome, "decision_id": decision.decision_id, "reason": reason}

    def _defer(self, document: DocumentIR, task: VlmReviewTask, reason: str, *, errors: list[str] | None = None) -> None:
        task.status = "deferred"
        decision = FinalReviewDecision(
            decision_id=sequential_id(
                "decision",
                (item.decision_id for item in document.final_decisions),
            ),
            task_id=task.task_id,
            outcome="deferred",
            blocking_resolved=False,
            reason=reason,
            decided_by="deterministic",
        )
        document.final_decisions.append(decision)
        task.final_decision_id = decision.decision_id
        task.result = {"outcome": "deferred", "reason": reason, "errors": errors or []}

    def _reviewer_context(self, document: DocumentIR, task: VlmReviewTask, feedback: list[str]) -> str:
        payload = {
            "task": {
                "task_id": task.task_id,
                "page_index": task.page_index,
                "reason_codes": task.reason_codes,
                "blocking": task.blocking,
                "scope": [scope.model_dump(mode="json") for scope in task.scope],
            },
            "candidate": self._candidate_context(document, task),
            "repair_feedback": feedback,
            "rules": [
                "Judge only the supplied page images and candidate IR.",
                "Return confirm when every routed scope is materially correct.",
                "For correct, emit only minimum atomic patches and keep target_id inside scope.",
                "Never rewrite a whole page and never add ESG facts not visible in evidence.",
                "Use zero-based row_index and col_index for table grids.",
                "Use abstain when the visual evidence cannot support a reliable decision.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:30000]

    def _verifier_context(self, document: DocumentIR, task: VlmReviewTask, reviewer: ReviewerResult, patches: list[AtomicPatch]) -> str:
        payload = {
            "task": {"task_id": task.task_id, "reason_codes": task.reason_codes},
            "candidate": self._candidate_context(document, task),
            "reviewer_result": reviewer.model_dump(mode="json", exclude={"raw_response", "usage"}),
            "proposed_patches": [patch.model_dump(mode="json") for patch in patches],
            "rules": [
                "Independently compare candidate and proposed patches against the supplied visual evidence.",
                "Accept only when every patch is supported, atomic, and complete for the routed risk.",
                "Reject on unsupported content, wrong target, destructive replacement, or missed material error.",
                "Abstain only when the evidence itself is unreadable or ambiguous.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:30000]

    @staticmethod
    def _candidate_context(document: DocumentIR, task: VlmReviewTask) -> dict[str, Any]:
        targets = {task.target_id, *(scope.target_id for scope in task.scope)}
        page = next((item for item in document.pages if item.page_index == task.page_index), None)
        result: dict[str, Any] = {
            "page": {
                "page_id": page.page_id,
                "printed_page_label": page.printed_page_label,
                "width": page.width,
                "height": page.height,
                "text": page.text[:5000],
                "quality_flags": page.quality_flags,
            }
            if page
            else None,
            "targets": [],
        }
        for item in [*document.blocks, *document.tables, *document.figures, *document.sections]:
            item_id = next(
                (
                    value
                    for value in (
                        getattr(item, "block_id", None),
                        getattr(item, "table_id", None),
                        getattr(item, "figure_id", None),
                        getattr(item, "section_id", None),
                    )
                    if value
                ),
                None,
            )
            if item_id not in targets:
                continue
            raw = item.model_dump(
                mode="json",
                exclude={"source_trace", "graph_edges", "observations", "markdown", "review_task_ids"},
            )
            raw["target_type"] = PatchGuard._target_type(item)
            if "cells" in raw:
                raw["cells"] = [
                    {
                        key: cell.get(key)
                        for key in ("cell_id", "row_index", "col_index", "text", "row_span", "col_span", "is_header", "unit_hint")
                    }
                    for cell in raw["cells"][:200]
                ]
            result["targets"].append(raw)
        return result

    def _build_request(
        self,
        task: VlmReviewTask,
        profile: ModelProfile,
        role: str,
        round_index: int,
        images: list[str],
        context: str,
    ) -> CloudChatRequest:
        if role == "reviewer":
            instruction = (
                "Return one JSON object with verdict (confirm|correct|abstain), findings, patches, confidence, "
                "abstain_reason, and quality_flags. Every patch must contain target_type, target_id, operation, "
                "field_path, before_value, proposed_value, evidence_refs, rationale, and confidence. "
                "Allowed operation values are ONLY: confirm, replace_block_text, replace_cell_text, set_table_grid, "
                "insert_table_row, set_bbox, set_printed_page_label, set_visual_type, set_caption, link_continuation, merge_blocks, "
                "split_block, add_quality_flags. Never use replace_row, replace_cell, insert_rows, update_field, or "
                "any invented operation. For a missing table row, emit exactly one insert_table_row patch when all "
                "existing rows remain valid; proposed_value must contain insert_before_row_index and cells covering "
                "the existing table width. For deletion, reordering, span changes, or corrections affecting existing "
                "rows, use set_table_grid and provide the COMPLETE final rectangular grid "
                "with row_count, column_count, and non-overlapping cells. Valid visual_type values are unknown, chart, "
                "diagram, illustration, photo, icon, decoration, and composite. Never emit set_caption when no visible "
                "caption exists and never propose an empty caption. findings and quality_flags must be arrays of strings."
            )
        else:
            instruction = "Return one JSON object with verdict (accept|reject|abstain), disagreements, and confidence."
        content: list[dict[str, Any]] = [{"type": "text", "text": f"{instruction}\n\n{context}"}]
        content.extend({"type": "image_url", "image_url": {"url": image}} for image in images)
        reasoning_effort = "low"
        thinking = {"type": "disabled"} if profile.family in {"qwen", "kimi"} else None
        has_table_scope = task.target_type == "table" or any(item.target_type in {"table", "cell"} for item in task.scope)
        max_tokens = 6000 if role == "reviewer" and has_table_scope else 3000 if role == "reviewer" else 1200
        return CloudChatRequest(
            request_id=f"{task.task_id}-{role}-r{round_index}",
            model_id=profile.model_id,
            max_tokens=max_tokens,
            temperature=0.1,
            response_format={"type": "json_object"},
            reasoning_effort=reasoning_effort,
            thinking=thinking,
            timeout_seconds=self.settings.qiniu_default_timeout_seconds,
            metadata={"task_type": task.task_type, "role": role, "round_index": round_index},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a controlled Document IR visual review agent. Your scope ends at faithful document "
                        "structure and OCR reconstruction; you do not extract or infer ESG facts. Output strict JSON only."
                    ),
                },
                {"role": "user", "content": content},
            ],
        )

    @staticmethod
    def _message_json(raw_response: dict[str, Any]) -> dict[str, Any]:
        choices = raw_response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Response has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Response message content is empty")
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            if start < 0:
                raise ValueError("Response contains no JSON object") from None
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        if not isinstance(value, dict):
            raise ValueError("Response JSON is not an object")
        return value

    @staticmethod
    def _normalize_reviewer_payload(document: DocumentIR, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        findings = normalized.get("findings")
        if isinstance(findings, dict):
            normalized["findings"] = [json.dumps(findings, ensure_ascii=False, sort_keys=True)]
        elif isinstance(findings, list):
            normalized["findings"] = [
                item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
                for item in findings
            ]
        patches = []
        for raw_patch in normalized.get("patches") or []:
            if not isinstance(raw_patch, dict):
                patches.append(raw_patch)
                continue
            patch = dict(raw_patch)
            operation = patch.get("operation")
            field_path = str(patch.get("field_path") or "")
            if operation == "update_field" and field_path in {"quality_flags", "visual_type", "caption"}:
                patch["operation"] = {
                    "quality_flags": "add_quality_flags",
                    "visual_type": "set_visual_type",
                    "caption": "set_caption",
                }[field_path]
            elif operation == "replace_cell" and patch.get("target_type") == "table":
                match = re.search(r"rows\[(\d+)]\.cells\[(\d+)]", field_path)
                table = next((item for item in document.tables if item.table_id == patch.get("target_id")), None)
                if match and table:
                    row_index, col_index = int(match.group(1)), int(match.group(2))
                    cell = next(
                        (item for item in table.cells if item.row_index == row_index and item.col_index == col_index),
                        None,
                    )
                    proposed = patch.get("proposed_value")
                    before = patch.get("before_value")
                    if cell:
                        patch.update(
                            {
                                "target_type": "cell",
                                "target_id": cell.cell_id,
                                "operation": "replace_cell_text",
                                "field_path": "text",
                                "before_value": before.get("text") if isinstance(before, dict) and "text" in before else cell.text,
                                "proposed_value": proposed.get("text") if isinstance(proposed, dict) and "text" in proposed else proposed,
                            }
                        )
            patches.append(patch)
        normalized["patches"] = patches
        return normalized

    @staticmethod
    def _diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
        diffs = []
        for target_id in sorted(set(before) | set(after)):
            first = before.get(target_id)
            second = after.get(target_id)
            if first == second:
                continue
            if isinstance(first, dict) and isinstance(second, dict):
                for key in sorted(set(first) | set(second)):
                    if first.get(key) != second.get(key):
                        diffs.append({"target_id": target_id, "field": key, "before": first.get(key), "after": second.get(key)})
            else:
                diffs.append({"target_id": target_id, "field": "$", "before": first, "after": second})
        return diffs

    @staticmethod
    def _selected(task: VlmReviewTask, selected_targets: set[str]) -> bool:
        if not selected_targets:
            return True
        return task.target_id in selected_targets or any(scope.target_id in selected_targets for scope in task.scope)

    @staticmethod
    def _risk(operation: str) -> str:
        if operation == "confirm":
            return "low"
        if operation in {"replace_block_text", "replace_cell_text", "set_visual_type", "set_caption", "set_printed_page_label", "add_quality_flags"}:
            return "medium"
        return "high"

    @staticmethod
    def _request_summary(task: VlmReviewTask, images: list[str], context: str) -> dict[str, Any]:
        return {
            "task_type": task.task_type,
            "page_index": task.page_index,
            "reason_codes": task.reason_codes,
            "image_count": len(images),
            "context_characters": len(context),
        }

    @staticmethod
    def _finish_report(document: DocumentIR) -> None:
        document.quality_report.update(
            {
                "agent_review_execution": "completed",
                "vlm_execution": "executed",
                "model_call_count": len(document.model_calls),
                "reviewer_result_count": len(document.reviewer_results),
                "guard_result_count": len(document.guard_results),
                "verifier_result_count": len(document.verifier_results),
                "final_decision_count": len(document.final_decisions),
                "auto_confirmed_count": sum(1 for item in document.final_decisions if item.outcome == "auto_confirmed"),
                "auto_corrected_count": sum(1 for item in document.final_decisions if item.outcome == "auto_corrected"),
                "human_required_count": sum(1 for item in document.final_decisions if item.outcome == "human_required"),
                "deferred_count": sum(1 for item in document.final_decisions if item.outcome == "deferred"),
            }
        )
