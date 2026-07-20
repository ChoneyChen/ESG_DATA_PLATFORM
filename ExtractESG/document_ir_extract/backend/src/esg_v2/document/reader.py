from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
        return validate_package(
            self.root,
            expected_type="document-ir-revision",
            expected_schema="document-ir-package-v1",
            required_entrypoints={
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
            },
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
        payload["figures"] = [self.figure(item) for item in figure_ids]
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

    def review_collection(self, name: str) -> list[dict[str, Any]]:
        if not self.is_package_v1:
            legacy = {
                "model_calls": "model_calls.json",
                "reviewer_results": "reviewer_results.json",
                "guard_results": "guard_results.json",
                "verifier_results": "verifier_results.json",
                "atomic_patches": "atomic_patches.json",
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
