from __future__ import annotations

from pydantic import ValidationError

from esg_v2.document.contracts import ConflictGroup, CorrectionPatch, DocumentIR, VlmReviewPayload
from esg_v2.document.identifiers import sequential_id


class ReviewResultInterpreter:
    """Turns provider responses into provider-neutral, auditable patch proposals."""

    def interpret(self, document: DocumentIR) -> DocumentIR:
        patches: list[CorrectionPatch] = []
        conflicts: list[ConflictGroup] = list(document.conflict_groups)
        for task in document.review_tasks:
            if task.status != "done" or not task.result:
                continue
            parsed = task.result.get("parsed_json")
            if not isinstance(parsed, dict):
                conflicts.append(
                    ConflictGroup(
                        conflict_id=sequential_id("conflict", (item.conflict_id for item in conflicts)),
                        target_id=task.target_id,
                        conflict_type="unparseable_review_output",
                        observation_refs=[task.task_id],
                        status="human_required",
                        routing_disposition="human_required",
                    )
                )
                continue
            try:
                payload = VlmReviewPayload.model_validate(parsed)
            except ValidationError:
                conflicts.append(
                    ConflictGroup(
                        conflict_id=sequential_id("conflict", (item.conflict_id for item in conflicts)),
                        target_id=task.target_id,
                        conflict_type="invalid_review_schema",
                        observation_refs=[task.task_id],
                        status="human_required",
                        routing_disposition="human_required",
                    )
                )
                continue
            confidence = payload.confidence
            needs_human = payload.needs_human_review
            corrected = payload.corrected_text_or_table
            patch_id = sequential_id("patch", (item.patch_id for item in patches))
            if corrected not in (None, "", [], {}):
                operation = "replace_table" if task.target_type == "table" else "replace_text"
                patch = CorrectionPatch(
                    patch_id=patch_id,
                    source_task_id=task.task_id,
                    target_type=task.target_type,
                    target_id=task.target_id,
                    operation=operation,
                    proposed_value=corrected,
                    confidence=confidence,
                    status="human_required",
                    rationale="VLM proposed content that differs from the primary parser; human or deterministic corroboration is required.",
                )
                patches.append(patch)
                conflicts.append(
                    ConflictGroup(
                        conflict_id=sequential_id("conflict", (item.conflict_id for item in conflicts)),
                        target_id=task.target_id,
                        conflict_type="vlm_parser_content_divergence",
                        observation_refs=[task.task_id],
                        patch_ids=[patch.patch_id],
                        status="human_required",
                        routing_disposition="human_required",
                    )
                )
            elif confidence >= 0.8 and not needs_human:
                patches.append(
                    CorrectionPatch(
                        patch_id=patch_id,
                        source_task_id=task.task_id,
                        target_type=task.target_type,
                        target_id=task.target_id,
                        operation="confirm",
                        confidence=confidence,
                        status="accepted",
                        rationale="The review confirmed the parser result without proposing replacement content.",
                    )
                )
            else:
                patches.append(
                    CorrectionPatch(
                        patch_id=patch_id,
                        source_task_id=task.task_id,
                        target_type=task.target_type,
                        target_id=task.target_id,
                        operation="confirm",
                        confidence=confidence,
                        status="human_required",
                        rationale="Review confidence or review state is insufficient for deterministic acceptance.",
                    )
                )
        document.correction_patches = patches
        document.conflict_groups = self._dedupe_conflicts(conflicts)
        return document

    @staticmethod
    def _dedupe_conflicts(conflicts):
        return list({item.conflict_id: item for item in conflicts}.values())
