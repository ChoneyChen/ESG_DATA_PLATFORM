from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from esg_v2.config import Settings
from esg_v2.document.contracts import (
    AtomicPatch,
    AtomicPatchProposal,
    BoundingBox,
    CandidateRevision,
    ConflictGroup,
    CorrectionPatch,
    DocumentIR,
    FinalReviewDecision,
    FigureIR,
    PatchTransactionIR,
    ReviewerPayload,
    ReviewerResult,
    SpreadIR,
    VerifierPayload,
    VerifierResult,
    VlmReviewTask,
)
from esg_v2.document.coordinate_mapper import CoordinateMapper
from esg_v2.document.geometry import bbox_containment
from esg_v2.document.patch_guard import DocumentPatchApplier, PatchGuard
from esg_v2.document.identifiers import sequential_id
from esg_v2.document.review_policy import ReviewPolicyEngine
from esg_v2.document.review_plan_compiler import ReviewPlanCompiler, ReviewPlanContractError
from esg_v2.document.operation_registry import OperationRegistry
from esg_v2.document.review_scheduler import ReviewScheduler
from esg_v2.document.transaction_coordinator import TransactionCoordinator
from esg_v2.document.convergence_engine import ConvergenceEngine
from esg_v2.document.review_context import ReviewContextCompiler
from esg_v2.document.review_response_adapter import ReviewResponseAdapter
from esg_v2.document.model_runner import (
    ModelRunner,
    ProviderAccountQuotaUnavailable,
    ReviewExecutionUnavailable,
)
from esg_v2.models.contracts import CloudChatResult
from esg_v2.models.model_registry import ModelHealthRegistry, ModelProfile, QiniuModelRegistry
from esg_v2.models.provider_rate_limit import ProviderRateLimitCoordinator
from esg_v2.models.qiniu_adapter import QiniuApiError, QiniuModelAdapter
from esg_v2.models.vision_input import QiniuVisionInputResolver


LogFn = Callable[[str], None]
PayloadT = TypeVar("PayloadT", bound=BaseModel)


