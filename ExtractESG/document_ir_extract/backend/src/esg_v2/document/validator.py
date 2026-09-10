from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any

from esg_v2.document.contracts import DocumentIR, ValidationIssue, ValidationReport
from esg_v2.document.identifiers import identifiers_match
from esg_v2.document.provenance import is_sensitive_remote_url
from esg_v2.document.structure_reconstruction import StructureReconstructor
from esg_v2.utils.hashing import sha256_file


class DocumentIrValidator:
    """Separates hard structural failure, pending automation, and genuine human disagreement."""

    RESOLVED_TASK_STATUSES = {"auto_resolved", "reviewed", "done"}
    AUTOMATION_PENDING_STATUSES = {"pending", "queued", "running", "deferred", "failed", "skipped"}

    def validate(self, document: DocumentIR, *, expected_page_count: int | None = None) -> DocumentIR:
        issues: list[ValidationIssue] = []
        page_count = len(document.pages)
        expected = expected_page_count if expected_page_count is not None else page_count
        pages_with_images = sum(1 for page in document.pages if page.page_image_path)
        layout_count = len(document.layout_objects)
        layout_with_geometry = sum(1 for item in document.layout_objects if item.bbox or item.polygon)
        block_count = len(document.blocks)
        blocks_with_geometry = sum(1 for item in document.blocks if item.bbox or item.polygon)
        tables_with_geometry = sum(1 for item in document.tables if item.bbox)
        table_cells = [cell for table in document.tables for cell in table.cells]
        cells_with_geometry = sum(1 for cell in table_cells if cell.bbox)
        tables_with_full_cell_geometry = sum(
            1 for table in document.tables if table.cells and all(cell.bbox for cell in table.cells)
        )
        tables_with_partial_cell_geometry = sum(
            1 for table in document.tables if any(cell.bbox for cell in table.cells) and not all(cell.bbox for cell in table.cells)
        )
        tables_without_cell_geometry = sum(
            1 for table in document.tables if table.cells and not any(cell.bbox for cell in table.cells)
        )
        figures_with_geometry = sum(1 for item in document.figures if item.bbox)
        confirmed_spreads = [item for item in document.spreads if item.status == "confirmed"]
        unresolved_logical_tables = [
            item
            for item in document.logical_tables
            if item.status == "review_required"
        ]
        entity_ids = [
            *(page.page_id for page in document.pages),
            *(block.block_id for block in document.blocks),
            *(table.table_id for table in document.tables),
            *(table.logical_table_id for table in document.logical_tables),
            *(figure.figure_id for figure in document.figures),
            *(spread.spread_id for spread in document.spreads),
            *(section.section_id for section in document.sections),
            *(cell.cell_id for table in document.tables for cell in table.cells),
        ]
        duplicate_entity_ids = sorted(entity_id for entity_id, count in Counter(entity_ids).items() if count > 1)
        integrity_issues, integrity_metrics, integrity_checks = self._audit_integrity(document)
        section_metrics = self._section_semantic_metrics(document)

        blocking_tasks = [task for task in document.review_tasks if task.blocking]
        optional_tasks = [task for task in document.review_tasks if not task.blocking]
        automation_pending = [
            task
            for task in blocking_tasks
            if task.status in self.AUTOMATION_PENDING_STATUSES and task.retryable
        ]
        system_blocked_tasks = [
            task
            for task in blocking_tasks
            if task.status in self.AUTOMATION_PENDING_STATUSES and not task.retryable
        ]
        human_tasks = [task for task in blocking_tasks if task.status == "human_required"]
        optional_unresolved = [task for task in optional_tasks if task.status not in self.RESOLVED_TASK_STATUSES]
        task_by_target = {
            target_id: task
            for task in document.review_tasks
            for target_id in (
                task.review_plan.mutable_target_ids
                if task.review_plan
                else [task.target_id, *(scope.target_id for scope in task.scope)]
            )
        }
        open_conflicts = [
            item
            for item in document.conflict_groups
            if item.blocking
            and item.status == "open"
            and item.routing_disposition in {"blocking_task", "unrouted"}
        ]
        pending_conflicts = [
            item
            for item in open_conflicts
            if task_by_target.get(item.target_id)
            and task_by_target[item.target_id].status in self.AUTOMATION_PENDING_STATUSES
            and task_by_target[item.target_id].retryable
        ]
        system_blocked_conflicts = [
            item
            for item in open_conflicts
            if (
                item.routing_disposition == "unrouted"
                or (
                    task_by_target.get(item.target_id)
                    and task_by_target[item.target_id].status in self.AUTOMATION_PENDING_STATUSES
                    and not task_by_target[item.target_id].retryable
                )
                or (
                    task_by_target.get(item.target_id)
                    and task_by_target[item.target_id].status in self.RESOLVED_TASK_STATUSES
                )
            )
        ]
        human_conflicts = [
            item
            for item in document.conflict_groups
            if item.blocking
            and (
                item.status == "human_required"
                or item.routing_disposition == "human_required"
            )
        ]

        if page_count == 0:
            issues.append(self._issue("no_pages", "blocking", "Document IR contains no pages."))
        if duplicate_entity_ids:
            preview = ", ".join(duplicate_entity_ids[:10])
            issues.append(
                self._issue(
                    "duplicate_entity_ids",
                    "blocking",
                    f"Targetable Document IR entity IDs must be globally unique; duplicates: {preview}.",
                )
            )
        issues.extend(integrity_issues)
        if expected_page_count is not None and page_count != expected_page_count:
            issues.append(self._issue("page_count_mismatch", "blocking", f"Expected {expected_page_count} pages but built {page_count}."))
        if document.metadata.source_pdf_path and pages_with_images < page_count:
            issues.append(self._issue("page_render_coverage_incomplete", "blocking", f"Rendered {pages_with_images}/{page_count} page images."))
        if not document.metadata.source_pdf_path:
            issues.append(self._issue("source_pdf_unavailable", "error", "The original PDF is unavailable for coordinate and visual verification."))
        if layout_count == 0:
            issues.append(self._issue("raw_layout_missing", "error", "No PaddleOCR raw layout objects were available; structure relies on Markdown fallback."))
        elif layout_with_geometry < layout_count:
            issues.append(self._issue("layout_geometry_incomplete", "warning", f"Geometry is present for {layout_with_geometry}/{layout_count} layout objects."))
        if block_count and blocks_with_geometry < block_count:
            issues.append(self._issue("block_geometry_incomplete", "warning", f"Geometry is present for {blocks_with_geometry}/{block_count} blocks."))
        if document.tables and tables_with_geometry < len(document.tables):
            issues.append(self._issue("table_geometry_incomplete", "error", f"Geometry is present for {tables_with_geometry}/{len(document.tables)} tables."))
        if table_cells and cells_with_geometry < len(table_cells):
            issues.append(
                self._issue(
                    "table_cell_geometry_incomplete",
                    "warning",
                    f"Cell geometry is present for {cells_with_geometry}/{len(table_cells)} table cells.",
                )
            )
        if document.figures and figures_with_geometry < len(document.figures):
            issues.append(self._issue("figure_geometry_incomplete", "warning", f"Geometry is present for {figures_with_geometry}/{len(document.figures)} figures."))
        if section_metrics["suspicious_section_title_count"]:
            issues.append(
                self._issue(
                    "section_semantic_titles_invalid",
                    "error",
                    f"{section_metrics['suspicious_section_title_count']} section titles are sentence-like, empty, or punctuation-only.",
                )
            )
        if section_metrics["sections_per_page"] > 1.5 and len(document.sections) > 20:
            issues.append(
                self._issue(
                    "section_oversegmentation_risk",
                    "warning",
                    f"Section density is {section_metrics['sections_per_page']:.3f} per page; inspect heading promotion rules.",
                )
            )
        if section_metrics["parentless_section_ratio"] > 0.65 and len(document.sections) > 20:
            issues.append(
                self._issue(
                    "section_hierarchy_flatness_risk",
                    "warning",
                    f"Parentless section ratio is {section_metrics['parentless_section_ratio']:.3f}.",
                )
            )
        if unresolved_logical_tables:
            issues.append(
                self._issue(
                    "logical_table_alignment_review_required",
                    "error",
                    (
                        f"{len(unresolved_logical_tables)} horizontal logical tables retain "
                        "incompatible physical row alignment."
                    ),
                )
            )
        if automation_pending or pending_conflicts:
            issues.append(
                self._issue(
                    "required_auto_review_pending",
                    "error",
                    f"{len(automation_pending)} blocking review tasks and {len(pending_conflicts)} routed conflicts still await automation.",
                )
            )
        if system_blocked_tasks or system_blocked_conflicts:
            issues.append(
                self._issue(
                    "review_chain_repair_required",
                    "error",
                    (
                        f"{len(system_blocked_tasks)} blocking review tasks and "
                        f"{len(system_blocked_conflicts)} conflicts cannot be solved by another "
                        "automatic retry; repair the local contract, evidence, or routing first."
                    ),
                )
            )
        if human_tasks or human_conflicts:
            issues.append(
                self._issue(
                    "genuine_human_review_required",
                    "error",
                    f"{len(human_tasks)} blocking review tasks and {len(human_conflicts)} conflicts remain after bounded agent review.",
                )
            )
        if optional_unresolved:
            issues.append(
                self._issue(
                    "optional_review_unresolved",
                    "warning",
                    f"{len(optional_unresolved)} optional visual reviews remain unresolved and do not block Evidence admission.",
                )
            )

        hard_failure = any(issue.severity == "blocking" for issue in issues)
        structural_errors = [
            issue
            for issue in issues
            if issue.severity == "error"
            and issue.code not in {"required_auto_review_pending", "genuine_human_review_required"}
        ]
        if hard_failure:
            readiness = "failed"
        elif structural_errors or system_blocked_tasks or system_blocked_conflicts:
            readiness = "repair_required"
        elif human_tasks or human_conflicts:
            readiness = "review_required"
        elif automation_pending or pending_conflicts:
            readiness = "auto_review_pending"
        elif issues:
            readiness = "ready_with_warnings"
        else:
            readiness = "ready"
        can_build_evidence = readiness in {"ready", "ready_with_warnings"}
        evidence_policy = self._evidence_policy(document, issues, can_build_evidence)
        report = ValidationReport(
            readiness=readiness,
            checks={
                "page_coverage_complete": page_count > 0 and page_count == expected,
                "page_render_coverage_complete": pages_with_images == page_count,
                "raw_layout_available": layout_count > 0,
                "all_layout_geometry_available": layout_count > 0 and layout_with_geometry == layout_count,
                "all_table_geometry_available": not document.tables or tables_with_geometry == len(document.tables),
                "all_table_cell_geometry_available": not table_cells or cells_with_geometry == len(table_cells),
                "logical_table_composition_ready": not unresolved_logical_tables,
                "section_semantic_quality_acceptable": section_metrics["suspicious_section_title_count"] == 0,
                "entity_ids_globally_unique": not duplicate_entity_ids,
                **integrity_checks,
                "blocking_review_tasks_resolved": (
                    not automation_pending
                    and not system_blocked_tasks
                    and not human_tasks
                ),
                "all_review_tasks_resolved": all(task.status in self.RESOLVED_TASK_STATUSES for task in document.review_tasks),
                "review_tasks_resolved": all(task.status in self.RESOLVED_TASK_STATUSES for task in document.review_tasks),
                "blocking_conflicts_resolved": (
                    not pending_conflicts
                    and not system_blocked_conflicts
                    and not human_conflicts
                ),
                "can_build_evidence": can_build_evidence,
                "can_build_limited_evidence": evidence_policy["mode"] == "limited",
            },
            metrics={
                "page_count": page_count,
                "expected_page_count": expected,
                "page_image_coverage": self._ratio(pages_with_images, page_count),
                "layout_geometry_coverage": self._ratio(layout_with_geometry, layout_count),
                "block_geometry_coverage": self._ratio(blocks_with_geometry, block_count),
                "table_geometry_coverage": self._ratio(tables_with_geometry, len(document.tables)),
                "table_cell_geometry_coverage": self._ratio(cells_with_geometry, len(table_cells)),
                "table_cell_count": len(table_cells),
                "table_cells_with_geometry_count": cells_with_geometry,
                "tables_with_full_cell_geometry_count": tables_with_full_cell_geometry,
                "tables_with_partial_cell_geometry_count": tables_with_partial_cell_geometry,
                "tables_without_cell_geometry_count": tables_without_cell_geometry,
                "figure_geometry_coverage": self._ratio(figures_with_geometry, len(document.figures)),
                "spread_candidate_count": len(document.spreads),
                "confirmed_spread_count": len(confirmed_spreads),
                "logical_table_count": len(document.logical_tables),
                "logical_table_review_required_count": len(unresolved_logical_tables),
                **section_metrics,
                "duplicate_entity_id_count": len(duplicate_entity_ids),
                **integrity_metrics,
                "blocking_review_count": len(blocking_tasks),
                "auto_review_pending_count": len(automation_pending),
                "system_repair_required_count": len(system_blocked_tasks),
                "human_review_required_count": len(human_tasks),
                "optional_unresolved_count": len(optional_unresolved),
                "pending_conflict_count": len(pending_conflicts),
                "system_blocked_conflict_count": len(system_blocked_conflicts),
                "human_conflict_count": len(human_conflicts),
                "historical_conflict_count": len(document.conflict_groups),
                "accepted_nonmaterial_conflict_count": sum(
                    1
                    for item in document.conflict_groups
                    if item.routing_disposition == "accepted_nonmaterial_difference"
                ),
                "active_blocking_conflict_count": len(open_conflicts),
            },
            issues=issues,
        )
        document.validation_report = report
        document.readiness = readiness
        document.quality_report["readiness"] = readiness
        document.quality_report["can_build_evidence"] = can_build_evidence
        document.quality_report["evidence_policy"] = evidence_policy
        document.quality_report["validation_issue_counts"] = {
            severity: sum(1 for issue in issues if issue.severity == severity)
            for severity in ("info", "warning", "error", "blocking")
        }
        return document

    @classmethod
    def _evidence_policy(cls, document, issues, ready):
        from esg_v2.document.patch_guard import PatchGuard
        local_codes = {"table_geometry_incomplete", "logical_table_alignment_review_required",
                       "required_auto_review_pending", "review_chain_repair_required", "genuine_human_review_required"}
        global_failure = any(i.severity == "blocking" or (i.severity == "error" and i.code not in local_codes) for i in issues)
        targets, excluded_pages, unlocated = set(), set(), []
        for task in document.review_tasks:
            if task.blocking and task.status not in cls.RESOLVED_TASK_STATUSES:
                targets.add(task.target_id)
                targets.update(s.target_id for s in task.scope if s.blocking)
                if task.review_plan:
                    targets.update(task.review_plan.required_decision_target_ids)
        targets.update(c.target_id for c in document.conflict_groups if c.blocking and c.status in {"open", "human_required"})
        targets.update(t.table_id for t in document.tables if not t.bbox)
        for table in document.logical_tables:
            if table.status == "review_required":
                targets.update(table.source_table_ids)
                excluded_pages.update(table.page_indices)
        for target_id in targets:
            target = PatchGuard._target(document, target_id)
            page = getattr(target, "page_index", None)
            if page is None and hasattr(target, "table_id"):
                page = getattr(PatchGuard._target(document, target.table_id), "page_index", None)
            if isinstance(page, int):
                excluded_pages.add(page)
            elif getattr(target, "page_indices", None):
                excluded_pages.update(target.page_indices)
            else:
                unlocated.append(target_id)
        available_pages = {p.page_index for p in document.pages} - excluded_pages
        mode = "full" if ready else "limited" if available_pages and not global_failure and not unlocated else "blocked"
        return {"schema_version": "evidence-availability-v1", "mode": mode,
                "excluded_target_ids": sorted(targets), "excluded_page_indices": sorted(excluded_pages),
                "available_page_count": len(available_pages), "unlocated_target_ids": sorted(unlocated),
                "reason": "Unresolved local objects and their pages are excluded; original review states and patches are unchanged."}

    @staticmethod
    def _section_semantic_metrics(document: DocumentIR) -> dict[str, float | int]:
        section_count = len(document.sections)
        page_count = len(document.pages)
        suspicious = sum(
            1 for section in document.sections if StructureReconstructor.is_suspicious_title(section.title)
        )
        single_page = sum(
            1
            for section in document.sections
            if section.end_page_index is not None and section.end_page_index == section.start_page_index
        )
        parentless = sum(1 for section in document.sections if not section.parent_section_id)
        return {
            "section_count": section_count,
            "sections_per_page": round(section_count / page_count, 4) if page_count else 0.0,
            "single_page_section_ratio": round(single_page / section_count, 4) if section_count else 0.0,
            "parentless_section_ratio": round(parentless / section_count, 4) if section_count else 0.0,
            "suspicious_section_title_count": suspicious,
        }

    def _audit_integrity(self, document: DocumentIR):
        problems: dict[str, list[str]] = {
            "invalid_page_indices": [],
            "duplicate_internal_ids": [],
            "dangling_references": [],
            "invalid_coordinates": [],
            "invalid_table_grids": [],
            "invalid_logical_tables": [],
            "invalid_section_hierarchy": [],
            "artifact_integrity": [],
            "source_trace_integrity": [],
            "sensitive_remote_urls": [],
            "identifier_contract": [],
        }

        page_by_index = {page.page_index: page for page in document.pages}
        page_indices = set(page_by_index)
        page_ids = {page.page_id for page in document.pages}
        block_by_id = {block.block_id: block for block in document.blocks}
        block_ids = set(block_by_id)
        layout_by_id = {item.layout_object_id: item for item in document.layout_objects}
        layout_ids = set(layout_by_id)
        table_by_id = {table.table_id: table for table in document.tables}
        table_ids = set(table_by_id)
        logical_table_by_id = {
            table.logical_table_id: table
            for table in document.logical_tables
        }
        logical_table_ids = set(logical_table_by_id)
        figure_by_id = {figure.figure_id: figure for figure in document.figures}
        figure_ids = set(figure_by_id)
        spread_by_id = {spread.spread_id: spread for spread in document.spreads}
        spread_ids = set(spread_by_id)
        section_by_id = {section.section_id: section for section in document.sections}
        section_ids = set(section_by_id)
        cell_by_id = {cell.cell_id: cell for table in document.tables for cell in table.cells}
        cell_ids = set(cell_by_id)
        artifact_by_id = {artifact.artifact_id: artifact for artifact in document.artifacts}
        artifact_ids = set(artifact_by_id)
        coordinate_by_id = {item.coordinate_system_id: item for item in document.coordinate_systems}
        coordinate_ids = set(coordinate_by_id)
        review_task_ids = {task.task_id for task in document.review_tasks}
        reviewer_result_ids = {
            item.reviewer_result_id
            for item in document.reviewer_results
        }
        patch_ids = {item.patch_id for item in document.atomic_patches}
        guard_result_ids = {item.guard_result_id for item in document.guard_results}
        verifier_result_ids = {
            item.verifier_result_id
            for item in document.verifier_results
        }
        transaction_by_id = {
            item.transaction_id: item
            for item in document.patch_transactions
        }
        transaction_ids = set(transaction_by_id)
        retired_entity_ids = {item.entity_id for item in document.retired_entities}
        entity_ids = (
            page_ids
            | block_ids
            | table_ids
            | logical_table_ids
            | figure_ids
            | spread_ids
            | section_ids
            | cell_ids
            | retired_entity_ids
        )

        expected_page_indices = set(range(len(document.pages)))
        if page_indices != expected_page_indices:
            problems["invalid_page_indices"].append(
                f"Physical page indices must be contiguous from zero; found {sorted(page_indices)}."
            )
        self._record_duplicate_ids(problems, "layout_object", [item.layout_object_id for item in document.layout_objects])
        self._record_duplicate_ids(problems, "artifact", [item.artifact_id for item in document.artifacts])
        self._record_duplicate_ids(problems, "coordinate_system", [item.coordinate_system_id for item in document.coordinate_systems])
        self._record_duplicate_ids(problems, "review_task", [item.task_id for item in document.review_tasks])
        self._record_duplicate_ids(
            problems,
            "patch_transaction",
            [item.transaction_id for item in document.patch_transactions],
        )
        self._record_duplicate_ids(problems, "spread", [item.spread_id for item in document.spreads])
        self._record_duplicate_ids(
            problems,
            "logical_table",
            [item.logical_table_id for item in document.logical_tables],
        )
        self._record_duplicate_ids(problems, "structure_edge", [item.edge_id for item in document.structure_edges])
        self._record_duplicate_ids(
            problems,
            "table_graph_edge",
            [edge.edge_id for table in document.tables for edge in table.graph_edges],
        )
        if document.metadata.source_artifacts.get("identifier_contract") == "document-ir-identifiers-v1":
            self._validate_identifier_contract(problems, document)

        def require(ref: str | None, valid: set[str], owner: str, field: str) -> None:
            if ref and ref not in valid:
                problems["dangling_references"].append(f"{owner}.{field} references missing ID {ref}.")

        def require_many(refs: list[str], valid: set[str], owner: str, field: str) -> None:
            for ref in refs:
                require(ref, valid, owner, field)

        for coordinate in document.coordinate_systems:
            if coordinate.page_index not in page_indices:
                problems["invalid_page_indices"].append(
                    f"Coordinate system {coordinate.coordinate_system_id} uses missing page {coordinate.page_index}."
                )
            if coordinate.width <= 0 or coordinate.height <= 0:
                problems["invalid_coordinates"].append(
                    f"Coordinate system {coordinate.coordinate_system_id} has non-positive dimensions."
                )

        for artifact in document.artifacts:
            if artifact.page_index is not None and artifact.page_index not in page_indices:
                problems["invalid_page_indices"].append(
                    f"Artifact {artifact.artifact_id} uses missing page {artifact.page_index}."
                )
            for page_index in artifact.page_indices:
                if page_index not in page_indices:
                    problems["invalid_page_indices"].append(
                        f"Artifact {artifact.artifact_id}.page_indices contains missing page {page_index}."
                    )
            if artifact.kind == "spread_image" and len(artifact.page_indices) != 2:
                problems["artifact_integrity"].append(
                    f"Spread artifact {artifact.artifact_id} must identify two physical pages."
                )

        for page in document.pages:
            owner = page.page_id
            require_many(page.block_ids, block_ids, owner, "block_ids")
            require_many(page.layout_object_ids, layout_ids, owner, "layout_object_ids")
            require_many(page.table_ids, table_ids, owner, "table_ids")
            require_many(page.figure_ids, figure_ids, owner, "figure_ids")
            require_many(page.coordinate_system_ids, coordinate_ids, owner, "coordinate_system_ids")
            require_many(page.review_task_ids, review_task_ids, owner, "review_task_ids")
            require_many(page.spread_ids, spread_ids, owner, "spread_ids")
            for coordinate_id in page.coordinate_system_ids:
                coordinate = coordinate_by_id.get(coordinate_id)
                if coordinate and coordinate.page_index != page.page_index:
                    problems["invalid_coordinates"].append(
                        f"{owner} references coordinate system {coordinate_id} from page {coordinate.page_index}."
                    )

        for block in document.blocks:
            owner = block.block_id
            self._validate_page_membership(problems, block.page_index, page_by_index, owner, "block_ids")
            require(block.section_id, section_ids, owner, "section_id")
            require(block.table_id, table_ids, owner, "table_id")
            require(block.figure_id, figure_ids, owner, "figure_id")
            require(block.layout_object_id, layout_ids, owner, "layout_object_id")
            require(block.crop_artifact_id, artifact_ids, owner, "crop_artifact_id")
            require_many(block.review_task_ids, review_task_ids, owner, "review_task_ids")
            if block.visual_role and not block.figure_id:
                problems["dangling_references"].append(
                    f"{owner}.visual_role is set without a figure_id."
                )
            section = section_by_id.get(block.section_id) if block.section_id else None
            if section and block.block_id not in section.block_ids:
                problems["invalid_section_hierarchy"].append(
                    f"{owner} points to {section.section_id}, but the section does not list the block."
                )

        for layout in document.layout_objects:
            owner = layout.layout_object_id
            self._validate_page_membership(problems, layout.page_index, page_by_index, owner, "layout_object_ids")
            require(layout.block_id, block_ids, owner, "block_id")
            require(layout.table_id, table_ids, owner, "table_id")
            require(layout.figure_id, figure_ids, owner, "figure_id")

        for table in document.tables:
            owner = table.table_id
            self._validate_page_membership(problems, table.page_index, page_by_index, owner, "table_ids")
            for page_index in table.page_indices:
                if page_index not in page_indices:
                    problems["invalid_page_indices"].append(f"{owner}.page_indices contains missing page {page_index}.")
            require(table.block_id, block_ids, owner, "block_id")
            require(table.crop_artifact_id, artifact_ids, owner, "crop_artifact_id")
            require(table.continues_from_table_id, table_ids, owner, "continues_from_table_id")
            require(table.continues_to_table_id, table_ids, owner, "continues_to_table_id")
            require_many(table.footnote_block_ids, block_ids, owner, "footnote_block_ids")
            require_many(table.review_task_ids, review_task_ids, owner, "review_task_ids")
            previous = table_by_id.get(table.continues_from_table_id) if table.continues_from_table_id else None
            following = table_by_id.get(table.continues_to_table_id) if table.continues_to_table_id else None
            if previous and previous.continues_to_table_id != table.table_id:
                problems["dangling_references"].append(
                    f"{owner}.continues_from_table_id is not reciprocated by {previous.table_id}."
                )
            if following and following.continues_from_table_id != table.table_id:
                problems["dangling_references"].append(
                    f"{owner}.continues_to_table_id is not reciprocated by {following.table_id}."
                )
            self._validate_table_grid(problems, table)

        physical_table_owners: dict[str, str] = {}
        for logical_table in document.logical_tables:
            owner = logical_table.logical_table_id
            require_many(logical_table.source_table_ids, table_ids, owner, "source_table_ids")
            require(logical_table.spread_id, spread_ids, owner, "spread_id")
            source_tables = [
                table_by_id[table_id]
                for table_id in logical_table.source_table_ids
                if table_id in table_by_id
            ]
            if len(logical_table.source_table_ids) < 2:
                problems["invalid_logical_tables"].append(
                    f"{owner} must compose at least two physical tables."
                )
            if len(set(logical_table.source_table_ids)) != len(logical_table.source_table_ids):
                problems["invalid_logical_tables"].append(
                    f"{owner}.source_table_ids contains duplicate physical tables."
                )
            if {item.table_id for item in logical_table.segments} != set(logical_table.source_table_ids):
                problems["invalid_logical_tables"].append(
                    f"{owner}.segments must cover every source table exactly once."
                )
            if sorted({item.page_index for item in source_tables}) != sorted(logical_table.page_indices):
                problems["invalid_logical_tables"].append(
                    f"{owner}.page_indices does not match its source tables."
                )
            for source_table in source_tables:
                previous_owner = physical_table_owners.get(source_table.table_id)
                if previous_owner and previous_owner != owner:
                    problems["invalid_logical_tables"].append(
                        f"{source_table.table_id} belongs to both {previous_owner} and {owner}."
                    )
                physical_table_owners[source_table.table_id] = owner
                if source_table.continuation_group_id != logical_table.continuation_group_id:
                    problems["invalid_logical_tables"].append(
                        f"{owner} group does not match {source_table.table_id}."
                    )
                if source_table.continuation_axis != logical_table.composition_axis:
                    problems["invalid_logical_tables"].append(
                        f"{owner} axis does not match {source_table.table_id}."
                    )
            self._validate_logical_table(
                problems,
                logical_table,
                table_by_id,
                cell_by_id,
                spread_by_id,
            )

        for figure in document.figures:
            owner = figure.figure_id
            self._validate_page_membership(problems, figure.page_index, page_by_index, owner, "figure_ids")
            require(figure.crop_artifact_id, artifact_ids, owner, "crop_artifact_id")
            require_many(figure.element_block_ids, block_ids, owner, "element_block_ids")
            require_many(figure.review_task_ids, review_task_ids, owner, "review_task_ids")
            for block_id in figure.element_block_ids:
                block = block_by_id.get(block_id)
                if block and block.figure_id != figure.figure_id:
                    problems["dangling_references"].append(
                        f"{owner}.element_block_ids includes {block_id}, but the block is bound to "
                        f"{block.figure_id or 'no figure'}."
                    )

        confirmed_page_membership: dict[int, str] = {}
        for spread in document.spreads:
            owner = spread.spread_id
            if len(spread.page_indices) != 2 or spread.page_indices[1] != spread.page_indices[0] + 1:
                problems["invalid_page_indices"].append(
                    f"{owner} must contain exactly two adjacent physical pages."
                )
            if len(spread.page_ids) != 2 or len(spread.placements) != 2:
                problems["dangling_references"].append(
                    f"{owner} must declare two page IDs and two composite placements."
                )
            require_many(spread.page_ids, page_ids, owner, "page_ids")
            require(spread.composite_artifact_id, artifact_ids, owner, "composite_artifact_id")
            require_many(spread.member_entity_ids, entity_ids, owner, "member_entity_ids")
            require_many(spread.review_task_ids, review_task_ids, owner, "review_task_ids")
            for page_id, page_index in zip(spread.page_ids, spread.page_indices):
                page = page_by_index.get(page_index)
                if page and page.page_id != page_id:
                    problems["dangling_references"].append(
                        f"{owner} maps page index {page_index} to {page_id}, expected {page.page_id}."
                    )
                if page and owner not in page.spread_ids:
                    problems["dangling_references"].append(
                        f"{owner} is not reciprocated by {page.page_id}.spread_ids."
                    )
            for placement in spread.placements:
                if placement.page_index not in spread.page_indices or placement.page_id not in spread.page_ids:
                    problems["dangling_references"].append(
                        f"{owner} placement references a page outside the spread."
                    )
                require(placement.source_coordinate_system_id, coordinate_ids, owner, "placement.coordinate_system")
            for link in spread.linked_entities:
                require(link.source_id, entity_ids, owner, "linked_entities.source_id")
                require(link.target_id, entity_ids, owner, "linked_entities.target_id")
            if spread.status == "confirmed":
                for page_index in spread.page_indices:
                    previous = confirmed_page_membership.get(page_index)
                    if previous and previous != spread.spread_id:
                        problems["dangling_references"].append(
                            f"Page {page_index} belongs to conflicting confirmed spreads {previous} and {owner}."
                        )
                    confirmed_page_membership[page_index] = owner

        for section in document.sections:
            owner = section.section_id
            require(section.heading_block_id, block_ids, owner, "heading_block_id")
            require(section.parent_section_id, section_ids, owner, "parent_section_id")
            require_many(section.child_section_ids, section_ids, owner, "child_section_ids")
            require_many(section.block_ids, block_ids, owner, "block_ids")
            self._validate_section(problems, section, section_by_id, block_by_id, page_indices)

        for edge in document.structure_edges:
            require(edge.source_id, entity_ids, edge.edge_id, "source_id")
            require(edge.target_id, entity_ids, edge.edge_id, "target_id")

        for task in document.review_tasks:
            require(task.target_id, entity_ids, task.task_id, "target_id")
            for scope in task.scope:
                require(scope.target_id, entity_ids, task.task_id, "scope.target_id")
            if task.review_plan:
                require_many(
                    task.review_plan.context_target_ids,
                    entity_ids,
                    task.task_id,
                    "review_plan.context_target_ids",
                )
                require_many(
                    task.review_plan.mutable_target_ids,
                    entity_ids,
                    task.task_id,
                    "review_plan.mutable_target_ids",
                )
                require_many(
                    task.review_plan.required_decision_target_ids,
                    entity_ids,
                    task.task_id,
                    "review_plan.required_decision_target_ids",
                )
                context_only = (
                    set(task.review_plan.context_target_ids)
                    - set(task.review_plan.mutable_target_ids)
                )
                if context_only & set(task.review_plan.required_decision_target_ids):
                    problems["dangling_references"].append(
                        f"{task.task_id} requires a decision for read-only context targets."
                    )

        for transaction in document.patch_transactions:
            owner = transaction.transaction_id
            require(transaction.task_id, review_task_ids, owner, "task_id")
            require(
                transaction.reviewer_result_id,
                reviewer_result_ids,
                owner,
                "reviewer_result_id",
            )
            require_many(transaction.patch_ids, patch_ids, owner, "patch_ids")
            require_many(transaction.target_ids, entity_ids, owner, "target_ids")
            require_many(
                transaction.required_target_ids,
                entity_ids,
                owner,
                "required_target_ids",
            )
            require(transaction.guard_result_id, guard_result_ids, owner, "guard_result_id")
            require(
                transaction.verifier_result_id,
                verifier_result_ids,
                owner,
                "verifier_result_id",
            )
            if not set(transaction.required_target_ids).issubset(set(transaction.target_ids)):
                problems["dangling_references"].append(
                    f"{owner}.required_target_ids must be a subset of target_ids."
                )
            for patch_id in transaction.patch_ids:
                patch = next(
                    (item for item in document.atomic_patches if item.patch_id == patch_id),
                    None,
                )
                if patch and patch.transaction_id != owner:
                    problems["dangling_references"].append(
                        f"{owner} lists {patch_id}, but the patch points to {patch.transaction_id}."
                    )

        for patch in document.atomic_patches:
            require(patch.transaction_id, transaction_ids, patch.patch_id, "transaction_id")
        for guard in document.guard_results:
            require(guard.transaction_id, transaction_ids, guard.guard_result_id, "transaction_id")
        for verifier in document.verifier_results:
            require_many(
                verifier.transaction_ids,
                transaction_ids,
                verifier.verifier_result_id,
                "transaction_ids",
            )
            decision_ids = [
                item.transaction_id
                for item in verifier.transaction_decisions
            ]
            require_many(
                decision_ids,
                transaction_ids,
                verifier.verifier_result_id,
                "transaction_decisions.transaction_id",
            )
            if decision_ids and (
                len(decision_ids) != len(set(decision_ids))
                or set(decision_ids) != set(verifier.transaction_ids)
            ):
                problems["dangling_references"].append(
                    f"{verifier.verifier_result_id}.transaction_decisions must decide "
                    "every linked transaction exactly once."
                )
        for candidate in document.candidate_revisions:
            require(
                candidate.transaction_id,
                transaction_ids,
                candidate.candidate_id,
                "transaction_id",
            )

        self._validate_geometry(problems, document, coordinate_by_id)
        self._validate_artifacts(problems, document)
        self._validate_source_traces(problems, document, artifact_ids)
        for path, value in self._walk_strings(document.model_dump(mode="json")):
            if is_sensitive_remote_url(value):
                problems["sensitive_remote_urls"].append(f"{path} contains a credential-bearing remote URL.")

        issue_specs = {
            "invalid_page_indices": ("invalid_page_indices", "blocking"),
            "duplicate_internal_ids": ("duplicate_internal_ids", "blocking"),
            "dangling_references": ("dangling_entity_references", "blocking"),
            "invalid_coordinates": ("invalid_coordinate_contract", "blocking"),
            "invalid_table_grids": ("invalid_table_grid", "blocking"),
            "invalid_logical_tables": ("invalid_logical_table", "blocking"),
            "invalid_section_hierarchy": ("invalid_section_hierarchy", "blocking"),
            "artifact_integrity": ("artifact_integrity_failed", "blocking"),
            "source_trace_integrity": ("source_trace_integrity_failed", "blocking"),
            "sensitive_remote_urls": ("sensitive_remote_url_exposed", "blocking"),
            "identifier_contract": ("identifier_contract_failed", "blocking"),
        }
        issues = []
        for key, values in problems.items():
            if not values:
                continue
            code, severity = issue_specs[key]
            preview = " ".join(values[:3])
            suffix = f" (+{len(values) - 3} more)" if len(values) > 3 else ""
            issues.append(self._issue(code, severity, f"{preview}{suffix}"))

        trace_entities = self._trace_entities(document)
        traced_entities = sum(1 for _, item in trace_entities if item.source_trace.artifact_ids)
        metrics = {
            "invalid_page_index_count": len(problems["invalid_page_indices"]),
            "duplicate_internal_id_count": len(problems["duplicate_internal_ids"]),
            "dangling_reference_count": len(problems["dangling_references"]),
            "invalid_coordinate_count": len(problems["invalid_coordinates"]),
            "invalid_table_grid_count": len(problems["invalid_table_grids"]),
            "invalid_logical_table_count": len(problems["invalid_logical_tables"]),
            "invalid_section_hierarchy_count": len(problems["invalid_section_hierarchy"]),
            "artifact_integrity_error_count": len(problems["artifact_integrity"]),
            "source_trace_error_count": len(problems["source_trace_integrity"]),
            "sensitive_remote_url_count": len(problems["sensitive_remote_urls"]),
            "identifier_contract_error_count": len(problems["identifier_contract"]),
            "source_trace_artifact_coverage": self._ratio(traced_entities, len(trace_entities)),
        }
        checks = {
            "page_indices_valid": not problems["invalid_page_indices"],
            "internal_ids_unique": not problems["duplicate_internal_ids"],
            "entity_references_valid": not problems["dangling_references"],
            "coordinate_contract_valid": not problems["invalid_coordinates"],
            "table_grids_valid": not problems["invalid_table_grids"],
            "logical_tables_valid": not problems["invalid_logical_tables"],
            "section_hierarchy_valid": not problems["invalid_section_hierarchy"],
            "artifact_integrity_valid": not problems["artifact_integrity"],
            "source_trace_integrity_valid": not problems["source_trace_integrity"],
            "sensitive_remote_urls_absent": not problems["sensitive_remote_urls"],
            "identifier_contract_valid": not problems["identifier_contract"],
        }
        return issues, metrics, checks

    @staticmethod
    def _validate_identifier_contract(problems, document: DocumentIR) -> None:
        groups = {
            "page": [item.page_id for item in document.pages],
            "section": [item.section_id for item in document.sections],
            "block": [item.block_id for item in document.blocks],
            "layout": [item.layout_object_id for item in document.layout_objects],
            "table": [item.table_id for item in document.tables],
            "logical-table": [item.logical_table_id for item in document.logical_tables],
            "figure": [item.figure_id for item in document.figures],
            "spread": [item.spread_id for item in document.spreads],
            "cell": [cell.cell_id for table in document.tables for cell in table.cells],
            "review": [item.task_id for item in document.review_tasks],
            "model-call": [item.call_id for item in document.model_calls],
            "reviewer": [item.reviewer_result_id for item in document.reviewer_results],
            "guard": [item.guard_result_id for item in document.guard_results],
            "verifier": [item.verifier_result_id for item in document.verifier_results],
            "patch": [item.patch_id for item in document.atomic_patches],
            "candidate": [item.candidate_id for item in document.candidate_revisions],
            "transaction": [item.transaction_id for item in document.patch_transactions],
            "decision": [item.decision_id for item in document.final_decisions],
            "conflict": [item.conflict_id for item in document.conflict_groups],
        }
        for kind, values in groups.items():
            _, invalid = identifiers_match(kind, values)
            for value in invalid:
                problems["identifier_contract"].append(f"Invalid {kind} ID {value}.")

        coordinate_pattern = re.compile(
            r"^page-\d{4}-(?:pdf-points|paddle-input|render-\d{2,3}dpi)$"
        )
        artifact_pattern = re.compile(
            r"^artifact-(?:source-pdf|ocr-raw|page-markdown-p\d{4}|page-image-p\d{4}|spread-p\d{4}-p\d{4}|crop-(?:table|figure)-p\d{4}-\d{4})$"
        )
        edge_pattern = re.compile(r"^edge-\d{6}$")
        table_edge_pattern = re.compile(r"^table-p\d{4}-\d{4}-edge-\d{5}$")
        for value in [item.coordinate_system_id for item in document.coordinate_systems]:
            if not coordinate_pattern.fullmatch(value):
                problems["identifier_contract"].append(f"Invalid coordinate-system ID {value}.")
        for value in [item.artifact_id for item in document.artifacts]:
            if not artifact_pattern.fullmatch(value):
                problems["identifier_contract"].append(f"Invalid artifact ID {value}.")
        for value in [item.edge_id for item in document.structure_edges]:
            if not edge_pattern.fullmatch(value):
                problems["identifier_contract"].append(f"Invalid structure-edge ID {value}.")
        for value in [edge.edge_id for table in document.tables for edge in table.graph_edges]:
            if not table_edge_pattern.fullmatch(value):
                problems["identifier_contract"].append(f"Invalid table-edge ID {value}.")

    @staticmethod
    def _record_duplicate_ids(problems, kind: str, values: list[str]) -> None:
        duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
        for value in duplicates:
            problems["duplicate_internal_ids"].append(f"Duplicate {kind} ID {value}.")

    @staticmethod
    def _validate_page_membership(problems, page_index, page_by_index, entity_id, collection_name) -> None:
        page = page_by_index.get(page_index)
        if page is None:
            problems["invalid_page_indices"].append(f"{entity_id} uses missing page {page_index}.")
            return
        if entity_id not in getattr(page, collection_name):
            problems["dangling_references"].append(
                f"{entity_id} is not listed in {page.page_id}.{collection_name}."
            )

    @staticmethod
    def _validate_table_grid(problems, table) -> None:
        occupied: dict[tuple[int, int], str] = {}
        table_cell_ids = {cell.cell_id for cell in table.cells}
        for cell in table.cells:
            if cell.table_id != table.table_id:
                problems["invalid_table_grids"].append(
                    f"{cell.cell_id} declares table_id={cell.table_id}, expected {table.table_id}."
                )
            if cell.page_index != table.page_index:
                problems["invalid_table_grids"].append(
                    f"{cell.cell_id} uses page {cell.page_index}, expected table page {table.page_index}."
                )
            if cell.row_index < 0 or cell.col_index < 0 or cell.row_span < 1 or cell.col_span < 1:
                problems["invalid_table_grids"].append(f"{cell.cell_id} has invalid row/column coordinates or span.")
                continue
            if cell.row_index + cell.row_span > table.row_count or cell.col_index + cell.col_span > table.column_count:
                problems["invalid_table_grids"].append(f"{cell.cell_id} exceeds the declared {table.row_count}x{table.column_count} grid.")
                continue
            for row_index in range(cell.row_index, cell.row_index + cell.row_span):
                for col_index in range(cell.col_index, cell.col_index + cell.col_span):
                    previous = occupied.get((row_index, col_index))
                    if previous and previous != cell.cell_id:
                        problems["invalid_table_grids"].append(
                            f"{table.table_id} cells {previous} and {cell.cell_id} overlap at ({row_index}, {col_index})."
                        )
                    occupied[(row_index, col_index)] = cell.cell_id
        expected = {
            (row_index, col_index)
            for row_index in range(max(0, table.row_count))
            for col_index in range(max(0, table.column_count))
        }
        missing = expected - set(occupied)
        if missing:
            problems["invalid_table_grids"].append(
                f"{table.table_id} has {len(missing)} uncovered grid positions."
            )
        for edge in table.graph_edges:
            if edge.table_id != table.table_id:
                problems["invalid_table_grids"].append(
                    f"{edge.edge_id} declares table_id={edge.table_id}, expected {table.table_id}."
                )
            if edge.source_cell_id not in table_cell_ids or edge.target_cell_id not in table_cell_ids:
                problems["invalid_table_grids"].append(f"{edge.edge_id} references a cell outside {table.table_id}.")

    @staticmethod
    def _validate_section(problems, section, section_by_id, block_by_id, page_indices) -> None:
        if (
            section.end_page_index is None
            or section.start_page_index not in page_indices
            or section.end_page_index not in page_indices
            or section.end_page_index < section.start_page_index
        ):
            problems["invalid_section_hierarchy"].append(
                f"{section.section_id} has invalid page range {section.start_page_index}..{section.end_page_index}."
            )
        parent = section_by_id.get(section.parent_section_id) if section.parent_section_id else None
        if parent:
            if section.section_id not in parent.child_section_ids:
                problems["invalid_section_hierarchy"].append(
                    f"{section.section_id} is not listed by parent {parent.section_id}."
                )
            if section.level <= parent.level:
                problems["invalid_section_hierarchy"].append(
                    f"{section.section_id} level {section.level} is not below parent {parent.section_id} level {parent.level}."
                )
            if (
                parent.end_page_index is not None
                and section.end_page_index is not None
                and (
                    section.start_page_index < parent.start_page_index
                    or section.end_page_index > parent.end_page_index
                )
            ):
                problems["invalid_section_hierarchy"].append(
                    f"{section.section_id} range is outside parent {parent.section_id}."
                )
        for child_id in section.child_section_ids:
            child = section_by_id.get(child_id)
            if child and child.parent_section_id != section.section_id:
                problems["invalid_section_hierarchy"].append(
                    f"{section.section_id} lists child {child_id}, but the child points to {child.parent_section_id}."
                )
        for block_id in section.block_ids:
            block = block_by_id.get(block_id)
            if block and block.section_id != section.section_id:
                problems["invalid_section_hierarchy"].append(
                    f"{section.section_id} lists {block_id}, but the block points to {block.section_id}."
                )
        heading = block_by_id.get(section.heading_block_id) if section.heading_block_id else None
        if heading and heading.section_id != section.section_id:
            problems["invalid_section_hierarchy"].append(
                f"{section.section_id} heading {heading.block_id} is assigned to {heading.section_id}."
            )

    @staticmethod
    def _validate_geometry(problems, document, coordinate_by_id) -> None:
        entities = [
            *((item.layout_object_id, item.page_index, item.bbox) for item in document.layout_objects),
            *((item.block_id, item.page_index, item.bbox) for item in document.blocks),
            *((item.table_id, item.page_index, item.bbox) for item in document.tables),
            *((item.figure_id, item.page_index, item.bbox) for item in document.figures),
            *((cell.cell_id, cell.page_index, cell.bbox) for table in document.tables for cell in table.cells),
            *((item.artifact_id, item.page_index, item.bbox) for item in document.artifacts if item.page_index is not None),
        ]
        has_coordinate_contract = bool(coordinate_by_id)
        for entity_id, page_index, bbox in entities:
            if bbox is None:
                continue
            if bbox.x0 >= bbox.x1 or bbox.y0 >= bbox.y1:
                problems["invalid_coordinates"].append(f"{entity_id} has a degenerate bounding box.")
                continue
            if not bbox.coordinate_system_id:
                if has_coordinate_contract:
                    problems["invalid_coordinates"].append(f"{entity_id} bounding box has no coordinate-system ID.")
                continue
            coordinate = coordinate_by_id.get(bbox.coordinate_system_id)
            if coordinate is None:
                problems["invalid_coordinates"].append(
                    f"{entity_id} references missing coordinate system {bbox.coordinate_system_id}."
                )
                continue
            if coordinate.page_index != page_index:
                problems["invalid_coordinates"].append(
                    f"{entity_id} uses coordinate system {bbox.coordinate_system_id} from page {coordinate.page_index}."
                )
            tolerance = max(1.0, max(coordinate.width, coordinate.height) * 0.005)
            if (
                bbox.x0 < -tolerance
                or bbox.y0 < -tolerance
                or bbox.x1 > coordinate.width + tolerance
                or bbox.y1 > coordinate.height + tolerance
            ):
                problems["invalid_coordinates"].append(
                    f"{entity_id} bounding box falls outside {bbox.coordinate_system_id}."
                )

    @staticmethod
    def _validate_artifacts(problems, document) -> None:
        for artifact in document.artifacts:
            if "://" in artifact.path and not artifact.path.startswith("file://"):
                continue
            path = Path(artifact.path.removeprefix("file://"))
            if not path.is_file():
                if not artifact.remote_uri:
                    problems["artifact_integrity"].append(
                        f"{artifact.artifact_id} local file is missing: {artifact.path}."
                    )
                continue
            if artifact.sha256:
                try:
                    actual = sha256_file(path)
                except OSError as exc:
                    problems["artifact_integrity"].append(f"{artifact.artifact_id} could not be hashed: {exc}.")
                    continue
                if actual != artifact.sha256:
                    problems["artifact_integrity"].append(f"{artifact.artifact_id} SHA-256 does not match its index.")

    @staticmethod
    def _trace_entities(document):
        return [
            *((item.page_id, item) for item in document.pages),
            *((item.layout_object_id, item) for item in document.layout_objects),
            *((item.block_id, item) for item in document.blocks),
            *((item.table_id, item) for item in document.tables),
            *((item.logical_table_id, item) for item in document.logical_tables),
            *((item.figure_id, item) for item in document.figures),
            *((item.spread_id, item) for item in document.spreads),
            *((cell.cell_id, cell) for table in document.tables for cell in table.cells),
        ]

    @classmethod
    def _validate_source_traces(cls, problems, document, artifact_ids) -> None:
        if not document.artifacts:
            return
        for entity_id, entity in cls._trace_entities(document):
            refs = entity.source_trace.artifact_ids
            if not refs:
                problems["source_trace_integrity"].append(f"{entity_id} has no source artifact ID.")
                continue
            for artifact_id in refs:
                if artifact_id not in artifact_ids:
                    problems["source_trace_integrity"].append(
                        f"{entity_id} references missing source artifact {artifact_id}."
                    )

    @staticmethod
    def _validate_logical_table(
        problems,
        logical_table,
        table_by_id,
        cell_by_id,
        spread_by_id,
    ) -> None:
        source_cell_ids = {
            cell.cell_id
            for table_id in logical_table.source_table_ids
            for cell in (table_by_id.get(table_id).cells if table_by_id.get(table_id) else [])
        }
        mapped_cell_ids = [mapping.source_cell_id for mapping in logical_table.cell_mappings]
        if set(mapped_cell_ids) != source_cell_ids or len(mapped_cell_ids) != len(source_cell_ids):
            problems["invalid_logical_tables"].append(
                f"{logical_table.logical_table_id}.cell_mappings must cover every source cell exactly once."
            )
        occupied: dict[tuple[int, int], str] = {}
        for mapping in logical_table.cell_mappings:
            source_cell = cell_by_id.get(mapping.source_cell_id)
            if source_cell is None:
                problems["invalid_logical_tables"].append(
                    f"{logical_table.logical_table_id} maps missing cell {mapping.source_cell_id}."
                )
                continue
            if (
                source_cell.table_id != mapping.source_table_id
                or mapping.source_table_id not in logical_table.source_table_ids
            ):
                problems["invalid_logical_tables"].append(
                    f"{logical_table.logical_table_id} maps {mapping.source_cell_id} to the wrong source table."
                )
            if (
                mapping.logical_row_index < 0
                or mapping.logical_col_index < 0
                or mapping.logical_row_index + mapping.row_span > logical_table.logical_row_count
                or mapping.logical_col_index + mapping.col_span > logical_table.logical_column_count
            ):
                problems["invalid_logical_tables"].append(
                    f"{logical_table.logical_table_id} mapping for {mapping.source_cell_id} is out of bounds."
                )
            for row_index in range(
                mapping.logical_row_index,
                mapping.logical_row_index + mapping.row_span,
            ):
                for column_index in range(
                    mapping.logical_col_index,
                    mapping.logical_col_index + mapping.col_span,
                ):
                    position = (row_index, column_index)
                    previous = occupied.get(position)
                    overlap_allowed = (
                        logical_table.composition_mode
                        == "horizontal_continue_last_column"
                        and position[1] == logical_table.logical_column_count - 1
                    )
                    if previous and previous != mapping.source_cell_id and not overlap_allowed:
                        problems["invalid_logical_tables"].append(
                            f"{logical_table.logical_table_id} overlaps {previous} and "
                            f"{mapping.source_cell_id} at {position}."
                        )
                    occupied[position] = mapping.source_cell_id
        logical_positions = [
            (cell.row_index, cell.col_index)
            for cell in logical_table.cells
        ]
        expected_positions = {
            (row_index, col_index)
            for row_index in range(logical_table.logical_row_count)
            for col_index in range(logical_table.logical_column_count)
        }
        if set(logical_positions) != expected_positions or len(logical_positions) != len(expected_positions):
            problems["invalid_logical_tables"].append(
                f"{logical_table.logical_table_id}.cells must cover the complete logical grid exactly once."
            )
        for cell in logical_table.cells:
            unknown_sources = set(cell.source_cell_ids) - source_cell_ids
            if unknown_sources:
                problems["invalid_logical_tables"].append(
                    f"{logical_table.logical_table_id} logical cell ({cell.row_index}, "
                    f"{cell.col_index}) references unknown source cells {sorted(unknown_sources)}."
                )
            mapped_sources = {
                mapping.source_cell_id
                for mapping in logical_table.cell_mappings
                if mapping.logical_row_index == cell.row_index
                and mapping.logical_col_index == cell.col_index
            }
            if set(cell.source_cell_ids) != mapped_sources:
                problems["invalid_logical_tables"].append(
                    f"{logical_table.logical_table_id} logical cell ({cell.row_index}, "
                    f"{cell.col_index}) does not match its source mappings."
                )
        if logical_table.spread_id:
            spread = spread_by_id.get(logical_table.spread_id)
            if spread and (
                spread.status != "confirmed"
                or not set(logical_table.page_indices).issubset(set(spread.page_indices))
            ):
                problems["invalid_logical_tables"].append(
                    f"{logical_table.logical_table_id} references an unconfirmed or incompatible spread."
                )

    @classmethod
    def _walk_strings(cls, value: Any, path: str = "$"):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from cls._walk_strings(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from cls._walk_strings(item, f"{path}[{index}]")
        elif isinstance(value, str):
            yield path, value

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 1.0

    @staticmethod
    def _issue(code, severity, message):
        return ValidationIssue(code=code, severity=severity, message=message)
