from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from esg_v2.document.contracts import DocumentIR, FigureIR, ReviewPlan, VlmReviewTask
from esg_v2.document.geometry import bbox_containment
from esg_v2.document.operation_registry import OperationRegistry


@dataclass(frozen=True)
class PlanContractIssue:
    code: str
    message: str
    target_ids: tuple[str, ...] = ()
    recommended_operation: str | None = None


class ReviewPlanContractError(ValueError):
    def __init__(self, issues: list[PlanContractIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in issues))


class ReviewPlanCompiler:
    """Compiles the final routed scope into one stable, executable review contract."""

    VERSION = "review-plan-v2"
    PAGE_TEXT_REASONS = {
        "native_text_present_ocr_weak",
        "native_ocr_text_length_divergence",
        "low_ocr_text_coverage",
        "low_ocr_text_coverage_on_data_visual",
    }
    TABLE_CLASSIFICATION_REASONS = {"local_only_table_candidate", "blank_visual_encoding_cells"}
    FIGURE_BINDING_REASONS = {"material_visual_binding_unresolved"}
    FIGURE_STRUCTURE_REASONS = {
        "material_chart_structure",
        "material_composite_data_visual",
        "page_text_coverage_support",
    }

    def compile(
        self,
        document: DocumentIR,
        task: VlmReviewTask,
        *,
        extra_context_target_ids: list[str] | None = None,
        extra_mutable_target_ids: list[str] | None = None,
    ) -> VlmReviewTask:
        if task.review_plan is not None:
            raise ReviewPlanContractError([
                PlanContractIssue(
                    code="review_plan_already_compiled",
                    message=f"Task {task.task_id} already has a compiled plan; recompilation is forbidden.",
                )
            ])

        reasons = set(task.reason_codes)
        reasons.update(reason for scope in task.scope for reason in scope.reason_codes)
        target_types = {scope.target_type for scope in task.scope} or {task.target_type}
        definition = self._definition(document, task, reasons, target_types)
        context_targets = self._unique([
            *definition[5],
            *(extra_context_target_ids or []),
        ])

        mutable_context_targets: list[str] = []
        if definition[0] == "page_text_coverage":
            mutable_context_targets = list(context_targets)
        elif definition[0] in {"figure_binding", "figure_semantic_structure"}:
            block_ids = {block.block_id for block in document.blocks}
            mutable_context_targets = [target_id for target_id in context_targets if target_id in block_ids]

        spread_targets: list[str] = []
        if definition[0] == "horizontal_page_spread":
            spread = next((item for item in document.spreads if item.spread_id == task.target_id), None)
            if spread:
                spread_targets = [*spread.page_ids, *spread.member_entity_ids]

        mutable_targets = self._unique([
            task.target_id,
            *(scope.target_id for scope in task.scope),
            *mutable_context_targets,
            *spread_targets,
            *(extra_mutable_target_ids or []),
        ])
        required_targets = self._unique([
            scope.target_id for scope in task.scope if scope.blocking
        ])
        if task.blocking and not required_targets:
            required_targets = [task.target_id]
        if task.blocking and definition[0] in {"horizontal_page_spread", "page_text_coverage"}:
            task.max_attempts = 3

        allowed_operations = OperationRegistry.for_review_kind(definition[0])
        if definition[0] == "figure_semantic_structure":
            figure = self._task_figure(document, task)
            chart_task = "material_chart_structure" in reasons or (
                figure is not None and figure.visual_type == "chart"
            )
            if chart_task:
                allowed_operations = [
                    operation for operation in allowed_operations
                    if operation != "upsert_figure_structure"
                ]
            elif figure is not None and figure.visual_type not in {"unknown", "chart", "composite"}:
                allowed_operations = [
                    operation for operation in allowed_operations
                    if operation != "upsert_chart_spec"
                ]
        plan = ReviewPlan(
            compiler_version=self.VERSION,
            review_kind=definition[0],
            question=definition[1],
            current_risk=definition[2],
            evidence_checklist=definition[3],
            allowed_operations=allowed_operations,
            automation_strategy=definition[4],
            context_target_ids=context_targets,
            mutable_target_ids=mutable_targets,
            required_decision_target_ids=required_targets,
        )
        plan.contract_hash = self._hash(plan)
        task.review_plan = plan
        task.prompt_intent = plan.question
        self.validate(document, task)
        self._apply_operation_schema(task)
        return task

    def validate(self, document: DocumentIR, task: VlmReviewTask) -> None:
        plan = task.review_plan
        if plan is None:
            raise ReviewPlanContractError([
                PlanContractIssue("review_plan_missing", f"Task {task.task_id} has no compiled review plan.")
            ])
        issues: list[PlanContractIssue] = []
        expected_hash = self._hash(plan)
        if plan.compiler_version != self.VERSION or plan.contract_hash != expected_hash:
            issues.append(PlanContractIssue(
                "review_plan_hash_mismatch",
                "The compiled review plan was changed after compilation.",
            ))
        mutable = set(plan.mutable_target_ids)
        missing_required = set(plan.required_decision_target_ids) - mutable
        if missing_required:
            issues.append(PlanContractIssue(
                "required_target_not_mutable",
                "Required decision targets are outside the writable scope.",
                tuple(sorted(missing_required)),
            ))
        for operation in plan.allowed_operations:
            try:
                OperationRegistry.get(operation)
            except ValueError as exc:
                issues.append(PlanContractIssue("unknown_operation", str(exc)))

        if plan.review_kind == "horizontal_page_spread":
            table_ids = {
                table.table_id
                for table in document.tables
                if table.table_id in set(plan.context_target_ids) | {
                    scope.target_id for scope in task.scope if scope.target_type == "table"
                }
            }
            missing_tables = table_ids - mutable
            if "set_table_grid" in plan.allowed_operations and missing_tables:
                issues.append(PlanContractIssue(
                    "spread_table_repair_not_writable",
                    "The plan allows table repair but participating spread tables are not writable.",
                    tuple(sorted(missing_tables)),
                    "set_table_grid",
                ))
            missing_scopes = {scope.target_id for scope in task.scope} - mutable
            if missing_scopes:
                issues.append(PlanContractIssue(
                    "spread_scope_not_writable",
                    "Spread confirmation and its routed child repairs must share one writable transaction scope.",
                    tuple(sorted(missing_scopes)),
                ))
        if issues:
            raise ReviewPlanContractError(issues)

    @classmethod
    def _hash(cls, plan: ReviewPlan) -> str:
        payload = plan.model_dump(mode="json", exclude={"contract_hash"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _apply_operation_schema(task: VlmReviewTask) -> None:
        operation_schema = (
            task.expected_schema.get("properties", {})
            .get("patches", {})
            .get("items", {})
            .get("properties", {})
            .get("operation")
        )
        if isinstance(operation_schema, dict) and task.review_plan:
            operation_schema["enum"] = list(task.review_plan.allowed_operations)

    def _definition(self, document, task, reasons, target_types):
        if "spread" in target_types:
            return (
                "horizontal_page_spread",
                "这两页是否应左右拼成一个逻辑版面？拼接后，跨缝结构和可见文字是否已被忠实表示？",
                "装订缝两侧存在连续内容。若仍按单页复审，同一张表、图或版面可能被误拆。",
                [
                    "先看拼接图，再看左右单页，判断横向连续关系。",
                    "确认左右顺序并核对跨缝表格、标题、数字、图示和正文。",
                    "确认 Spread、结构修复和横向链接必须作为一个事务提交。",
                    "只修可定位子对象，不改写物理页边界。",
                ],
                "classify_and_link",
                self._spread_context(document, task),
            )
        if "table" in target_types and reasons & self.TABLE_CLASSIFICATION_REASONS:
            return (
                "table_candidate_classification",
                "这个区域究竟是真表格、信息图/图表、重复碎片，还是纯装饰？",
                "稀疏网格可能是传统解析器产生的假表格。",
                ["先分类再重建。", "非表格候选必须退役。", "真实表格须保留全部可见文字和数字。"],
                "classify_then_reconstruct",
                [],
            )
        if "table" in target_types:
            return (
                "table_structure_reconstruction",
                "当前表格的行列、合并单元格、表头和文字是否与原页一致？",
                "表格结构、视觉表头或 OCR 与本地解析结果不一致。",
                [
                    "保留每个正确源单元格并提交 source_cell_ids 映射。",
                    "列出候选中缺失的可见文字并给出视觉证据。",
                    "涉及重排、合并或删除时提交完整最终网格。",
                ],
                "review_verify_repair",
                [],
            )
        if reasons & self.FIGURE_BINDING_REASONS and not reasons & self.PAGE_TEXT_REASONS:
            figure = self._task_figure(document, task)
            return (
                "figure_binding",
                "当前视觉区域的边界、标题和组成部件是否属于同一个对象？",
                "相邻图片、标题或文字的语义归属尚未确定。",
                ["确认是否为一个完整视觉对象。", "绑定已有文字块，不重复创建。"],
                "optional_enrichment",
                self._figure_context_targets(document, figure),
            )
        if "figure" in target_types or reasons & self.FIGURE_STRUCTURE_REASONS:
            figure = self._task_figure(document, task)
            return (
                "figure_semantic_structure",
                "图表或信息图中的类型、图例、关系和可见文字是否被忠实建模？",
                "重要视觉信息尚未形成可检索的 Document IR 语义。",
                [
                    "图表优先返回紧凑 ChartSpec。",
                    "核对标题、类别、序列、数值、单位和图例。",
                    "只记录页面可见内容，无法判断时 abstain。",
                ],
                "review_verify_repair",
                self._figure_context_targets(document, figure),
            )
        if reasons & self.PAGE_TEXT_REASONS:
            return (
                "page_text_coverage",
                "页面中可见的重要文字、数字和图内文本，是否都已经进入 Document IR？",
                "原生 PDF 文字与 OCR 可见文字覆盖异常。",
                ["核对标题、正文、数字、年份、代码和脚注。", "使用局部可定位修订，不整页重写。"],
                "review_verify_repair",
                [
                    *(block.block_id for block in document.blocks if block.page_index == task.page_index),
                    *(table.table_id for table in document.tables if table.page_index == task.page_index),
                    *(figure.figure_id for figure in document.figures if figure.page_index == task.page_index),
                ],
            )
        return (
            "generic_document_ir_review",
            "当前候选是否忠实表示页面中实际可见的文档结构？",
            "质量路由发现了无法由确定性规则排除的结构风险。",
            ["只判断当前作用域。", "修订必须局部、可定位、可溯源。", "证据不足时 abstain。"],
            "review_verify_repair",
            [],
        )

    @staticmethod
    def _spread_context(document: DocumentIR, task: VlmReviewTask) -> list[str]:
        spread = next((item for item in document.spreads if item.spread_id == task.target_id), None)
        return list(spread.member_entity_ids) if spread else []

    @staticmethod
    def _task_figure(document: DocumentIR, task: VlmReviewTask) -> FigureIR | None:
        ids = [task.target_id, *(scope.target_id for scope in task.scope if scope.target_type == "figure")]
        return next((figure for figure in document.figures if figure.figure_id in ids), None)

    @staticmethod
    def _figure_context_targets(document: DocumentIR, figure: FigureIR | None, *, limit: int = 16) -> list[str]:
        if figure is None:
            return []
        page = next((item for item in document.pages if item.page_index == figure.page_index), None)
        candidates: list[tuple[int, int, str]] = []
        for block in document.blocks:
            if block.page_index != figure.page_index:
                continue
            score = None
            if block.figure_id == figure.figure_id or block.block_id in figure.element_block_ids:
                score = 0
            elif block.bbox is not None and figure.bbox is not None:
                if bbox_containment(block.bbox, figure.bbox) >= 0.25:
                    score = 1
                else:
                    overlap = max(0.0, min(block.bbox.x1, figure.bbox.x1) - max(block.bbox.x0, figure.bbox.x0))
                    width = max(1.0, block.bbox.x1 - block.bbox.x0)
                    gap = max(0.0, block.bbox.y0 - figure.bbox.y1, figure.bbox.y0 - block.bbox.y1)
                    if overlap / width >= 0.30 and gap <= 60:
                        score = 2
            if score is not None:
                candidates.append((score, block.order, block.block_id))
        nearby_table_ids = [
            table.table_id
            for table in document.tables
            if table.page_index == figure.page_index
            and table.bbox is not None
            and figure.bbox is not None
            and abs(table.bbox.y0 - figure.bbox.y1) <= 80
        ][:3]
        return ReviewPlanCompiler._unique([
            *([page.page_id] if page else []),
            *nearby_table_ids,
            *(block_id for _, _, block_id in sorted(candidates)[:limit]),
        ])

    @staticmethod
    def _unique(values) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))