class AgentReviewOrchestrator:
    """Runs bounded visual review without allowing a model to mutate Document IR directly."""

    def __init__(
        self,
        settings: Settings,
        *,
        api_key: str | None = None,
        telemetry: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.settings = settings
        self.adapter = QiniuModelAdapter(settings, api_key=api_key)
        self.rate_limits = ProviderRateLimitCoordinator(
            settings,
            self.adapter.credential_scope_id,
        )
        self.registry = QiniuModelRegistry(settings, self.adapter)
        self.input_resolver = QiniuVisionInputResolver()
        self.guard = PatchGuard()
        self.applier = DocumentPatchApplier()
        self.policy = ReviewPolicyEngine()
        self.plan_compiler = ReviewPlanCompiler()
        self.scheduler = ReviewScheduler(settings)
        self.transactions = TransactionCoordinator()
        self.convergence = ConvergenceEngine()
        self.context_compiler = ReviewContextCompiler()
        self.response_adapter = ReviewResponseAdapter()
        self.telemetry = telemetry
        self.model_runner = ModelRunner(
            settings,
            self.adapter,
            self.registry,
            self.response_adapter,
            telemetry=telemetry,
            rate_limits=self.rate_limits,
        )

    def execute(
        self,
        document: DocumentIR,
        *,
        review_target_ids: list[str] | None = None,
        max_auto_review_rounds: int = 3,
        log: LogFn | None = None,
    ) -> DocumentIR:
        selected_targets = set(review_target_ids or [])
        for task in document.review_tasks:
            try:
                self.plan_compiler.validate(document, task)
            except ReviewPlanContractError as exc:
                task.status = "failed"
                task.failure_class = "system_contract"
                task.failure_owner = "system"
                task.retryable = False
                task.result = {
                    "plan_preflight_issues": [
                        {
                            "code": issue.code,
                            "message": issue.message,
                            "target_ids": list(issue.target_ids),
                            "recommended_operation": issue.recommended_operation,
                        }
                        for issue in exc.issues
                    ]
                }
        retryable_statuses = {"pending", "queued", "deferred", "failed", "skipped"}
        deterministic_task_ids: list[str] = []
        for task in document.review_tasks:
            if (
                not self._selected(task, selected_targets)
                or task.status not in retryable_statuses
                or not task.retryable
            ):
                continue
            proposal = self.policy.deterministic_proposal(document, task)
            if proposal is None:
                continue
            task.status = "running"
            self._resolve_deterministic_proposal(document, task, proposal.proposal, proposal.reason)
            deterministic_task_ids.append(task.task_id)

        remaining_selected = [
            task
            for task in document.review_tasks
            if (
                self._selected(task, selected_targets)
                and task.status in retryable_statuses
                and task.retryable
            )
        ]
        if not remaining_selected:
            for task in document.review_tasks:
                if task.status == "pending" and selected_targets and not self._selected(task, selected_targets):
                    self._defer(document, task, "outside_targeted_review_scope")
            document.quality_report["review_scheduler"] = {
                "mode": "deterministic_only",
                "eligible_count": len(deterministic_task_ids),
                "scheduled_count": len(deterministic_task_ids),
                "deterministic_preflight_count": len(deterministic_task_ids),
                "scheduled_blocking_count": sum(
                    1 for task in document.review_tasks if task.task_id in deterministic_task_ids and task.blocking
                ),
                "scheduled_optional_count": sum(
                    1 for task in document.review_tasks if task.task_id in deterministic_task_ids and not task.blocking
                ),
                "scheduler_deferred": 0,
                "batch_preflight_group_count": 0,
                "batch_preflight_task_count": 0,
                "completeness_mode": self.settings.review_completeness_mode,
                "hard_limit": self.settings.max_vlm_reviews_per_ir_run,
                "blocking_cap": self.settings.max_blocking_vlm_reviews_per_ir_run,
                "optional_cap": self.settings.max_optional_vlm_reviews_per_ir_run,
            }
            document.quality_report["model_registry"] = {
                "catalog_skipped": "all selected tasks resolved by deterministic preflight"
            }
            self._finish_report(document)
            document.correction_patches = [
                CorrectionPatch.model_validate(patch.model_dump(mode="json"))
                for patch in document.atomic_patches
            ]
            return document
        cached_provider_block = self.rate_limits.account_block()
        if cached_provider_block is not None:
            reason = self._provider_block_reason(cached_provider_block, cached=True)
            skipped = self._defer_provider_blocked_tasks(
                document,
                remaining_selected,
                reason,
                provider_state=cached_provider_block,
            )
            document.quality_report["provider_rate_limit"] = {
                **self.rate_limits.snapshot(),
                "run_short_circuited": True,
                "http_skipped_task_count": skipped,
            }
            document.quality_report["model_registry"] = {
                "catalog_skipped": "provider account TPD circuit is open",
                "health": ModelHealthRegistry.snapshot(),
            }
            self._finish_report(document)
            return document
        try:
            catalog = self.registry.refresh()
        except QiniuApiError as exc:
            if exc.account_wide_rate_limit:
                provider_state = self.rate_limits.record_tpd(
                    str(exc),
                    retry_after_seconds=exc.retry_after_seconds,
                    request_id=exc.request_id,
                )
                reason = self._provider_block_reason(provider_state, cached=False)
                skipped = self._defer_provider_blocked_tasks(
                    document,
                    remaining_selected,
                    reason,
                    provider_state=provider_state,
                )
                document.quality_report["provider_rate_limit"] = {
                    **self.rate_limits.snapshot(),
                    "run_short_circuited": True,
                    "limit_trigger_stage": "model_catalog",
                    "http_skipped_task_count": skipped,
                }
                document.quality_report["model_registry"] = {
                    "catalog_error": str(exc),
                    "catalog_skipped": "provider account TPD circuit opened during model catalog request",
                    "health": ModelHealthRegistry.snapshot(),
                }
                self._finish_report(document)
                return document
            catalog = {"catalog_error": str(exc), "health": ModelHealthRegistry.snapshot()}
            for task in remaining_selected:
                self._defer(document, task, f"model_catalog_unavailable: {exc}")
            document.quality_report["model_registry"] = catalog
            self._finish_report(document)
            return document
        except Exception as exc:
            catalog = {"catalog_error": str(exc), "health": ModelHealthRegistry.snapshot()}
            for task in document.review_tasks:
                if (
                    self._selected(task, selected_targets)
                    and task.status in retryable_statuses
                    and task.retryable
                ):
                    self._defer(document, task, f"model_catalog_unavailable: {exc}")
            document.quality_report["model_registry"] = catalog
            self._finish_report(document)
            return document

        schedule = self.scheduler.schedule(
            [
                task
                for task in document.review_tasks
                if self._selected(task, selected_targets)
                and task.status in retryable_statuses
                and task.retryable
            ],
            explicitly_targeted=bool(selected_targets),
            deterministic_count=len(deterministic_task_ids),
        )
        scheduled = set(schedule.scheduled_task_ids)
        document.quality_report["review_scheduler"] = schedule.metrics
        document.quality_report["review_batch_preflight"] = {
            "groups": [list(group) for group in schedule.batch_preflight_groups],
            "status": "scheduled_for_complete_evaluation",
        }
        provider_blocked = False
        provider_state: dict[str, Any] = {}
        provider_reason = ""
        provider_skipped_count = 0
        for task in schedule.ordered_tasks:
            if task.status not in retryable_statuses or not task.retryable:
                continue
            if task.task_id not in scheduled:
                self._defer(document, task, "review_task_budget_deferred")
                continue
            if provider_blocked:
                provider_skipped_count += self._defer_provider_blocked_tasks(
                    document,
                    [task],
                    provider_reason,
                    provider_state=provider_state,
                )
                continue
            try:
                self._run_task(
                    document,
                    task,
                    max_rounds=min(max_auto_review_rounds, task.max_attempts),
                    log=log,
                )
            except ProviderAccountQuotaUnavailable as exc:
                provider_blocked = True
                provider_state = exc.provider_state or self.rate_limits.snapshot()
                provider_reason = str(exc)
                self._defer(
                    document,
                    task,
                    provider_reason,
                    errors=[provider_reason],
                )
                task.result = {
                    **(task.result or {}),
                    "provider_state": provider_state,
                    "http_attempted": True,
                    "resume_stage": task.resume_stage,
                }
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
        document.quality_report["provider_rate_limit"] = {
            **self.rate_limits.snapshot(),
            "run_short_circuited": provider_blocked,
            "http_skipped_task_count": provider_skipped_count,
        }
        self._finish_report(document)
        document.correction_patches = [
            CorrectionPatch.model_validate(patch.model_dump(mode="json")) for patch in document.atomic_patches
        ]
        return document

    def _run_task(self, document: DocumentIR, task: VlmReviewTask, *, max_rounds: int, log: LogFn | None) -> None:
        task.status = "running"
        task.execution_count += 1
        task.last_attempt_run_id = document.metadata.run_id
        task.last_attempt_at = datetime.now(timezone.utc).isoformat()
        image_limit = 3 if task.target_type == "spread" else 2
        images = self.input_resolver.resolve(task.input_refs, limit=image_limit)
        if not images:
            self._defer(document, task, "no_visual_input_available")
            return

        feedback: list[str] = []
        infrastructure_errors: list[str] = []
        reviewer_exclude_family: str | None = None
        required_targets = set(
            task.review_plan.required_decision_target_ids
            if task.review_plan
            else [scope.target_id for scope in task.scope if scope.blocking]
        )
        if task.blocking and not required_targets:
            required_targets.add(task.target_id)
        resolved_required_targets = {
            target_id
            for transaction in document.patch_transactions
            if transaction.task_id == task.task_id and transaction.status == "accepted"
            for target_id in transaction.target_ids
        }
        if not resolved_required_targets:
            resolved_required_targets = self.transactions.covered_targets(
                document,
                [
                    patch
                    for patch in document.atomic_patches
                    if patch.source_task_id == task.task_id and patch.status == "accepted"
                ],
            )
        checkpoint = self._verifier_checkpoint(document, task)
        if checkpoint is not None:
            terminal, feedback, reviewer_exclude_family = self._resume_verifier_checkpoint(
                document,
                task,
                checkpoint,
                images=images,
                required_targets=required_targets,
                resolved_required_targets=resolved_required_targets,
                log=log,
            )
            if terminal:
                return
            task.resume_stage = "repair_pending"
        for local_round in range(1, max_rounds + 1):
            task.attempt_count += 1
            round_index = task.attempt_count
            task.resume_stage = "reviewer_pending"
            if log:
                log(
                    f"Agent review {task.task_id}: reviewer attempt {round_index} "
                    f"(run round {local_round}/{max_rounds})"
                )
            try:
                task.reviewer_call_count += 1
                reviewer_payload, cloud_result, profile = self._call_model(
                    document,
                    task,
                    role="reviewer",
                    round_index=round_index,
                    images=images,
                    context=self.context_compiler.reviewer(document, task, feedback),
                    payload_type=ReviewerPayload,
                    exclude_family=reviewer_exclude_family,
                )
            except ProviderAccountQuotaUnavailable:
                raise
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
                if local_round < max_rounds:
                    reviewer_exclude_family = profile.family
                    continue
                self._semantic_unresolved(document, task, "reviewer_abstained_after_repair", reviewer_result)
                return

            allowed_targets = set(
                task.review_plan.mutable_target_ids
                if task.review_plan
                else [task.target_id, *(scope.target_id for scope in task.scope)]
            )
            scoped_table_ids = {
                table.table_id
                for table in document.tables
                if table.table_id in allowed_targets
            }
            allowed_targets.update(
                cell.cell_id
                for table in document.tables
                if table.table_id in scoped_table_ids
                for cell in table.cells
            )
            reviewed_targets = {
                decision.target_id
                for decision in reviewer_payload.scope_decisions
                if decision.decision in {"confirm", "propose_patch"}
            }
            if task.target_type == "spread":
                reviewed_targets.discard(task.target_id)
            transactions = self.transactions.plan(
                document,
                task,
                reviewer_result,
                patches,
                required_targets - resolved_required_targets,
            )
            passed_transactions: list[PatchTransactionIR] = []
            passed_patches: list[AtomicPatch] = []
            candidates_by_transaction: dict[str, CandidateRevision] = {}
            failed_feedback: list[str] = []
            for transaction in transactions:
                transaction_patches = [
                    patch
                    for patch in patches
                    if patch.transaction_id == transaction.transaction_id
                ]
                guard = self.guard.evaluate(
                    document,
                    task.task_id,
                    transaction_patches,
                    allowed_target_ids=allowed_targets,
                    required_target_ids=set(transaction.required_target_ids),
                    reviewed_target_ids=reviewed_targets,
                    allowed_operations=set(task.review_plan.allowed_operations) if task.review_plan else None,
                    required_any_operations=(
                        {"confirm_spread", "reject_spread"}
                        if task.target_type == "spread"
                        and task.target_id in transaction.target_ids
                        else None
                    ),
                    transaction_id=transaction.transaction_id,
                )
                document.guard_results.append(guard)
                task.guard_result_ids.append(guard.guard_result_id)
                transaction.guard_result_id = guard.guard_result_id
                if guard.passed:
                    transaction.status = "guard_passed"
                    for patch in transaction_patches:
                        patch.status = "guard_passed"
                    passed_transactions.append(transaction)
                    passed_patches.extend(transaction_patches)
                    candidates_by_transaction[transaction.transaction_id] = self._record_candidate(
                        document,
                        task,
                        transaction_patches,
                        status="proposed",
                        transaction_id=transaction.transaction_id,
                    )
                    continue
                classification = self.convergence.classify_guard_failure(document, guard)
                transaction.status = "guard_failed"
                transaction.failure_class = classification["failure_class"]
                transaction.failure_owner = classification["failure_owner"]
                transaction.retryable = classification["retryable"]
                transaction.failure_fingerprint = classification["fingerprint"]
                transaction.failure_messages = classification["messages"]
                for patch in transaction_patches:
                    patch.status = "guard_failed"
                failed_feedback.extend(classification["messages"])
                self._record_candidate(
                    document,
                    task,
                    transaction_patches,
                    status="guard_failed",
                    transaction_id=transaction.transaction_id,
                )

            if not passed_patches:
                retryable = bool(transactions) and all(
                    transaction.retryable
                    for transaction in transactions
                    if transaction.status == "guard_failed"
                )
                classification = self.convergence.dominant_transaction_failure(transactions)
                feedback = failed_feedback or ["No guard-passing patch transaction was produced."]
                if local_round < max_rounds and retryable:
                    reviewer_exclude_family = profile.family
                    continue
                if classification["failure_class"] in {"model_protocol", "repeated_failure"}:
                    classification = {
                        "failure_class": "repeated_failure",
                        "failure_owner": "model",
                        "retryable": False,
                        "failure_fingerprint": self.convergence.failure_fingerprint(
                            "repeated_failure",
                            feedback,
                        ),
                    }
                self._defer(
                    document,
                    task,
                    "automation_proposal_invalid_after_repair",
                    errors=feedback,
                    **classification,
                )
                return

            if log:
                log(f"Agent review {task.task_id}: independent verifier round {round_index}")
            try:
                task.verifier_call_count += 1
                task.resume_stage = "verifier_pending"
                verifier_payload, verifier_cloud, verifier_profile = self._call_model(
                    document,
                    task,
                    role="verifier",
                    round_index=round_index,
                    images=images,
                    context=self.context_compiler.verifier(document, task, reviewer_result, passed_patches),
                    payload_type=VerifierPayload,
                    exclude_family=profile.family,
                )
            except ProviderAccountQuotaUnavailable:
                for transaction in passed_transactions:
                    transaction.status = "guard_passed"
                    transaction.failure_class = "rate_limit"
                    transaction.failure_owner = "service"
                    transaction.retryable = True
                    candidates_by_transaction[transaction.transaction_id].status = "proposed"
                for patch in passed_patches:
                    patch.status = "guard_passed"
                raise
            except ReviewExecutionUnavailable as exc:
                infrastructure_errors.append(str(exc))
                for transaction in passed_transactions:
                    transaction.status = "guard_passed"
                    transaction.failure_class = self.convergence.reason_failure(str(exc))["failure_class"]
                    transaction.failure_owner = "service"
                    transaction.retryable = True
                    candidate = candidates_by_transaction[transaction.transaction_id]
                    candidate.status = "proposed"
                for patch in passed_patches:
                    patch.status = "guard_passed"
                self._defer(document, task, str(exc), errors=infrastructure_errors)
                task.resume_stage = "verifier_pending"
                task.result = {
                    **(task.result or {}),
                    "resume_stage": "verifier_pending",
                    "checkpoint_transaction_ids": [item.transaction_id for item in passed_transactions],
                    "checkpoint_reviewer_result_id": reviewer_result.reviewer_result_id,
                }
                return

            transaction_decisions, transaction_decision_errors = (
                self.transactions.normalize_verifier_decisions(
                    verifier_payload,
                    passed_transactions,
                )
            )
            verifier_verdict = self.transactions.aggregate_verdict(transaction_decisions)
            verifier_disagreements = list(
                dict.fromkeys(
                    [
                        *verifier_payload.disagreements,
                        *(
                            message
                            for decision in transaction_decisions
                            for message in decision.disagreements
                        ),
                        *transaction_decision_errors,
                    ]
                )
            )
            verifier = VerifierResult(
                verifier_result_id=sequential_id(
                    "verifier",
                    (item.verifier_result_id for item in document.verifier_results),
                ),
                task_id=task.task_id,
                model_id=verifier_profile.model_id,
                model_family=verifier_profile.family,
                reviewer_result_id=reviewer_result.reviewer_result_id,
                transaction_ids=[item.transaction_id for item in passed_transactions],
                transaction_decisions=transaction_decisions,
                patch_ids=[patch.patch_id for patch in passed_patches],
                verdict=verifier_verdict,
                disagreements=verifier_disagreements,
                confidence=min(
                    (item.confidence for item in transaction_decisions),
                    default=verifier_payload.confidence,
                ),
                usage=verifier_cloud.usage,
                latency_ms=verifier_cloud.latency_ms,
                raw_response=verifier_cloud.raw_response,
            )
            document.verifier_results.append(verifier)
            task.verifier_result_ids.append(verifier.verifier_result_id)
            for transaction in passed_transactions:
                transaction.verifier_result_id = verifier.verifier_result_id

            if transaction_decision_errors:
                fingerprint = self.convergence.failure_fingerprint(
                    "model_protocol",
                    transaction_decision_errors,
                )
                for transaction in passed_transactions:
                    transaction.status = "rejected"
                    transaction.failure_class = "model_protocol"
                    transaction.failure_owner = "model"
                    transaction.retryable = local_round < max_rounds
                    transaction.failure_fingerprint = fingerprint
                    transaction.failure_messages = transaction_decision_errors
                    candidates_by_transaction[transaction.transaction_id].status = "rejected"
                for patch in passed_patches:
                    patch.status = "rejected"
                if local_round < max_rounds:
                    feedback = transaction_decision_errors
                    reviewer_exclude_family = profile.family
                    continue
                self._defer(
                    document,
                    task,
                    "verifier_transaction_protocol_invalid_after_repair",
                    errors=transaction_decision_errors,
                    failure_class="repeated_failure",
                    failure_owner="model",
                    retryable=False,
                    failure_fingerprint=self.convergence.failure_fingerprint(
                        "repeated_failure",
                        transaction_decision_errors,
                    ),
                )
                return

            decisions_by_id = {
                item.transaction_id: item
                for item in transaction_decisions
            }
            rejected_transactions: list[PatchTransactionIR] = []
            system_failed_transactions: list[PatchTransactionIR] = []
            for transaction in passed_transactions:
                decision = decisions_by_id[transaction.transaction_id]
                transaction_patches = [
                    patch
                    for patch in passed_patches
                    if patch.transaction_id == transaction.transaction_id
                ]
                candidate = candidates_by_transaction[transaction.transaction_id]
                if decision.verdict == "accept":
                    transaction.status = "verified"
                    candidate.status = "verified"
                    try:
                        self.applier.apply(document, transaction_patches)
                    except Exception as exc:
                        message = (
                            f"Transaction {transaction.transaction_id} failed during local "
                            f"commit: {type(exc).__name__}: {exc}"
                        )
                        transaction.status = "rejected"
                        transaction.failure_class = "system_contract"
                        transaction.failure_owner = "system"
                        transaction.retryable = False
                        transaction.failure_messages = [message]
                        transaction.failure_fingerprint = self.convergence.failure_fingerprint(
                            "system_contract",
                            [message],
                        )
                        candidate.status = "rejected"
                        for patch in transaction_patches:
                            patch.status = "rejected"
                        system_failed_transactions.append(transaction)
                        continue
                    transaction.status = "accepted"
                    candidate.status = "accepted"
                    continue

                transaction.status = "rejected"
                transaction.failure_class = (
                    "verifier_disagreement"
                    if decision.verdict == "reject"
                    else "semantic_ambiguity"
                )
                transaction.failure_owner = "model"
                transaction.retryable = local_round < max_rounds
                transaction.failure_messages = (
                    decision.disagreements
                    or [
                        f"Independent verifier returned {decision.verdict} for "
                        f"{transaction.transaction_id}."
                    ]
                )
                transaction.failure_fingerprint = self.convergence.failure_fingerprint(
                    transaction.failure_class,
                    transaction.failure_messages,
                )
                candidate.status = "rejected"
                for patch in transaction_patches:
                    patch.status = "rejected"
                rejected_transactions.append(transaction)

            resolved_required_targets.update(
                target_id
                for transaction in passed_transactions
                if transaction.status == "accepted"
                for target_id in transaction.target_ids
            )
            if required_targets.issubset(resolved_required_targets):
                self._resolve_task(document, task, reviewer_result, verifier)
                self._resolve_superseded_optional_tasks(document, task.task_id)
                return

            feedback = [
                *failed_feedback,
                *(
                    message
                    for transaction in rejected_transactions
                    for message in transaction.failure_messages
                ),
                *(
                    message
                    for transaction in system_failed_transactions
                    for message in transaction.failure_messages
                ),
                "The following required targets remain unresolved after accepting independent "
                f"transactions: {sorted(required_targets - resolved_required_targets)}.",
            ]
            if system_failed_transactions:
                selected = system_failed_transactions[0]
                self._defer(
                    document,
                    task,
                    "patch_transaction_commit_failed",
                    errors=feedback,
                    failure_class=selected.failure_class,
                    failure_owner=selected.failure_owner,
                    retryable=False,
                    failure_fingerprint=selected.failure_fingerprint,
                )
                return
            if local_round < max_rounds:
                reviewer_exclude_family = profile.family
                continue
            if rejected_transactions:
                self._semantic_unresolved(
                    document,
                    task,
                    "independent_verifier_disagreed_after_repair",
                    reviewer_result,
                    verifier=verifier,
                )
            else:
                self._defer(
                    document,
                    task,
                    "required_review_targets_incomplete_after_partial_acceptance",
                    errors=feedback,
                    failure_class="repeated_failure",
                    failure_owner="model",
                    retryable=False,
                    failure_fingerprint=self.convergence.failure_fingerprint(
                        "repeated_failure",
                        feedback,
                    ),
                )
            return

    def _verifier_checkpoint(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
    ) -> dict[str, Any] | None:
        guards = {item.guard_result_id: item for item in document.guard_results}
        eligible = [
            transaction
            for transaction in document.patch_transactions
            if transaction.task_id == task.task_id
            and transaction.guard_result_id in guards
            and guards[transaction.guard_result_id].passed
            and (
                transaction.status == "guard_passed"
                or (
                    transaction.status == "rejected"
                    and transaction.failure_owner == "service"
                    and transaction.failure_class in {"model_service", "rate_limit"}
                )
            )
            and not transaction.verifier_result_id
        ]
        if not eligible:
            return None
        reviewer_ids = [
            reviewer_id
            for reviewer_id in reversed(task.reviewer_result_ids)
            if any(item.reviewer_result_id == reviewer_id for item in eligible)
        ]
        if not reviewer_ids:
            reviewer_ids = [
                str(item.reviewer_result_id)
                for item in reversed(eligible)
                if item.reviewer_result_id
            ]
        if not reviewer_ids:
            return None
        reviewer_id = reviewer_ids[0]
        reviewer = next(
            (
                item
                for item in reversed(document.reviewer_results)
                if item.reviewer_result_id == reviewer_id
            ),
            None,
        )
        if reviewer is None:
            return None
        transactions = [item for item in eligible if item.reviewer_result_id == reviewer_id]
        transaction_ids = {item.transaction_id for item in transactions}
        patches = [
            patch
            for patch in document.atomic_patches
            if patch.transaction_id in transaction_ids and patch.status != "accepted"
        ]
        if not patches:
            return None
        candidates: dict[str, CandidateRevision] = {}
        for transaction in transactions:
            transaction.status = "guard_passed"
            transaction.failure_class = "none"
            transaction.failure_owner = "none"
            transaction.retryable = True
            transaction.failure_fingerprint = None
            transaction.failure_messages = []
            for patch in patches:
                if patch.transaction_id == transaction.transaction_id:
                    patch.status = "guard_passed"
            candidate = next(
                (
                    item
                    for item in reversed(document.candidate_revisions)
                    if item.transaction_id == transaction.transaction_id
                ),
                None,
            )
            if candidate is None:
                candidate = self._record_candidate(
                    document,
                    task,
                    [patch for patch in patches if patch.transaction_id == transaction.transaction_id],
                    status="proposed",
                    transaction_id=transaction.transaction_id,
                )
            else:
                candidate.status = "proposed"
            candidates[transaction.transaction_id] = candidate
        task.resume_stage = "verifier_pending"
        return {
            "reviewer": reviewer,
            "transactions": transactions,
            "patches": patches,
            "candidates": candidates,
        }

    def _resume_verifier_checkpoint(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        checkpoint: dict[str, Any],
        *,
        images: list[str],
        required_targets: set[str],
        resolved_required_targets: set[str],
        log: LogFn | None,
    ) -> tuple[bool, list[str], str | None]:
        reviewer: ReviewerResult = checkpoint["reviewer"]
        transactions: list[PatchTransactionIR] = checkpoint["transactions"]
        patches: list[AtomicPatch] = checkpoint["patches"]
        candidates: dict[str, CandidateRevision] = checkpoint["candidates"]
        round_index = max(1, reviewer.attempt)
        if log:
            log(
                f"Agent review {task.task_id}: resuming verifier from Guard checkpoint "
                f"{[item.transaction_id for item in transactions]}"
            )
        try:
            task.verifier_call_count += 1
            verifier_payload, verifier_cloud, verifier_profile = self._call_model(
                document,
                task,
                role="verifier",
                round_index=round_index,
                images=images,
                context=self.context_compiler.verifier(document, task, reviewer, patches),
                payload_type=VerifierPayload,
                exclude_family=reviewer.model_family,
            )
        except ProviderAccountQuotaUnavailable:
            raise
        except ReviewExecutionUnavailable as exc:
            self._preserve_verifier_checkpoint(transactions, patches, candidates, str(exc))
            self._defer(document, task, str(exc), errors=[str(exc)])
            task.resume_stage = "verifier_pending"
            task.result = {
                **(task.result or {}),
                "resume_stage": "verifier_pending",
                "checkpoint_transaction_ids": [item.transaction_id for item in transactions],
                "checkpoint_reviewer_result_id": reviewer.reviewer_result_id,
            }
            return True, [], reviewer.model_family

        decisions, errors = self.transactions.normalize_verifier_decisions(
            verifier_payload,
            transactions,
        )
        verifier = VerifierResult(
            verifier_result_id=sequential_id(
                "verifier",
                (item.verifier_result_id for item in document.verifier_results),
            ),
            task_id=task.task_id,
            model_id=verifier_profile.model_id,
            model_family=verifier_profile.family,
            reviewer_result_id=reviewer.reviewer_result_id,
            transaction_ids=[item.transaction_id for item in transactions],
            transaction_decisions=decisions,
            patch_ids=[patch.patch_id for patch in patches],
            verdict=self.transactions.aggregate_verdict(decisions),
            disagreements=list(
                dict.fromkeys(
                    [
                        *verifier_payload.disagreements,
                        *(message for item in decisions for message in item.disagreements),
                        *errors,
                    ]
                )
            ),
            confidence=min(
                (item.confidence for item in decisions),
                default=verifier_payload.confidence,
            ),
            usage=verifier_cloud.usage,
            latency_ms=verifier_cloud.latency_ms,
            raw_response=verifier_cloud.raw_response,
        )
        document.verifier_results.append(verifier)
        task.verifier_result_ids.append(verifier.verifier_result_id)
        for transaction in transactions:
            transaction.verifier_result_id = verifier.verifier_result_id

        if errors:
            self._preserve_verifier_checkpoint(transactions, patches, candidates, " | ".join(errors))
            self._defer(
                document,
                task,
                "verifier_transaction_protocol_invalid",
                errors=errors,
                failure_class="model_protocol",
                failure_owner="model",
                retryable=True,
            )
            task.resume_stage = "verifier_pending"
            return True, errors, reviewer.model_family

        by_id = {item.transaction_id: item for item in decisions}
        feedback: list[str] = []
        for transaction in transactions:
            decision = by_id[transaction.transaction_id]
            transaction_patches = [
                patch for patch in patches if patch.transaction_id == transaction.transaction_id
            ]
            candidate = candidates[transaction.transaction_id]
            if decision.verdict == "accept":
                transaction.status = "verified"
                candidate.status = "verified"
                try:
                    self.applier.apply(document, transaction_patches)
                except Exception as exc:
                    message = (
                        f"Transaction {transaction.transaction_id} failed during resumed local commit: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    transaction.status = "rejected"
                    transaction.failure_class = "system_contract"
                    transaction.failure_owner = "system"
                    transaction.retryable = False
                    transaction.failure_messages = [message]
                    candidate.status = "rejected"
                    for patch in transaction_patches:
                        patch.status = "rejected"
                    self._defer(
                        document,
                        task,
                        "patch_transaction_commit_failed",
                        errors=[message],
                        failure_class="system_contract",
                        failure_owner="system",
                        retryable=False,
                    )
                    return True, [], reviewer.model_family
                transaction.status = "accepted"
                candidate.status = "accepted"
                continue
            transaction.status = "rejected"
            transaction.failure_class = (
                "verifier_disagreement"
                if decision.verdict == "reject"
                else "semantic_ambiguity"
            )
            transaction.failure_owner = "model"
            transaction.retryable = True
            transaction.failure_messages = decision.disagreements or [
                f"Independent verifier returned {decision.verdict} for {transaction.transaction_id}."
            ]
            transaction.failure_fingerprint = self.convergence.failure_fingerprint(
                transaction.failure_class,
                transaction.failure_messages,
            )
            candidate.status = "rejected"
            for patch in transaction_patches:
                patch.status = "rejected"
            feedback.extend(transaction.failure_messages)

        resolved_required_targets.update(
            target_id
            for transaction in transactions
            if transaction.status == "accepted"
            for target_id in transaction.target_ids
        )
        if required_targets.issubset(resolved_required_targets):
            self._resolve_task(document, task, reviewer, verifier)
            self._resolve_superseded_optional_tasks(document, task.task_id)
            return True, [], reviewer.model_family
        feedback.append(
            "The following required targets remain unresolved after resuming the verifier checkpoint: "
            f"{sorted(required_targets - resolved_required_targets)}."
        )
        return False, feedback, reviewer.model_family

    @staticmethod
    def _preserve_verifier_checkpoint(
        transactions: list[PatchTransactionIR],
        patches: list[AtomicPatch],
        candidates: dict[str, CandidateRevision],
        reason: str,
    ) -> None:
        for transaction in transactions:
            transaction.status = "guard_passed"
            transaction.failure_class = "rate_limit" if "rate_limit" in reason else "model_service"
            transaction.failure_owner = "service"
            transaction.retryable = True
            transaction.failure_messages = [reason]
            candidates[transaction.transaction_id].status = "proposed"
        for patch in patches:
            patch.status = "guard_passed"

    def _resolve_deterministic_proposal(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        proposal: AtomicPatchProposal,
        reason: str,
    ) -> None:
        patch = AtomicPatch(
            patch_id=sequential_id("patch", (item.patch_id for item in document.atomic_patches)),
            source_task_id=task.task_id,
            target_type=proposal.target_type,
            target_id=proposal.target_id,
            operation=proposal.operation,
            field_path=proposal.field_path,
            before_value=proposal.before_value,
            proposed_value=proposal.proposed_value,
            evidence_refs=proposal.evidence_refs or task.input_refs,
            confidence=proposal.confidence,
            risk_level=self._risk(proposal.operation),
            rationale=proposal.rationale or reason,
        )
        target = self.guard._target(document, patch.target_id)
        if patch.operation != "confirm" and target is not None and patch.before_value is None:
            patch.before_value = self.guard._current_value(patch, target)
        transaction = PatchTransactionIR(
            transaction_id=sequential_id(
                "transaction",
                (item.transaction_id for item in document.patch_transactions),
            ),
            task_id=task.task_id,
            patch_ids=[patch.patch_id],
            target_ids=sorted(self.transactions.covered_targets(document, [patch])),
        )
        patch.transaction_id = transaction.transaction_id
        document.atomic_patches.append(patch)
        document.patch_transactions.append(transaction)

        allowed_targets = set(
            task.review_plan.mutable_target_ids
            if task.review_plan
            else [task.target_id, *(scope.target_id for scope in task.scope)]
        )
        required_targets = set(
            task.review_plan.required_decision_target_ids
            if task.review_plan
            else [scope.target_id for scope in task.scope if scope.blocking]
        )
        if task.blocking and not required_targets:
            required_targets.add(task.target_id)
        transaction.required_target_ids = sorted(
            required_targets & set(transaction.target_ids)
        )
        guard = self.guard.evaluate(
            document,
            task.task_id,
            [patch],
            allowed_target_ids=allowed_targets,
            required_target_ids=required_targets,
            allowed_operations=set(task.review_plan.allowed_operations) if task.review_plan else None,
            transaction_id=transaction.transaction_id,
        )
        guard.requires_independent_verifier = False
        document.guard_results.append(guard)
        task.guard_result_ids.append(guard.guard_result_id)
        transaction.guard_result_id = guard.guard_result_id
        if not guard.passed:
            patch.status = "guard_failed"
            transaction.status = "guard_failed"
            classification = self.convergence.classify_guard_failure(document, guard)
            transaction.failure_class = classification["failure_class"]
            transaction.failure_owner = classification["failure_owner"]
            transaction.retryable = classification["retryable"]
            transaction.failure_fingerprint = classification["fingerprint"]
            transaction.failure_messages = classification["messages"]
            self._record_candidate(
                document,
                task,
                [patch],
                status="guard_failed",
                transaction_id=transaction.transaction_id,
            )
            self._defer(
                document,
                task,
                "deterministic_preflight_guard_failed",
                errors=[check.message for check in guard.checks if not check.passed],
                failure_class=transaction.failure_class,
                failure_owner=transaction.failure_owner,
                retryable=transaction.retryable,
                failure_fingerprint=transaction.failure_fingerprint,
            )
            return

        patch.status = "guard_passed"
        transaction.status = "verified"
        candidate = self._record_candidate(
            document,
            task,
            [patch],
            status="verified",
            transaction_id=transaction.transaction_id,
        )
        self.applier.apply(document, [patch])
        candidate.status = "accepted"
        transaction.status = "accepted"
        decision = FinalReviewDecision(
            decision_id=sequential_id(
                "decision",
                (item.decision_id for item in document.final_decisions),
            ),
            task_id=task.task_id,
            outcome="auto_corrected",
            blocking_resolved=True,
            accepted_patch_ids=[patch.patch_id],
            guard_result_ids=[guard.guard_result_id],
            reason=reason,
            decided_by="deterministic",
        )
        document.final_decisions.append(decision)
        task.final_decision_id = decision.decision_id
        task.status = "auto_resolved"
        task.retryable = False
        task.resume_stage = "complete"
        task.result = {
            "outcome": decision.outcome,
            "decision_id": decision.decision_id,
            "strategy": "deterministic_preflight",
        }
        self._resolve_superseded_optional_tasks(document, task.task_id)
        resolved_targets = {
            target_id
            for transaction in document.patch_transactions
            if transaction.task_id == task.task_id and transaction.status == "accepted"
            for target_id in transaction.target_ids
        }
        for conflict in document.conflict_groups:
            if conflict.target_id in resolved_targets and conflict.status in {"open", "human_required"}:
                conflict.status = "auto_resolved"
                conflict.routing_disposition = "deterministically_resolved"
                conflict.resolution = reason

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
        # Tests and service composition may replace the adapter after construction.
        self.model_runner.adapter = self.adapter
        self.model_runner.registry = self.registry
        if self.telemetry:
            self.telemetry({
                "event": "logical_model_call_started",
                "task_id": task.task_id,
                "role": role,
                "round_index": round_index,
            })
        return self.model_runner.run(
            document,
            task,
            role=role,
            round_index=round_index,
            images=images,
            context=context,
            payload_type=payload_type,
            alias_normalizer=self.response_adapter.normalize_legacy_aliases,
            exclude_family=exclude_family,
        )

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
        proposals = list(payload.patches)
        if payload.verdict == "confirm":
            scope = task.scope or []
            proposals = [
                AtomicPatchProposal(
                    target_type=item.target_type,
                    target_id=item.target_id,
                    operation="confirm_spread" if item.target_type == "spread" else "confirm",
                    proposed_value=(
                        {"reading_direction": "left_to_right"}
                        if item.target_type == "spread"
                        else None
                    ),
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
            if task.target_type == "spread":
                inferred_link = self._unique_spread_link_proposal(
                    document,
                    task,
                    confidence=payload.confidence,
                )
                if inferred_link is not None:
                    proposals.append(inferred_link)
        elif payload.verdict == "propose_patch":
            covered_targets = {proposal.target_id for proposal in proposals}
            proposals.extend(
                AtomicPatchProposal(
                    target_type=decision.target_type,
                    target_id=decision.target_id,
                    operation="confirm",
                    evidence_refs=task.input_refs,
                    rationale=decision.rationale or "Reviewer explicitly confirmed this routed scope.",
                    confidence=decision.confidence or payload.confidence,
                )
                for decision in payload.scope_decisions
                if decision.decision == "confirm" and decision.target_id not in covered_targets
            )

        patches = []
        for index, proposal in enumerate(proposals, start=1):
            patch = AtomicPatch(
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
                    before_value=None,
                    proposed_value=proposal.proposed_value,
                    evidence_refs=proposal.evidence_refs or task.input_refs,
                    confidence=proposal.confidence,
                    risk_level=self._risk(proposal.operation),
                    rationale=proposal.rationale,
                )
            target = self.guard._target(document, patch.target_id)
            actual_target_type = self.guard._target_type(target) if target is not None else None
            if actual_target_type and patch.target_type != actual_target_type:
                patch.target_type = actual_target_type
            if patch.operation != "confirm" and target is not None:
                patch.before_value = self.guard._current_value(patch, target)
            if patch.operation == "set_bbox" and isinstance(patch.proposed_value, dict) and target is not None:
                proposed_bbox = dict(patch.proposed_value)
                page_index = getattr(target, "page_index", None)
                page = next((item for item in document.pages if item.page_index == page_index), None)
                if page:
                    canonical_id = next(
                        (
                            coordinate_id
                            for coordinate_id in page.coordinate_system_ids
                            if coordinate_id.endswith("-pdf-points")
                        ),
                        None,
                    )
                    proposed_bbox.setdefault("unit", "points")
                    proposed_bbox.setdefault("origin", "top_left")
                    proposed_bbox.setdefault("coordinate_system_id", canonical_id)
                    patch.proposed_value = proposed_bbox
            if patch.operation == "add_visual_text_block":
                self._normalize_visual_text_patch(document, task, patch)
            if patch.operation == "upsert_figure_structure":
                self._normalize_figure_structure_patch(document, patch)
            patch.evidence_refs = self._tracked_evidence_refs(
                document,
                patch.evidence_refs,
                task.input_refs,
            )
            patches.append(patch)
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

    @staticmethod
    def _unique_spread_link_proposal(
        document: DocumentIR,
        task: VlmReviewTask,
        *,
        confidence: float,
    ) -> AtomicPatchProposal | None:
        spread = next(
            (item for item in document.spreads if item.spread_id == task.target_id),
            None,
        )
        if spread is None or len(spread.page_indices) != 2:
            return None
        entity_pages = PatchGuard._entity_page_indices(document)
        left_page, right_page = spread.page_indices
        for entity_type in ("table", "figure", "block"):
            left_ids = []
            right_ids = []
            for entity_id in spread.member_entity_ids:
                entity = PatchGuard._target(document, entity_id)
                if PatchGuard._target_type(entity) != entity_type:
                    continue
                pages = entity_pages.get(entity_id, set())
                if pages == {left_page}:
                    left_ids.append(entity_id)
                elif pages == {right_page}:
                    right_ids.append(entity_id)
            if len(left_ids) == 1 and len(right_ids) == 1:
                return AtomicPatchProposal(
                    target_type="spread",
                    target_id=spread.spread_id,
                    operation="link_horizontal_continuation",
                    proposed_value={
                        "links": [
                            {
                                "source_id": left_ids[0],
                                "target_id": right_ids[0],
                                "confidence": confidence,
                            }
                        ]
                    },
                    evidence_refs=list(task.input_refs),
                    rationale=(
                        "Reviewer confirmed the spread and the local seam inventory contains "
                        f"one unambiguous {entity_type} on each member page."
                    ),
                    confidence=confidence,
                )
        return None

    @staticmethod
    def _tracked_evidence_refs(
        document: DocumentIR,
        proposed_refs: list[str],
        task_refs: list[str],
    ) -> list[str]:
        tracked = {
            value
            for artifact in document.artifacts
            for value in (artifact.path, artifact.remote_uri)
            if value
        }
        selected = [value for value in proposed_refs if value in tracked]
        if not selected:
            selected = [value for value in task_refs if value in tracked]
        return list(dict.fromkeys(selected or task_refs))

    @staticmethod
    def _normalize_visual_text_patch(
        document: DocumentIR,
        task: VlmReviewTask,
        patch: AtomicPatch,
    ) -> None:
        payload = dict(patch.proposed_value) if isinstance(patch.proposed_value, dict) else {}
        block_aliases = {
            "text": "paragraph",
            "body": "paragraph",
            "body_text": "paragraph",
            "visual_text": "paragraph",
            "title": "heading",
            "label": "unknown",
            "figure_caption": "caption",
            "table_caption": "caption",
        }
        block_type = str(payload.get("block_type") or "paragraph").casefold()
        payload["block_type"] = block_aliases.get(block_type, block_type)
        figure_id = str(payload.get("figure_id") or "")
        figure = next(
            (
                item
                for item in document.figures
                if item.figure_id == figure_id and item.bbox is not None
            ),
            None,
        )
        if figure is None and not payload.get("bbox"):
            context_ids = {
                *((task.review_plan.context_target_ids if task.review_plan else [])),
                *(scope.target_id for scope in task.scope),
            }
            figures = [
                item
                for item in document.figures
                if item.figure_id in context_ids and item.bbox is not None
            ]
            if len(figures) == 1:
                figure = figures[0]
                payload["figure_id"] = figure.figure_id
        if figure is not None and not payload.get("bbox"):
            payload["bbox"] = figure.bbox.model_dump(mode="json")
        if isinstance(payload.get("bbox"), dict):
            page_index = getattr(PatchGuard._target(document, patch.target_id), "page_index", None)
            page = next((item for item in document.pages if item.page_index == page_index), None)
            if page:
                coordinate_id = next(
                    (
                        value
                        for value in page.coordinate_system_ids
                        if value.endswith("-pdf-points")
                    ),
                    None,
                )
                payload["bbox"].setdefault("unit", "points")
                payload["bbox"].setdefault("origin", "top_left")
                payload["bbox"].setdefault("coordinate_system_id", coordinate_id)
        patch.proposed_value = payload

    def _normalize_figure_structure_patch(
        self,
        document: DocumentIR,
        patch: AtomicPatch,
    ) -> None:
        payload = dict(patch.proposed_value) if isinstance(patch.proposed_value, dict) else {}
        figure = PatchGuard._target(document, patch.target_id)
        page = next(
            (
                item
                for item in document.pages
                if item.page_index == getattr(figure, "page_index", None)
            ),
            None,
        )
        coordinate_id = next(
            (
                value
                for value in (page.coordinate_system_ids if page else [])
                if value.endswith("-pdf-points")
            ),
            None,
        )
        elements = []
        for raw in payload.get("elements") or []:
            normalized = dict(raw) if isinstance(raw, dict) else raw
            if isinstance(normalized, dict):
                normalized["text"] = (
                    normalized.get("text")
                    or normalized.get("visible_text")
                    or normalized.get("label")
                    or ""
                )
                if isinstance(normalized.get("bbox"), dict):
                    normalized["bbox"] = self._normalize_figure_element_bbox(
                        document,
                        figure,
                        normalized,
                        coordinate_id=coordinate_id,
                    )
            elements.append(normalized)
        payload["elements"] = elements
        payload["relations"] = payload.get("relations") or []
        patch.proposed_value = payload

    @staticmethod
    def _normalize_figure_element_bbox(
        document: DocumentIR,
        figure: Any,
        element: dict[str, Any],
        *,
        coordinate_id: str | None,
    ) -> dict[str, Any]:
        raw_bbox = dict(element["bbox"])
        if not isinstance(figure, FigureIR) or figure.bbox is None:
            raw_bbox.setdefault("unit", "points")
            raw_bbox.setdefault("origin", "top_left")
            raw_bbox.setdefault("coordinate_system_id", coordinate_id)
            return raw_bbox

        try:
            candidate = BoundingBox.model_validate(raw_bbox)
        except Exception:
            return raw_bbox

        declared_space = str(
            element.get("bbox_space")
            or raw_bbox.get("coordinate_system_id")
            or raw_bbox.get("unit")
            or ""
        ).casefold()
        normalized_space = (
            candidate.unit == "normalized"
            or declared_space in {
                "figure_normalized",
                f"{figure.figure_id}-normalized".casefold(),
            }
            or (
                min(candidate.x0, candidate.y0, candidate.x1, candidate.y1) >= 0
                and max(candidate.x0, candidate.y0, candidate.x1, candidate.y1) <= 1
            )
        )
        if normalized_space:
            width = figure.bbox.x1 - figure.bbox.x0
            height = figure.bbox.y1 - figure.bbox.y0
            mapped = BoundingBox(
                x0=figure.bbox.x0 + candidate.x0 * width,
                y0=figure.bbox.y0 + candidate.y0 * height,
                x1=figure.bbox.x0 + candidate.x1 * width,
                y1=figure.bbox.y0 + candidate.y1 * height,
                unit="points",
                origin="top_left",
                coordinate_system_id=coordinate_id,
            )
            element["coordinate_normalization"] = "figure_normalized_to_page_pdf_points"
            return mapped.model_dump(mode="json")

        page_local = (
            candidate.unit == "points"
            and bbox_containment(candidate, figure.bbox) >= 0.75
        )
        if page_local:
            raw_bbox["unit"] = "points"
            raw_bbox["origin"] = "top_left"
            raw_bbox["coordinate_system_id"] = coordinate_id
            return raw_bbox

        crop = next(
            (
                item
                for item in document.artifacts
                if item.artifact_id == figure.crop_artifact_id
            ),
            None,
        )
        page = next(
            (item for item in document.pages if item.page_index == figure.page_index),
            None,
        )
        systems = {
            item.coordinate_system_id: item
            for item in document.coordinate_systems
        }
        canonical = systems.get(coordinate_id or "")
        image_system = next(
            (
                systems.get(system_id)
                for system_id in (page.coordinate_system_ids if page else [])
                if systems.get(system_id)
                and systems[system_id].name == "rendered_page_pixels"
            ),
            None,
        )
        crop_width = getattr(crop, "width_pixels", None)
        crop_height = getattr(crop, "height_pixels", None)
        pixel_bbox = getattr(crop, "page_pixel_bbox", None)
        if (
            crop
            and canonical
            and image_system
            and crop_width
            and crop_height
            and pixel_bbox
            and min(candidate.x0, candidate.y0, candidate.x1, candidate.y1) >= 0
            and candidate.x0 <= crop_width * 1.05
            and candidate.x1 <= crop_width * 1.05
            and candidate.y0 <= crop_height * 1.05
            and candidate.y1 <= crop_height * 1.05
        ):
            local = BoundingBox(
                x0=pixel_bbox.x0 + min(float(crop_width), candidate.x0),
                y0=pixel_bbox.y0 + min(float(crop_height), candidate.y0),
                x1=pixel_bbox.x0 + min(float(crop_width), candidate.x1),
                y1=pixel_bbox.y0 + min(float(crop_height), candidate.y1),
                unit="pixels",
                origin="top_left",
                coordinate_system_id=image_system.coordinate_system_id,
            )
            mapped = CoordinateMapper.to_canonical_bbox(local, image_system, canonical)
            mapped.x0 = max(figure.bbox.x0, min(figure.bbox.x1, mapped.x0))
            mapped.y0 = max(figure.bbox.y0, min(figure.bbox.y1, mapped.y0))
            mapped.x1 = max(figure.bbox.x0, min(figure.bbox.x1, mapped.x1))
            mapped.y1 = max(figure.bbox.y0, min(figure.bbox.y1, mapped.y1))
            element["coordinate_normalization"] = "legacy_crop_pixels_to_page_pdf_points"
            return mapped.model_dump(mode="json")

        raw_bbox.setdefault("unit", "points")
        raw_bbox.setdefault("origin", "top_left")
        raw_bbox.setdefault("coordinate_system_id", coordinate_id)
        return raw_bbox

    def _record_candidate(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        patches: list[AtomicPatch],
        *,
        status: str,
        transaction_id: str | None = None,
    ) -> CandidateRevision:
        target_ids = list(dict.fromkeys(patch.target_id for patch in patches))
        before = self.applier.snapshot(document, target_ids)
        after = deepcopy(before)
        before_entities, before_retired, before_edges = self._structural_inventory(document)
        after_entities = set(before_entities)
        after_retired = set(before_retired)
        after_edges = set(before_edges)
        if status != "guard_failed":
            candidate_document = document.model_copy(deep=True)
            candidate_patches = [patch.model_copy(deep=True) for patch in patches]
            self.applier.apply(candidate_document, candidate_patches)
            after = self.applier.snapshot(candidate_document, target_ids)
            after_entities, after_retired, after_edges = self._structural_inventory(candidate_document)
        candidate = CandidateRevision(
            candidate_id=sequential_id(
                "candidate",
                (item.candidate_id for item in document.candidate_revisions),
            ),
            task_id=task.task_id,
            transaction_id=transaction_id,
            based_on_run_id=document.metadata.run_id,
            patch_ids=[patch.patch_id for patch in patches],
            target_snapshots=before,
            before_snapshots=before,
            after_snapshots=after,
            diffs=self._diff_snapshots(before, after),
            created_entity_ids=sorted(after_entities - before_entities),
            retired_entity_ids=sorted(after_retired - before_retired),
            created_structure_edge_ids=sorted(after_edges - before_edges),
            status=status,
        )
        document.candidate_revisions.append(candidate)
        return candidate

    @staticmethod
    def _structural_inventory(document: DocumentIR) -> tuple[set[str], set[str], set[str]]:
        entities = {
            *(page.page_id for page in document.pages),
            *(block.block_id for block in document.blocks),
            *(table.table_id for table in document.tables),
            *(table.logical_table_id for table in document.logical_tables),
            *(figure.figure_id for figure in document.figures),
            *(spread.spread_id for spread in document.spreads),
            *(section.section_id for section in document.sections),
            *(cell.cell_id for table in document.tables for cell in table.cells),
        }
        retired = {item.entity_id for item in document.retired_entities}
        edges = {item.edge_id for item in document.structure_edges}
        return entities, retired, edges

    def _resolve_task(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        reviewer: ReviewerResult,
        verifier: VerifierResult,
    ) -> None:
        accepted = [
            patch
            for patch in document.atomic_patches
            if patch.source_task_id == task.task_id and patch.status == "accepted"
        ]
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
            reviewer_result_ids=list(task.reviewer_result_ids),
            guard_result_ids=list(task.guard_result_ids),
            verifier_result_ids=list(task.verifier_result_ids),
            reason=(
                "All required review targets are covered by independently guarded patch "
                "transactions and an independent model-family verifier."
            ),
            decided_by="agent_consensus",
        )
        document.final_decisions.append(decision)
        task.final_decision_id = decision.decision_id
        task.status = "auto_resolved"
        task.result = {"outcome": decision.outcome, "decision_id": decision.decision_id}
        task.failure_class = "none"
        task.failure_owner = "none"
        task.retryable = False
        task.resume_stage = "complete"
        task.failure_fingerprint = None
        resolved_targets = {
            target_id
            for transaction in document.patch_transactions
            if transaction.task_id == task.task_id and transaction.status == "accepted"
            for target_id in transaction.target_ids
        }
        for conflict in document.conflict_groups:
            if conflict.target_id in resolved_targets and conflict.status in {"open", "human_required"}:
                conflict.status = "auto_resolved"
                conflict.routing_disposition = "deterministically_resolved"
                conflict.resolution = decision.reason

    def _resolve_superseded_optional_tasks(
        self,
        document: DocumentIR,
        source_task_id: str,
    ) -> None:
        accepted = [
            patch
            for patch in document.atomic_patches
            if patch.source_task_id == source_task_id and patch.status == "accepted"
        ]
        covered = self.transactions.covered_targets(document, accepted)
        if not covered:
            return
        unresolved_statuses = {"pending", "queued", "deferred", "failed", "skipped"}
        for task in document.review_tasks:
            if (
                task.task_id == source_task_id
                or task.blocking
                or task.status not in unresolved_statuses
            ):
                continue
            plan_kind = task.review_plan.review_kind if task.review_plan else ""
            target = self.guard._target(document, task.target_id)
            resolved = False
            if (
                plan_kind == "figure_binding"
                and isinstance(target, FigureIR)
                and target.figure_id in covered
                and target.visual_status == "reviewed"
            ):
                resolved = True
            elif (
                plan_kind == "horizontal_page_spread"
                and isinstance(target, SpreadIR)
                and target.spread_id in covered
                and target.status in {"confirmed", "rejected"}
            ):
                resolved = True
            if not resolved:
                continue
            decision = FinalReviewDecision(
                decision_id=sequential_id(
                    "decision",
                    (item.decision_id for item in document.final_decisions),
                ),
                task_id=task.task_id,
                outcome="auto_confirmed",
                blocking_resolved=True,
                accepted_patch_ids=[patch.patch_id for patch in accepted],
                reason=(
                    f"Optional task was superseded by independently guarded and verified corrections "
                    f"from {source_task_id}; no second model call is required."
                ),
                decided_by="deterministic",
            )
            document.final_decisions.append(decision)
            task.final_decision_id = decision.decision_id
            task.status = "auto_resolved"
            task.failure_class = "none"
            task.failure_owner = "none"
            task.retryable = False
            task.resume_stage = "complete"
            task.failure_fingerprint = None
            task.result = {
                "outcome": "auto_confirmed",
                "decision_id": decision.decision_id,
                "strategy": "superseded_by_verified_patch",
                "source_task_id": source_task_id,
            }

    def _semantic_unresolved(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        reason: str,
        reviewer: ReviewerResult,
        *,
        verifier: VerifierResult | None = None,
    ) -> None:
        task.failure_class = (
            "semantic_ambiguity"
            if reason.startswith("reviewer_abstained")
            else "verifier_disagreement"
        )
        task.failure_owner = "model"
        task.retryable = False
        task.resume_stage = "complete"
        task.failure_fingerprint = self.convergence.failure_fingerprint(
            task.failure_class,
            [reason, *(verifier.disagreements if verifier else [])],
        )
        if task.blocking:
            outcome = "human_required"
            task.status = "human_required"
            current_patch_ids = set(reviewer.patch_ids)
            for patch in document.atomic_patches:
                if (
                    patch.patch_id in current_patch_ids
                    and patch.status != "accepted"
                    and not reason.startswith("patch_guard_failed")
                ):
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
                        routing_disposition="human_required",
                    )
                )
            else:
                conflict.status = "human_required"
                conflict.routing_disposition = "human_required"
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
        task.result = {
            "outcome": outcome,
            "decision_id": decision.decision_id,
            "reason": reason,
            "failure_class": task.failure_class,
            "failure_owner": task.failure_owner,
            "retryable": task.retryable,
            "failure_fingerprint": task.failure_fingerprint,
        }

    def _defer(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        reason: str,
        *,
        errors: list[str] | None = None,
        failure_class: str | None = None,
        failure_owner: str | None = None,
        retryable: bool | None = None,
        failure_fingerprint: str | None = None,
    ) -> None:
        classification = self.convergence.reason_failure(reason)
        task.failure_class = failure_class or classification["failure_class"]
        task.failure_owner = failure_owner or classification["failure_owner"]
        task.retryable = (
            classification["retryable"]
            if retryable is None
            else retryable
        )
        task.failure_fingerprint = failure_fingerprint or self.convergence.failure_fingerprint(
            task.failure_class,
            [reason, *(errors or [])],
        )
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
        task.result = {
            "outcome": "deferred",
            "reason": reason,
            "errors": errors or [],
            "failure_class": task.failure_class,
            "failure_owner": task.failure_owner,
            "retryable": task.retryable,
            "failure_fingerprint": task.failure_fingerprint,
            "resume_stage": task.resume_stage,
        }

    @staticmethod
    def _provider_block_reason(provider_state: dict[str, object], *, cached: bool) -> str:
        retry_after = int(float(provider_state.get("retry_after_seconds") or 0))
        source = "cached_provider_circuit" if cached else "provider_response"
        return (
            "qiniu_account_rate_limited: TPD account quota is unavailable; "
            f"source={source}; retry_after_seconds={retry_after}"
        )

    def _defer_provider_blocked_tasks(
        self,
        document: DocumentIR,
        tasks: list[VlmReviewTask],
        reason: str,
        *,
        provider_state: dict[str, object],
    ) -> int:
        count = 0
        for task in tasks:
            if task.status not in {"pending", "queued", "deferred", "failed", "skipped"}:
                continue
            self._defer(document, task, reason, errors=[reason])
            task.result = {
                **(task.result or {}),
                "provider_state": provider_state,
                "http_attempted": False,
                "short_circuited": True,
                "resume_stage": task.resume_stage,
            }
            count += 1
        return count

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
        return (
            task.task_id in selected_targets
            or task.target_id in selected_targets
            or any(scope.target_id in selected_targets for scope in task.scope)
        )

    @staticmethod
    def _risk(operation: str) -> str:
        return OperationRegistry.risk_level(operation)

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
