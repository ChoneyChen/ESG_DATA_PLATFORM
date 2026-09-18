from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from esg_v2.document.contracts import DocumentIR, LocalPdfForensics, ReviewScopeItem, VlmReviewTask
from esg_v2.document.geometry import bbox_area_ratio
from esg_v2.document.review_plan_compiler import ReviewPlanCompiler
from esg_v2.document.spread_preflight import SpreadPreflightClassifier
from esg_v2.document.text_normalization import canonical_page_text, source_character_recall, visible_text


@dataclass(frozen=True)
class QualityRoutingResult:
    document: DocumentIR
    review_tasks: list[VlmReviewTask]
    quality_report: dict[str, object]


class OcrQualityRouter:
    """Routes only risk-bearing regions to paid/model review."""

    def route(self, document: DocumentIR, forensics: LocalPdfForensics | None = None) -> QualityRoutingResult:
        document = SpreadPreflightClassifier().classify(document)
        plan_compiler = ReviewPlanCompiler()
        review_tasks: list[VlmReviewTask] = []
        native_by_page = {page.page_index: page for page in (forensics.pages if forensics else [])}
        artifacts = {artifact.artifact_id: artifact for artifact in document.artifacts}
        task_ids_by_target: dict[str, list[str]] = defaultdict(list)
        scopes_by_page: dict[int, list[ReviewScopeItem]] = defaultdict(list)
        refs_by_target: dict[str, list[str]] = defaultdict(list)
        priority_by_page: dict[int, str] = {}
        priority_by_target: dict[str, str] = {}
        spread_reasons: dict[str, list[str]] = defaultdict(list)
        spread_refs: dict[str, list[str]] = defaultdict(list)
        spread_context_targets: dict[str, list[str]] = defaultdict(list)
        spread_scopes: dict[str, list[ReviewScopeItem]] = defaultdict(list)
        spread_priorities: dict[str, str] = {}
        spread_blocking: dict[str, bool] = defaultdict(bool)
        priority_rank = {"low": 0, "normal": 1, "high": 2, "critical": 3}
        candidate_spread_by_page = {
            page_index: spread
            for spread in document.spreads
            if spread.status in {"candidate", "ambiguous"} and spread.requires_detailed_review
            for page_index in spread.page_indices
        }
        tables_by_page: dict[int, list] = defaultdict(list)
        figures_by_page: dict[int, list] = defaultdict(list)
        blocks_by_figure: dict[str, list] = defaultdict(list)
        for table in document.tables:
            tables_by_page[table.page_index].append(table)
        for figure in document.figures:
            figures_by_page[figure.page_index].append(figure)
        for block in document.blocks:
            if block.figure_id:
                blocks_by_figure[block.figure_id].append(block)
        suppressed_optional_visuals = 0

        def add_scope(page_index, target_type, target_id, reasons, *, bbox=None, blocking=True, priority="normal", refs=None):
            if not reasons:
                return
            spread = candidate_spread_by_page.get(page_index)
            if spread is not None:
                spread_reasons[spread.spread_id].extend(reasons)
                spread_refs[spread.spread_id].extend(refs or [])
                spread_blocking[spread.spread_id] = (
                    spread_blocking[spread.spread_id] or blocking
                )
                if target_id != spread.spread_id:
                    spread_context_targets[spread.spread_id].append(target_id)
                    spread_scopes[spread.spread_id].append(
                        ReviewScopeItem(
                            target_type=target_type,
                            target_id=target_id,
                            reason_codes=list(dict.fromkeys(reasons)),
                            bbox=bbox,
                            blocking=blocking,
                        )
                    )
                current = spread_priorities.get(spread.spread_id, "low")
                if priority_rank[priority] > priority_rank[current]:
                    spread_priorities[spread.spread_id] = priority
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
            refs_by_target[target_id].extend(refs or [])
            current = priority_by_page.get(page_index, "low")
            if priority_rank[priority] > priority_rank[current]:
                priority_by_page[page_index] = priority
            target_priority = priority_by_target.get(target_id, "low")
            if priority_rank[priority] > priority_rank[target_priority]:
                priority_by_target[target_id] = priority

        if forensics and forensics.page_count is not None and forensics.page_count != len(document.pages):
            for page in document.pages:
                self._add_flag(page.quality_flags, "pdf_ocr_page_count_mismatch")

        for page in document.pages:
            native = native_by_page.get(page.page_index)
            reasons: list[str] = []
            page_canonical_text = canonical_page_text(document, page)
            visible_text_length = len(visible_text(page_canonical_text))
            if visible_text_length < 40:
                self._add_flag(page.quality_flags, "low_ocr_text_coverage")
            if native and native.native_text_length is not None:
                native_text = (
                    native.native_text_for_comparison
                    if native.native_text_for_comparison is not None
                    else native.native_text
                )
                native_text_length = (
                    native.native_text_for_comparison_length
                    if native.native_text_for_comparison_length is not None
                    else native.native_text_length
                )
                if native_text_length >= 80 and visible_text_length < 20:
                    reasons.append("native_text_present_ocr_weak")
                    self._add_flag(page.quality_flags, "native_text_present_ocr_weak")
                elif (
                    native_text_length >= 80
                    and visible_text_length >= 80
                    and visible_text_length < native_text_length * 0.60
                    and source_character_recall(native_text or "", page_canonical_text) < 0.75
                ):
                    reasons.append("native_ocr_text_length_divergence")
            if visible_text_length < 40 and not reasons:
                material_data_visual = any(
                    self._material_data_visual(figure, page, blocks_by_figure.get(figure.figure_id, []))
                    for figure in figures_by_page.get(page.page_index, [])
                )
                if material_data_visual:
                    reasons.append("low_ocr_text_coverage_on_data_visual")
                    self._add_flag(page.quality_flags, "low_ocr_text_coverage_on_data_visual")
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
            advisory_reasons: list[str] = []
            priority = "high"
            if not table.bbox:
                reasons.append("table_geometry_missing")
            if table.row_count == 0 or table.column_count == 0:
                reasons.append("empty_or_unparsed_table")
                priority = "critical"
            if table.continuation_group_id:
                advisory_reasons.append("cross_page_link_confirmation")
            blank_body = sum(1 for cell in table.cells if cell.row_index > 0 and not cell.text.strip())
            if (
                (
                    "embedded_table_images_present" in table.quality_flags
                    or "local_only_table_candidate" in table.quality_flags
                )
                and table.column_count >= 3
                and blank_body >= max(4, len(table.cells) // 4)
            ):
                reasons.append("blank_visual_encoding_cells")
            if "local_table_dimension_conflict" in table.quality_flags:
                advisory_reasons.append("local_table_dimension_conflict")
            local = native_by_page.get(table.page_index)
            if local and local.table_candidates and len(local.table_candidates) > 0 and not table.cells:
                reasons.append("local_table_ocr_structure_conflict")
                priority = "critical"
            for reason in [*reasons, *advisory_reasons]:
                self._add_flag(table.quality_flags, reason)
            if not reasons:
                continue
            refs = self._target_visual_refs(table.crop_artifact_id, table.page_index, document, artifacts)
            add_scope(
                table.page_index,
                "table",
                table.table_id,
                reasons,
                bbox=table.bbox,
                blocking=True,
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
            blocking = False
            data_visual = self._material_data_visual(
                figure,
                page,
                blocks_by_figure.get(figure.figure_id, []),
            )
            if figure.visual_type == "chart" and area_ratio >= 0.03:
                reasons.append("material_chart_structure")
                blocking = True
            elif figure.visual_type == "composite" and data_visual:
                reasons.append("material_composite_data_visual")
                blocking = True
            elif (
                figure.visual_type in {"diagram", "composite", "unknown"}
                and area_ratio >= 0.15
                and "image_binding_unresolved" in figure.quality_flags
            ):
                reasons.append("material_visual_binding_unresolved")
            page_coverage_routed = any(
                scope.target_type == "page"
                for scope in scopes_by_page.get(figure.page_index, [])
            )
            if (
                page_coverage_routed
                and figure.visual_type in {"diagram", "composite", "unknown"}
                and area_ratio >= 0.08
                and "image_binding_unresolved" in figure.quality_flags
            ):
                reasons.append("page_text_coverage_support")
            if not reasons:
                suppressed_optional_visuals += 1
                continue
            refs = self._target_visual_refs(figure.crop_artifact_id, figure.page_index, document, artifacts)
            add_scope(
                figure.page_index,
                "figure",
                figure.figure_id,
                reasons,
                bbox=figure.bbox,
                blocking=blocking,
                priority="high" if blocking else "low",
                refs=[*refs, *([figure.image_path] if figure.image_path else [])],
            )
            self._add_flag(figure.quality_flags, "visual_structure_review_candidate")

        pages_by_index = {page.page_index: page for page in document.pages}
        for spread in sorted(document.spreads, key=lambda item: (item.page_indices, item.spread_id)):
            if spread.status not in {"candidate", "ambiguous"}:
                continue
            artifact = artifacts.get(spread.composite_artifact_id)
            member_pages = [pages_by_index.get(page_index) for page_index in spread.page_indices]
            refs = [
                artifact.remote_uri or artifact.path if artifact else None,
                *(page.page_image_path for page in member_pages if page and page.page_image_path),
                *spread_refs.get(spread.spread_id, []),
                *(page.markdown_path for page in member_pages if page and page.markdown_path),
            ]
            reasons = list(
                dict.fromkeys(
                    [
                        "possible_horizontal_page_spread",
                        *spread.reason_codes,
                        *spread_reasons.get(spread.spread_id, []),
                    ]
                )
            )
            is_blocking = spread_blocking.get(spread.spread_id, False)
            task = self._task(
                len(review_tasks) + 1,
                "horizontal_spread_review",
                "spread",
                spread.spread_id,
                spread.page_indices[0],
                reasons,
                "Decide whether the adjacent pages form one horizontal spread and preserve cross-seam structure.",
                refs,
                bbox=None,
                scope=[
                    ReviewScopeItem(
                        target_type="spread",
                        target_id=spread.spread_id,
                        reason_codes=reasons,
                        blocking=is_blocking,
                    ),
                    *self._unique_scopes(spread_scopes.get(spread.spread_id, [])),
                ],
                blocking=is_blocking,
                priority=(
                    spread_priorities.get(spread.spread_id, "critical")
                    if is_blocking
                    else "low"
                ),
            )
            plan_compiler.compile(
                document,
                task,
                extra_context_target_ids=spread_context_targets.get(spread.spread_id, []),
                extra_mutable_target_ids=[
                    *spread.member_entity_ids,
                    *spread_context_targets.get(spread.spread_id, []),
                ],
            )
            task.prompt_intent = task.review_plan.question
            review_tasks.append(task)
            task_ids_by_target[spread.spread_id].append(task.task_id)
            for target_id in [
                *spread.page_ids,
                *task.review_plan.context_target_ids,
                *task.review_plan.required_decision_target_ids,
            ]:
                task_ids_by_target[target_id].append(task.task_id)

        for page_index in sorted(scopes_by_page):
            page = pages_by_index[page_index]
            scopes = scopes_by_page[page_index]
            page_scopes = [scope for scope in scopes if scope.target_type == "page"]
            structured_scopes = [
                scope
                for scope in scopes
                if scope.target_type in {"table", "figure"} and scope.blocking
            ]
            if page_scopes and structured_scopes:
                for scope in structured_scopes:
                    self._add_flag(scope.reason_codes, "page_text_coverage_support")
                scopes = [scope for scope in scopes if scope.target_type != "page"]

            for scope in scopes:
                target_refs = refs_by_target.get(scope.target_id, [])
                local_visual_refs = [
                    ref
                    for ref in [*target_refs, page.page_image_path]
                    if ref and not ref.startswith(("http://", "https://", "kodo://"))
                ]
                cloud_visual_refs = [
                    ref
                    for ref in [*target_refs, *page.remote_image_urls]
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
                    for ref in [
                        *((cloud_visual_refs + page_cloud_refs) if page_cloud_refs else local_visual_refs),
                        page.markdown_path,
                    ]
                    if ref
                ]
                task = self._task(
                    len(review_tasks) + 1,
                    self._task_type(scope),
                    scope.target_type,
                    scope.target_id,
                    page_index,
                    scope.reason_codes,
                    "Verify the routed Document IR scope. Confirm it, propose bounded atomic patches, or abstain.",
                    refs,
                    bbox=scope.bbox,
                    scope=[scope],
                    blocking=scope.blocking,
                    priority=priority_by_target.get(
                        scope.target_id,
                        priority_by_page.get(page_index, "normal"),
                    ),
                )
                plan_compiler.compile(
                    document,
                    task,
                    extra_context_target_ids=[page.page_id] if page_scopes and structured_scopes else [],
                )
                review_tasks.append(task)
                task_ids_by_target[page.page_id].append(task.task_id)
                task_ids_by_target[scope.target_id].append(task.task_id)

        self._attach_task_ids(document, task_ids_by_target)
        task_by_target = {
            target_id: task
            for task in review_tasks
            for target_id in (
                task.review_plan.mutable_target_ids
                if task.review_plan
                else [task.target_id, *(scope.target_id for scope in task.scope)]
            )
        }
        for conflict in document.conflict_groups:
            if conflict.status != "open":
                continue
            routed_task = task_by_target.get(conflict.target_id)
            if routed_task:
                conflict.routing_disposition = (
                    "blocking_task" if routed_task.blocking else "optional_task"
                )
            elif not conflict.blocking:
                conflict.status = "resolved"
                conflict.routing_disposition = "accepted_nonmaterial_difference"
                conflict.resolution = (
                    conflict.resolution
                    or "Non-blocking parser observation retained without a review task."
                )
        flag_counts = Counter()
        for item in [*document.pages, *document.blocks, *document.tables, *document.figures, *document.spreads]:
            flag_counts.update(item.quality_flags)
        task_counts = Counter(task.task_type for task in review_tasks)
        quality_report = {
            "page_count": len(document.pages),
            "block_count": len(document.blocks),
            "layout_object_count": len(document.layout_objects),
            "table_count": len(document.tables),
            "figure_count": len(document.figures),
            "spread_count": len(document.spreads),
            "horizontal_spread_candidate_count": sum(
                1 for spread in document.spreads if spread.status in {"candidate", "ambiguous"}
            ),
            "horizontal_spread_content_crossing_count": sum(
                1 for spread in document.spreads if spread.classification == "content_crossing"
            ),
            "horizontal_spread_visual_continuity_count": sum(
                1 for spread in document.spreads if spread.classification == "visual_continuity"
            ),
            "horizontal_spread_uncertain_count": sum(
                1 for spread in document.spreads if spread.classification == "uncertain"
            ),
            "horizontal_spread_local_resolved_count": sum(
                1 for spread in document.spreads if not spread.requires_detailed_review
            ),
            "blocking_horizontal_spread_review_count": sum(
                1
                for task in review_tasks
                if task.task_type == "horizontal_spread_review" and task.blocking
            ),
            "optional_horizontal_spread_review_count": sum(
                1
                for task in review_tasks
                if task.task_type == "horizontal_spread_review" and not task.blocking
            ),
            "structure_edge_count": len(document.structure_edges),
            "review_task_count": len(review_tasks),
            "blocking_review_task_count": sum(1 for task in review_tasks if task.blocking),
            "optional_review_task_count": sum(1 for task in review_tasks if not task.blocking),
            "suppressed_optional_visual_review_count": suppressed_optional_visuals,
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
            *[(item, item.spread_id) for item in document.spreads],
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
            compound_group_id=target_id if target_type == "spread" else f"page-{page_index + 1:04d}",
            scope=scope or [],
            blocking=blocking,
            priority=priority,
            reason_codes=reason_codes,
            prompt_intent=prompt_intent,
            input_refs=list(dict.fromkeys(ref for ref in input_refs if ref)),
            expected_schema={
                "type": "object",
                "required": ["verdict", "findings", "scope_decisions", "patches", "confidence"],
                "properties": {
                    "verdict": {"enum": ["confirm", "propose_patch", "abstain"]},
                    "findings": {"type": "array"},
                    "scope_decisions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["target_type", "target_id", "decision", "rationale", "confidence"],
                            "properties": {
                                "target_type": {"enum": ["page", "spread", "block", "cell", "table", "figure", "section"]},
                                "target_id": {"type": "string"},
                                "decision": {"enum": ["confirm", "propose_patch", "abstain"]},
                                "rationale": {"type": ["string", "null"]},
                                "confidence": {"type": "number"},
                            },
                        },
                    },
                    "patches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["target_type", "target_id", "operation", "proposed_value", "evidence_refs", "rationale", "confidence"],
                            "properties": {
                                "target_type": {"enum": ["page", "spread", "block", "cell", "table", "figure", "section"]},
                                "target_id": {"type": "string"},
                                "operation": {
                                    "enum": [
                                        "confirm", "confirm_spread", "reject_spread", "link_horizontal_continuation",
                                        "replace_block_text", "replace_cell_text", "set_table_grid",
                                        "insert_table_row", "set_bbox", "set_printed_page_label", "set_visual_type",
                                        "set_caption", "link_continuation", "merge_blocks", "split_block", "add_quality_flags",
                                        "retire_table_candidate", "add_visual_text_block", "set_figure_legend_text",
                                        "upsert_figure_structure",
                                    ]
                                },
                                "field_path": {"type": ["string", "null"]},
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
    def _unique_scopes(scopes: list[ReviewScopeItem]) -> list[ReviewScopeItem]:
        by_target: dict[str, ReviewScopeItem] = {}
        for scope in scopes:
            existing = by_target.get(scope.target_id)
            if existing is None:
                by_target[scope.target_id] = scope.model_copy(deep=True)
                continue
            existing.reason_codes = list(
                dict.fromkeys([*existing.reason_codes, *scope.reason_codes])
            )
            existing.blocking = existing.blocking or scope.blocking
            existing.bbox = existing.bbox or scope.bbox
        return list(by_target.values())

    @staticmethod
    def _add_flag(flags, flag):
        if flag not in flags:
            flags.append(flag)

    @staticmethod
    def _material_data_visual(figure, page, related_blocks) -> bool:
        area_ratio = bbox_area_ratio(
            figure.bbox,
            page_width=page.width if page else None,
            page_height=page.height if page else None,
        )
        if area_ratio < 0.03:
            return False
        if figure.visual_type == "chart":
            return True
        text = " ".join(
            [figure.caption or "", *figure.legend_text, *(block.text for block in related_blocks)]
        )
        has_data_signal = any(character.isdigit() for character in text) or any(
            token in text.lower()
            for token in ("%", "kg", "tco2", "mwh", "kwh", "比例", "總量", "总量", "年度")
        )
        return figure.visual_type == "composite" and area_ratio >= 0.08 and has_data_signal

    @staticmethod
    def _task_type(scope):
        if scope is None:
            return "page_compound_review"
        return {
            "page": "ocr_native_conflict_review",
            "table": "table_structure_review",
            "figure": "figure_chart_review",
            "spread": "horizontal_spread_review",
        }.get(scope.target_type, "low_confidence_region_review")

    @staticmethod
    def _native_text_recall(native_text: str, ocr_text: str) -> float:
        return source_character_recall(native_text, ocr_text)
