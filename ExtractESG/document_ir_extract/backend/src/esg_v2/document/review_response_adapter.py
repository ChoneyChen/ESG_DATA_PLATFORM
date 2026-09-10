from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from esg_v2.document.chart_spec_normalizer import ChartSpecNormalizer
from esg_v2.document.contracts import BlockIR, DocumentIR, FigureIR, TableIR, VlmReviewTask
from esg_v2.document.model_json_decoder import ModelJsonObjectDecoder
from esg_v2.document.operation_registry import PATCH_OPERATION_NAMES
from esg_v2.document.patch_guard import PatchGuard
from esg_v2.document.text_normalization import comparison_key


class ModelOutputTruncated(ValueError):
    pass


class ReviewResponseAdapter:
    """Converts provider output and legacy aliases into the canonical review protocol."""

    @staticmethod
    def message_json(raw_response: dict[str, Any]) -> dict[str, Any]:
        choices = raw_response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Response has no choices")
        choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = str(choice.get("finish_reason") or "").lower()
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        if finish_reason in {"length", "max_tokens"}:
            raise ModelOutputTruncated("Model output stopped at the configured output-token limit")
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
        decoded = ModelJsonObjectDecoder.decode(text)
        value = decoded.value
        if decoded.repair_codes:
            raw_flags = value.get("quality_flags")
            flags = raw_flags if isinstance(raw_flags, list) else [raw_flags] if isinstance(raw_flags, str) else []
            value["quality_flags"] = list(dict.fromkeys([*flags, *decoded.repair_codes]))
        return value

    def normalize_reviewer(
        self,
        document: DocumentIR,
        payload: dict[str, Any],
        task: VlmReviewTask,
        *,
        alias_normalizer: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = alias_normalizer(document, payload, task=task)
        self._normalize_non_table_visual_abstention(document, normalized, task)
        for patch in normalized.get("patches", []):
            if not isinstance(patch, dict):
                continue
            value = patch.get("proposed_value")
            if not isinstance(value, dict):
                continue
            if patch.get("operation") == "upsert_chart_spec":
                raw_patch_refs = patch.get("evidence_refs")
                patch_refs = (
                    [raw_patch_refs]
                    if isinstance(raw_patch_refs, str)
                    else list(raw_patch_refs or [])
                )
                patch["evidence_refs"] = patch_refs
                chart = ChartSpecNormalizer.normalize(
                    value,
                    evidence_refs=[
                        *patch_refs,
                        *task.input_refs,
                    ],
                )
                if chart != value:
                    patch["proposed_value"] = chart
                    flags = normalized.setdefault("quality_flags", [])
                    if "chart_spec_aliases_normalized" not in flags:
                        flags.append("chart_spec_aliases_normalized")
                continue
            if patch.get("operation") != "set_table_grid":
                continue
            target = PatchGuard._target(document, str(patch.get("target_id") or ""))
            if not isinstance(target, TableIR):
                continue
            self._complete_table_repair_contract(value, target, patch, task)
        return normalized

    @staticmethod
    def _normalize_non_table_visual_abstention(
        document: DocumentIR,
        normalized: dict[str, Any],
        task: VlmReviewTask,
    ) -> None:
        if normalized.get("verdict") != "abstain":
            return
        if not task.review_plan or task.review_plan.review_kind != "table_candidate_classification":
            return
        table = PatchGuard._target(document, task.target_id)
        if not isinstance(table, TableIR) or "local_only_table_candidate" not in table.quality_flags:
            return
        raw_findings = normalized.get("findings") or []
        if isinstance(raw_findings, str):
            raw_findings = [raw_findings]
        explanation = " ".join(
            str(value)
            for value in [
                *raw_findings,
                normalized.get("abstain_reason") or "",
                *(decision.get("rationale") or "" for decision in normalized.get("scope_decisions", []) if isinstance(decision, dict)),
            ]
        ).casefold()
        decisive_non_table = any(
            phrase in explanation
            for phrase in (
                "not a table",
                "not tabular",
                "non-tabular",
                "non tabular",
                "不是表格",
                "非表格",
                "treemap",
            )
        )
        visual_classification = any(
            phrase in explanation
            for phrase in ("visualization", "visualisation", "infographic", "chart", "treemap", "图表", "可视化")
        )
        if not decisive_non_table or not visual_classification:
            return
        rationale = (
            "The reviewer explicitly classified the local OCR candidate as a non-tabular visual; "
            "the adapter converts that unambiguous classification into the registered retirement operation."
        )
        normalized.update(
            {
                "verdict": "propose_patch",
                "scope_decisions": [
                    {
                        "target_type": "table",
                        "target_id": table.table_id,
                        "decision": "propose_patch",
                        "rationale": rationale,
                        "confidence": max(0.9, float(normalized.get("confidence") or 0.0)),
                    }
                ],
                "patches": [
                    {
                        "target_type": "table",
                        "target_id": table.table_id,
                        "operation": "retire_table_candidate",
                        "proposed_value": {"disposition": "non_table_visual"},
                        "evidence_refs": list(task.input_refs),
                        "rationale": rationale,
                        "confidence": max(0.9, float(normalized.get("confidence") or 0.0)),
                    }
                ],
                "abstain_reason": None,
            }
        )
        flags = normalized.setdefault("quality_flags", [])
        if "adapter_non_table_visual_classification" not in flags:
            flags.append("adapter_non_table_visual_classification")

    @staticmethod
    def normalize_verifier(payload: dict[str, Any]) -> dict[str, Any]:
        """Normalizes common OpenAI-compatible verifier aliases before validation."""

        normalized = dict(payload)
        accept_aliases = {
            "accept", "accepted", "approve", "approved", "confirm", "confirmed",
            "pass", "passed", "agree", "agreed", "valid", "propose_patch",
        }
        reject_aliases = {
            "reject", "rejected", "decline", "declined", "disagree", "disagreed",
            "needs_repair", "requires_repair", "invalid",
        }
        abstain_aliases = {
            "abstain", "unknown", "uncertain", "cannot_determine", "insufficient_evidence",
        }

        def canonical(value: Any, default: str = "abstain") -> str:
            raw = str(value or "").strip().casefold().replace(" ", "_")
            if raw in accept_aliases:
                return "accept"
            if raw in reject_aliases:
                return "reject"
            if raw in abstain_aliases:
                return "abstain"
            return default

        def messages(value: Any) -> list[str]:
            if value is None:
                return []
            if isinstance(value, str):
                return [value] if value.strip() else []
            if isinstance(value, dict):
                return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
            if isinstance(value, (list, tuple)):
                return [
                    item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
                    for item in value
                    if str(item).strip()
                ]
            return [str(value)]

        try:
            top_confidence = max(0.0, min(1.0, float(normalized.get("confidence", 0.5))))
        except (TypeError, ValueError):
            top_confidence = 0.5

        raw_decisions = (
            normalized.get("transaction_decisions")
            or normalized.get("decisions")
            or normalized.get("transactions")
            or []
        )
        if isinstance(raw_decisions, dict):
            raw_decisions = [raw_decisions]
        decisions: list[dict[str, Any]] = []
        if isinstance(raw_decisions, list):
            for raw in raw_decisions:
                if not isinstance(raw, dict) or not raw.get("transaction_id"):
                    continue
                decision = dict(raw)
                decision["verdict"] = canonical(
                    raw.get("verdict") or raw.get("decision") or raw.get("status")
                )
                try:
                    decision["confidence"] = max(
                        0.0,
                        min(1.0, float(raw.get("confidence", top_confidence))),
                    )
                except (TypeError, ValueError):
                    decision["confidence"] = top_confidence
                decision["disagreements"] = messages(
                    raw.get("disagreements")
                    or raw.get("reasons")
                    or raw.get("reason")
                    or raw.get("rationale")
                )
                decisions.append(decision)

        top_verdict = canonical(
            normalized.get("verdict") or normalized.get("decision") or normalized.get("status"),
            default="",
        )
        if not top_verdict and decisions:
            verdicts = {item["verdict"] for item in decisions}
            top_verdict = "reject" if "reject" in verdicts else "abstain" if "abstain" in verdicts else "accept"
        normalized["verdict"] = top_verdict or "abstain"
        normalized["confidence"] = top_confidence
        normalized["disagreements"] = messages(
            normalized.get("disagreements")
            or normalized.get("reasons")
            or normalized.get("reason")
        )
        for decision in decisions:
            if decision["verdict"] in {"reject", "abstain"} and not decision["disagreements"]:
                decision["disagreements"] = list(normalized["disagreements"])
        if normalized["verdict"] in {"reject", "abstain"} and not normalized["disagreements"]:
            if not decisions or any(
                decision["verdict"] in {"reject", "abstain"} and not decision["disagreements"]
                for decision in decisions
            ):
                raise ValueError(
                    "Verifier reject/abstain must identify a concrete evidence disagreement; "
                    "an empty negative verdict is not a valid review decision."
                )
        normalized["transaction_decisions"] = decisions
        return normalized

    @staticmethod
    def _complete_table_repair_contract(
        value: dict[str, Any],
        table: TableIR,
        patch: dict[str, Any],
        task: VlmReviewTask,
    ) -> None:
        cells = value.get("cells") if isinstance(value.get("cells"), list) else []
        by_position = {(cell.row_index, cell.col_index): cell for cell in table.cells}
        by_text: dict[str, list] = {}
        for cell in table.cells:
            key = comparison_key(cell.text)
            if key:
                by_text.setdefault(key, []).append(cell)
        for raw in cells:
            if not isinstance(raw, dict):
                continue
            mapped = [str(value) for value in raw.get("source_cell_ids", []) if value]
            explicit_id = str(raw.get("cell_id") or "")
            if explicit_id and any(cell.cell_id == explicit_id for cell in table.cells):
                mapped.append(explicit_id)
            position = (int(raw.get("row_index") or 0), int(raw.get("col_index") or 0))
            original = by_position.get(position)
            original_key = comparison_key(original.text) if original else ""
            if original and original_key and original_key == comparison_key(str(raw.get("text") or "")):
                mapped.append(original.cell_id)
            text_matches = by_text.get(comparison_key(str(raw.get("text") or "")), [])
            if len(text_matches) == 1:
                mapped.append(text_matches[0].cell_id)
            raw["source_cell_ids"] = list(dict.fromkeys(mapped))
            raw.setdefault("visual_evidence_refs", list(patch.get("evidence_refs") or task.input_refs))
        value.setdefault("missing_text", [])
        value.setdefault("visual_evidence_refs", list(patch.get("evidence_refs") or task.input_refs))
        value.setdefault(
            "repair_reason",
            "Reconstruct the complete visible page-local grid while preserving mapped source cells.",
        )

    @staticmethod
    def normalize_legacy_aliases(
        document: DocumentIR,
        payload: dict[str, Any],
        *,
        task: VlmReviewTask | None = None,
    ) -> dict[str, Any]:
        normalized = dict(payload)
        normalized_flags: list[str] = []

        def string_list(value: Any) -> list[str]:
            if value is None:
                return []
            if isinstance(value, list):
                return [
                    item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
                    for item in value
                ]
            if isinstance(value, str):
                return [value]
            return [json.dumps(value, ensure_ascii=False, sort_keys=True)]

        def confidence(value: Any, default: float = 0.5) -> float:
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                return default

        default_targets: list[tuple[str, str]] = []
        if task:
            default_targets = [(scope.target_type, scope.target_id) for scope in task.scope]
            if not default_targets:
                default_targets = [(task.target_type, task.target_id)]
        default_target = default_targets[0] if len(default_targets) == 1 else None

        raw_patches = (
            normalized.get("patches")
            or normalized.get("proposed_patches")
            or normalized.get("corrections")
            or []
        )
        if isinstance(raw_patches, dict):
            raw_patches = [raw_patches]
        if not isinstance(raw_patches, list):
            raw_patches = []

        raw_verdict = str(
            normalized.get("verdict")
            or normalized.get("decision")
            or normalized.get("status")
            or ""
        ).strip().casefold().replace(" ", "_")
        patch_verdicts = {
            "correct",
            "replace",
            "fix",
            "update",
            "needs_correction",
            "requires_correction",
            "needs_patch",
            "needs_repair",
            "requires_repair",
            "repair",
            "reject",
            "rejected",
            "invalid",
            "reject_as_nontable",
            "reject_table_as_visual",
            "reject_invalid_candidate_table",
            *(name for name in PATCH_OPERATION_NAMES if name != "confirm"),
        }
        confirm_verdicts = {"confirm", "confirmed", "pass", "accept", "accepted", "valid", "keep"}
        abstain_verdicts = {"abstain", "unknown", "uncertain", "cannot_determine", "insufficient_evidence"}
        if raw_verdict in patch_verdicts | confirm_verdicts | abstain_verdicts and raw_verdict not in {
            "confirm",
            "propose_patch",
            "abstain",
        }:
            normalized_flags.append("non_contract_verdict_normalized")

        non_table_aliases = {
            "reject_as_nontable": "non_table_visual",
            "reject_table_as_visual": "non_table_visual",
            "reject_invalid_candidate_table": "non_table_visual",
            "non_table": "non_table_visual",
            "not_a_table": "non_table_visual",
            "decoration": "decoration",
            "decorative": "decoration",
            "duplicate": "duplicate_fragment",
            "duplicate_fragment": "duplicate_fragment",
        }
        top_level_classification = str(
            normalized.get("classification")
            or normalized.get("object_type")
            or normalized.get("disposition")
            or ""
        ).strip().casefold().replace(" ", "_")
        inferred_disposition = non_table_aliases.get(raw_verdict) or non_table_aliases.get(top_level_classification)
        spread_decision = str(
            normalized.get("spread_decision")
            or normalized.get("page_relationship")
            or top_level_classification
        ).strip().casefold().replace(" ", "_")
        spread_confirm_aliases = {
            "confirm_spread",
            "horizontal_spread",
            "join_pages",
            "join_next",
            "left_right_spread",
        }
        spread_reject_aliases = {
            "reject_spread",
            "standalone",
            "standalone_pages",
            "not_a_spread",
        }
        if (
            default_target
            and default_target[0] == "spread"
            and not raw_patches
            and spread_decision in spread_confirm_aliases | spread_reject_aliases
        ):
            raw_patches = [
                {
                    "target_type": "spread",
                    "target_id": default_target[1],
                    "operation": (
                        "confirm_spread"
                        if spread_decision in spread_confirm_aliases
                        else "reject_spread"
                    ),
                    "proposed_value": {
                        "reading_direction": normalized.get("reading_direction") or "left_to_right"
                    },
                    "rationale": normalized.get("reason")
                    or normalized.get("explanation")
                    or "Normalized horizontal spread classification.",
                }
            ]
            normalized_flags.append("spread_classification_normalized")
        if (
            inferred_disposition
            and default_target
            and default_target[0] == "table"
            and not raw_patches
        ):
            raw_patches = [
                {
                    "target_type": "table",
                    "target_id": default_target[1],
                    "operation": "retire_table_candidate",
                    "proposed_value": {"disposition": inferred_disposition},
                    "rationale": normalized.get("reason")
                    or normalized.get("explanation")
                    or "Reviewer classified the routed candidate as non-table.",
                }
            ]

        if (
            default_target
            and default_target[0] == "table"
            and not raw_patches
            and isinstance(normalized.get("cells"), list)
        ):
            raw_patches = [
                {
                    "target_type": "table",
                    "target_id": default_target[1],
                    "operation": "set_table_grid",
                    "proposed_value": {
                        "row_count": normalized.get("row_count"),
                        "column_count": normalized.get("column_count"),
                        "cells": normalized.get("cells"),
                    },
                    "rationale": normalized.get("reason") or "Normalized top-level table reconstruction.",
                }
            ]
            normalized_flags.append("top_level_table_grid_normalized")

        patches: list[Any] = []
        operation_aliases = {
            "join_pages": "confirm_spread",
            "merge_pages_horizontal": "confirm_spread",
            "standalone_pages": "reject_spread",
            "not_a_spread": "reject_spread",
            "link_horizontal": "link_horizontal_continuation",
            "correct_ocr": "correct_ocr_text",
            "correct_text": "correct_ocr_text",
            "fix_ocr_text": "correct_ocr_text",
            "add_visual_text": "add_visual_text_block",
            "recover_visual_text": "add_visual_text_block",
            "bind_visual_text": "bind_block_to_figure",
            "bind_figure_text": "bind_block_to_figure",
            "replace_row": "set_table_grid",
            "replace_table": "set_table_grid",
            "insert_rows": "insert_table_row",
            "add_row": "insert_table_row",
            "remove_table": "retire_table_candidate",
            "delete_table": "retire_table_candidate",
            "retire_table": "retire_table_candidate",
            "reject_table": "retire_table_candidate",
            "reject_as_nontable": "retire_table_candidate",
            "reject_table_as_visual": "retire_table_candidate",
        }
        for raw_patch in raw_patches:
            if not isinstance(raw_patch, dict):
                continue
            patch = dict(raw_patch)
            target_id = (
                patch.get("target_id")
                or patch.get("object_id")
                or patch.get("table_id")
                or (default_target[1] if default_target else None)
            )
            target = PatchGuard._target(document, str(target_id)) if target_id else None
            actual_target_type = PatchGuard._target_type(target) if target is not None else None
            target_type = (
                actual_target_type
                or patch.get("target_type")
                or patch.get("object_type")
                or (default_target[0] if default_target else None)
            )
            operation = str(patch.get("operation") or patch.get("action") or "").strip().casefold()
            operation = operation_aliases.get(operation, operation)
            field_path = str(patch.get("field_path") or patch.get("field") or "")
            proposed_value = next(
                (
                    patch[key]
                    for key in (
                        "proposed_value",
                        "new_value",
                        "after_value",
                        "after",
                        "value",
                        "replacement",
                        "corrected_value",
                        "corrected_text",
                        "grid",
                    )
                    if key in patch
                ),
                None,
            )
            raw_evidence_refs = (
                patch.get("evidence_refs")
                or patch.get("visual_evidence_refs")
                or patch.get("evidence_ids")
                or patch.get("evidence")
                or []
            )
            if isinstance(raw_evidence_refs, str):
                raw_evidence_refs = [raw_evidence_refs]
            elif isinstance(raw_evidence_refs, (tuple, set)):
                raw_evidence_refs = list(raw_evidence_refs)
            elif not isinstance(raw_evidence_refs, list):
                raw_evidence_refs = [raw_evidence_refs]
            review_kind = task.review_plan.review_kind if task and task.review_plan else ""
            if (
                operation in {"replace_block_text", "replace_cell_text"}
                and review_kind in {"page_text_coverage", "horizontal_page_spread"}
                and actual_target_type in {"block", "cell"}
            ):
                operation = "correct_ocr_text"
                field_path = "text"
                normalized_flags.append("ocr_correction_operation_normalized")
            elif operation == "correct_ocr_text":
                field_path = "text"
            if (
                operation in {"confirm_spread", "reject_spread", "link_horizontal_continuation"}
                and task is not None
                and task.target_type == "spread"
            ):
                target_id = task.target_id
                target = PatchGuard._target(document, target_id)
                actual_target_type = "spread"
                target_type = "spread"
                normalized_flags.append("spread_operation_target_normalized")
            if operation == "bind_block_to_figure" and not isinstance(target, BlockIR):
                original_figure = target if isinstance(target, FigureIR) else None
                block_id = (
                    proposed_value.get("block_id")
                    if isinstance(proposed_value, dict)
                    else None
                )
                if not block_id:
                    referenced_blocks = [
                        str(ref)
                        for ref in raw_evidence_refs
                        if isinstance(PatchGuard._target(document, str(ref)), BlockIR)
                    ]
                    if len(referenced_blocks) == 1:
                        block_id = referenced_blocks[0]
                block = PatchGuard._target(document, str(block_id or ""))
                binding_payload = dict(proposed_value) if isinstance(proposed_value, dict) else {}
                if (
                    isinstance(block, BlockIR)
                    and original_figure is not None
                    and binding_payload.get("visual_role")
                ):
                    target_id = block.block_id
                    target = block
                    actual_target_type = "block"
                    target_type = "block"
                    binding_payload.setdefault("figure_id", original_figure.figure_id)
                    binding_payload.setdefault("relation", "element")
                    proposed_value = binding_payload
                    normalized_flags.append("figure_binding_target_normalized_to_block")
                else:
                    normalized_flags.append("invalid_figure_binding_patch_dropped")
                    continue
            if operation == "bind_block_to_figure":
                binding_payload = proposed_value if isinstance(proposed_value, dict) else {}
                relation = str(binding_payload.get("relation") or "element")
                if (
                    not isinstance(target, BlockIR)
                    or not binding_payload.get("figure_id")
                    or (relation != "caption" and not binding_payload.get("visual_role"))
                ):
                    normalized_flags.append("invalid_figure_binding_patch_dropped")
                    continue
            if operation == "set_caption" and not str(proposed_value or "").strip():
                normalized_flags.append("empty_caption_patch_dropped")
                continue
            if operation == "add_visual_text_block" and isinstance(proposed_value, dict):
                caption_type = str(proposed_value.get("block_type") or "").casefold()
                if caption_type in {"figure_caption", "table_caption"}:
                    caption_target = target if isinstance(target, (FigureIR, TableIR)) else None
                    if caption_target is None:
                        referenced_id = (
                            proposed_value.get("figure_id")
                            if caption_type == "figure_caption"
                            else proposed_value.get("table_id")
                        )
                        referenced = PatchGuard._target(document, str(referenced_id or ""))
                        if isinstance(referenced, (FigureIR, TableIR)):
                            caption_target = referenced
                    if caption_target is not None:
                        operation = "set_caption"
                        target_id = (
                            caption_target.figure_id
                            if isinstance(caption_target, FigureIR)
                            else caption_target.table_id
                        )
                        target_type = "figure" if isinstance(caption_target, FigureIR) else "table"
                        target = caption_target
                        actual_target_type = target_type
                        field_path = "caption"
                        proposed_value = str(proposed_value.get("text") or "").strip()
                        normalized_flags.append("visual_caption_normalized_to_set_caption")
            if operation == "update_field" and field_path in {"quality_flags", "visual_type", "caption", "legend_text"}:
                operation = {
                    "quality_flags": "add_quality_flags",
                    "visual_type": "set_visual_type",
                    "caption": "set_caption",
                    "legend_text": "set_figure_legend_text",
                }[field_path]
            if operation == "set_visual_type" and isinstance(target, TableIR):
                visual_type = str(proposed_value or "").casefold()
                operation = "retire_table_candidate"
                target_type = "table"
                proposed_value = {
                    "disposition": "decoration" if visual_type == "decoration" else "non_table_visual"
                }
                normalized_flags.append("table_visual_rejection_normalized")
            if operation == "add_visual_text_block" and isinstance(target, FigureIR):
                page = next(
                    (item for item in document.pages if item.page_index == target.page_index),
                    None,
                )
                if page:
                    if not isinstance(proposed_value, dict):
                        proposed_value = {"text": str(proposed_value or "")}
                    else:
                        proposed_value = dict(proposed_value)
                    proposed_value.setdefault("figure_id", target.figure_id)
                    target_id = page.page_id
                    target_type = "page"
                    normalized_flags.append("visual_text_target_normalized_to_page")
            if operation == "retire_table_candidate":
                disposition = (
                    proposed_value.get("disposition")
                    if isinstance(proposed_value, dict)
                    else non_table_aliases.get(str(proposed_value or "").casefold())
                )
                proposed_value = {"disposition": disposition or inferred_disposition or "non_table_visual"}
                target_type = "table"
            if operation in {"confirm_spread", "reject_spread"}:
                proposed_value = dict(proposed_value) if isinstance(proposed_value, dict) else {}
                proposed_value.setdefault("reading_direction", "left_to_right")
                target_type = "spread"
            if operation == "replace_cell" and target_type == "table":
                match = re.search(r"rows\[(\d+)]\.cells\[(\d+)]", field_path)
                table = target if isinstance(target, TableIR) else None
                if match and table:
                    row_index, col_index = int(match.group(1)), int(match.group(2))
                    cell = next(
                        (item for item in table.cells if item.row_index == row_index and item.col_index == col_index),
                        None,
                    )
                    if cell:
                        target_type = "cell"
                        target_id = cell.cell_id
                        operation = "replace_cell_text"
                        field_path = "text"
                        if isinstance(proposed_value, dict):
                            proposed_value = proposed_value.get("text")
            if not target_id or not target_type or not operation:
                continue
            patches.append(
                {
                    "target_type": target_type,
                    "target_id": str(target_id),
                    "operation": operation,
                    "field_path": field_path or None,
                    "proposed_value": proposed_value,
                    "evidence_refs": (
                        raw_evidence_refs
                    ),
                    "rationale": patch.get("rationale")
                    or patch.get("reason")
                    or patch.get("explanation")
                    or "Normalized reviewer proposal.",
                    "confidence": confidence(patch.get("confidence"), confidence(normalized.get("confidence"))),
                }
            )

        if patches:
            verdict = "propose_patch"
        elif raw_verdict in abstain_verdicts:
            verdict = "abstain"
        elif raw_verdict == "correct":
            verdict = "confirm"
        elif raw_verdict in patch_verdicts:
            verdict = "propose_patch"
        elif raw_verdict in confirm_verdicts:
            verdict = "confirm"
        else:
            verdict = "abstain"
            normalized_flags.append("missing_contract_verdict_normalized_to_abstain")

        raw_scope_decisions = normalized.get("scope_decisions") or []
        if isinstance(raw_scope_decisions, dict):
            raw_scope_decisions = [raw_scope_decisions]
        scope_decisions = []
        for index, raw_decision in enumerate(raw_scope_decisions):
            if not isinstance(raw_decision, dict):
                continue
            decision_value = str(
                raw_decision.get("decision")
                or raw_decision.get("verdict")
                or raw_decision.get("operation")
                or raw_decision.get("action")
                or ""
            ).strip().casefold()
            if decision_value in patch_verdicts:
                decision_value = "propose_patch"
            elif decision_value in confirm_verdicts:
                decision_value = "confirm"
            elif decision_value in abstain_verdicts:
                decision_value = "abstain"
            target_type = raw_decision.get("target_type")
            target_id = raw_decision.get("target_id")
            if (not target_type or not target_id) and index < len(default_targets):
                target_type, target_id = default_targets[index]
            if target_type and target_id and decision_value in {"confirm", "propose_patch", "abstain"}:
                scope_decisions.append(
                    {
                        "target_type": target_type,
                        "target_id": target_id,
                        "decision": decision_value,
                        "rationale": raw_decision.get("rationale") or raw_decision.get("reason"),
                        "confidence": confidence(raw_decision.get("confidence"), confidence(normalized.get("confidence"))),
                    }
                )
        if not scope_decisions and default_targets:
            scope_decisions = [
                {
                    "target_type": target_type,
                    "target_id": target_id,
                    "decision": verdict,
                    "rationale": normalized.get("reason") or normalized.get("explanation"),
                    "confidence": confidence(normalized.get("confidence")),
                }
                for target_type, target_id in default_targets
            ]

        normalized["verdict"] = verdict
        normalized["findings"] = string_list(normalized.get("findings"))
        normalized["scope_decisions"] = scope_decisions
        normalized["patches"] = patches
        normalized["confidence"] = confidence(normalized.get("confidence"))
        normalized["abstain_reason"] = (
            normalized.get("abstain_reason")
            or normalized.get("reason")
            if verdict == "abstain"
            else normalized.get("abstain_reason")
        )
        normalized["quality_flags"] = list(
            dict.fromkeys([*string_list(normalized.get("quality_flags")), *normalized_flags])
        )
        return normalized
