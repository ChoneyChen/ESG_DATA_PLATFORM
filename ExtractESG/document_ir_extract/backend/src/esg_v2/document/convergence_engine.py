from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from esg_v2.document.contracts import DocumentIR, GuardResult, PatchTransactionIR, TableIR
from esg_v2.document.patch_guard import PatchGuard


class ConvergenceEngine:
    """Classifies failures and turns Guard diagnostics into executable model feedback."""

    def classify_guard_failure(self, document: DocumentIR, guard: GuardResult) -> dict[str, Any]:
        failed = [
            check for check in guard.checks
            if not check.passed and check.severity in {"error", "blocking"}
        ]
        codes = [check.code for check in failed]
        messages = list(dict.fromkeys([
            *(check.message for check in failed),
            *self.actionable_feedback(document, guard, failed),
        ]))
        evidence_failure = any(
            marker in code
            for code in codes
            for marker in ("evidence_resolves", "figure_structure_evidence", "crop_artifact")
        )
        system_failure = any(
            marker in code
            for code in codes
            for marker in ("atomic_batch_application", "before_matches")
        )
        if evidence_failure:
            failure_class, owner, retryable = "evidence_missing", "evidence", False
        elif system_failure:
            failure_class, owner, retryable = "system_contract", "system", False
        else:
            failure_class, owner, retryable = "model_protocol", "model", True
        transaction = next(
            (item for item in document.patch_transactions if item.transaction_id == guard.transaction_id),
            None,
        )
        transaction_patches = [
            patch for patch in document.atomic_patches if patch.patch_id in set(guard.patch_ids)
        ]
        semantic_context = [
            f"task:{guard.task_id}",
            f"targets:{','.join(sorted(transaction.target_ids if transaction else []))}",
            "operations:" + ",".join(sorted(
                f"{patch.target_id}:{patch.operation}" for patch in transaction_patches
            )),
        ]
        fingerprint = self.failure_fingerprint(failure_class, [*semantic_context, *codes, *messages])
        repeated = any(
            item.failure_fingerprint == fingerprint
            and item.status == "guard_failed"
            and item.task_id == guard.task_id
            and item.transaction_id != guard.transaction_id
            for item in document.patch_transactions
        )
        if repeated:
            failure_class = "repeated_failure"
            fingerprint = self.failure_fingerprint(failure_class, [*semantic_context, *codes, *messages])
        return {
            "failure_class": failure_class,
            "failure_owner": owner,
            "retryable": retryable,
            "fingerprint": fingerprint,
            "messages": messages,
            "guard_feedback": [check.details for check in failed if check.details],
        }

    @staticmethod
    def actionable_feedback(document: DocumentIR, guard: GuardResult, failed_checks) -> list[str]:
        feedback: list[str] = []
        codes = [check.code for check in failed_checks]
        for check in failed_checks:
            details = check.details or {}
            missing_targets = details.get("missing_target_ids") or details.get("missing_source_cell_ids") or []
            if missing_targets:
                feedback.append(
                    f"Missing targets/cells: {missing_targets}. Recommended operation: "
                    f"{details.get('recommended_operation') or 'repair_the_same_transaction'}."
                )
            conflicting = details.get("conflicting_source_text") or {}
            if conflicting:
                feedback.append(
                    "Preserve or explicitly map these source texts: "
                    + json.dumps(conflicting, ensure_ascii=False, sort_keys=True)
                )
        patches = [patch for patch in document.atomic_patches if patch.patch_id in set(guard.patch_ids)]
        if "horizontal_table_composition_resolved" in codes:
            for patch in patches:
                if patch.operation != "link_horizontal_continuation" or not isinstance(patch.proposed_value, dict):
                    continue
                for raw in patch.proposed_value.get("links", []):
                    source = PatchGuard._target(document, str(raw.get("source_id") or ""))
                    target = PatchGuard._target(document, str(raw.get("target_id") or ""))
                    if isinstance(source, TableIR) and isinstance(target, TableIR):
                        feedback.append(
                            f"Rebuild {target.table_id} as a complete {source.row_count}x1 page-local grid "
                            f"with source-cell mappings and visual evidence, then resubmit that repair, "
                            f"confirm_spread, and link_horizontal_continuation in one transaction."
                        )
        if any(code.endswith("source_text_retention") or code.endswith("numeric_tokens_preserved") for code in codes):
            feedback.append(
                "For a true OCR substitution use correct_ocr_text on the exact cell; otherwise retain every "
                "source_cell_id in TableGridRepairProposal and list visually missing text separately."
            )
        if any(code.endswith("visual_text_bbox") for code in codes):
            feedback.append(
                "Use set_caption for visible captions or bind_block_to_figure for existing canonical blocks; do not guess a bbox."
            )
        return list(dict.fromkeys(feedback))

    @staticmethod
    def failure_fingerprint(failure_class: str, values: list[str]) -> str:
        normalized = [
            re.sub(
                r"\b(?:patch|guard|transaction|reviewer|verifier|candidate)-\d{6}\b",
                "<runtime-id>",
                value,
            )
            for value in values
        ]
        payload = json.dumps([failure_class, *sorted(set(normalized))], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def dominant_transaction_failure(transactions: list[PatchTransactionIR]) -> dict[str, Any]:
        failed = [item for item in transactions if item.status == "guard_failed"]
        if not failed:
            return {"failure_class": "model_protocol", "failure_owner": "model", "retryable": True, "failure_fingerprint": None}
        selected = next((item for item in failed if not item.retryable), failed[0])
        return {
            "failure_class": selected.failure_class,
            "failure_owner": selected.failure_owner,
            "retryable": selected.retryable,
            "failure_fingerprint": selected.failure_fingerprint,
        }

    @staticmethod
    def reason_failure(reason: str) -> dict[str, Any]:
        if "rate_limit" in reason:
            return {"failure_class": "rate_limit", "failure_owner": "service", "retryable": True}
        if reason == "no_visual_input_available":
            return {"failure_class": "evidence_missing", "failure_owner": "evidence", "retryable": False}
        if "budget_deferred" in reason or "outside_targeted_review_scope" in reason:
            return {"failure_class": "scheduler_deferred", "failure_owner": "service", "retryable": True}
        if "request_config" in reason or "thinking_budget" in reason:
            return {"failure_class": "system_contract", "failure_owner": "system", "retryable": False}
        return {"failure_class": "model_service", "failure_owner": "service", "retryable": True}
