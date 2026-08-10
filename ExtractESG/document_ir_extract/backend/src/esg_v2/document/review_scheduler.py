from __future__ import annotations

from dataclasses import dataclass

from esg_v2.config import Settings
from esg_v2.document.contracts import VlmReviewTask


@dataclass(frozen=True)
class ReviewSchedule:
    ordered_tasks: tuple[VlmReviewTask, ...]
    scheduled_task_ids: frozenset[str]
    batch_preflight_groups: tuple[tuple[str, ...], ...]
    metrics: dict[str, object]


class ReviewScheduler:
    """Blocking-first scheduler with a completeness mode that never silently drops optional work."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def schedule(
        self,
        tasks: list[VlmReviewTask],
        *,
        explicitly_targeted: bool,
        deterministic_count: int = 0,
    ) -> ReviewSchedule:
        ordered = tuple(sorted(
            tasks,
            key=lambda task: (
                not task.blocking,
                task.resume_stage != "verifier_pending",
                {"critical": 0, "high": 1, "normal": 2, "low": 3}.get(task.priority, 4),
                task.page_index,
                task.task_id,
            ),
        ))
        hard_limit = max(0, self.settings.max_vlm_reviews_per_ir_run)
        if explicitly_targeted:
            selected = list(ordered[: hard_limit or len(ordered)])
            mode = "explicit_targets"
        else:
            blocking = [task for task in ordered if task.blocking]
            optional = [task for task in ordered if not task.blocking]
            blocking_cap = max(0, self.settings.max_blocking_vlm_reviews_per_ir_run)
            optional_cap = max(0, self.settings.max_optional_vlm_reviews_per_ir_run)
            if self.settings.review_completeness_mode:
                blocking_cap = max(blocking_cap, len(blocking))
                optional_cap = max(optional_cap, len(optional))
                mode = "completeness"
            else:
                mode = "bounded"
            selected = [*blocking[:blocking_cap], *optional[:optional_cap]]
            if hard_limit:
                selected = selected[:hard_limit]

        selected_ids = frozenset(task.task_id for task in selected)
        optional_selected = [task for task in selected if not task.blocking]
        groups: list[tuple[str, ...]] = []
        for review_kind in ("horizontal_page_spread", "figure_binding", "figure_semantic_structure"):
            ids = tuple(
                task.task_id
                for task in optional_selected
                if task.review_plan and task.review_plan.review_kind == review_kind
            )
            if ids:
                groups.append(ids)
        deferred = len(ordered) - len(selected_ids)
        metrics = {
            "mode": mode,
            "eligible_count": len(ordered),
            "scheduled_count": len(selected_ids),
            "deterministic_preflight_count": deterministic_count,
            "scheduled_blocking_count": sum(1 for task in selected if task.blocking),
            "scheduled_optional_count": sum(1 for task in selected if not task.blocking),
            "scheduled_verifier_resume_count": sum(
                1 for task in selected if task.resume_stage == "verifier_pending"
            ),
            "scheduler_deferred": deferred,
            "batch_preflight_group_count": len(groups),
            "batch_preflight_task_count": sum(len(group) for group in groups),
            "batch_preflight_execution": "grouped_scheduling_only",
            "hard_limit": hard_limit,
            "blocking_cap": self.settings.max_blocking_vlm_reviews_per_ir_run,
            "optional_cap": self.settings.max_optional_vlm_reviews_per_ir_run,
            "completeness_mode": self.settings.review_completeness_mode,
        }
        return ReviewSchedule(ordered, selected_ids, tuple(groups), metrics)
