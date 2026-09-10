from __future__ import annotations

import html
import json
import re
from difflib import SequenceMatcher
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from esg_v2.document.chart_spec_normalizer import ChartSpecNormalizer
from esg_v2.document.contracts import (
    AtomicPatch,
    BlockIR,
    BoundingBox,
    CellIR,
    ChartSpec,
    DocumentIR,
    FigureIR,
    GuardCheck,
    GuardResult,
    PageIR,
    RetiredEntityIR,
    SectionIR,
    SourceTrace,
    SpreadEntityLink,
    SpreadIR,
    StructureEdge,
    TableGridRepairProposal,
    TableIR,
)
from esg_v2.document.identifiers import sequential_id
from esg_v2.document.geometry import bbox_containment, bbox_iou, union_bboxes
from esg_v2.document.logical_table_builder import LogicalTableBuilder
from esg_v2.document.operation_registry import OperationRegistry
from esg_v2.document.spread_pair_analysis import SpreadPairAnalyzer
from esg_v2.document.text_normalization import canonical_page_text, comparison_key, visible_text
from esg_v2.storage.package_layout import cell_id as canonical_cell_id
from esg_v2.storage.package_layout import page_object_id


class PatchGuard:
    def evaluate(
        self,
        document: DocumentIR,
        task_id: str,
        patches: list[AtomicPatch],
        *,
        allowed_target_ids: set[str] | None = None,
        required_target_ids: set[str] | None = None,
        reviewed_target_ids: set[str] | None = None,
        allowed_operations: set[str] | None = None,
        required_any_operations: set[str] | None = None,
        transaction_id: str | None = None,
    ) -> GuardResult:
        checks: list[GuardCheck] = []
        if not patches:
            checks.append(self._check("patches_present", False, "blocking", "Correction verdict supplied no atomic patches."))
        if required_any_operations:
            present = {patch.operation for patch in patches} & required_any_operations
            checks.append(
                self._check(
                    "required_classification_operation",
                    bool(present),
                    "blocking",
                    f"Patch batch includes one required classification operation: {sorted(present)}."
                    if present
                    else f"Patch batch must include one of {sorted(required_any_operations)}.",
                )
            )
        confirmed_spread_ids = {
            patch.target_id
            for patch in patches
            if patch.operation == "confirm_spread"
        }
        horizontally_linked_spread_ids = {
            patch.target_id
            for patch in patches
            if patch.operation == "link_horizontal_continuation"
        }
        for spread_id in sorted(confirmed_spread_ids):
            spread = self._target(document, spread_id)
            requires_link = (
                isinstance(spread, SpreadIR)
                and self._spread_has_linkable_opposite_entities(document, spread)
            )
            checks.append(
                self._check(
                    f"required_horizontal_link:{spread_id}",
                    not requires_link or spread_id in horizontally_linked_spread_ids,
                    "blocking",
                    "Confirmed spread includes explicit cross-seam entity links."
                    if requires_link and spread_id in horizontally_linked_spread_ids
                    else (
                        "Confirmed spread has no same-type cross-seam entities that require a link."
                        if not requires_link
                        else "Confirmed spread has linkable cross-seam entities but no horizontal link patch."
                    ),
                )
            )
        for patch in patches:
            if allowed_operations is not None:
                operation_allowed = patch.operation in allowed_operations
                checks.append(
                    self._check(
                        f"{patch.patch_id}:operation_in_review_plan",
                        operation_allowed,
                        "blocking",
                        f"Operation {patch.operation} is allowed by the typed review plan."
                        if operation_allowed
                        else f"Operation {patch.operation} is outside the typed review plan.",
                    )
                )
            if allowed_target_ids is not None:
                allowed = patch.target_id in allowed_target_ids
                checks.append(
                    self._check(
                        f"{patch.patch_id}:target_in_review_scope",
                        allowed,
                        "blocking",
                        "Patch target is inside the routed review scope."
                        if allowed
                        else f"Patch target {patch.target_id} is outside the routed review scope.",
                    )
                )
            target = self._target(document, patch.target_id)
            checks.append(self._check(f"{patch.patch_id}:target_exists", target is not None, "blocking", f"Target {patch.target_id} exists." if target else f"Target {patch.target_id} does not exist."))
            if target is None:
                continue
            actual_target_type = self._target_type(target)
            target_type_matches = actual_target_type == patch.target_type
            checks.append(
                self._check(
                    f"{patch.patch_id}:target_type_matches_entity",
                    target_type_matches,
                    "blocking",
                    f"Patch target_type {patch.target_type} matches target {patch.target_id}."
                    if target_type_matches
                    else (
                        f"Patch declares target_type {patch.target_type}, but target {patch.target_id} "
                        f"is a {actual_target_type or 'unknown entity type'}."
                    ),
                )
            )
            operation_target_valid = OperationRegistry.supports_target(
                patch.operation,
                actual_target_type or "",
            )
            checks.append(
                self._check(
                    f"{patch.patch_id}:operation_target_contract",
                    operation_target_valid,
                    "blocking",
                    (
                        f"Operation {patch.operation} supports target type {actual_target_type}."
                        if operation_target_valid
                        else f"Operation {patch.operation} cannot be applied to target type {actual_target_type}."
                    ),
                    details={
                        "target_id": patch.target_id,
                        "actual_target_type": actual_target_type,
                        "allowed_target_types": sorted(OperationRegistry.get(patch.operation).target_types),
                        "recommended_operation": "use_an_operation_registered_for_the_target_type",
                    },
                )
            )
            try:
                checks.extend(self._operation_checks(document, patch, target, actual_target_type))
            except Exception as exc:
                checks.append(
                    self._check(
                        f"{patch.patch_id}:operation_payload_safe",
                        False,
                        "blocking",
                        f"Patch operation payload cannot be validated safely: {type(exc).__name__}: {exc}",
                    )
                )
        if required_target_ids:
            covered = set(reviewed_target_ids or set())
            for patch in patches:
                covered.add(patch.target_id)
                target = self._target(document, patch.target_id)
                if isinstance(target, CellIR):
                    covered.add(target.table_id)
                if patch.operation == "bind_block_to_figure" and isinstance(patch.proposed_value, dict):
                    figure_id = str(patch.proposed_value.get("figure_id") or "")
                    if figure_id:
                        covered.add(figure_id)
            for target_id in sorted(required_target_ids):
                checks.append(
                    self._check(
                        f"required_target:{target_id}",
                        target_id in covered,
                        "blocking",
                        (
                            f"Blocking review target {target_id} is explicitly confirmed or corrected."
                            if target_id in covered
                            else (
                                f"Blocking review target {target_id} has no recognized confirm/propose_patch "
                                "scope decision and is not directly corrected by this transaction."
                            )
                        ),
                        details=(
                            {}
                            if target_id in covered
                            else {
                                "missing_target_ids": [target_id],
                                "recommended_operation": "confirm_or_patch_required_target",
                            }
                        ),
                    )
                )
        blocking_failure = any(not check.passed and check.severity in {"error", "blocking"} for check in checks)
        if patches and not blocking_failure:
            try:
                staged = DocumentPatchApplier().validate(document, patches)
                linked_group_ids = {
                    table.continuation_group_id
                    for patch in patches
                    if patch.operation == "link_horizontal_continuation"
                    for raw in (
                        patch.proposed_value.get("links", [])
                        if isinstance(patch.proposed_value, dict)
                        else []
                    )
                    for table in (
                        self._target(staged, str(raw.get("source_id") or "")),
                        self._target(staged, str(raw.get("target_id") or "")),
                    )
                    if isinstance(table, TableIR) and table.continuation_group_id
                }
                unresolved = [
                    table.logical_table_id
                    for table in staged.logical_tables
                    if table.continuation_group_id in linked_group_ids
                    and table.status == "review_required"
                ]
                checks.append(
                    self._check(
                        "horizontal_table_composition_resolved",
                        not unresolved,
                        "blocking",
                        (
                            "Horizontal table segments have compatible row alignment."
                            if not unresolved
                            else (
                                "Horizontal table continuation still has incompatible row alignment: "
                                f"{', '.join(unresolved)}. Repair the page-local table grids before "
                                "accepting the cross-seam link."
                            )
                        ),
                    )
                )
                checks.append(
                    self._check(
                        "atomic_batch_application",
                        True,
                        "info",
                        "The complete patch batch applies successfully to an isolated Document IR copy.",
                    )
                )
            except Exception as exc:
                checks.append(
                    self._check(
                        "atomic_batch_application",
                        False,
                        "blocking",
                        f"The patch batch cannot be applied safely: {type(exc).__name__}: {exc}",
                    )
                )
        passed = not any(not check.passed and check.severity in {"error", "blocking"} for check in checks)
        return GuardResult(
            guard_result_id=sequential_id(
                "guard",
                (item.guard_result_id for item in document.guard_results),
            ),
            task_id=task_id,
            transaction_id=transaction_id,
            patch_ids=[patch.patch_id for patch in patches],
            passed=passed,
            requires_independent_verifier=any(patch.operation != "confirm" for patch in patches),
            checks=checks,
        )

    def _operation_checks(
        self,
        document: DocumentIR,
        patch: AtomicPatch,
        target: Any,
        actual_target_type: str | None,
    ) -> list[GuardCheck]:
        checks = []
        if patch.operation in {"replace_text", "replace_table"} and patch.target_type == "page":
            checks.append(self._check(f"{patch.patch_id}:no_whole_page_rewrite", False, "blocking", "Whole-page replacement is forbidden."))
        else:
            checks.append(self._check(f"{patch.patch_id}:atomic_scope", True, "info", "Patch scope is atomic."))

        current = self._current_value(patch, target)
        if patch.before_value is not None:
            matches = self._equivalent(current, patch.before_value)
            checks.append(self._check(f"{patch.patch_id}:before_matches", matches, "blocking", "Patch before_value matches the candidate." if matches else "Patch before_value does not match the candidate."))

        if patch.operation in {"replace_block_text", "replace_cell_text", "replace_text"}:
            proposed = str(patch.proposed_value or "")
            checks.append(self._check(f"{patch.patch_id}:nonempty_text", bool(proposed.strip()), "blocking", "Proposed text is non-empty."))
            original = str(current or "")
            if original:
                noise_tokens = self._suspected_ocr_noise_tokens(original)
                retained_ratio = self._source_text_retention(
                    self._without_noise_tokens(original, noise_tokens),
                    proposed,
                )
                checks.append(
                    self._check(
                        f"{patch.patch_id}:source_text_retention",
                        retained_ratio >= 0.80,
                        "blocking",
                        f"Noise-adjusted source-character retention ratio is {retained_ratio:.3f}.",
                    )
                )
                original_numbers = self._numbers(original)
                proposed_numbers = self._numbers(proposed)
                material_numbers = self._material_numbers(original_numbers, noise_tokens)
                missing_numbers = [value for value in material_numbers if value not in proposed_numbers]
                checks.append(
                    self._check(
                        f"{patch.patch_id}:numeric_tokens_preserved",
                        not missing_numbers,
                        "blocking",
                        f"Material numeric tokens before={material_numbers}, after={proposed_numbers}, missing={missing_numbers}.",
                    )
                )
                if noise_tokens:
                    checks.append(
                        self._check(
                            f"{patch.patch_id}:suspected_ocr_noise",
                            True,
                            "warning",
                            f"Retention ignored suspected duplicated OCR tokens={noise_tokens}; they remain visible in the audit diff.",
                        )
                    )

        if patch.operation == "correct_ocr_text":
            proposed = str(patch.proposed_value or "").strip()
            original = str(current or "").strip()
            length_limit = max(len(original) * 4, len(original) + 200)
            evidence_resolves = any(
                value in patch.evidence_refs
                for artifact in document.artifacts
                for value in (artifact.path, artifact.remote_uri)
                if value
            )
            changed_numbers = self._numbers(original) != self._numbers(proposed)
            checks.extend(
                [
                    self._check(
                        f"{patch.patch_id}:ocr_correction_nonempty",
                        bool(original and proposed),
                        "blocking",
                        "OCR correction has non-empty source and replacement text.",
                    ),
                    self._check(
                        f"{patch.patch_id}:ocr_correction_is_change",
                        comparison_key(original) != comparison_key(proposed),
                        "blocking",
                        "OCR correction changes the routed source object.",
                    ),
                    self._check(
                        f"{patch.patch_id}:ocr_correction_bounded",
                        len(proposed) <= length_limit,
                        "blocking",
                        f"OCR correction remains object-local ({len(proposed)} <= {length_limit} characters).",
                    ),
                    self._check(
                        f"{patch.patch_id}:ocr_correction_confidence",
                        float(patch.confidence or 0.0) >= 0.85,
                        "blocking",
                        "OCR correction confidence is at least 0.85.",
                    ),
                    self._check(
                        f"{patch.patch_id}:ocr_correction_evidence",
                        evidence_resolves,
                        "blocking",
                        "OCR correction resolves to tracked visual evidence.",
                    ),
                    self._check(
                        f"{patch.patch_id}:ocr_numeric_substitution_audited",
                        True,
                        "warning" if changed_numbers else "info",
                        (
                            f"Numeric OCR substitution is explicit and requires the independent verifier: "
                            f"before={self._numbers(original)}, after={self._numbers(proposed)}."
                            if changed_numbers
                            else "OCR correction changes no numeric token."
                        ),
                    ),
                ]
            )

        if patch.operation == "set_bbox":
            try:
                bbox = BoundingBox.model_validate(patch.proposed_value)
                page_index = getattr(target, "page_index", None)
                page = next((item for item in document.pages if item.page_index == page_index), None)
                inside = bbox.x0 <= bbox.x1 and bbox.y0 <= bbox.y1
                if page and page.width and page.height:
                    inside = inside and bbox.x0 >= 0 and bbox.y0 >= 0 and bbox.x1 <= page.width and bbox.y1 <= page.height
                checks.append(self._check(f"{patch.patch_id}:bbox_inside_page", inside, "blocking", "Bounding box is valid and inside the page."))
            except Exception:
                checks.append(self._check(f"{patch.patch_id}:bbox_schema", False, "blocking", "Bounding box payload is invalid."))

        if patch.operation in {"set_table_grid", "replace_table"}:
            payload = patch.proposed_value
            try:
                proposal = TableGridRepairProposal.model_validate(payload)
                valid = bool(proposal.cells)
            except (TypeError, ValueError):
                proposal = None
                valid = False
            checks.append(
                self._check(
                    f"{patch.patch_id}:table_grid_schema",
                    valid,
                    "blocking",
                    (
                        "Table grid contains positive dimensions and cells."
                        if valid
                        else (
                            "Table grid requires positive row_count and column_count plus a non-empty cells list. "
                            "A valid physical table cannot be retired by sending a zero-sized grid."
                        )
                    ),
                )
            )
            if valid:
                rows = proposal.row_count
                columns = proposal.column_count
                occupied = set()
                cells_valid = True
                for proposed_cell in proposal.cells:
                    try:
                        row = proposed_cell.row_index
                        column = proposed_cell.col_index
                        row_span = proposed_cell.row_span
                        col_span = proposed_cell.col_span
                        cells_valid = cells_valid and 0 <= row < rows and 0 <= column < columns
                        cells_valid = cells_valid and row + row_span <= rows and column + col_span <= columns
                        for occupied_row in range(row, row + row_span):
                            for occupied_column in range(column, column + col_span):
                                position = (occupied_row, occupied_column)
                                if position in occupied:
                                    cells_valid = False
                                occupied.add(position)
                    except (KeyError, TypeError, ValueError):
                        cells_valid = False
                cells_valid = cells_valid and len(occupied) == rows * columns
                checks.append(self._check(f"{patch.patch_id}:table_cell_positions", cells_valid, "blocking", "Table cells form one complete, non-overlapping declared grid."))
                if hasattr(target, "cells"):
                    original_text = " ".join(str(cell.text or "").strip() for cell in target.cells if str(cell.text or "").strip())
                    proposed_text = " ".join(cell.text.strip() for cell in proposal.cells if cell.text.strip())
                    source_cells = {cell.cell_id: cell for cell in target.cells if str(cell.text or "").strip()}
                    source_by_position = {
                        (cell.row_index, cell.col_index): cell
                        for cell in target.cells
                        if str(cell.text or "").strip()
                    }
                    for proposed_cell in proposal.cells:
                        if proposed_cell.source_cell_ids:
                            continue
                        source = source_by_position.get((proposed_cell.row_index, proposed_cell.col_index))
                        if source is None:
                            continue
                        before_key = comparison_key(source.text)
                        after_key = comparison_key(proposed_cell.text)
                        similarity = SequenceMatcher(None, before_key, after_key).ratio()
                        if before_key and after_key and (
                            similarity >= 0.50 or after_key.startswith(before_key)
                        ):
                            proposed_cell.source_cell_ids.append(source.cell_id)
                    mapped_source_ids = {
                        source_id
                        for cell in proposal.cells
                        for source_id in cell.source_cell_ids
                    }
                    unknown_source_ids = sorted(mapped_source_ids - set(source_cells))
                    missing_source_ids = sorted(set(source_cells) - mapped_source_ids)
                    checks.append(
                        self._check(
                            f"{patch.patch_id}:table_source_cell_mapping",
                            not unknown_source_ids and not missing_source_ids,
                            "blocking",
                            (
                                "Every non-empty source cell is mapped exactly into the proposed grid."
                                if not unknown_source_ids and not missing_source_ids
                                else "The proposed grid does not provide a complete valid source-cell mapping."
                            ),
                            details={
                                "missing_source_cell_ids": missing_source_ids,
                                "unknown_source_cell_ids": unknown_source_ids,
                                "conflicting_source_text": {
                                    source_id: source_cells[source_id].text
                                    for source_id in missing_source_ids
                                },
                                "recommended_operation": "set_table_grid",
                            },
                        )
                    )
                    visual_refs = set(proposal.visual_evidence_refs) | set(patch.evidence_refs)
                    visual_refs.update(
                        ref
                        for cell in proposal.cells
                        for ref in cell.visual_evidence_refs
                    )
                    visual_refs.update(
                        ref
                        for item in proposal.missing_text
                        for ref in item.visual_evidence_refs
                    )
                    checks.append(
                        self._check(
                            f"{patch.patch_id}:table_visual_evidence",
                            bool(visual_refs),
                            "blocking",
                            "Table repair includes visual evidence references.",
                            details={
                                "visual_evidence_refs": sorted(visual_refs),
                                "missing_text": [item.model_dump(mode="json") for item in proposal.missing_text],
                                "recommended_operation": "set_table_grid",
                            },
                        )
                    )
                    noise_tokens = self._suspected_ocr_noise_tokens(original_text)
                    retention = self._source_text_retention(
                        self._without_noise_tokens(original_text, noise_tokens),
                        proposed_text,
                    )
                    checks.append(
                        self._check(
                            f"{patch.patch_id}:table_text_retention",
                            retention >= 0.90,
                            "blocking",
                            f"Noise-adjusted source-character retention ratio is {retention:.3f}.",
                        )
                    )
                    original_numbers = self._numbers(original_text)
                    proposed_numbers = self._numbers(proposed_text)
                    material_numbers = self._material_numbers(original_numbers, noise_tokens)
                    missing_numbers = [value for value in material_numbers if value not in proposed_numbers]
                    checks.append(
                        self._check(
                            f"{patch.patch_id}:table_numeric_tokens_preserved",
                            not missing_numbers,
                            "blocking",
                            f"Material table numeric tokens before={material_numbers}, after={proposed_numbers}, missing={missing_numbers}.",
                        )
                    )
                    if noise_tokens:
                        checks.append(
                            self._check(
                                f"{patch.patch_id}:suspected_ocr_noise",
                                True,
                                "warning",
                                f"Retention ignored suspected duplicated OCR tokens={noise_tokens}; they remain visible in the audit diff.",
                            )
                        )
                    checks.extend(self._table_geometry_preservation_checks(document, patch, target))

        if patch.operation == "insert_table_row":
            payload = patch.proposed_value
            valid = isinstance(payload, dict) and isinstance(payload.get("cells"), list)
            valid = valid and hasattr(target, "row_count") and hasattr(target, "column_count")
            try:
                insert_at = int(payload.get("insert_before_row_index")) if isinstance(payload, dict) else -1
                valid = valid and 0 <= insert_at <= target.row_count
            except (AttributeError, TypeError, ValueError):
                valid = False
                insert_at = -1
            occupied_columns = set()
            if valid:
                for raw in payload["cells"]:
                    try:
                        column = int(raw["col_index"])
                        span = max(1, int(raw.get("col_span") or raw.get("colspan") or 1))
                        valid = valid and 0 <= column < target.column_count and column + span <= target.column_count
                        for occupied_column in range(column, column + span):
                            if occupied_column in occupied_columns:
                                valid = False
                            occupied_columns.add(occupied_column)
                    except (KeyError, TypeError, ValueError):
                        valid = False
                valid = valid and len(occupied_columns) == target.column_count
            checks.append(self._check(f"{patch.patch_id}:insert_row_schema", valid, "blocking", f"Inserted row at index {insert_at} fully covers the existing table width without overlap."))

        if patch.operation == "set_visual_type":
            valid_types = {"unknown", "chart", "diagram", "illustration", "photo", "icon", "decoration", "composite"}
            checks.append(
                self._check(
                    f"{patch.patch_id}:visual_type_enum",
                    patch.proposed_value in valid_types,
                    "blocking",
                    "Visual type is a supported Document IR value.",
                )
            )

        if patch.operation == "set_caption":
            proposed_caption = str(patch.proposed_value or "").strip()
            checks.append(
                self._check(
                    f"{patch.patch_id}:caption_nonempty",
                    bool(proposed_caption),
                    "blocking",
                    "Caption patches must contain visible source text; absence of a caption is represented by no patch.",
                )
            )

        if patch.operation == "retire_table_candidate":
            payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
            disposition = payload.get("disposition")
            local_only = isinstance(target, TableIR) and "local_only_table_candidate" in target.quality_flags
            checks.extend(
                [
                    self._check(
                        f"{patch.patch_id}:retirement_disposition",
                        disposition in {"non_table_visual", "decoration", "duplicate_fragment"},
                        "blocking",
                        "Retirement disposition is explicit and supported.",
                    ),
                    self._check(
                        f"{patch.patch_id}:local_only_retirement",
                        local_only,
                        "blocking",
                        "Only a local-only secondary parser candidate may be retired automatically.",
                    ),
                ]
            )

        if patch.operation == "add_visual_text_block":
            payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
            text = str(payload.get("text") or "").strip()
            block_type = payload.get("block_type", "paragraph")
            bbox_valid = False
            try:
                bbox = BoundingBox.model_validate(payload.get("bbox"))
                bbox_valid = (
                    isinstance(target, PageIR)
                    and bbox.x0 <= bbox.x1
                    and bbox.y0 <= bbox.y1
                    and bbox.x0 >= 0
                    and bbox.y0 >= 0
                    and (not target.width or bbox.x1 <= target.width)
                    and (not target.height or bbox.y1 <= target.height)
                )
            except Exception:
                bbox_valid = False
            duplicate = self._normalize_visible_text(text) in {
                self._normalize_visible_text(block.text)
                for block in document.blocks
                if block.page_index == getattr(target, "page_index", -1) and block.text
            }
            evidence_resolves = any(
                artifact.path in patch.evidence_refs or artifact.remote_uri in patch.evidence_refs
                for artifact in document.artifacts
            )
            checks.extend(
                [
                    self._check(
                        f"{patch.patch_id}:visual_text_nonempty",
                        bool(text),
                        "blocking",
                        "Visual text block contains visible source text.",
                    ),
                    self._check(
                        f"{patch.patch_id}:visual_text_bbox",
                        bbox_valid,
                        "blocking",
                        "Visual text block has a valid page-local bounding box.",
                    ),
                    self._check(
                        f"{patch.patch_id}:visual_text_block_type",
                        block_type in {"heading", "paragraph", "list", "caption", "footnote", "unknown"},
                        "blocking",
                        "Visual text block uses a supported semantic block type.",
                    ),
                    self._check(
                        f"{patch.patch_id}:visual_text_not_duplicate",
                        not duplicate,
                        "blocking",
                        "Visual text is not already represented by an equivalent page block.",
                    ),
                    self._check(
                        f"{patch.patch_id}:visual_text_evidence_resolves",
                        evidence_resolves,
                        "blocking",
                        "Visual text evidence resolves to a tracked Document IR artifact.",
                    ),
                ]
            )

        if patch.operation == "set_figure_legend_text":
            proposed = patch.proposed_value
            valid = (
                isinstance(proposed, list)
                and bool(proposed)
                and all(isinstance(item, str) and item.strip() for item in proposed)
            )
            checks.append(
                self._check(
                    f"{patch.patch_id}:legend_text_schema",
                    valid,
                    "blocking",
                    "Figure legend text is a non-empty list of visible labels.",
                )
            )

        if patch.operation == "bind_block_to_figure":
            payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
            figure = self._target(document, str(payload.get("figure_id") or ""))
            relation = str(payload.get("relation") or "element")
            visual_role = payload.get("visual_role")
            supported_roles = {"node_title", "node_body", "label", "legend", "value", "annotation"}
            same_page = (
                isinstance(target, BlockIR)
                and isinstance(figure, FigureIR)
                and target.page_index == figure.page_index
            )
            binding_available = isinstance(target, BlockIR) and (
                target.figure_id in {None, "", getattr(figure, "figure_id", None)}
            )
            geometry_supported = False
            if same_page and target.bbox is not None and figure.bbox is not None:
                geometry_supported = (
                    relation == "caption"
                    or bbox_containment(target.bbox, figure.bbox) >= 0.25
                )
            evidence_resolves = any(
                value in patch.evidence_refs
                for artifact in document.artifacts
                for value in (artifact.path, artifact.remote_uri)
                if value
            )
            checks.extend(
                [
                    self._check(
                        f"{patch.patch_id}:figure_binding_target",
                        same_page,
                        "blocking",
                        "Existing block and target figure are on the same page.",
                    ),
                    self._check(
                        f"{patch.patch_id}:figure_binding_relation",
                        relation in {"element", "caption"},
                        "blocking",
                        "Figure binding relation is element or caption.",
                    ),
                    self._check(
                        f"{patch.patch_id}:figure_binding_role",
                        relation == "caption" or visual_role in supported_roles,
                        "blocking",
                        "Element binding includes a supported visual role.",
                    ),
                    self._check(
                        f"{patch.patch_id}:figure_binding_available",
                        binding_available,
                        "blocking",
                        "Block is unbound or already bound to the same figure.",
                    ),
                    self._check(
                        f"{patch.patch_id}:figure_binding_geometry",
                        geometry_supported,
                        "blocking",
                        "Block geometry supports the proposed page-local figure relationship.",
                    ),
                    self._check(
                        f"{patch.patch_id}:figure_binding_confidence",
                        float(patch.confidence or 0.0) >= 0.80,
                        "blocking",
                        "Figure binding confidence is at least 0.80.",
                    ),
                    self._check(
                        f"{patch.patch_id}:figure_binding_evidence",
                        evidence_resolves,
                        "blocking",
                        "Figure binding resolves to tracked visual evidence.",
                    ),
                ]
            )
        if patch.operation == "upsert_figure_structure":
            payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
            elements = payload.get("elements")
            relations = payload.get("relations", [])
            roles = {"node_title", "node_body", "label", "legend", "value", "annotation"}
            relation_types = {"visual_parent_of", "visual_connected_to", "visual_flow_to"}
            aliases: set[str] = set()
            element_errors: list[str] = []
            elements_valid = isinstance(target, FigureIR) and isinstance(elements, list) and bool(elements)
            if not isinstance(target, FigureIR):
                element_errors.append("target is not a FigureIR")
            if not isinstance(elements, list) or not elements:
                element_errors.append("elements must be a non-empty list")
            existing_blocks = {block.block_id: block for block in document.blocks}
            if elements_valid:
                for index, raw in enumerate(elements):
                    if not isinstance(raw, dict):
                        elements_valid = False
                        element_errors.append(f"element[{index}] is not an object")
                        continue
                    alias = str(raw.get("element_id") or "").strip()
                    text = visible_text(str(raw.get("text") or ""))
                    role = str(raw.get("visual_role") or "")
                    block_type = str(raw.get("block_type") or "unknown")
                    block_id = str(raw.get("block_id") or "")
                    label = alias or f"element[{index}]"
                    if not alias:
                        elements_valid = False
                        element_errors.append(f"{label} has no element_id")
                    elif alias in aliases:
                        elements_valid = False
                        element_errors.append(f"{label} repeats an element_id")
                    aliases.add(alias)
                    if not text:
                        elements_valid = False
                        element_errors.append(f"{label} has no visible text")
                    if role not in roles:
                        elements_valid = False
                        element_errors.append(f"{label} has unsupported visual_role={role!r}")
                    if block_type not in {"unknown", "paragraph", "caption"}:
                        elements_valid = False
                        element_errors.append(f"{label} has unsupported block_type={block_type!r}")
                    try:
                        bbox = BoundingBox.model_validate(raw.get("bbox"))
                        if bbox.x0 >= bbox.x1 or bbox.y0 >= bbox.y1:
                            elements_valid = False
                            element_errors.append(f"{label} has a degenerate bbox")
                        elif target.bbox is None:
                            elements_valid = False
                            element_errors.append("target figure has no canonical bbox")
                        else:
                            containment = bbox_containment(bbox, target.bbox)
                            if containment < 0.75:
                                elements_valid = False
                                element_errors.append(
                                    f"{label} bbox containment is {containment:.3f}; "
                                    "use figure-normalized 0..1 coordinates or canonical page PDF points"
                                )
                    except Exception as exc:
                        elements_valid = False
                        element_errors.append(
                            f"{label} bbox is invalid: {type(exc).__name__}"
                        )
                    if block_id:
                        existing = existing_blocks.get(block_id)
                        if existing is None:
                            elements_valid = False
                            element_errors.append(f"{label} references missing block_id={block_id}")
                        elif existing.figure_id != target.figure_id:
                            elements_valid = False
                            element_errors.append(
                                f"{label} block_id={block_id} is not bound to {target.figure_id}"
                            )
                        elif comparison_key(existing.text) != comparison_key(text):
                            elements_valid = False
                            element_errors.append(
                                f"{label} text does not match existing block_id={block_id}"
                            )
                    else:
                        duplicate = any(
                            block.figure_id == target.figure_id
                            and comparison_key(block.text) == comparison_key(text)
                            for block in document.blocks
                        )
                        if duplicate:
                            elements_valid = False
                            element_errors.append(
                                f"{label} duplicates existing visible text; bind its block_id instead"
                            )

            relations_valid = isinstance(relations, list)
            relation_errors: list[str] = []
            if not relations_valid:
                relation_errors.append("relations must be a list")
            if relations_valid:
                for index, raw in enumerate(relations):
                    if not isinstance(raw, dict):
                        relations_valid = False
                        relation_errors.append(f"relation[{index}] is not an object")
                        continue
                    source = str(raw.get("source_element_id") or "")
                    target_id = str(raw.get("target_element_id") or "")
                    relation = raw.get("relation")
                    if source not in aliases:
                        relations_valid = False
                        relation_errors.append(f"relation[{index}] has unknown source={source!r}")
                    if target_id not in aliases:
                        relations_valid = False
                        relation_errors.append(f"relation[{index}] has unknown target={target_id!r}")
                    if relation not in relation_types:
                        relations_valid = False
                        relation_errors.append(
                            f"relation[{index}] has unsupported relation={relation!r}"
                        )
                    if source == target_id:
                        relations_valid = False
                        relation_errors.append(f"relation[{index}] is a self-link")
            evidence_resolves = any(
                artifact.path in patch.evidence_refs or artifact.remote_uri in patch.evidence_refs
                for artifact in document.artifacts
            )
            checks.extend(
                [
                    self._check(
                        f"{patch.patch_id}:figure_structure_elements",
                        elements_valid,
                        "blocking",
                        (
                            "Figure structure elements are localized, visible, unique, and bound to the target figure."
                            if elements_valid
                            else "Figure structure element errors: " + "; ".join(element_errors[:12])
                        ),
                    ),
                    self._check(
                        f"{patch.patch_id}:figure_structure_relations",
                        relations_valid,
                        "blocking",
                        (
                            "Figure structure relations reference declared elements and supported relation types."
                            if relations_valid
                            else "Figure structure relation errors: " + "; ".join(relation_errors[:12])
                        ),
                    ),
                    self._check(
                        f"{patch.patch_id}:figure_structure_evidence",
                        evidence_resolves,
                        "blocking",
                        (
                            "Figure structure evidence resolves to a tracked Document IR artifact."
                            if evidence_resolves
                            else "Figure structure evidence_refs do not resolve to a tracked Document IR artifact."
                        ),
                    ),
                ]
            )
        if patch.operation == "upsert_chart_spec":
            raw_chart = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
            raw_visual_refs = raw_chart.get("visual_evidence_refs")
            if not isinstance(raw_visual_refs, list):
                raw_visual_refs = []
            evidence_refs = {
                *patch.evidence_refs,
                *(str(ref) for ref in raw_visual_refs if ref),
            }
            schema_errors: list[str] = []
            try:
                chart = ChartSpec.model_validate(patch.proposed_value)
                point_count = sum(len(series.points) for series in chart.series)
                chart_valid = bool(chart.series) and point_count > 0
                evidence_refs.update(chart.visual_evidence_refs)
                evidence_refs.update(
                    ref
                    for series in chart.series
                    for point in series.points
                    for ref in point.evidence_refs
                )
            except (TypeError, ValueError) as exc:
                chart = None
                chart_valid = False
                point_count = 0
                schema_errors = [str(exc)]
            evidence_resolves = any(
                artifact.path in evidence_refs or artifact.remote_uri in evidence_refs
                for artifact in document.artifacts
            )
            aliases = ChartSpecNormalizer.recognized_aliases(raw_chart)
            checks.extend(
                [
                    self._check(
                        f"{patch.patch_id}:chart_spec_schema",
                        chart_valid,
                        "blocking",
                        f"ChartSpec contains {point_count} visible data points."
                        if chart_valid
                        else "ChartSpec requires at least one series and one visible data point.",
                        details={
                            "schema_errors": schema_errors,
                            "recognized_aliases": aliases,
                            "adapter_contract_mismatch": bool(aliases),
                            "recommended_operation": (
                                "normalize_chart_spec_aliases"
                                if aliases
                                else "return_canonical_chart_spec"
                            ),
                        },
                    ),
                    self._check(
                        f"{patch.patch_id}:chart_spec_evidence",
                        evidence_resolves,
                        "blocking",
                        (
                            "ChartSpec resolves to visual evidence."
                            if evidence_resolves
                            else "ChartSpec visual evidence does not resolve to a tracked artifact."
                        ),
                        details={
                            "visual_evidence_refs": sorted(evidence_refs),
                            "recommended_operation": "upsert_chart_spec",
                        },
                    ),
                ]
            )

        if patch.operation in {"confirm_spread", "reject_spread"}:
            payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
            reading_direction = payload.get("reading_direction", "left_to_right")
            checks.extend(
                [
                    self._check(
                        f"{patch.patch_id}:spread_target",
                        isinstance(target, SpreadIR),
                        "blocking",
                        "Spread classification targets a formal two-page SpreadIR.",
                    ),
                    self._check(
                        f"{patch.patch_id}:spread_reading_direction",
                        reading_direction in {"left_to_right", "right_to_left"},
                        "blocking",
                        "Spread reading direction is explicit and supported.",
                    ),
                ]
            )
            if patch.operation == "confirm_spread" and isinstance(target, SpreadIR):
                conflict = next(
                    (
                        spread.spread_id
                        for spread in document.spreads
                        if spread.spread_id != target.spread_id
                        and spread.status == "confirmed"
                        and set(spread.page_indices) & set(target.page_indices)
                    ),
                    None,
                )
                checks.append(
                    self._check(
                        f"{patch.patch_id}:spread_membership_unique",
                        conflict is None,
                        "blocking",
                        "No physical page belongs to another confirmed spread."
                        if conflict is None
                        else f"Spread conflicts with already confirmed {conflict}.",
                    )
                )

        if patch.operation == "link_horizontal_continuation":
            payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
            raw_links = payload.get("links")
            links_valid = isinstance(target, SpreadIR) and isinstance(raw_links, list) and bool(raw_links)
            entity_pages = self._entity_page_indices(document)
            if links_valid:
                for raw in raw_links:
                    if not isinstance(raw, dict):
                        links_valid = False
                        continue
                    source_id = str(raw.get("source_id") or "")
                    target_id = str(raw.get("target_id") or "")
                    source_pages = entity_pages.get(source_id, set())
                    target_pages = entity_pages.get(target_id, set())
                    source_entity = self._target(document, source_id)
                    target_entity = self._target(document, target_id)
                    links_valid = links_valid and bool(source_pages) and bool(target_pages)
                    links_valid = links_valid and source_pages.issubset(set(target.page_indices))
                    links_valid = links_valid and target_pages.issubset(set(target.page_indices))
                    links_valid = links_valid and source_pages != target_pages
                    links_valid = links_valid and self._target_type(source_entity) in {"block", "table", "figure"}
                    links_valid = links_valid and self._target_type(source_entity) == self._target_type(target_entity)
                    if target.reading_direction == "left_to_right":
                        links_valid = links_valid and source_pages == {target.page_indices[0]}
                        links_valid = links_valid and target_pages == {target.page_indices[1]}
            checks.append(
                self._check(
                    f"{patch.patch_id}:horizontal_links_valid",
                    links_valid,
                    "blocking",
                    (
                        "Horizontal continuation links join existing same-type entities on opposite member pages."
                        if links_valid
                        else "Horizontal continuation must target the SpreadIR and join existing same-type entities on opposite member pages."
                    ),
                )
            )

        operation_targets = {
            "confirm_spread": {"spread"},
            "reject_spread": {"spread"},
            "link_horizontal_continuation": {"spread"},
            "replace_block_text": {"block"},
            "replace_cell_text": {"cell"},
            "correct_ocr_text": {"block", "cell"},
            "set_table_grid": {"table"},
            "insert_table_row": {"table"},
            "replace_table": {"table"},
            "set_printed_page_label": {"page"},
            "set_visual_type": {"figure"},
            "set_caption": {"table", "figure"},
            "add_quality_flags": {"page", "block", "cell", "table", "figure", "section"},
            "link_continuation": {"table"},
            "merge_blocks": {"block"},
            "split_block": {"block"},
            "retire_table_candidate": {"table"},
            "add_visual_text_block": {"page"},
            "set_figure_legend_text": {"figure"},
            "upsert_figure_structure": {"figure"},
            "bind_block_to_figure": {"block"},
        }
        allowed_types = operation_targets.get(patch.operation)
        if allowed_types:
            operation_matches = patch.target_type in allowed_types and actual_target_type in allowed_types
            checks.append(
                self._check(
                    f"{patch.patch_id}:operation_target_type",
                    operation_matches,
                    "blocking",
                    f"Operation {patch.operation} is valid for the declared and actual target type."
                    if operation_matches
                    else (
                        f"Operation {patch.operation} requires {sorted(allowed_types)}, but declared type is "
                        f"{patch.target_type} and actual type is {actual_target_type or 'unknown'}."
                    ),
                )
            )

        evidence_ok = bool(patch.evidence_refs)
        checks.append(self._check(f"{patch.patch_id}:evidence_refs", evidence_ok, "warning", "Patch carries source evidence references." if evidence_ok else "Patch has no explicit evidence reference; task inputs will be used by the verifier."))
        return checks

    @staticmethod
    def _target(document: DocumentIR, target_id: str):
        primary_collections = (
            (document.pages, "page_id"),
            (document.blocks, "block_id"),
            (document.tables, "table_id"),
            (document.logical_tables, "logical_table_id"),
            (document.figures, "figure_id"),
            (document.spreads, "spread_id"),
            (document.sections, "section_id"),
        )
        for collection, primary_id_field in primary_collections:
            for item in collection:
                if getattr(item, primary_id_field, None) == target_id:
                    return item
        for table in document.tables:
            for cell in table.cells:
                if cell.cell_id == target_id:
                    return cell
        return None

    @staticmethod
    def _target_type(target: Any) -> str | None:
        type_map = (
            (PageIR, "page"),
            (BlockIR, "block"),
            (TableIR, "table"),
            # Logical tables are derived and not directly mutable by review patches.
            (FigureIR, "figure"),
            (SpreadIR, "spread"),
            (SectionIR, "section"),
            (CellIR, "cell"),
        )
        return next((name for model_type, name in type_map if isinstance(target, model_type)), None)

    @staticmethod
    def _current_value(patch: AtomicPatch, target):
        operation_fields = {
            "replace_block_text": "text",
            "replace_cell_text": "text",
            "correct_ocr_text": "text",
            "replace_text": "text",
            "set_bbox": "bbox",
            "set_printed_page_label": "printed_page_label",
            "set_visual_type": "visual_type",
            "set_caption": "caption",
            "set_figure_legend_text": "legend_text",
            "upsert_figure_structure": "element_block_ids",
        }
        if patch.operation == "retire_table_candidate":
            return target.model_dump(mode="json")
        if patch.operation in {"confirm_spread", "reject_spread", "link_horizontal_continuation"}:
            return target.model_dump(mode="json")
        if patch.operation == "add_visual_text_block":
            return None
        if patch.operation == "bind_block_to_figure":
            return {
                "figure_id": getattr(target, "figure_id", None),
                "visual_role": getattr(target, "visual_role", None),
            }
        if patch.operation == "upsert_figure_structure":
            return {
                "element_block_ids": list(target.element_block_ids),
                "relations": [
                    edge.model_dump(mode="json")
                    for edge in []
                ],
            }
        if patch.operation in {"set_table_grid", "replace_table"} and hasattr(target, "cells"):
            return {
                "row_count": target.row_count,
                "column_count": target.column_count,
                "cells": [
                    {
                        "row_index": cell.row_index,
                        "col_index": cell.col_index,
                        "text": cell.text,
                        "row_span": cell.row_span,
                        "col_span": cell.col_span,
                        "is_header": cell.is_header,
                    }
                    for cell in target.cells
                ],
            }
        if patch.operation == "insert_table_row" and hasattr(target, "row_count"):
            return {"row_count": target.row_count, "column_count": target.column_count}
        field = patch.field_path or operation_fields.get(patch.operation)
        value = getattr(target, field, None) if field else target
        return value.model_dump(mode="json") if hasattr(value, "model_dump") else value

    @staticmethod
    def _equivalent(first, second) -> bool:
        return json.dumps(first, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(second, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _numbers(value: str) -> list[str]:
        return re.findall(r"(?<![\w.])-?\d+(?:[.,]\d+)?%?", value)

    @staticmethod
    def _source_text_retention(original: str, proposed: str) -> float:
        """Measure how much source text survives while allowing evidence-backed additions."""
        original_chars = re.findall(r"\w", visible_text(original).casefold(), flags=re.UNICODE)
        proposed_chars = re.findall(r"\w", visible_text(proposed).casefold(), flags=re.UNICODE)
        if not original_chars:
            return 1.0
        retained = sum((Counter(original_chars) & Counter(proposed_chars)).values())
        return retained / len(original_chars)

    @staticmethod
    def _normalize_visible_text(value: str) -> str:
        return comparison_key(value)

    @staticmethod
    def _suspected_ocr_noise_tokens(value: str) -> list[str]:
        tokens = []
        for token in re.findall(r"\d{7,}", value):
            pairs = [token[index : index + 2] for index in range(0, len(token) - 1, 2)]
            paired_repetition = pairs and sum(pair[0] == pair[1] for pair in pairs) / len(pairs) >= 0.75
            repeated_half = len(token) % 2 == 0 and token[: len(token) // 2] == token[len(token) // 2 :]
            if paired_repetition or repeated_half:
                tokens.append(token)
        return list(dict.fromkeys(tokens))

    @staticmethod
    def _without_noise_tokens(value: str, noise_tokens: list[str]) -> str:
        result = value
        for token in noise_tokens:
            result = result.replace(token, " ")
        return result

    @staticmethod
    def _material_numbers(values: list[str], noise_tokens: list[str]) -> list[str]:
        noise = set(noise_tokens)
        return [
            value
            for value in values
            if value.lstrip("-").replace(",", "").replace(".", "").replace("%", "") not in noise
        ]

    def _table_geometry_preservation_checks(
        self,
        document: DocumentIR,
        patch: AtomicPatch,
        target: TableIR,
    ) -> list[GuardCheck]:
        before_count = sum(1 for cell in target.cells if cell.bbox)
        if before_count == 0:
            return []
        staged = document.model_copy(deep=True)
        staged_patch = patch.model_copy(deep=True)
        try:
            DocumentPatchApplier().apply_one(staged, staged_patch)
            candidate = self._target(staged, patch.target_id)
            after_count = sum(1 for cell in candidate.cells if cell.bbox) if isinstance(candidate, TableIR) else 0
        except Exception:
            return []
        return [
            self._check(
                f"{patch.patch_id}:cell_geometry_not_erased",
                after_count > 0,
                "blocking",
                f"Candidate preserves geometry for {after_count}/{len(candidate.cells)} cells; source had {before_count}/{len(target.cells)}."
                if after_count
                else f"Candidate would erase all {before_count} localized source cells.",
            ),
            self._check(
                f"{patch.patch_id}:cell_geometry_coverage",
                after_count >= before_count,
                "warning",
                f"Localized cell count changes from {before_count} to {after_count}.",
            ),
        ]

    @staticmethod
    def _entity_page_indices(document: DocumentIR) -> dict[str, set[int]]:
        values: dict[str, set[int]] = {
            page.page_id: {page.page_index} for page in document.pages
        }
        values.update({block.block_id: {block.page_index} for block in document.blocks})
        values.update({table.table_id: set(table.page_indices or [table.page_index]) for table in document.tables})
        values.update({figure.figure_id: {figure.page_index} for figure in document.figures})
        values.update(
            {
                cell.cell_id: {cell.page_index}
                for table in document.tables
                for cell in table.cells
            }
        )
        return values

    @classmethod
    def _spread_has_linkable_opposite_entities(
        cls,
        document: DocumentIR,
        spread: SpreadIR,
    ) -> bool:
        return SpreadPairAnalyzer().analyze(document, spread).unique_link_pair is not None

    @staticmethod
    def _check(
        code: str,
        passed: bool,
        severity: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> GuardCheck:
        return GuardCheck(
            code=code,
            passed=passed,
            severity=severity,
            message=message,
            details=details or {},
        )


class DocumentPatchApplier:
    def validate(self, document: DocumentIR, patches: list[AtomicPatch]) -> DocumentIR:
        staged_document = document.model_copy(deep=True)
        staged_patches = [patch.model_copy(deep=True) for patch in patches]
        for patch in staged_patches:
            self.apply_one(staged_document, patch)
        LogicalTableBuilder().build(staged_document)
        return staged_document

    def apply(self, document: DocumentIR, patches: list[AtomicPatch]) -> DocumentIR:
        self.validate(document, patches)
        for patch in patches:
            self.apply_one(document, patch)
            patch.status = "accepted"
        LogicalTableBuilder().build(document)
        return document

    def apply_one(self, document: DocumentIR, patch: AtomicPatch) -> None:
        target = PatchGuard._target(document, patch.target_id)
        if target is None:
            raise KeyError(f"Patch target not found: {patch.target_id}")
        if patch.operation in {"confirm"}:
            self._add_flag(target, "agent_review_confirmed")
        elif patch.operation in {"replace_block_text", "replace_cell_text", "correct_ocr_text", "replace_text"}:
            target.text = str(patch.proposed_value)
            self._add_flag(target, "agent_corrected")
            if patch.operation == "correct_ocr_text":
                self._add_flag(target, "agent_verified_ocr_correction")
        elif patch.operation == "set_bbox":
            target.bbox = BoundingBox.model_validate(patch.proposed_value)
            self._add_flag(target, "agent_corrected")
        elif patch.operation == "set_printed_page_label":
            target.printed_page_label = str(patch.proposed_value)
            self._add_flag(target, "agent_corrected")
        elif patch.operation == "set_visual_type":
            target.visual_type = patch.proposed_value
            self._add_flag(target, "agent_corrected")
        elif patch.operation == "set_caption":
            target.caption = str(patch.proposed_value)
            self._add_flag(target, "agent_corrected")
        elif patch.operation == "set_figure_legend_text":
            target.legend_text = [str(value).strip() for value in patch.proposed_value]
            self._add_flag(target, "agent_corrected")
        elif patch.operation == "upsert_figure_structure":
            self._upsert_figure_structure(document, target, patch)
        elif patch.operation == "upsert_chart_spec":
            target.chart_spec = ChartSpec.model_validate(patch.proposed_value)
            target.visual_type = "chart"
            target.visual_status = "reviewed"
            self._add_flag(target, "agent_chart_spec_verified")
        elif patch.operation == "bind_block_to_figure":
            self._bind_block_to_figure(document, target, patch)
        elif patch.operation == "confirm_spread":
            self._set_spread_status(document, target, patch, "confirmed")
        elif patch.operation == "reject_spread":
            self._set_spread_status(document, target, patch, "rejected")
        elif patch.operation == "link_horizontal_continuation":
            self._link_horizontal_continuation(document, target, patch.proposed_value)
        elif patch.operation == "retire_table_candidate":
            self._retire_table_candidate(document, target, patch)
        elif patch.operation == "add_visual_text_block":
            self._add_visual_text_block(document, target, patch)
        elif patch.operation == "add_quality_flags":
            for value in patch.proposed_value or []:
                self._add_flag(target, str(value))
        elif patch.operation in {"set_table_grid", "replace_table"}:
            self._set_table_grid(target, patch.proposed_value)
        elif patch.operation == "insert_table_row":
            self._insert_table_row(target, patch.proposed_value)
        elif patch.operation == "link_continuation":
            self._link_continuation(document, target, patch.proposed_value)
        elif patch.operation == "merge_blocks":
            self._merge_blocks(document, target, patch.proposed_value)
        elif patch.operation == "split_block":
            self._split_block(document, target, patch.proposed_value)
        else:
            raise ValueError(f"Unsupported patch operation: {patch.operation}")
        if patch.operation not in {"confirm", "confirm_spread", "reject_spread", "link_horizontal_continuation"}:
            page_index = getattr(target, "page_index", None)
            if isinstance(page_index, int):
                self._refresh_page_text(document, page_index)

    @staticmethod
    def snapshot(document: DocumentIR, target_ids: list[str]) -> dict[str, Any]:
        result = {}
        for target_id in target_ids:
            target = PatchGuard._target(document, target_id)
            result[target_id] = deepcopy(target.model_dump(mode="json")) if target is not None else None
        return result

    @staticmethod
    def _set_table_grid(table, payload) -> None:
        if not hasattr(table, "cells"):
            raise TypeError("set_table_grid target is not a table")
        row_count = int(payload["row_count"])
        column_count = int(payload["column_count"])
        original_cells = list(table.cells)
        original_row_count = table.row_count
        original_column_count = table.column_count
        cells = []
        for index, raw in enumerate(payload["cells"]):
            row_index = int(raw["row_index"])
            col_index = int(raw["col_index"])
            bbox, geometry_flag, inherited = DocumentPatchApplier._cell_geometry(
                table,
                raw,
                original_cells=original_cells,
                original_row_count=original_row_count,
                original_column_count=original_column_count,
                row_count=row_count,
                column_count=column_count,
            )
            cells.append(
                CellIR(
                    cell_id=DocumentPatchApplier._canonical_cell_id(table, row_index, col_index),
                    table_id=table.table_id,
                    page_index=table.page_index,
                    row_index=row_index,
                    col_index=col_index,
                    text=str(raw.get("text") or raw.get("content") or ""),
                    row_span=max(1, int(raw.get("row_span") or 1)),
                    col_span=max(1, int(raw.get("col_span") or raw.get("colspan") or 1)),
                    is_header=bool(raw.get("is_header", row_index == 0)),
                    column_header_path=list(
                        raw.get("column_header_path")
                        or (inherited.column_header_path if inherited else [])
                    ),
                    row_header_path=list(
                        raw.get("row_header_path")
                        or (inherited.row_header_path if inherited else [])
                    ),
                    unit_hint=raw.get("unit_hint"),
                    bbox=bbox,
                    quality_flags=["agent_corrected", geometry_flag],
                    source_cell_ids=list(
                        raw.get("source_cell_ids")
                        or ([inherited.cell_id] if inherited else [])
                    ),
                    source_trace=(
                        inherited.source_trace.model_copy(deep=True)
                        if inherited
                        else table.source_trace.model_copy(deep=True)
                    ),
                )
            )
        table.row_count = row_count
        table.column_count = column_count
        table.cells = cells
        table.graph_edges = []
        table.header_row_indices = sorted({cell.row_index for cell in cells if cell.is_header})
        table.markdown = DocumentPatchApplier._table_html(table)
        for flag in (
            "semantic_grid_without_cell_geometry",
            "cell_geometry_partial_after_agent_patch",
            "cell_geometry_preserved_after_agent_patch",
        ):
            if flag in table.quality_flags:
                table.quality_flags.remove(flag)
        localized = sum(1 for cell in cells if cell.bbox)
        if localized == len(cells):
            DocumentPatchApplier._add_flag(table, "cell_geometry_preserved_after_agent_patch")
        elif localized:
            DocumentPatchApplier._add_flag(table, "cell_geometry_partial_after_agent_patch")
        else:
            DocumentPatchApplier._add_flag(table, "semantic_grid_without_cell_geometry")
        DocumentPatchApplier._add_flag(table, "agent_corrected")

    @staticmethod
    def _cell_geometry(
        table,
        raw,
        *,
        original_cells: list[CellIR],
        original_row_count: int,
        original_column_count: int,
        row_count: int,
        column_count: int,
    ) -> tuple[BoundingBox | None, str, CellIR | None]:
        if raw.get("bbox"):
            return BoundingBox.model_validate(raw["bbox"]), "cell_geometry_from_agent_evidence", None
        row_index = int(raw["row_index"])
        col_index = int(raw["col_index"])
        row_span = max(1, int(raw.get("row_span") or 1))
        col_span = max(1, int(raw.get("col_span") or raw.get("colspan") or 1))
        proposed_text = DocumentPatchApplier._normalize_cell_text(
            str(raw.get("text") or raw.get("content") or "")
        )
        same_position = next(
            (
                cell
                for cell in original_cells
                if cell.row_index == row_index and cell.col_index == col_index
            ),
            None,
        )
        if (
            same_position
            and same_position.bbox
            and (
                (original_row_count == row_count and original_column_count == column_count)
                or DocumentPatchApplier._normalize_cell_text(same_position.text) == proposed_text
            )
        ):
            return same_position.bbox.model_copy(deep=True), "cell_geometry_inherited_by_position", same_position

        text_matches = [
            cell
            for cell in original_cells
            if cell.bbox
            and proposed_text
            and DocumentPatchApplier._normalize_cell_text(cell.text) == proposed_text
        ]
        if len(text_matches) == 1:
            return text_matches[0].bbox.model_copy(deep=True), "cell_geometry_inherited_by_text", text_matches[0]

        if original_row_count == row_count and original_column_count == column_count:
            covered = [
                cell
                for cell in original_cells
                if cell.bbox
                and row_index <= cell.row_index < row_index + row_span
                and col_index <= cell.col_index < col_index + col_span
            ]
            combined = union_bboxes([cell.bbox for cell in covered if cell.bbox])
            if combined:
                return combined, "cell_geometry_inherited_by_span", covered[0] if len(covered) == 1 else None

        for observation in table.observations:
            if observation.row_count != row_count or observation.column_count != column_count:
                continue
            observed = next(
                (
                    cell
                    for cell in observation.cells
                    if cell.row_index == row_index and cell.col_index == col_index and cell.bbox
                ),
                None,
            )
            if observed and observed.bbox:
                return observed.bbox.model_copy(deep=True), "cell_geometry_from_table_observation", None
        return None, "cell_geometry_unresolved_after_agent_patch", None

    @staticmethod
    def _normalize_cell_text(value: str) -> str:
        return comparison_key(value)

    @staticmethod
    def _insert_table_row(table, payload) -> None:
        if not hasattr(table, "cells"):
            raise TypeError("insert_table_row target is not a table")
        insert_at = int(payload["insert_before_row_index"])
        for cell in table.cells:
            if cell.row_index >= insert_at:
                cell.row_index += 1
        new_cells = []
        for index, raw in enumerate(payload["cells"]):
            column = int(raw["col_index"])
            new_cells.append(
                CellIR(
                    cell_id=DocumentPatchApplier._canonical_cell_id(table, insert_at, column),
                    table_id=table.table_id,
                    page_index=table.page_index,
                    row_index=insert_at,
                    col_index=column,
                    text=str(raw.get("text") or raw.get("content") or ""),
                    row_span=1,
                    col_span=max(1, int(raw.get("col_span") or raw.get("colspan") or 1)),
                    is_header=bool(raw.get("is_header", True)),
                    unit_hint=raw.get("unit_hint"),
                    bbox=BoundingBox.model_validate(raw["bbox"]) if raw.get("bbox") else None,
                    quality_flags=["agent_inserted_table_row"],
                    source_trace=table.source_trace,
                )
            )
        table.cells.extend(new_cells)
        table.cells.sort(key=lambda cell: (cell.row_index, cell.col_index))
        for cell in table.cells:
            cell.cell_id = DocumentPatchApplier._canonical_cell_id(table, cell.row_index, cell.col_index)
        table.graph_edges = []
        table.row_count += 1
        table.header_row_indices = sorted({cell.row_index for cell in table.cells if cell.is_header})
        table.markdown = DocumentPatchApplier._table_html(table)
        DocumentPatchApplier._add_flag(table, "agent_corrected")

    @staticmethod
    def _table_html(table) -> str:
        rows = []
        for row_index in range(table.row_count):
            cells = []
            for cell in sorted((item for item in table.cells if item.row_index == row_index), key=lambda item: item.col_index):
                tag = "th" if cell.is_header else "td"
                spans = ""
                if cell.row_span > 1:
                    spans += f' rowspan="{cell.row_span}"'
                if cell.col_span > 1:
                    spans += f' colspan="{cell.col_span}"'
                cells.append(f"<{tag}{spans}>{html.escape(cell.text)}</{tag}>")
            rows.append(f"<tr>{''.join(cells)}</tr>")
        return f"<table>{''.join(rows)}</table>"

    @staticmethod
    def _link_continuation(document, table, payload) -> None:
        target_id = str(payload.get("continues_to_table_id") or payload.get("target_table_id") or "")
        following = next((item for item in document.tables if item.table_id == target_id), None)
        if not target_id or following is None:
            raise ValueError("Continuation target table is invalid")
        group_id = str(payload.get("continuation_group_id") or table.continuation_group_id or f"table-group-{table.table_id}")
        table.continuation_group_id = group_id
        following.continuation_group_id = group_id
        table.continues_to_table_id = following.table_id
        following.continues_from_table_id = table.table_id

    @staticmethod
    def _set_spread_status(
        document: DocumentIR,
        spread: SpreadIR,
        patch: AtomicPatch,
        status: str,
    ) -> None:
        if not isinstance(spread, SpreadIR):
            raise TypeError("Spread classification target is not a SpreadIR")
        payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
        spread.status = status
        spread.reading_direction = payload.get("reading_direction", spread.reading_direction)
        spread.confidence = patch.confidence if patch.confidence is not None else spread.confidence
        spread.quality_flags = [
            flag
            for flag in spread.quality_flags
            if flag not in {"horizontal_spread_candidate", "horizontal_spread_confirmed", "horizontal_spread_rejected"}
        ]
        spread.quality_flags.append(
            "horizontal_spread_confirmed" if status == "confirmed" else "horizontal_spread_rejected"
        )
        pages = {page.page_id: page for page in document.pages}
        for page_id in spread.page_ids:
            page = pages.get(page_id)
            if page is None:
                continue
            page.quality_flags = [
                flag
                for flag in page.quality_flags
                if flag not in {"horizontal_spread_candidate", "horizontal_spread_confirmed", "horizontal_spread_rejected"}
            ]
            page.quality_flags.append(
                "horizontal_spread_confirmed" if status == "confirmed" else "horizontal_spread_rejected"
            )

    @staticmethod
    def _link_horizontal_continuation(document: DocumentIR, spread: SpreadIR, payload) -> None:
        if not isinstance(spread, SpreadIR):
            raise TypeError("Horizontal continuation target is not a SpreadIR")
        raw_links = (payload or {}).get("links") or []
        table_by_id = {table.table_id: table for table in document.tables}
        for raw in raw_links:
            source_id = str(raw["source_id"])
            target_id = str(raw["target_id"])
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 1.0))))
            if not any(
                link.source_id == source_id and link.target_id == target_id
                for link in spread.linked_entities
            ):
                spread.linked_entities.append(
                    SpreadEntityLink(
                        source_id=source_id,
                        target_id=target_id,
                        confidence=confidence,
                    )
                )
            if not any(
                edge.source_id == source_id
                and edge.target_id == target_id
                and edge.relation == "horizontal_continuation"
                for edge in document.structure_edges
            ):
                document.structure_edges.append(
                    StructureEdge(
                        edge_id=f"edge-{len(document.structure_edges) + 1:06d}",
                        source_id=source_id,
                        target_id=target_id,
                        relation="horizontal_continuation",
                        confidence=confidence,
                        source="agent_spread_review",
                    )
                )
            first = table_by_id.get(source_id)
            second = table_by_id.get(target_id)
            if first and second:
                group_id = (
                    first.continuation_group_id
                    or second.continuation_group_id
                    or f"spread-table-group-{spread.spread_id}-{first.table_id}-{second.table_id}"
                )
                first.continuation_group_id = group_id
                second.continuation_group_id = group_id
                first.continuation_axis = "horizontal"
                second.continuation_axis = "horizontal"
                first.continues_to_table_id = second.table_id
                second.continues_from_table_id = first.table_id

    @staticmethod
    def _merge_blocks(document, target, payload) -> None:
        merge_ids = [str(item) for item in (payload or {}).get("merge_block_ids", [])]
        merge_blocks = [item for item in document.blocks if item.block_id in merge_ids and item.page_index == target.page_index]
        ordered = sorted([target, *merge_blocks], key=lambda item: item.order)
        target.text = str((payload or {}).get("text") or "\n".join(item.text for item in ordered if item.text))
        removed = {item.block_id for item in merge_blocks}
        document.blocks = [item for item in document.blocks if item.block_id not in removed]
        page = next((item for item in document.pages if item.page_index == target.page_index), None)
        if page:
            page.block_ids = [item for item in page.block_ids if item not in removed]
        DocumentPatchApplier._add_flag(target, "agent_corrected")

    @staticmethod
    def _split_block(document, target, payload) -> None:
        parts = (payload or {}).get("parts") or []
        if len(parts) < 2:
            raise ValueError("split_block requires at least two parts")
        target.text = str(parts[0].get("text") or "")
        additions = []
        for index, raw in enumerate(parts[1:], start=2):
            additions.append(
                BlockIR(
                    block_id=f"{target.block_id}-split-{index:02d}",
                    page_index=target.page_index,
                    order=target.order + index - 1,
                    block_type=target.block_type,
                    text=str(raw.get("text") or ""),
                    bbox=BoundingBox.model_validate(raw["bbox"]) if raw.get("bbox") else None,
                    section_id=target.section_id,
                    quality_flags=["agent_corrected"],
                    source_trace=target.source_trace,
                )
            )
        document.blocks.extend(additions)
        page = next((item for item in document.pages if item.page_index == target.page_index), None)
        if page:
            insert_at = page.block_ids.index(target.block_id) + 1 if target.block_id in page.block_ids else len(page.block_ids)
            page.block_ids[insert_at:insert_at] = [item.block_id for item in additions]
        DocumentPatchApplier._add_flag(target, "agent_corrected")

    @staticmethod
    def _retire_table_candidate(document: DocumentIR, table: TableIR, patch: AtomicPatch) -> None:
        payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
        snapshot = table.model_dump(mode="json")
        document.retired_entities.append(
            RetiredEntityIR(
                entity_id=table.table_id,
                entity_type="table",
                page_index=table.page_index,
                disposition=payload["disposition"],
                snapshot=snapshot,
                evidence_refs=list(patch.evidence_refs),
                reason=patch.rationale or "Review classified the local-only table candidate as non-canonical.",
                source_task_id=patch.source_task_id,
                retired_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        if payload["disposition"] == "non_table_visual":
            DocumentPatchApplier._materialize_reclassified_figure(document, table)
        document.tables = [item for item in document.tables if item.table_id != table.table_id]
        page = next((item for item in document.pages if item.page_index == table.page_index), None)
        if page:
            page.table_ids = [item for item in page.table_ids if item != table.table_id]
        for block in document.blocks:
            if block.table_id != table.table_id:
                continue
            block.table_id = None
            if block.block_type == "table_markdown":
                block.block_type = "unknown"
            DocumentPatchApplier._add_flag(block, "retired_table_candidate_source_preserved")
        for layout in document.layout_objects:
            if layout.table_id == table.table_id:
                layout.table_id = None
                DocumentPatchApplier._add_flag(layout, "retired_table_candidate_source_preserved")
        retired_ids = {table.table_id, *(cell.cell_id for cell in table.cells)}
        document.structure_edges = [
            edge
            for edge in document.structure_edges
            if edge.source_id not in retired_ids and edge.target_id not in retired_ids
        ]

    @staticmethod
    def _materialize_reclassified_figure(document: DocumentIR, table: TableIR) -> None:
        if table.bbox is None:
            return
        overlapping = next(
            (
                figure
                for figure in document.figures
                if figure.page_index == table.page_index
                and (
                    bbox_iou(figure.bbox, table.bbox) >= 0.35
                    or bbox_containment(table.bbox, figure.bbox) >= 0.70
                    or bbox_containment(figure.bbox, table.bbox) >= 0.70
                )
            ),
            None,
        )
        if overlapping:
            DocumentPatchApplier._add_flag(overlapping, "local_table_candidate_reclassified_as_visual")
            return
        existing_ids = {figure.figure_id for figure in document.figures}
        sequence = 1
        figure_id = page_object_id("figure", table.page_index, sequence)
        while figure_id in existing_ids:
            sequence += 1
            figure_id = page_object_id("figure", table.page_index, sequence)
        figure = FigureIR(
            figure_id=figure_id,
            page_index=table.page_index,
            order=max(
                (item.order for item in document.figures if item.page_index == table.page_index),
                default=-1,
            )
            + 1,
            visual_type="unknown",
            visual_status="candidate",
            bbox=table.bbox.model_copy(deep=True),
            quality_flags=["local_table_candidate_reclassified_as_visual"],
            source_trace=table.source_trace.model_copy(deep=True),
        )
        document.figures.append(figure)
        page = next((item for item in document.pages if item.page_index == table.page_index), None)
        if page and figure.figure_id not in page.figure_ids:
            page.figure_ids.append(figure.figure_id)

    @staticmethod
    def _add_visual_text_block(document: DocumentIR, page: PageIR, patch: AtomicPatch) -> None:
        payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
        existing_ids = {block.block_id for block in document.blocks}
        sequence = 1
        block_id = page_object_id("block", page.page_index, sequence)
        while block_id in existing_ids:
            sequence += 1
            block_id = page_object_id("block", page.page_index, sequence)
        figure_id = str(payload.get("figure_id") or "") or None
        if figure_id and not any(
            figure.figure_id == figure_id and figure.page_index == page.page_index
            for figure in document.figures
        ):
            raise ValueError(f"Visual text figure target is invalid: {figure_id}")
        artifact_ids = [
            artifact.artifact_id
            for artifact in document.artifacts
            if artifact.path in patch.evidence_refs or artifact.remote_uri in patch.evidence_refs
        ]
        block = BlockIR(
            block_id=block_id,
            page_index=page.page_index,
            order=max(
                (item.order for item in document.blocks if item.page_index == page.page_index),
                default=-1,
            )
            + 1,
            block_type=payload.get("block_type", "paragraph"),
            text=str(payload["text"]).strip(),
            bbox=BoundingBox.model_validate(payload["bbox"]),
            figure_id=figure_id,
            quality_flags=["agent_corrected", "visual_text_recovered"],
            source_trace=SourceTrace(
                parser="agent-review",
                parser_version="review-policy-v0.5",
                artifact_path=patch.evidence_refs[0] if patch.evidence_refs else None,
                artifact_ids=artifact_ids,
                confidence=patch.confidence,
                notes=[patch.rationale] if patch.rationale else [],
            ),
        )
        document.blocks.append(block)
        page.block_ids.append(block.block_id)
        normalized_page = PatchGuard._normalize_visible_text(page.text)
        normalized_block = PatchGuard._normalize_visible_text(block.text)
        if normalized_block and normalized_block not in normalized_page:
            page.text = f"{page.text}\n{block.text}".strip()
            page.text_length = len(page.text)

    @staticmethod
    def _upsert_figure_structure(
        document: DocumentIR,
        figure: FigureIR,
        patch: AtomicPatch,
    ) -> None:
        if not isinstance(figure, FigureIR):
            raise TypeError("Figure structure target is not a FigureIR")
        payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
        page = next(
            (item for item in document.pages if item.page_index == figure.page_index),
            None,
        )
        if page is None:
            raise ValueError("Figure structure target page is missing")
        existing_blocks = {block.block_id: block for block in document.blocks}
        artifact_ids = [
            artifact.artifact_id
            for artifact in document.artifacts
            if artifact.path in patch.evidence_refs or artifact.remote_uri in patch.evidence_refs
        ]
        aliases: dict[str, str] = {}
        for raw in payload.get("elements") or []:
            alias = str(raw["element_id"]).strip()
            requested_block_id = str(raw.get("block_id") or "")
            if requested_block_id:
                block = existing_blocks[requested_block_id]
                block.visual_role = raw["visual_role"]
                DocumentPatchApplier._add_flag(block, "agent_visual_structure_bound")
            else:
                block_id = DocumentPatchApplier._next_block_id(document, page.page_index)
                block = BlockIR(
                    block_id=block_id,
                    page_index=page.page_index,
                    order=max(
                        (
                            item.order
                            for item in document.blocks
                            if item.page_index == page.page_index
                        ),
                        default=-1,
                    )
                    + 1,
                    block_type=str(raw.get("block_type") or "unknown"),
                    text=visible_text(str(raw["text"])),
                    bbox=BoundingBox.model_validate(raw["bbox"]),
                    figure_id=figure.figure_id,
                    visual_role=raw["visual_role"],
                    quality_flags=["agent_corrected", "visual_element_recovered"],
                    source_trace=SourceTrace(
                        parser="agent-review",
                        parser_version="review-policy-v0.8",
                        artifact_path=patch.evidence_refs[0] if patch.evidence_refs else None,
                        artifact_ids=artifact_ids,
                        confidence=patch.confidence,
                        notes=[patch.rationale] if patch.rationale else [],
                    ),
                )
                document.blocks.append(block)
                existing_blocks[block_id] = block
                page.block_ids.append(block_id)
            aliases[alias] = block.block_id
            if block.block_id not in figure.element_block_ids:
                figure.element_block_ids.append(block.block_id)
            DocumentPatchApplier._add_structure_edge(
                document,
                figure.figure_id,
                block.block_id,
                "visual_contains",
                patch.confidence or 0.8,
                "agent_figure_structure",
            )
            normalized_page = comparison_key(page.text)
            normalized_block = comparison_key(block.text)
            if normalized_block and normalized_block not in normalized_page:
                page.text = f"{page.text}\n{block.text}".strip()
                page.text_length = len(page.text)

        for raw in payload.get("relations") or []:
            DocumentPatchApplier._add_structure_edge(
                document,
                aliases[str(raw["source_element_id"])],
                aliases[str(raw["target_element_id"])],
                raw["relation"],
                max(0.0, min(1.0, float(raw.get("confidence", patch.confidence or 0.8)))),
                "agent_figure_structure",
            )
        figure.visual_status = "reviewed"
        figure.quality_flags = [
            flag
            for flag in figure.quality_flags
            if flag not in {"image_binding_unresolved", "material_visual_binding_unresolved"}
        ]
        DocumentPatchApplier._add_flag(figure, "agent_visual_structure_recorded")

    @staticmethod
    def _bind_block_to_figure(
        document: DocumentIR,
        block: BlockIR,
        patch: AtomicPatch,
    ) -> None:
        if not isinstance(block, BlockIR):
            raise TypeError("Figure binding target is not a BlockIR")
        payload = patch.proposed_value if isinstance(patch.proposed_value, dict) else {}
        figure = PatchGuard._target(document, str(payload.get("figure_id") or ""))
        if not isinstance(figure, FigureIR) or figure.page_index != block.page_index:
            raise ValueError("Figure binding target is missing or on another page")
        relation = str(payload.get("relation") or "element")
        block.figure_id = figure.figure_id
        if relation == "caption":
            block.block_type = "caption"
            figure.caption = block.text
            DocumentPatchApplier._add_structure_edge(
                document,
                block.block_id,
                figure.figure_id,
                "caption_of",
                patch.confidence or 0.8,
                "agent_figure_binding",
            )
        else:
            block.visual_role = payload["visual_role"]
            if block.block_id not in figure.element_block_ids:
                figure.element_block_ids.append(block.block_id)
            DocumentPatchApplier._add_structure_edge(
                document,
                figure.figure_id,
                block.block_id,
                "visual_contains",
                patch.confidence or 0.8,
                "agent_figure_binding",
            )
        figure.visual_status = "reviewed"
        figure.quality_flags = [
            flag
            for flag in figure.quality_flags
            if flag not in {"image_binding_unresolved", "material_visual_binding_unresolved"}
        ]
        DocumentPatchApplier._add_flag(figure, "agent_visual_binding_verified")
        DocumentPatchApplier._add_flag(block, "agent_visual_structure_bound")

    @staticmethod
    def _refresh_page_text(document: DocumentIR, page_index: int) -> None:
        page = next(
            (item for item in document.pages if item.page_index == page_index),
            None,
        )
        if page is None:
            return
        page.text = canonical_page_text(document, page)
        page.text_length = len(page.text)

    @staticmethod
    def _next_block_id(document: DocumentIR, page_index: int) -> str:
        existing_ids = {block.block_id for block in document.blocks}
        sequence = 1
        block_id = page_object_id("block", page_index, sequence)
        while block_id in existing_ids:
            sequence += 1
            block_id = page_object_id("block", page_index, sequence)
        return block_id

    @staticmethod
    def _add_structure_edge(
        document: DocumentIR,
        source_id: str,
        target_id: str,
        relation: str,
        confidence: float,
        source: str,
    ) -> None:
        if any(
            edge.source_id == source_id
            and edge.target_id == target_id
            and edge.relation == relation
            for edge in document.structure_edges
        ):
            return
        document.structure_edges.append(
            StructureEdge(
                edge_id=f"edge-{len(document.structure_edges) + 1:06d}",
                source_id=source_id,
                target_id=target_id,
                relation=relation,
                confidence=confidence,
                source=source,
            )
        )

    @staticmethod
    def _add_flag(target, value: str) -> None:
        if hasattr(target, "quality_flags") and value not in target.quality_flags:
            target.quality_flags.append(value)

    @staticmethod
    def _canonical_cell_id(table, row_index: int, col_index: int) -> str:
        match = re.fullmatch(r"table-p\d{4}-(\d{4})", str(table.table_id))
        table_sequence = int(match.group(1)) if match else max(1, int(getattr(table, "order", 0)) + 1)
        return canonical_cell_id(table.page_index, table_sequence, row_index, col_index)
