from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from esg_v2.document.contracts import DocumentIR
from esg_v2.storage.package_layout import (
    DocumentIrPackageLayout,
    relative_path,
    write_file_index,
    write_json,
    write_jsonl,
)
from esg_v2.storage.package_validator import validate_package


class DocumentIrWriter:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.layout = DocumentIrPackageLayout(output_dir)

    def write(self, document: DocumentIR) -> dict[str, Path]:
        portable = document.model_copy(deep=True)
        self._make_portable(portable)
        paths = self._write_semantic_content(portable)
        paths.update(self._write_observations(portable))
        paths.update(self._write_quality(portable))
        paths.update(self._write_review_ledger(portable))
        paths["artifact_index"] = write_json(
            self.layout.artifact_index,
            {
                "schema_version": "document-ir-artifact-index-v1",
                "artifact_count": len(portable.artifacts),
                "artifacts": [item.model_dump(mode="json") for item in portable.artifacts],
            },
        )
        paths["document_ir"] = write_json(self.layout.snapshot, portable.model_dump(mode="json"))
        paths["manifest"] = self._write_manifest(portable)
        paths["file_index"] = write_file_index(self.output_dir, self.layout.file_index)
        validate_package(
            self.output_dir,
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
        ).require_valid()
        return paths

    def _write_semantic_content(self, document: DocumentIR) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        page_rows = []
        table_rows = []
        figure_rows = []
        edge_rows = [item.model_dump(mode="json") for item in document.structure_edges]

        for page in sorted(document.pages, key=lambda item: item.page_index):
            page_path = self.layout.page(page.page_index)
            blocks = [item for item in document.blocks if item.page_index == page.page_index]
            edge_ids = [
                item.edge_id
                for item in document.structure_edges
                if item.source_id == page.page_id
                or item.source_id in page.block_ids
                or item.target_id in page.block_ids
            ]
            write_json(
                page_path,
                {
                    "schema_version": "document-ir-page-v1",
                    "page": page.model_dump(mode="json"),
                    "blocks": [item.model_dump(mode="json") for item in blocks],
                    "table_ids": list(page.table_ids),
                    "figure_ids": list(page.figure_ids),
                    "structure_edge_ids": edge_ids,
                },
            )
            page_rows.append(
                {
                    "page_id": page.page_id,
                    "page_index": page.page_index,
                    "page_number": page.page_number,
                    "printed_page_label": page.printed_page_label,
                    "path": relative_path(self.output_dir, page_path),
                    "block_count": len(blocks),
                    "table_count": len(page.table_ids),
                    "figure_count": len(page.figure_ids),
                }
            )

        for table in sorted(document.tables, key=lambda item: (item.page_index, item.order, item.table_id)):
            table_path = self.layout.table(table.table_id)
            write_json(table_path, table.model_dump(mode="json"))
            table_rows.append(
                {
                    "table_id": table.table_id,
                    "page_index": table.page_index,
                    "page_indices": table.page_indices,
                    "path": relative_path(self.output_dir, table_path),
                    "row_count": table.row_count,
                    "column_count": table.column_count,
                    "caption": table.caption,
                }
            )

        for figure in sorted(document.figures, key=lambda item: (item.page_index, item.order, item.figure_id)):
            figure_path = self.layout.figure(figure.figure_id)
            write_json(figure_path, figure.model_dump(mode="json"))
            figure_rows.append(
                {
                    "figure_id": figure.figure_id,
                    "page_index": figure.page_index,
                    "path": relative_path(self.output_dir, figure_path),
                    "visual_type": figure.visual_type,
                    "visual_status": figure.visual_status,
                    "caption": figure.caption,
                }
            )

        paths["page_index"] = write_json(
            self.layout.page_index,
            {"schema_version": "document-ir-page-index-v1", "page_count": len(page_rows), "pages": page_rows},
        )
        paths["table_index"] = write_json(
            self.layout.table_index,
            {"schema_version": "document-ir-table-index-v1", "table_count": len(table_rows), "tables": table_rows},
        )
        paths["figure_index"] = write_json(
            self.layout.figure_index,
            {"schema_version": "document-ir-figure-index-v1", "figure_count": len(figure_rows), "figures": figure_rows},
        )
        paths["structure_edges"] = write_jsonl(self.layout.structure_edges, edge_rows)
        paths["coordinate_systems"] = write_json(
            self.layout.coordinate_systems,
            {
                "schema_version": "document-ir-coordinate-index-v1",
                "coordinate_system_count": len(document.coordinate_systems),
                "coordinate_systems": [item.model_dump(mode="json") for item in document.coordinate_systems],
            },
        )

        metadata = document.metadata.model_copy(deep=True)
        metadata.local_forensics = None
        paths["canonical_document"] = write_json(
            self.layout.canonical_document,
            {
                "schema_version": document.schema_version,
                "metadata": metadata.model_dump(mode="json"),
                "readiness": document.readiness,
                "sections": [item.model_dump(mode="json") for item in document.sections],
                "collections": {
                    "pages": relative_path(self.output_dir, self.layout.page_index),
                    "tables": relative_path(self.output_dir, self.layout.table_index),
                    "figures": relative_path(self.output_dir, self.layout.figure_index),
                    "structure_edges": relative_path(self.output_dir, self.layout.structure_edges),
                    "coordinate_systems": relative_path(self.output_dir, self.layout.coordinate_systems),
                },
                "counts": self._counts(document),
            },
        )
        return paths

    def _write_observations(self, document: DocumentIR) -> dict[str, Path]:
        for page in document.pages:
            write_json(
                self.layout.paddle_layout(page.page_index),
                {
                    "schema_version": "document-ir-layout-observation-v1",
                    "page_id": page.page_id,
                    "page_index": page.page_index,
                    "layout_objects": [
                        item.model_dump(mode="json")
                        for item in document.layout_objects
                        if item.page_index == page.page_index
                    ],
                },
            )
        local_forensics = document.metadata.local_forensics.model_dump(mode="json") if document.metadata.local_forensics else None
        return {
            "local_forensics": write_json(
                self.layout.local_forensics,
                {"schema_version": "document-ir-local-forensics-v1", "forensics": local_forensics},
            )
        }

    def _write_quality(self, document: DocumentIR) -> dict[str, Path]:
        return {
            "quality_report": write_json(self.layout.quality_report, document.quality_report),
            "validation_report": write_json(
                self.layout.validation_report,
                document.validation_report.model_dump(mode="json"),
            ),
        }

    def _write_review_ledger(self, document: DocumentIR) -> dict[str, Path]:
        paths = {
            "model_calls": write_jsonl(
                self.layout.review_collection("calls/model-calls.jsonl"),
                (item.model_dump(mode="json") for item in document.model_calls),
            ),
            "reviewer_results": write_jsonl(
                self.layout.review_collection("results/reviewer-results.jsonl"),
                (item.model_dump(mode="json") for item in document.reviewer_results),
            ),
            "guard_results": write_jsonl(
                self.layout.review_collection("results/guard-results.jsonl"),
                (item.model_dump(mode="json") for item in document.guard_results),
            ),
            "verifier_results": write_jsonl(
                self.layout.review_collection("results/verifier-results.jsonl"),
                (item.model_dump(mode="json") for item in document.verifier_results),
            ),
            "atomic_patches": write_jsonl(
                self.layout.review_collection("patches/atomic-patches.jsonl"),
                (item.model_dump(mode="json") for item in document.atomic_patches),
            ),
            "correction_patches": write_jsonl(
                self.layout.review_collection("patches/correction-patches.jsonl"),
                (item.model_dump(mode="json") for item in document.correction_patches),
            ),
            "final_decisions": write_jsonl(
                self.layout.review_collection("decisions/final-decisions.jsonl"),
                (item.model_dump(mode="json") for item in document.final_decisions),
            ),
            "conflict_groups": write_jsonl(
                self.layout.review_collection("conflicts/conflict-groups.jsonl"),
                (item.model_dump(mode="json") for item in document.conflict_groups),
            ),
        }
        candidate_rows = []
        for candidate in document.candidate_revisions:
            path = self.layout.candidate(candidate.candidate_id)
            write_json(path, candidate.model_dump(mode="json"))
            candidate_rows.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "task_id": candidate.task_id,
                    "status": candidate.status,
                    "path": relative_path(self.output_dir, path),
                }
            )

        task_rows = []
        for task in document.review_tasks:
            task_path = self.layout.review_task(task.task_id)
            write_json(
                task_path,
                {
                    "schema_version": "document-ir-review-task-v1",
                    "task": task.model_dump(mode="json"),
                    "related_ids": {
                        "model_call_ids": [item.call_id for item in document.model_calls if item.task_id == task.task_id],
                        "reviewer_result_ids": [item.reviewer_result_id for item in document.reviewer_results if item.task_id == task.task_id],
                        "patch_ids": [item.patch_id for item in document.atomic_patches if item.source_task_id == task.task_id],
                        "guard_result_ids": [item.guard_result_id for item in document.guard_results if item.task_id == task.task_id],
                        "verifier_result_ids": [item.verifier_result_id for item in document.verifier_results if item.task_id == task.task_id],
                        "candidate_ids": [item.candidate_id for item in document.candidate_revisions if item.task_id == task.task_id],
                        "final_decision_id": task.final_decision_id,
                    },
                },
            )
            task_rows.append(
                {
                    "task_id": task.task_id,
                    "page_index": task.page_index,
                    "target_id": task.target_id,
                    "status": task.status,
                    "blocking": task.blocking,
                    "path": relative_path(self.output_dir, task_path),
                }
            )

        paths["review_tasks"] = write_json(
            self.layout.review_index,
            {
                "schema_version": "document-ir-review-index-v1",
                "task_count": len(task_rows),
                "tasks": task_rows,
                "candidates": candidate_rows,
                "collections": {
                    key: relative_path(self.output_dir, value)
                    for key, value in paths.items()
                },
            },
        )
        paths["candidate_revisions"] = self.layout.review_index
        return paths

    def _write_manifest(self, document: DocumentIR) -> Path:
        counts = self._counts(document)
        entrypoints = {
            "canonical_document": relative_path(self.output_dir, self.layout.canonical_document),
            "pages": relative_path(self.output_dir, self.layout.page_index),
            "tables": relative_path(self.output_dir, self.layout.table_index),
            "figures": relative_path(self.output_dir, self.layout.figure_index),
            "structure_edges": relative_path(self.output_dir, self.layout.structure_edges),
            "coordinate_systems": relative_path(self.output_dir, self.layout.coordinate_systems),
            "local_forensics": relative_path(self.output_dir, self.layout.local_forensics),
            "artifacts": relative_path(self.output_dir, self.layout.artifact_index),
            "quality_report": relative_path(self.output_dir, self.layout.quality_report),
            "validation_report": relative_path(self.output_dir, self.layout.validation_report),
            "review": relative_path(self.output_dir, self.layout.review_index),
            "integrity": relative_path(self.output_dir, self.layout.file_index),
            "snapshot_export": relative_path(self.output_dir, self.layout.snapshot),
        }
        manifest = {
            "package_type": "document-ir-revision",
            "package_schema_version": "document-ir-package-v1",
            "run_id": document.metadata.run_id,
            "ocr_run_id": document.metadata.ocr_run_id,
            "ir_revision": document.metadata.ir_revision,
            "parent_ir_run_id": document.metadata.parent_ir_run_id,
            "schema_version": document.schema_version,
            "pipeline_version": document.metadata.pipeline_version,
            "identifier_contract": document.metadata.source_artifacts.get("identifier_contract"),
            "readiness": document.readiness,
            "can_build_evidence": document.validation_report.checks.get("can_build_evidence", False),
            "source": {
                "pdf_sha256": document.metadata.source_pdf_sha256,
                "ocr_run_id": document.metadata.ocr_run_id,
            },
            "entrypoints": entrypoints,
            "counts": counts,
            **{f"{key}_count": value for key, value in counts.items()},
            "written_at": datetime.now(timezone.utc).isoformat(),
        }
        return write_json(self.layout.manifest, manifest)

    @staticmethod
    def _counts(document: DocumentIR) -> dict[str, int]:
        return {
            "page": len(document.pages),
            "section": len(document.sections),
            "block": len(document.blocks),
            "layout_object": len(document.layout_objects),
            "table": len(document.tables),
            "figure": len(document.figures),
            "structure_edge": len(document.structure_edges),
            "artifact": len(document.artifacts),
            "review_task": len(document.review_tasks),
            "correction_patch": len(document.correction_patches),
            "model_call": len(document.model_calls),
            "reviewer_result": len(document.reviewer_results),
            "atomic_patch": len(document.atomic_patches),
            "guard_result": len(document.guard_results),
            "verifier_result": len(document.verifier_results),
            "final_decision": len(document.final_decisions),
            "candidate_revision": len(document.candidate_revisions),
            "conflict_group": len(document.conflict_groups),
        }

    def _make_portable(self, document: DocumentIR) -> None:
        ocr_root = Path(str(document.metadata.source_artifacts.get("ocr_root_dir") or ""))
        ocr_run_id = document.metadata.ocr_run_id

        def portable(value: str | None) -> str | None:
            if not value or "://" in value:
                return value
            path = Path(value)
            if not path.is_absolute():
                return path.as_posix()
            try:
                return path.resolve().relative_to(self.output_dir.resolve()).as_posix()
            except ValueError:
                pass
            if str(ocr_root) and ocr_root.is_absolute():
                try:
                    suffix = path.resolve().relative_to(ocr_root.resolve()).as_posix()
                    return f"ocr-package://{ocr_run_id}/{suffix}"
                except ValueError:
                    pass
            if document.metadata.source_pdf_sha256 and path == Path(document.metadata.source_pdf_path or ""):
                return f"source-pdf://sha256/{document.metadata.source_pdf_sha256}"
            return f"external-local://{path.name}"

        document.metadata.source_pdf_path = portable(document.metadata.source_pdf_path)
        document.metadata.ocr_manifest_path = f"ocr-package://{ocr_run_id}/manifest.json"
        raw_suffix = "provider/result.jsonl" if "provider" in str(document.metadata.raw_jsonl_path) else "raw/result.jsonl"
        document.metadata.raw_jsonl_path = f"ocr-package://{ocr_run_id}/{raw_suffix}"
        document.metadata.source_artifacts.pop("ocr_root_dir", None)
        document.metadata.source_artifacts.pop("ocr_manifest", None)
        document.metadata.source_artifacts["ocr_package_uri"] = f"ocr-package://{ocr_run_id}/"
        document.metadata.source_artifacts["ocr_manifest_uri"] = f"ocr-package://{ocr_run_id}/manifest.json"
        if document.metadata.local_forensics:
            document.metadata.local_forensics.pdf_path = portable(document.metadata.local_forensics.pdf_path)
            for local_page in document.metadata.local_forensics.pages:
                local_page.page_image_path = portable(local_page.page_image_path)
        for artifact in document.artifacts:
            artifact.path = portable(artifact.path) or ""
        for page in document.pages:
            page.markdown_path = portable(page.markdown_path)
            page.page_image_path = portable(page.page_image_path)
            page.image_paths = [portable(item) or item for item in page.image_paths]
            page.output_image_paths = [portable(item) or item for item in page.output_image_paths]
        for figure in document.figures:
            figure.image_path = portable(figure.image_path)
        for _, entity in self._trace_entities(document):
            trace = entity.source_trace
            trace.artifact_path = portable(trace.artifact_path)
            trace.raw_jsonl_path = portable(trace.raw_jsonl_path)
            trace.page_markdown_path = portable(trace.page_markdown_path)
        for task in document.review_tasks:
            task.input_refs = [portable(item) or item for item in task.input_refs]
        for patch in [*document.atomic_patches, *document.correction_patches]:
            patch.evidence_refs = [portable(item) or item for item in patch.evidence_refs]

    @staticmethod
    def _trace_entities(document: DocumentIR):
        return [
            *((item.page_id, item) for item in document.pages),
            *((item.layout_object_id, item) for item in document.layout_objects),
            *((item.block_id, item) for item in document.blocks),
            *((item.table_id, item) for item in document.tables),
            *((item.figure_id, item) for item in document.figures),
            *((item.cell_id, item) for table in document.tables for item in table.cells),
        ]
