from __future__ import annotations

from esg_v2.document.contracts import (
    AtomicPatch,
    CellIR,
    DocumentIR,
    PatchTransactionIR,
    ReviewerResult,
    SpreadIR,
    TableIR,
    VerifierPayload,
    VerifierTransactionDecision,
    VlmReviewTask,
)
from esg_v2.document.identifiers import sequential_id
from esg_v2.document.patch_guard import PatchGuard


class TransactionCoordinator:
    """Owns atomic patch grouping and verifier transaction coverage."""

    def plan(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        reviewer: ReviewerResult,
        patches: list[AtomicPatch],
        required_targets: set[str],
    ) -> list[PatchTransactionIR]:
        spread_key = f"spread-composition:{task.target_id}" if task.target_type == "spread" else None
        spread = PatchGuard._target(document, task.target_id) if spread_key else None
        linked_ids = {
            str(raw.get(key) or "")
            for patch in patches
            if patch.operation == "link_horizontal_continuation"
            and isinstance(patch.proposed_value, dict)
            for raw in patch.proposed_value.get("links", [])
            if isinstance(raw, dict)
            for key in ("source_id", "target_id")
            if raw.get(key)
        }
        composition_table_ids = {
            target_id
            for target_id in {
                *linked_ids,
                *(spread.member_entity_ids if isinstance(spread, SpreadIR) else []),
                *(task.review_plan.mutable_target_ids if task.review_plan else []),
                *(scope.target_id for scope in task.scope if scope.target_type in {"table", "cell"}),
            }
            if isinstance(PatchGuard._target(document, target_id), TableIR)
        }
        grouped: dict[str, list[AtomicPatch]] = {}
        for patch in patches:
            key = patch.target_id
            target = PatchGuard._target(document, patch.target_id)
            if isinstance(target, CellIR):
                key = target.table_id
            if spread_key and (
                patch.target_type == "spread"
                or patch.target_id in linked_ids
                or patch.target_id in composition_table_ids
                or isinstance(target, CellIR) and target.table_id in composition_table_ids
            ):
                key = spread_key
            grouped.setdefault(key, []).append(patch)

        transactions: list[PatchTransactionIR] = []
        for transaction_patches in grouped.values():
            transaction_id = sequential_id(
                "transaction",
                (item.transaction_id for item in [*document.patch_transactions, *transactions]),
            )
            target_ids = self.covered_targets(document, transaction_patches)
            transaction = PatchTransactionIR(
                transaction_id=transaction_id,
                task_id=task.task_id,
                reviewer_result_id=reviewer.reviewer_result_id,
                patch_ids=[patch.patch_id for patch in transaction_patches],
                target_ids=sorted(target_ids),
                required_target_ids=sorted(required_targets & target_ids),
            )
            for patch in transaction_patches:
                patch.transaction_id = transaction_id
            transactions.append(transaction)
        document.patch_transactions.extend(transactions)
        return transactions

    @staticmethod
    def covered_targets(document: DocumentIR, patches: list[AtomicPatch]) -> set[str]:
        covered: set[str] = set()
        page_ids = {page.page_index: page.page_id for page in document.pages}

        def add_entity(target_id: str) -> None:
            covered.add(target_id)
            target = PatchGuard._target(document, target_id)
            if isinstance(target, CellIR):
                covered.add(target.table_id)
            page_index = getattr(target, "page_index", None)
            if isinstance(page_index, int) and page_index in page_ids:
                covered.add(page_ids[page_index])

        for patch in patches:
            add_entity(patch.target_id)
            if patch.operation == "link_horizontal_continuation":
                for raw in patch.proposed_value.get("links", []) if isinstance(patch.proposed_value, dict) else []:
                    for key in ("source_id", "target_id"):
                        target_id = str(raw.get(key) or "")
                        if target_id:
                            add_entity(target_id)
            if patch.operation == "bind_block_to_figure" and isinstance(patch.proposed_value, dict):
                figure_id = str(patch.proposed_value.get("figure_id") or "")
                if figure_id:
                    add_entity(figure_id)
        return covered

    @staticmethod
    def normalize_verifier_decisions(
        payload: VerifierPayload,
        transactions: list[PatchTransactionIR],
    ) -> tuple[list[VerifierTransactionDecision], list[str]]:
        expected = [item.transaction_id for item in transactions]
        if not payload.transaction_decisions:
            return ([
                VerifierTransactionDecision(
                    transaction_id=transaction_id,
                    verdict=payload.verdict,
                    disagreements=list(payload.disagreements),
                    confidence=payload.confidence,
                )
                for transaction_id in expected
            ], [])
        received = [item.transaction_id for item in payload.transaction_decisions]
        duplicate = sorted({item for item in received if received.count(item) > 1})
        missing = sorted(set(expected) - set(received))
        unknown = sorted(set(received) - set(expected))
        errors = []
        if duplicate:
            errors.append(f"Verifier returned duplicate transaction decisions: {duplicate}.")
        if missing:
            errors.append(f"Verifier omitted transaction decisions: {missing}.")
        if unknown:
            errors.append(f"Verifier returned unknown transaction decisions: {unknown}.")
        if errors:
            return ([
                VerifierTransactionDecision(
                    transaction_id=transaction_id,
                    verdict="abstain",
                    disagreements=list(errors),
                    confidence=0.0,
                )
                for transaction_id in expected
            ], errors)
        by_id = {item.transaction_id: item for item in payload.transaction_decisions}
        return ([by_id[transaction_id] for transaction_id in expected], [])

    @staticmethod
    def aggregate_verdict(decisions: list[VerifierTransactionDecision]) -> str:
        verdicts = {item.verdict for item in decisions}
        if verdicts == {"accept"}:
            return "accept"
        if "reject" in verdicts:
            return "reject"
        return "abstain"
