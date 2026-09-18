from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from esg_v2.document.convergence_engine import ConvergenceEngine
from esg_v2.document.contracts import DocumentIR
from esg_v2.storage.package_layout import package_dir, read_jsonl, resolve_package_path
from esg_v2.storage.package_validator import PackageValidationResult, validate_package


class DocumentIrPackageReader:
    def __init__(self, root: Path, *, ocr_output_root: Path | None = None):
        self.root = root
        self.ocr_output_root = ocr_output_root
        self.manifest_path = root / "manifest.json"
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Document IR manifest not found: {self.manifest_path}")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.is_package_v1 = self.manifest.get("package_schema_version") == "document-ir-package-v1"

    def validate_integrity(self) -> PackageValidationResult:
        if not self.is_package_v1:
            raise ValueError("Package integrity validation is available only for document-ir-package-v1")
        required_entrypoints = {
            "canonical_document",
            "pages",
            "tables",
            "figures",
            "structure_edges",
            "coordinate_systems",
            "local_forensics",
            "artifacts",
            "quality_report",
            "validation_report",
            "review",
            "integrity",
            "snapshot_export",
        }
        if self.manifest.get("schema_version") in {
            "document-ir-v0.6",
            "document-ir-v0.7",
            "document-ir-v0.8",
            "document-ir-v0.9",
            "document-ir-v0.10",
            "document-ir-v0.11",
            "document-ir-v0.12",
        }:
            required_entrypoints.add("spreads")
        if self.manifest.get("schema_version") in {
            "document-ir-v0.7",
            "document-ir-v0.8",
            "document-ir-v0.9",
            "document-ir-v0.10",
            "document-ir-v0.11",
            "document-ir-v0.12",
        }:
            required_entrypoints.add("logical_tables")
        return validate_package(
            self.root,
            expected_type="document-ir-revision",
            expected_schema="document-ir-package-v1",
            required_entrypoints=required_entrypoints,
        )

    def entrypoint(self, key: str, legacy: str | None = None) -> Path:
        entrypoints = self.manifest.get("entrypoints") or {}
        relative = entrypoints.get(key) if isinstance(entrypoints, dict) else None
        if relative:
            return resolve_package_path(self.root, str(relative))
        if legacy:
            return resolve_package_path(self.root, legacy)
        raise KeyError(f"Document IR entrypoint not declared: {key}")

    def read_json(self, key: str, legacy: str | None = None) -> Any:
        path = self.entrypoint(key, legacy)
        if not path.exists():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def read_jsonl(self, relative: str) -> list[Any]:
        return read_jsonl(resolve_package_path(self.root, relative))

    def load_document(self, *, hydrate_local_paths: bool = False) -> DocumentIR:
        path = self.entrypoint("snapshot_export", "document_ir.json")
        document = DocumentIR.model_validate_json(path.read_text(encoding="utf-8"))
        if hydrate_local_paths:
            self._hydrate_local_paths(document)
        return document

    def list_pages(self) -> list[dict[str, Any]]:
        if not self.is_package_v1:
            return [item.model_dump(mode="json") for item in self.load_document().pages]
        return list(self.read_json("pages").get("pages") or [])

    def page(self, page_index: int) -> dict[str, Any]:
        if page_index < 0:
            raise ValueError("page_index must be non-negative")
        if not self.is_package_v1:
            return self._read_legacy_json(f"pages/page_{page_index + 1:04d}.json")
        row = next((item for item in self.list_pages() if item.get("page_index") == page_index), None)
        if not row:
            raise FileNotFoundError(f"Page not found: {page_index}")
        payload = self._read_relative(str(row["path"]))
        layout_path = self.root / "observations" / "paddle-layout" / f"page-{page_index + 1:04d}.json"
        layout_payload = json.loads(layout_path.read_text(encoding="utf-8")) if layout_path.exists() else {}
        table_ids = set(payload.get("table_ids") or [])
        figure_ids = set(payload.get("figure_ids") or [])
        edge_ids = set(payload.get("structure_edge_ids") or [])
        payload["layout_objects"] = layout_payload.get("layout_objects") or []
        payload["tables"] = [self.table(item) for item in table_ids]
        payload["logical_tables"] = [
            self.logical_table(item)
            for item in payload.get("logical_table_ids") or []
        ]
        payload["figures"] = [self.figure(item) for item in figure_ids]
        payload["spreads"] = [self.spread(item) for item in payload.get("spread_ids") or []]
        edge_path = self.entrypoint("structure_edges")
        payload["structure_edges"] = [item for item in read_jsonl(edge_path) if item.get("edge_id") in edge_ids]
        return payload

    def list_tables(self) -> list[dict[str, Any]]:
        if not self.is_package_v1:
            return [item.model_dump(mode="json") for item in self.load_document().tables]
        return list(self.read_json("tables").get("tables") or [])

    def table(self, table_id: str) -> dict[str, Any]:
        if not self.is_package_v1:
            return self._read_legacy_json(f"tables/{Path(table_id).name}.json")
        row = next((item for item in self.list_tables() if item.get("table_id") == table_id), None)
        if not row:
            raise FileNotFoundError(f"Table not found: {table_id}")
        return self._read_relative(str(row["path"]))

    def list_logical_tables(self) -> list[dict[str, Any]]:
        if not self.is_package_v1:
            return [item.model_dump(mode="json") for item in self.load_document().logical_tables]
        if "logical_tables" not in (self.manifest.get("entrypoints") or {}):
            return []
        return list(self.read_json("logical_tables").get("logical_tables") or [])

    def logical_table(self, logical_table_id: str) -> dict[str, Any]:
        if not self.is_package_v1:
            return next(
                item.model_dump(mode="json")
                for item in self.load_document().logical_tables
                if item.logical_table_id == logical_table_id
            )
        row = next(
            (
                item
                for item in self.list_logical_tables()
                if item.get("logical_table_id") == logical_table_id
            ),
            None,
        )
        if not row:
            raise FileNotFoundError(f"Logical table not found: {logical_table_id}")
        return self._read_relative(str(row["path"]))

    def list_figures(self) -> list[dict[str, Any]]:
        if not self.is_package_v1:
            return [item.model_dump(mode="json") for item in self.load_document().figures]
        return list(self.read_json("figures").get("figures") or [])

    def figure(self, figure_id: str) -> dict[str, Any]:
        if not self.is_package_v1:
            return self._read_legacy_json(f"figures/{Path(figure_id).name}.json")
        row = next((item for item in self.list_figures() if item.get("figure_id") == figure_id), None)
        if not row:
            raise FileNotFoundError(f"Figure not found: {figure_id}")
        return self._read_relative(str(row["path"]))

    def list_spreads(self) -> list[dict[str, Any]]:
        if not self.is_package_v1:
            return [item.model_dump(mode="json") for item in self.load_document().spreads]
        if "spreads" not in (self.manifest.get("entrypoints") or {}):
            return []
        return list(self.read_json("spreads").get("spreads") or [])

    def spread(self, spread_id: str) -> dict[str, Any]:
        if not self.is_package_v1:
            return next(
                item.model_dump(mode="json")
                for item in self.load_document().spreads
                if item.spread_id == spread_id
            )
        row = next((item for item in self.list_spreads() if item.get("spread_id") == spread_id), None)
        if not row:
            raise FileNotFoundError(f"Spread not found: {spread_id}")
        return self._read_relative(str(row["path"]))

    def review_tasks(self) -> list[dict[str, Any]]:
        if not self.is_package_v1:
            return self._read_legacy_json("review_tasks.json")
        index = self.read_json("review")
        return [self._read_relative(str(row["path"]))["task"] for row in index.get("tasks") or []]

    def review_task_bundle(self, task_id: str) -> dict[str, Any]:
        if not self.is_package_v1:
            return self._read_legacy_json(f"reviews/{Path(task_id).name}.json")
        index = self.read_json("review")
        row = next((item for item in index.get("tasks") or [] if item.get("task_id") == task_id), None)
        if not row:
            raise FileNotFoundError(f"Review task not found: {task_id}")
        task_file = self._read_relative(str(row["path"]))
        related = task_file.get("related_ids") or {}
        return {
            "task": task_file.get("task"),
            "model_calls": self._filter(self.review_collection("model_calls"), "call_id", related.get("model_call_ids")),
            "reviewer_results": self._filter(
                self.review_collection("reviewer_results"), "reviewer_result_id", related.get("reviewer_result_ids")
            ),
            "patches": self._filter(self.review_collection("atomic_patches"), "patch_id", related.get("patch_ids")),
            "patch_transactions": self._filter(
                self.review_collection("patch_transactions"),
                "transaction_id",
                related.get("transaction_ids"),
            ),
            "guard_results": self._filter(
                self.review_collection("guard_results"), "guard_result_id", related.get("guard_result_ids")
            ),
            "verifier_results": self._filter(
                self.review_collection("verifier_results"), "verifier_result_id", related.get("verifier_result_ids")
            ),
            "candidate_revisions": self._candidate_rows(related.get("candidate_ids") or []),
            "final_decision": next(
                (
                    item
                    for item in self.review_collection("final_decisions")
                    if item.get("decision_id") == related.get("final_decision_id")
                ),
                None,
            ),
        }

    def human_review_inbox(self) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {
            "human_required": [],
            "system_blocked": [],
            "blocking_deferred": [],
            "optional_deferred": [],
            "resolved": [],
        }
        for task in self.review_tasks():
            group = self._review_group(task)
            bundle = self.review_task_bundle(str(task["task_id"]))
            bundle["guidance"] = self._review_guidance(bundle)
            bundle["visual_context"] = self._review_visual_context(task, bundle)
            groups[group].append(bundle)
        validation_issues = self._unresolved_validation_issues()
        return {
            "schema_version": "document-ir-human-review-inbox-v1",
            "counts": {
                **{key: len(value) for key, value in groups.items()},
                "validation_blocked": len(validation_issues),
            },
            "groups": groups,
            "validation_issues": validation_issues,
        }

    def unresolved_review_worklist(self) -> dict[str, Any]:
        """Return compact current work without loading resolved visual/audit bundles."""

        entries: list[dict[str, Any]] = []
        counts = {
            "human_required": 0,
            "system_blocked": 0,
            "blocking_deferred": 0,
            "optional_deferred": 0,
        }
        for task in self.review_tasks():
            group = self._review_group(task)
            if group == "resolved":
                continue
            bundle = self.review_task_bundle(str(task["task_id"]))
            entries.append(
                {
                    "group": group,
                    "bundle": {
                        "task": bundle.get("task") or task,
                        "guidance": self._review_guidance(bundle),
                    },
                }
            )
            counts[group] += 1
        validation_issues = self._unresolved_validation_issues()
        counts["validation_blocked"] = len(validation_issues)
        return {
            "schema_version": "document-ir-review-worklist-v1",
            "counts": counts,
            "entries": entries,
            "validation_issues": validation_issues,
        }

    def _unresolved_validation_issues(self) -> list[dict[str, Any]]:
        report = self.read_json("validation_report", "quality/validation-report.json")
        open_task_targets = {
            task.get("target_id")
            for task in self.review_tasks()
            if self._review_group(task) != "resolved"
        }
        result: list[dict[str, Any]] = []
        for issue in report.get("issues") or []:
            if issue.get("severity") not in {"error", "blocking"}:
                continue
            if issue.get("code") == "table_geometry_incomplete" and not issue.get("target_id"):
                for row in self.list_tables():
                    table = self.table(str(row["table_id"]))
                    if table.get("bbox") or table.get("table_id") in open_task_targets:
                        continue
                    result.append({
                        "code": "table_geometry_incomplete",
                        "severity": "error",
                        "message": "表格缺少可靠页面坐标；请先核对它是否为独立表格。",
                        "target_id": table.get("table_id"),
                        "page_index": table.get("page_index"),
                        "action": "repair",
                    })
                continue
            if not issue.get("target_id") and open_task_targets:
                continue
            if issue.get("target_id") in open_task_targets:
                continue
            result.append({**issue, "action": "repair" if issue.get("target_id") else "inspect"})
        return result

    @staticmethod
    def _review_group(task: dict[str, Any]) -> str:
        status = str(task.get("status") or "pending")
        blocking = bool(task.get("blocking"))
        if status == "human_required":
            return "human_required"
        if status in {"pending", "queued", "running", "deferred", "failed", "skipped"}:
            if not DocumentIrPackageReader._can_retry(task):
                return "system_blocked"
            return "blocking_deferred" if blocking else "optional_deferred"
        return "resolved"

    @staticmethod
    def _can_retry(task: dict[str, Any]) -> bool:
        if bool(task.get("retryable", True)):
            return True
        return (
            task.get("failure_class") == "system_contract"
            and (task.get("result") or {}).get("reason")
            == "required_review_targets_incomplete_after_guard_confirmation"
        )

    def review_collection(self, name: str) -> list[dict[str, Any]]:
        if not self.is_package_v1:
            legacy = {
                "model_calls": "model_calls.json",
                "reviewer_results": "reviewer_results.json",
                "guard_results": "guard_results.json",
                "verifier_results": "verifier_results.json",
                "atomic_patches": "atomic_patches.json",
                "patch_transactions": "patch_transactions.json",
                "correction_patches": "correction_patches.json",
                "final_decisions": "final_decisions.json",
                "conflict_groups": "conflict_groups.json",
                "candidate_revisions": "candidate_revisions.json",
            }
            return self._read_legacy_json(legacy[name])
        index = self.read_json("review")
        collections = index.get("collections") or {}
        if name == "candidate_revisions":
            return self._candidate_rows([item.get("candidate_id") for item in index.get("candidates") or []])
        relative = collections.get(name)
        if not relative:
            return []
        return self.read_jsonl(str(relative))

    def _candidate_rows(self, candidate_ids: list[str]) -> list[dict[str, Any]]:
        index = self.read_json("review")
        wanted = set(candidate_ids)
        return [
            self._read_relative(str(row["path"]))
            for row in index.get("candidates") or []
            if row.get("candidate_id") in wanted
        ]

    def _read_relative(self, relative: str) -> Any:
        path = resolve_package_path(self.root, relative)
        if not path.exists():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_legacy_json(self, relative: str) -> Any:
        return self._read_relative(relative)

    @staticmethod
    def _filter(rows: list[dict[str, Any]], key: str, values: list[str] | None) -> list[dict[str, Any]]:
        wanted = set(values or [])
        return [row for row in rows if row.get(key) in wanted]

    def _hydrate_local_paths(self, document: DocumentIR) -> None:
        def hydrate(value: str | None) -> str | None:
            if not value:
                return value
            if value.startswith("ocr-package://") and self.ocr_output_root:
                suffix = value.removeprefix("ocr-package://")
                run_id, _, relative = suffix.partition("/")
                return str(resolve_package_path(package_dir(self.ocr_output_root, run_id), relative))
            if "://" in value:
                return value
            if Path(value).is_absolute():
                if self.is_package_v1:
                    raise ValueError(f"Package v1 contains a non-portable absolute path: {value}")
                return value
            return str(resolve_package_path(self.root, value))

        for artifact in document.artifacts:
            artifact.path = hydrate(artifact.path) or artifact.path
        for page in document.pages:
            page.markdown_path = hydrate(page.markdown_path)
            page.page_image_path = hydrate(page.page_image_path)
            page.image_paths = [hydrate(item) or item for item in page.image_paths]
            page.output_image_paths = [hydrate(item) or item for item in page.output_image_paths]
        for figure in document.figures:
            figure.image_path = hydrate(figure.image_path)
        if document.metadata.local_forensics:
            for page in document.metadata.local_forensics.pages:
                page.page_image_path = hydrate(page.page_image_path)
        for task in document.review_tasks:
            task.input_refs = [hydrate(item) or item for item in task.input_refs]

    def _review_visual_context(self, task: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
        page_index = int(task.get("page_index") or 0)
        try:
            page_payload = self.page(page_index)
        except FileNotFoundError:
            page_payload = {}
        page = page_payload.get("page") or {}
        refs = list(task.get("input_refs") or [])
        spread = None
        if task.get("target_type") == "spread":
            try:
                spread = self.spread(str(task.get("target_id")))
            except (FileNotFoundError, StopIteration):
                spread = None
        member_pages = []
        for member_index in (spread or {}).get("page_indices") or []:
            try:
                member = self.page(int(member_index)).get("page") or {}
            except FileNotFoundError:
                continue
            if member.get("page_image_path"):
                member_pages.append(
                    {
                        "page_index": member_index,
                        "page_number": member.get("page_number", int(member_index) + 1),
                        "printed_page_label": member.get("printed_page_label"),
                        "page_image_path": member.get("page_image_path"),
                    }
                )
        return {
            "page_index": page_index,
            "page_number": page.get("page_number", page_index + 1),
            "printed_page_label": page.get("printed_page_label"),
            "page_image_path": page.get("page_image_path"),
            "crop_refs": [ref for ref in refs if "/crops/" in ref or "crop" in Path(ref).name.lower()],
            "spread_refs": [ref for ref in refs if "/spreads/" in ref or "spread-" in Path(ref).name.lower()],
            "member_pages": member_pages,
            "spread": spread,
            "input_refs": refs,
            "target_ids": [
                task.get("target_id"),
                *(scope.get("target_id") for scope in task.get("scope") or []),
            ],
        }

    @staticmethod
    def _review_guidance(bundle: dict[str, Any]) -> dict[str, Any]:
        task = bundle.get("task") or {}
        plan = task.get("review_plan") or {}
        reason_codes = list(dict.fromkeys([*(task.get("reason_codes") or []), *(
            code
            for scope in task.get("scope") or []
            for code in scope.get("reason_codes") or []
        )]))
        diagnoses = {
            "local_only_table_candidate": "本地解析发现了 OCR 主结构之外的候选，需要确认它是真表格、非表格视觉还是重复碎片。",
            "blank_visual_encoding_cells": "候选网格很稀疏，空白、颜色或图形可能并不代表真实单元格。",
            "empty_or_unparsed_table": "OCR 标记了表格区域，但没有形成可用的行列结构。",
            "table_geometry_missing": "表格对象存在，但缺少可靠的页面坐标或单元格定位。",
            "local_table_ocr_structure_conflict": "OCR 与本地 PDF 解析对表格行列结构判断不一致。",
            "local_table_dimension_conflict": "两个解析来源给出的表格行列数量不一致。",
            "probable_missing_visual_header_row": "页面上可能有视觉表头尚未进入当前表格网格。",
            "material_visual_binding_unresolved": "视觉对象与相邻标题、图例或正文的归属关系不够可靠。",
            "material_chart_structure": "重要图表中的图例、标签或结构仍需核对。",
            "material_composite_data_visual": "复合信息图包含多个视觉部件，当前结构可能不完整。",
            "native_text_present_ocr_weak": "原生 PDF 含有较多文字，而 OCR 结果明显偏少，可能漏读。",
            "native_ocr_text_length_divergence": "OCR 比可靠原生文字明显更短且覆盖率偏低。",
            "embedded_document_preview_native_text_excluded_from_coverage": "页面含有嵌入式文档缩略图；其微缩文字仍保留在原始证据中，但不参与本页正文覆盖率比较。",
            "embedded_document_preview_candidate": "该小型视觉对象包含密集微缩文字，当前按嵌入式文档预览候选处理。",
            "low_ocr_text_coverage": "页面可见文字进入 Document IR 的覆盖率不足。",
            "low_ocr_text_coverage_on_data_visual": "数据视觉页中的可见文字或数字覆盖不足。",
            "possible_horizontal_page_spread": "相邻两页在装订缝两侧出现连续内容，单页复审会把同一逻辑页面拆开。",
            "adjacent_pages_touch_opposite_binding_edges": "左页内容触达右边界、右页内容触达左边界，存在左右拼接关系。",
            "cross_seam_vertical_alignment": "装订缝两侧对象在垂直方向高度对齐，像是同一个横向结构。",
            "printed_page_pair_consistent": "报告印刷页码符合偶数左页、相邻奇数右页的跨页版式。",
            "cross_seam_visual_structure": "表格、图片或图示在两页装订缝处连续。",
            "pixel_seam_continuity": "左右页靠近装订缝的有效像素在相同高度连续，排除了仅靠版面框误判的多数情况。",
            "printed_page_label_uncertain": "物理页序与报告印刷页码之间的对应关系不够可靠。",
            "ocr_typo": "文字可能存在 OCR 误识别，需要与页面图核对。",
            "review_task_budget_deferred": "本轮自动审核额度已用完，任务尚未执行，不代表内容一定有错。",
            "automation_proposal_invalid_after_repair": "模型连续给出了不满足本地安全约束的补丁，系统已停止自动尝试并等待重新规划。",
            "deterministic_preflight_guard_failed": "确定性预处理建议未通过本地安全校验，系统没有修改 IR。",
            "no_visual_input_available": "任务缺少可供视觉模型核对的页面图或区域截图。",
            "outside_targeted_review_scope": "本轮只处理指定目标，该任务被保留到后续自动批次。",
        }
        failed_checks = [
            check
            for guard in bundle.get("guard_results") or []
            for check in guard.get("checks") or []
            if not check.get("passed")
        ]
        disagreements = [
            item
            for verifier in bundle.get("verifier_results") or []
            for item in verifier.get("disagreements") or []
        ]
        status = str(task.get("status") or "")
        result = task.get("result") or {}
        final_decision = bundle.get("final_decision") or {}
        terminal_reason = str(result.get("reason") or final_decision.get("reason") or "")
        resume_stage = str(task.get("resume_stage") or result.get("resume_stage") or "reviewer_pending")
        provider_state = result.get("provider_state") if isinstance(result.get("provider_state"), dict) else {}
        failure_class = str(task.get("failure_class") or result.get("failure_class") or "none")
        failure_owner = str(task.get("failure_owner") or result.get("failure_owner") or "none")
        retryable = DocumentIrPackageReader._can_retry(task)
        stored_failure_class = failure_class
        if failure_class == "model_service" and terminal_reason:
            derived_failure = ConvergenceEngine.reason_failure(terminal_reason)
            if derived_failure["failure_class"] != "model_service":
                failure_class = derived_failure["failure_class"]
                failure_owner = derived_failure["failure_owner"]
                retryable = derived_failure["retryable"]
        adapter_contract_mismatch = any(
            bool((check.get("details") or {}).get("adapter_contract_mismatch"))
            for check in failed_checks
        )
        if status == "human_required":
            failure_category = "semantic_decision"
        elif failure_class != "none":
            failure_category = failure_class
        elif terminal_reason.startswith("review_task_budget_deferred"):
            failure_category = "automation_budget"
        elif terminal_reason.startswith(
            ("all_", "no_healthy_", "model_catalog_unavailable", "qiniu_account_rate_limited", "agent_review_internal_error")
        ):
            failure_category = "temporary_model_service"
        elif terminal_reason.startswith(("automation_proposal_invalid", "deterministic_preflight_guard_failed")):
            failure_category = "automation_replan"
        else:
            failure_category = None

        patches = bundle.get("patches") or []
        human_patches = [patch for patch in patches if patch.get("status") == "human_required"]
        passed_patch_ids = {
            patch_id
            for guard in bundle.get("guard_results") or []
            if guard.get("passed")
            for patch_id in guard.get("patch_ids") or []
        }
        safe_patch_ids = [
            patch.get("patch_id")
            for patch in human_patches
            if patch.get("patch_id") in passed_patch_ids
        ]
        unsafe_patch_ids = [
            patch.get("patch_id")
            for patch in human_patches
            if patch.get("patch_id") not in passed_patch_ids
        ]

        if status == "human_required":
            if human_patches:
                action = "只判断候选补丁是否与原页一致。拒绝补丁后，还要明确保留当前 IR 或发起定向修复。"
            else:
                action = "当前没有可接受补丁。确认现有 IR 正确并写明依据，或发起一次有明确目标的定向修复。"
        elif (
            not bool(task.get("blocking"))
            and status in {"deferred", "failed", "skipped", "pending", "queued"}
        ):
            action = (
                "这是非阻塞增强。要进入已解决，可运行视觉复核并通过 Guard/Verifier；"
                "也可明确接受当前非关键状态，但这不等于确认候选关系真实存在。"
            )
        elif status in {"deferred", "failed", "skipped", "pending", "queued"}:
            if not retryable and failure_class == "evidence_missing":
                action = "不要重复调用模型。先补齐缺失的页图、拼接图或区域截图，再新建修复 revision。"
            elif not retryable and failure_class == "system_contract":
                action = (
                    "不要重复调用模型。模型已经给出可识别的图表别名；应由本地 ChartSpecNormalizer "
                    "转换后重新执行 Guard。"
                    if adapter_contract_mismatch
                    else "不要重复调用模型。先修复本地契约、作用域或原子应用错误，再重新路由该任务。"
                )
            elif not retryable and failure_class == "repeated_failure":
                action = (
                    "同一任务、同一目标和同一操作语义的 Guard 失败已连续出现；"
                    "继续点击重试不会解决。请查看具体失败条件后改变提案策略。"
                )
            elif failure_class == "model_protocol":
                action = (
                    "模型已经返回内容，但 JSON 或 Schema 尚未进入正式审核合同。系统应先执行无损"
                    "适配器修复，再继续 Schema、Guard 和 Verifier；不要把它当成模型服务宕机。"
                )
            elif terminal_reason.startswith("qiniu_account_rate_limited"):
                retry_after = int(float(provider_state.get("retry_after_seconds") or 0))
                action = (
                    "七牛账号每日 Token 限额已触发，系统已停止本轮剩余 HTTP 请求。"
                    + (f"预计约 {retry_after} 秒后再试，" if retry_after else "等待额度恢复后再试，")
                    + "或改用具有独立额度的 API Key；现在重复点击不会解决。"
                )
            elif resume_stage == "verifier_pending":
                action = (
                    "Reviewer 候选及 Patch Guard 检查点已经保留。下次自动重试会优先从独立 Verifier "
                    "继续，不会重新阅读并生成同一候选。"
                )
            else:
                action = "这是可自动重试项，不要求人工判断内容。系统会切换模型或等待服务恢复后再试。"
        else:
            action = "该任务已有结论，可查看审计轨迹，无需再次操作。"

        if status == "human_required" and disagreements:
            why_human = "主复核模型与独立验证模型对同一份可读证据仍有实质分歧。"
        elif status == "human_required" and "reviewer_abstained_after_repair" in terminal_reason:
            why_human = "证据可见，但模型在限定次数内仍无法作出可靠判断。"
        elif status == "human_required":
            why_human = "这是需要业务人员判断的语义歧义，不是模型服务或 JSON 格式故障。"
        else:
            why_human = "无需人工内容判断。"

        target_type = str(task.get("target_type") or "page")
        target_labels = {
            "page": "页面",
            "block": "文字块",
            "table": "表格候选",
            "figure": "视觉对象",
            "cell": "表格单元格",
            "section": "章节",
            "spread": "左右跨页候选",
        }
        page_indices = (bundle.get("visual_context") or {}).get("spread", {}).get("page_indices") or []
        if target_type == "spread" and len(page_indices) == 2:
            title = f"第 {page_indices[0] + 1}–{page_indices[1] + 1} 页 · 左右跨页检查"
        else:
            title = f"第 {int(task.get('page_index') or 0) + 1} 页 · {target_labels.get(target_type, target_type)}检查"
        transactions = bundle.get("patch_transactions") or []
        transaction_summary = {
            status_name: sum(1 for item in transactions if item.get("status") == status_name)
            for status_name in (
                "proposed",
                "guard_passed",
                "guard_failed",
                "verified",
                "accepted",
                "rejected",
            )
        }
        return {
            "title": title,
            "review_kind": plan.get("review_kind", "generic_document_ir_review"),
            "question": plan.get("question") or "当前候选是否忠实表示原页中实际可见的文档结构？",
            "current_risk": plan.get("current_risk") or "质量路由发现了需要核对的结构风险。",
            "evidence_checklist": plan.get("evidence_checklist") or [
                "查看完整页面和局部截图。",
                "比较当前 IR 与候选差异。",
                "只依据页面可见内容作出判断。",
            ],
            "human_boundary": plan.get("human_boundary"),
            "diagnosis": [diagnoses.get(code, code.replace("_", " ")) for code in reason_codes]
            or ["系统根据质量路由要求复核该区域。"],
            "recommended_action": action,
            "why_human": why_human,
            "failure_category": failure_category,
            "failure_class": failure_class,
            "stored_failure_class": stored_failure_class,
            "failure_owner": failure_owner,
            "failure_fingerprint": task.get("failure_fingerprint"),
            "retryable": retryable,
            "terminal_reason": terminal_reason or None,
            "failed_guard_checks": failed_checks,
            "verifier_disagreements": disagreements,
            "safe_patch_ids": safe_patch_ids,
            "unsafe_patch_ids": unsafe_patch_ids,
            "can_keep_current": status == "human_required" and not human_patches,
            "can_retry_automation": (
                retryable
                and status in {"deferred", "failed", "skipped", "pending", "queued"}
            ),
            "can_accept_current_nonmaterial": (
                not bool(task.get("blocking"))
                and status in {"deferred", "failed", "skipped", "pending", "queued"}
            ),
            "transaction_summary": transaction_summary,
            "resume_stage": resume_stage,
            "execution_count": int(task.get("execution_count") or 0),
            "reviewer_call_count": int(task.get("reviewer_call_count") or 0),
            "verifier_call_count": int(task.get("verifier_call_count") or 0),
            "last_attempt_run_id": task.get("last_attempt_run_id"),
            "last_attempt_at": task.get("last_attempt_at"),
            "provider_state": provider_state,
            "mutable_target_ids": plan.get("mutable_target_ids") or [],
            "context_target_ids": plan.get("context_target_ids") or [],
            "required_decision_target_ids": plan.get("required_decision_target_ids") or [],
            "impact": "阻塞 Evidence 阶段" if task.get("blocking") and status not in {"auto_resolved", "reviewed", "done"} else "不阻塞或已解决",
        }
