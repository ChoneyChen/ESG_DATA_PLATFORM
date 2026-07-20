from __future__ import annotations

import html
import json
import re
from copy import deepcopy
from typing import Any

from esg_v2.document.contracts import (
    AtomicPatch,
    BlockIR,
    BoundingBox,
    CellIR,
    DocumentIR,
    FigureIR,
    GuardCheck,
    GuardResult,
    PageIR,
    SectionIR,
    TableIR,
)
from esg_v2.document.identifiers import sequential_id


class PatchGuard:
    def evaluate(
        self,
        document: DocumentIR,
        task_id: str,
        patches: list[AtomicPatch],
        *,
        allowed_target_ids: set[str] | None = None,
        required_target_ids: set[str] | None = None,
    ) -> GuardResult:
        checks: list[GuardCheck] = []
        if not patches:
            checks.append(self._check("patches_present", False, "blocking", "Correction verdict supplied no atomic patches."))
        for patch in patches:
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
            covered = set()
            for patch in patches:
                covered.add(patch.target_id)
                target = self._target(document, patch.target_id)
                if isinstance(target, CellIR):
                    covered.add(target.table_id)
            for target_id in sorted(required_target_ids):
                checks.append(
                    self._check(
                        f"required_target:{target_id}",
                        target_id in covered,
                        "blocking",
                        f"Blocking review target {target_id} is explicitly confirmed or corrected.",
                    )
                )
        blocking_failure = any(not check.passed and check.severity in {"error", "blocking"} for check in checks)
        if patches and not blocking_failure:
            try:
                DocumentPatchApplier().validate(document, patches)
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
            patch_ids=[patch.patch_id for patch in patches],
            passed=passed,
            requires_independent_verifier=bool(patches),
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
                retained_ratio = min(len(proposed), len(original)) / max(len(proposed), len(original))
                checks.append(self._check(f"{patch.patch_id}:non_destructive_length", retained_ratio >= 0.45, "blocking", f"Text length retention ratio is {retained_ratio:.3f}."))
                original_numbers = self._numbers(original)
                proposed_numbers = self._numbers(proposed)
                checks.append(self._check(f"{patch.patch_id}:numeric_tokens_preserved", original_numbers == proposed_numbers, "warning", f"Numeric tokens before={original_numbers}, after={proposed_numbers}."))

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
                valid = (
                    isinstance(payload, dict)
                    and isinstance(payload.get("cells"), list)
                    and int(payload.get("row_count") or 0) > 0
                    and int(payload.get("column_count") or 0) > 0
                )
            except (TypeError, ValueError):
                valid = False
            checks.append(self._check(f"{patch.patch_id}:table_grid_schema", valid, "blocking", "Table grid contains positive dimensions and cells."))
            if valid:
                rows = int(payload["row_count"])
                columns = int(payload["column_count"])
                occupied = set()
                cells_valid = True
                for raw in payload["cells"]:
                    try:
                        row = int(raw["row_index"])
                        column = int(raw["col_index"])
                        row_span = max(1, int(raw.get("row_span") or 1))
                        col_span = max(1, int(raw.get("col_span") or raw.get("colspan") or 1))
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
                    proposed_text = " ".join(str(cell.get("text") or "").strip() for cell in payload["cells"] if str(cell.get("text") or "").strip())
                    retention = min(len(original_text), len(proposed_text)) / max(len(original_text), len(proposed_text), 1)
                    checks.append(
                        self._check(
                            f"{patch.patch_id}:table_text_retention",
                            retention >= 0.55,
                            "blocking",
                            f"Whole-grid text length retention ratio is {retention:.3f}.",
                        )
                    )
                    original_numbers = self._numbers(original_text)
                    proposed_numbers = self._numbers(proposed_text)
                    checks.append(
                        self._check(
                            f"{patch.patch_id}:table_numeric_tokens_preserved",
                            original_numbers == proposed_numbers,
                            "warning",
                            f"Table numeric tokens before={original_numbers}, after={proposed_numbers}.",
                        )
                    )

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

        operation_targets = {
            "replace_block_text": {"block"},
            "replace_cell_text": {"cell"},
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
            (document.figures, "figure_id"),
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
            (FigureIR, "figure"),
            (SectionIR, "section"),
            (CellIR, "cell"),
        )
        return next((name for model_type, name in type_map if isinstance(target, model_type)), None)

    @staticmethod
    def _current_value(patch: AtomicPatch, target):
        operation_fields = {
            "replace_block_text": "text",
            "replace_cell_text": "text",
            "replace_text": "text",
            "set_bbox": "bbox",
            "set_printed_page_label": "printed_page_label",
            "set_visual_type": "visual_type",
            "set_caption": "caption",
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
    def _check(code: str, passed: bool, severity: str, message: str) -> GuardCheck:
        return GuardCheck(code=code, passed=passed, severity=severity, message=message)


class DocumentPatchApplier:
    def validate(self, document: DocumentIR, patches: list[AtomicPatch]) -> None:
        staged_document = document.model_copy(deep=True)
        staged_patches = [patch.model_copy(deep=True) for patch in patches]
        for patch in staged_patches:
            self.apply_one(staged_document, patch)

    def apply(self, document: DocumentIR, patches: list[AtomicPatch]) -> DocumentIR:
        self.validate(document, patches)
        for patch in patches:
            self.apply_one(document, patch)
            patch.status = "accepted"
        return document

    def apply_one(self, document: DocumentIR, patch: AtomicPatch) -> None:
        target = PatchGuard._target(document, patch.target_id)
        if target is None:
            raise KeyError(f"Patch target not found: {patch.target_id}")
        if patch.operation in {"confirm"}:
            self._add_flag(target, "agent_review_confirmed")
        elif patch.operation in {"replace_block_text", "replace_cell_text", "replace_text"}:
            target.text = str(patch.proposed_value)
            self._add_flag(target, "agent_corrected")
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
        source = table.source_trace
        cells = []
        for index, raw in enumerate(payload["cells"]):
            row_index = int(raw["row_index"])
            col_index = int(raw["col_index"])
            cells.append(
                CellIR(
                    cell_id=raw.get("cell_id") or f"{table.table_id}-r{row_index + 1:03d}-c{col_index + 1:03d}",
                    table_id=table.table_id,
                    page_index=table.page_index,
                    row_index=row_index,
                    col_index=col_index,
                    text=str(raw.get("text") or raw.get("content") or ""),
                    row_span=max(1, int(raw.get("row_span") or 1)),
                    col_span=max(1, int(raw.get("col_span") or raw.get("colspan") or 1)),
                    is_header=bool(raw.get("is_header", row_index == 0)),
                    unit_hint=raw.get("unit_hint"),
                    bbox=BoundingBox.model_validate(raw["bbox"]) if raw.get("bbox") else None,
                    quality_flags=["agent_corrected"],
                    source_trace=source,
                )
            )
        table.row_count = row_count
        table.column_count = column_count
        table.cells = cells
        table.header_row_indices = sorted({cell.row_index for cell in cells if cell.is_header})
        table.markdown = DocumentPatchApplier._table_html(table)
        DocumentPatchApplier._add_flag(table, "agent_corrected")

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
                    cell_id=f"{table.table_id}-agent-row-{insert_at + 1:03d}-cell-{index + 1:03d}",
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
    def _add_flag(target, value: str) -> None:
        if hasattr(target, "quality_flags") and value not in target.quality_flags:
            target.quality_flags.append(value)
