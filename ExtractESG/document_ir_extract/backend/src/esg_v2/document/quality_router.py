from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from esg_v2.document.contracts import DocumentIR, LocalPdfForensics, ReviewScopeItem, VlmReviewTask
from esg_v2.document.geometry import bbox_area_ratio
from esg_v2.document.parser_fusion import ParserFusion


@dataclass(frozen=True)
class QualityRoutingResult:
    document: DocumentIR
    review_tasks: list[VlmReviewTask]
    quality_report: dict[str, object]


class OcrQualityRouter:
    """Routes only risk-bearing regions to paid/model review."""

    def route(self, document: DocumentIR, forensics: LocalPdfForensics | None = None) -> QualityRoutingResult:
        review_tasks: list[VlmReviewTask] = []
        native_by_page = {page.page_index: page for page in (forensics.pages if forensics else [])}
        artifacts = {artifact.artifact_id: artifact for artifact in document.artifacts}
        task_ids_by_target: dict[str, list[str]] = defaultdict(list)
        scopes_by_page: dict[int, list[ReviewScopeItem]] = defaultdict(list)
        refs_by_page: dict[int, list[str]] = defaultdict(list)
        priority_by_page: dict[int, str] = {}
        priority_rank = {"low": 0, "normal": 1, "high": 2, "critical": 3}

        def add_scope(page_index, target_type, target_id, reasons, *, bbox=None, blocking=True, priority="normal", refs=None):
            if not reasons:
                return
            scopes_by_page[page_index].append(
                ReviewScopeItem(
                    target_type=target_type,
                    target_id=target_id,
                    reason_codes=list(dict.fromkeys(reasons)),
                    bbox=bbox,
                    blocking=blocking,
                )
            )
            refs_by_page[page_index].extend(refs or [])
            current = priority_by_page.get(page_index, "low")
            if priority_rank[priority] > priority_rank[current]:
                priority_by_page[page_index] = priority

        if forensics and forensics.page_count is not None and forensics.page_count != len(document.pages):
            for page in document.pages:
                self._add_flag(page.quality_flags, "pdf_ocr_page_count_mismatch")

        for page in document.pages:
            native = native_by_page.get(page.page_index)
            reasons: list[str] = []
            task_type = "page_visual_read"
            visible_text_length = len(ParserFusion._visible_text(page.text))
            if visible_text_length < 40:
                reasons.append("low_ocr_text_coverage")
                self._add_flag(page.quality_flags, "low_ocr_text_coverage")
            if native and native.native_text_length is not None:
                if native.native_text_length >= 80 and visible_text_length < 20:
                    reasons.append("native_text_present_ocr_weak")
                    task_type = "ocr_native_conflict_review"
                    self._add_flag(page.quality_flags, "native_text_present_ocr_weak")
                elif native.native_text_length >= 80 and visible_text_length >= 80:
                    ratio = abs(native.native_text_length - visible_text_length) / max(native.native_text_length, visible_text_length)
                    if ratio > 0.65:
                        reasons.append("native_ocr_text_length_divergence")
                        task_type = "ocr_native_conflict_review"
            if reasons:
                add_scope(
                    page.page_index,
                    "page",
                    page.page_id,
                    reasons,
                    blocking=True,
                    priority="critical" if "native_text_present_ocr_weak" in reasons else "high",
                    refs=[ref for ref in [*page.remote_image_urls, page.page_image_path, page.markdown_path] if ref],
                )

        for table in document.tables:
            reasons: list[str] = []
            blocking = False
            priority = "normal"
            if not table.bbox:
                reasons.append("table_geometry_missing")
                blocking = True
                priority = "high"
            if table.row_count == 0 or table.column_count == 0:
                reasons.append("empty_or_unparsed_table")
                blocking = True
                priority = "critical"
            first_row = [cell for cell in table.cells if cell.row_index == 0]
            if (
                "embedded_table_images_present" in table.quality_flags
                and first_row
                and any(len(cell.text.strip()) >= 30 for cell in first_row)
            ):
                reasons.append("probable_missing_visual_header_row")
                blocking = True
                priority = "high"
            if table.continuation_group_id:
                reasons.append("cross_page_link_confirmation")
            blank_body = sum(1 for cell in table.cells if cell.row_index > 0 and not cell.text.strip())
            if table.column_count >= 3 and blank_body >= max(4, len(table.cells) // 4):
                reasons.append("blank_visual_encoding_cells")
                blocking = True
                priority = "high"
            if "local_table_dimension_conflict" in table.quality_flags:
                reasons.append("local_table_dimension_conflict")
            local = native_by_page.get(table.page_index)
            if local and local.table_candidates and len(local.table_candidates) > 0 and not table.cells:
                reasons.append("local_table_ocr_structure_conflict")
                blocking = True
                priority = "critical"
            if not reasons:
                continue
            for reason in reasons:
                self._add_flag(table.quality_flags, reason)
            refs = self._target_visual_refs(table.crop_artifact_id, table.page_index, document, artifacts)
            add_scope(
                table.page_index,
                "table",
                table.table_id,
                reasons,
                bbox=table.bbox,
                blocking=blocking,
                priority=priority,
                refs=refs,
            )

        for figure in document.figures:
            page = next((item for item in document.pages if item.page_index == figure.page_index), None)
            area_ratio = bbox_area_ratio(
                figure.bbox,
                page_width=page.width if page else None,
                page_height=page.height if page else None,
            )
            reasons = []
            if figure.visual_type in {"chart", "diagram", "composite"}:
                reasons.append("semantic_visual_structure")
            if figure.visual_type == "unknown" and area_ratio >= 0.04:
                reasons.append("unclassified_material_visual")
            if "image_binding_unresolved" in figure.quality_flags:
                reasons.append("image_binding_unresolved")
            if not reasons:
                continue
            refs = self._target_visual_refs(figure.crop_artifact_id, figure.page_index, document, artifacts)
            add_scope(
                figure.page_index,
                "figure",
                figure.figure_id,
                reasons,
                bbox=figure.bbox,
                blocking=False,
                priority="normal" if figure.visual_type in {"chart", "diagram", "composite"} else "low",
                refs=[*refs, *([figure.image_path] if figure.image_path else [])],
            )
            self._add_flag(figure.quality_flags, "visual_structure_review_candidate")

        pages_by_index = {page.page_index: page for page in document.pages}
        for page_index in sorted(scopes_by_page):
            page = pages_by_index[page_index]
            scopes = scopes_by_page[page_index]
            all_reasons = list(dict.fromkeys(reason for scope in scopes for reason in scope.reason_codes))
            local_visual_refs = [
                ref
                for ref in [page.page_image_path, *refs_by_page[page_index]]
                if ref and not ref.startswith(("http://", "https://", "kodo://"))
            ]
            cloud_visual_refs = [
                ref
                for ref in [*refs_by_page[page_index], *page.remote_image_urls]
                if ref and ref.startswith(("http://", "https://", "kodo://"))
            ]
            page_cloud_refs = [
                artifact.remote_uri
                for artifact in document.artifacts
                if artifact.kind == "page_image"
                and artifact.page_index == page_index
                and artifact.remote_uri
            ]
            refs = [
                ref
                for ref in [*((page_cloud_refs + cloud_visual_refs) if page_cloud_refs else local_visual_refs), page.markdown_path]
                if ref
            ]
            task = self._task(
                len(review_tasks) + 1,
                "page_compound_review",
                "page",
                page.page_id,
                page_index,
                all_reasons,
                "Verify page coverage and every scoped Document IR structure. Confirm, propose only atomic local corrections, or abstain.",
                refs,
                bbox=None,
                scope=scopes,
                blocking=any(scope.blocking for scope in scopes),
                priority=priority_by_page.get(page_index, "normal"),
            )
            review_tasks.append(task)
            task_ids_by_target[page.page_id].append(task.task_id)
            for scope in scopes:
                task_ids_by_target[scope.target_id].append(task.task_id)

        self._attach_task_ids(document, task_ids_by_target)
        flag_counts = Counter()
        for item in [*document.pages, *document.blocks, *document.tables, *document.figures]:
            flag_counts.update(item.quality_flags)
        task_counts = Counter(task.task_type for task in review_tasks)
        quality_report = {
            "page_count": len(document.pages),
            "block_count": len(document.blocks),
            "layout_object_count": len(document.layout_objects),
            "table_count": len(document.tables),
            "figure_count": len(document.figures),
            "structure_edge_count": len(document.structure_edges),
            "review_task_count": len(review_tasks),
            "blocking_review_task_count": sum(1 for task in review_tasks if task.blocking),
            "optional_review_task_count": sum(1 for task in review_tasks if not task.blocking),
            "quality_flag_counts": dict(flag_counts),
            "review_task_type_counts": dict(task_counts),
            "local_forensics_errors": forensics.errors if forensics else [],
            "vlm_execution": "not_requested",
        }
        document.review_tasks = review_tasks
        document.quality_report = quality_report
        return QualityRoutingResult(document=document, review_tasks=review_tasks, quality_report=quality_report)

    @staticmethod
    def _target_visual_refs(crop_artifact_id, page_index, document, artifacts):
        refs: list[str] = []
        if crop_artifact_id and crop_artifact_id in artifacts:
            refs.append(artifacts[crop_artifact_id].path)
        page = next((item for item in document.pages if item.page_index == page_index), None)
        if page and page.page_image_path:
            refs.append(page.page_image_path)
        if page:
            refs.extend(page.remote_image_urls)
        return refs

    @staticmethod
    def _attach_task_ids(document, task_ids_by_target):
        pages_by_index = {page.page_index: page for page in document.pages}
        for item, target_id in [
            *[(item, item.page_id) for item in document.pages],
            *[(item, item.block_id) for item in document.blocks],
            *[(item, item.table_id) for item in document.tables],
            *[(item, item.figure_id) for item in document.figures],
        ]:
            item.review_task_ids = list(dict.fromkeys([*item.review_task_ids, *task_ids_by_target.get(target_id, [])]))
            if hasattr(item, "page_index") and target_id not in {getattr(item, "page_id", None)}:
                page = pages_by_index.get(item.page_index)
                if page:
                    page.review_task_ids = list(dict.fromkeys([*page.review_task_ids, *item.review_task_ids]))

    @staticmethod
    def _task(
        counter,
        task_type,
        target_type,
        target_id,
        page_index,
        reason_codes,
        prompt_intent,
        input_refs,
        bbox,
        *,
        scope=None,
        blocking=True,
        priority="normal",
    ):
        return VlmReviewTask(
            task_id=f"review-{counter:06d}",
            task_type=task_type,
            target_type=target_type,
            target_id=target_id,
            page_index=page_index,
            bbox=bbox,
            compound_group_id=f"page-{page_index + 1:04d}",
            scope=scope or [],
            blocking=blocking,
            priority=priority,
            reason_codes=reason_codes,
            prompt_intent=prompt_intent,
            input_refs=list(dict.fromkeys(ref for ref in input_refs if ref)),
            expected_schema={
                "type": "object",
                "required": ["verdict", "findings", "patches", "confidence"],
                "properties": {
                    "verdict": {"enum": ["confirm", "correct", "abstain"]},
                    "findings": {"type": "array"},
                    "patches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["target_type", "target_id", "operation", "proposed_value", "evidence_refs", "rationale", "confidence"],
                            "properties": {
                                "target_type": {"enum": ["page", "block", "cell", "table", "figure", "section"]},
                                "target_id": {"type": "string"},
                                "operation": {
                                    "enum": [
                                        "confirm", "replace_block_text", "replace_cell_text", "set_table_grid",
                                        "insert_table_row", "set_bbox", "set_printed_page_label", "set_visual_type",
                                        "set_caption", "link_continuation", "merge_blocks", "split_block", "add_quality_flags",
                                    ]
                                },
                                "field_path": {"type": ["string", "null"]},
                                "before_value": {},
                                "proposed_value": {},
                                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                                "rationale": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                        },
                    },
                    "confidence": {"type": "number"},
                    "abstain_reason": {"type": ["string", "null"]},
                    "quality_flags": {"type": "array"},
                },
            },
        )

    @staticmethod
    def _add_flag(flags, flag):
        if flag not in flags:
            flags.append(flag)
