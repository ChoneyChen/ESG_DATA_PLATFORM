"""Document archive queries own physical evidence reads; the API composes dependencies.

The getters let a running app bind its configured stores without importing its
composition root or retaining a second global configuration.
"""

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from esg_document.document.contracts import DocumentIrJobState
from esg_document.document.reader import DocumentIrPackageReader
from esg_document.document.catalog import DocumentIrCatalog
from esg_document.document.versioning import DocumentIrVersionManager
from esg_document.document.identity import (
    manifest_document_id,
    manifest_document_label,
    normalize_sha256,
)
from esg_document.storage.package_layout import package_dir, require_package_dir_name


class DocumentArchive:

    def __init__(self, settings, jobs):
        (self._settings, self._jobs) = (settings, jobs)

    @property
    def settings(self):
        return self._settings()

    @property
    def jobs(self):
        return self._jobs()

    def list_document_ir_jobs(self) -> list[DocumentIrJobState]:
        states = {state.run_id: state for state in self.jobs.list()}
        for manifest_path in self.settings.document_ir_output_root.glob(
            "*/manifest.json"
        ):
            run_id = manifest_path.parent.name
            if run_id in states:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                states[run_id].summary = {
                    **states[run_id].summary,
                    **self._document_ir_summary(manifest, states[run_id].telemetry),
                }
                states[run_id].manifest_path = manifest_path
            else:
                state = self._document_ir_state_from_manifest(run_id)
                if state:
                    states[run_id] = state
        return sorted(states.values(), key=lambda item: item.run_id, reverse=True)

    def list_document_ir_review_worklist(self) -> dict[str, object]:
        catalog = DocumentIrCatalog(
            self.settings.document_ir_output_root,
            ocr_output_root=self.settings.output_root,
        )
        latest_runs = catalog.list_latest_lineage_revisions()
        entries: list[dict[str, object]] = []
        failed_run_ids: list[str] = []
        for row in latest_runs:
            run_id = str(row["run_id"])
            try:
                payload = self._ir_reader(run_id).unresolved_review_worklist()
            except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
                failed_run_ids.append(run_id)
                continue
            run = {
                "run_id": run_id,
                "status": "done",
                "summary": {
                    "document_id": row.get("document_id"),
                    "document_label": row.get("document_label"),
                    "lineage_id": row.get("lineage_id"),
                    "ir_revision": row.get("ir_revision"),
                    "readiness": row.get("readiness"),
                    "written_at": row.get("written_at"),
                },
            }
            for item in payload.get("entries") or []:
                entries.append({"run": run, **item})
        return {
            "schema_version": "document-ir-review-worklist-catalog-v1",
            "latest_run_count": len(latest_runs),
            "failed_run_ids": failed_run_ids,
            "entries": entries,
        }

    def get_document_ir_job(self, run_id: str) -> DocumentIrJobState:
        state = self.jobs.get(run_id)
        if not state:
            state = self._document_ir_state_from_manifest(run_id)
        if not state:
            raise HTTPException(status_code=404, detail="Document IR run not found")
        manifest_path = state.output_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            state.summary = {
                **state.summary,
                **self._document_ir_summary(manifest, state.telemetry),
            }
            state.manifest_path = manifest_path
        return state

    def get_document_ir_manifest(self, run_id: str) -> dict[str, object]:
        output_dir = self._document_ir_output_dir(run_id)
        if not output_dir:
            raise HTTPException(status_code=404, detail="Document IR run not found")
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail="Manifest not available yet")
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def get_document_ir_quality_report(self, run_id: str) -> dict[str, object]:
        return self._read_ir_entrypoint(run_id, "quality_report")

    def get_document_ir_validation_report(self, run_id: str) -> dict[str, object]:
        return self._read_ir_entrypoint(run_id, "validation_report")

    def get_document_ir_document(self, run_id: str) -> dict[str, object]:
        return self._ir_reader(run_id).load_document().model_dump(mode="json")

    def list_document_ir_pages(self, run_id: str) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._ir_reader(run_id).load_document().pages
        ]

    def get_document_ir_page(self, run_id: str, page_index: int) -> dict[str, object]:
        if page_index < 0:
            raise HTTPException(
                status_code=400, detail="page_index must be non-negative"
            )
        try:
            return self._ir_reader(run_id).page(page_index)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    def list_document_ir_tables(self, run_id: str) -> list[dict[str, object]]:
        return self._ir_reader(run_id).tables()

    def get_document_ir_table(self, run_id: str, table_id: str) -> dict[str, object]:
        safe_table_id = Path(table_id).name
        if safe_table_id != table_id:
            raise HTTPException(status_code=400, detail="Invalid table id")
        try:
            return self._ir_reader(run_id).table(safe_table_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    def list_document_ir_logical_tables(self, run_id: str) -> list[dict[str, object]]:
        return self._ir_reader(run_id).list_logical_tables()

    def get_document_ir_logical_table(
        self, run_id: str, logical_table_id: str
    ) -> dict[str, object]:
        safe_logical_table_id = Path(logical_table_id).name
        if safe_logical_table_id != logical_table_id:
            raise HTTPException(status_code=400, detail="Invalid logical table id")
        try:
            return self._ir_reader(run_id).logical_table(safe_logical_table_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    def list_document_ir_figures(self, run_id: str) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._ir_reader(run_id).load_document().figures
        ]

    def get_document_ir_structure_edges(self, run_id: str) -> list[dict[str, object]]:
        reader = self._ir_reader(run_id)
        return reader.read_jsonl(str(reader.manifest["entrypoints"]["structure_edges"]))
        return self._read_ir_json(run_id, "structure_edges.json")

    def get_document_ir_correction_patches(
        self, run_id: str
    ) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("correction_patches")

    def get_document_ir_model_calls(self, run_id: str) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("model_calls")

    def get_document_ir_reviewer_results(self, run_id: str) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("reviewer_results")

    def get_document_ir_atomic_patches(self, run_id: str) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("atomic_patches")

    def get_document_ir_patch_transactions(
        self, run_id: str
    ) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("patch_transactions")

    def get_document_ir_guard_results(self, run_id: str) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("guard_results")

    def get_document_ir_verifier_results(self, run_id: str) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("verifier_results")

    def get_document_ir_final_decisions(self, run_id: str) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("final_decisions")

    def get_document_ir_candidate_revisions(
        self, run_id: str
    ) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("candidate_revisions")

    def get_document_ir_review_bundle(
        self, run_id: str, task_id: str
    ) -> dict[str, object]:
        safe_task_id = Path(task_id).name
        if safe_task_id != task_id:
            raise HTTPException(status_code=400, detail="Invalid task id")
        try:
            return self._ir_reader(run_id).review_task_bundle(safe_task_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    def get_document_ir_conflict_groups(self, run_id: str) -> list[dict[str, object]]:
        return self._ir_reader(run_id).review_collection("conflict_groups")

    def get_document_ir_artifact_index(self, run_id: str) -> list[dict[str, object]]:
        payload = self._read_ir_entrypoint(run_id, "artifacts")
        return (
            payload.get("artifacts", payload) if isinstance(payload, dict) else payload
        )

    def list_document_ir_revisions(self, ocr_run_id: str) -> list[dict[str, object]]:
        try:
            require_package_dir_name(ocr_run_id)
            return DocumentIrVersionManager(
                self.settings.document_ir_output_root
            ).list_revisions(ocr_run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    def list_document_ir_documents(self) -> list[dict[str, object]]:
        return DocumentIrCatalog(
            self.settings.document_ir_output_root,
            ocr_output_root=self.settings.output_root,
        ).list_documents()

    def list_document_revisions(self, document_id: str) -> list[dict[str, object]]:
        try:
            return DocumentIrCatalog(
                self.settings.document_ir_output_root,
                ocr_output_root=self.settings.output_root,
            ).list_document_revisions(document_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    def get_document_ir_review_tasks(self, run_id: str) -> list[dict[str, object]]:
        output_dir = self._document_ir_output_dir(run_id)
        if not output_dir:
            raise HTTPException(status_code=404, detail="Document IR run not found")
        return self._ir_reader(run_id).review_tasks()

    def get_document_ir_human_review_inbox(self, run_id: str) -> dict[str, object]:
        output_dir = self._document_ir_output_dir(run_id)
        if not output_dir:
            raise HTTPException(status_code=404, detail="Document IR run not found")
        return self._ir_reader(run_id).human_review_inbox()

    def list_document_ir_artifacts(self, run_id: str) -> dict[str, object]:
        output_dir = self._document_ir_output_dir(run_id)
        if not output_dir:
            raise HTTPException(status_code=404, detail="Document IR run not found")
        if not output_dir.exists():
            return {"run_id": run_id, "files": []}
        files = []
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                if path.name == ".DS_Store":
                    continue
                rel = path.relative_to(output_dir)
                files.append(
                    {
                        "path": str(rel),
                        "size": path.stat().st_size,
                        "url": f"/document_ir_output/{run_id}/{rel.as_posix()}",
                    }
                )
        return {"run_id": run_id, "files": files}

    def _document_ir_output_dir(self, run_id: str) -> Path | None:
        state = self.jobs.get(run_id)
        if state:
            return state.output_dir
        try:
            output_dir = package_dir(self.settings.document_ir_output_root, run_id)
        except ValueError:
            return None
        if (output_dir / "manifest.json").exists():
            return output_dir
        return None

    def _read_ir_json(self, run_id: str, relative_path: str):
        output_dir = self._document_ir_output_dir(run_id)
        if not output_dir:
            raise HTTPException(status_code=404, detail="Document IR run not found")
        path = output_dir / relative_path
        try:
            path.resolve().relative_to(output_dir.resolve())
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid artifact path"
            ) from None
        if not path.exists():
            raise HTTPException(
                status_code=404, detail=f"Artifact not available: {relative_path}"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def _ir_reader(self, run_id: str) -> DocumentIrPackageReader:
        output_dir = self._document_ir_output_dir(run_id)
        if not output_dir:
            raise HTTPException(status_code=404, detail="Document IR run not found")
        try:
            return DocumentIrPackageReader(
                output_dir, ocr_output_root=self.settings.output_root
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    def _read_ir_entrypoint(self, run_id: str, key: str):
        try:
            return self._ir_reader(run_id).read_json(key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    def _document_ir_state_from_manifest(
        self, run_id: str
    ) -> DocumentIrJobState | None:
        try:
            output_dir = package_dir(self.settings.document_ir_output_root, run_id)
        except ValueError:
            return None
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.exists():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state = DocumentIrJobState(
            run_id=run_id,
            ocr_run_id=str(manifest.get("ocr_run_id") or ""),
            status="done",
            message="Document IR job completed",
            output_dir=output_dir,
            manifest_path=manifest_path,
            summary=self._document_ir_summary(manifest, {}),
        )
        self.jobs.put(state)
        return state

    def _document_ir_summary(
        self, manifest: dict[str, object], telemetry: dict[str, object]
    ) -> dict[str, object]:
        model_calls = telemetry.get("model_calls")
        if not isinstance(model_calls, dict):
            model_calls = {}
        ocr_run_id = str(manifest.get("ocr_run_id") or "")
        ocr_manifest = self._read_ocr_manifest(ocr_run_id)
        source = manifest.get("source")
        source = source if isinstance(source, dict) else {}
        lineage_id = DocumentIrVersionManager(
            self.settings.document_ir_output_root
        ).lineage_id(manifest)
        return {
            "document_id": manifest_document_id(manifest),
            "document_label": manifest_document_label(manifest, ocr_manifest),
            "external_document_id": manifest.get("external_document_id"),
            "source_pdf_sha256": normalize_sha256(
                source.get("pdf_sha256") or source.get("sha256")
            ),
            "lineage_id": lineage_id,
            "ocr_run_id": ocr_run_id,
            "readiness": manifest.get("readiness", "unknown"),
            "ir_revision": manifest.get("ir_revision", 1),
            "can_build_evidence": manifest.get("can_build_evidence", False),
            "can_build_limited_evidence": manifest.get(
                "can_build_limited_evidence", False
            ),
            "evidence_policy": manifest.get("evidence_policy", {}),
            "page_count": manifest.get("page_count", 0),
            "page_coverage": manifest.get("page_coverage") or {"mode": "all"},
            "block_count": manifest.get("block_count", 0),
            "table_count": manifest.get("table_count", 0),
            "figure_count": manifest.get("figure_count", 0),
            "spread_count": manifest.get("spread_count", 0),
            "review_task_count": manifest.get("review_task_count", 0),
            "model_actual_attempts": model_calls.get("actual_attempts", 0),
            "review_retry_result": manifest.get("review_retry_result"),
            "retention": manifest.get("retention"),
            "written_at": manifest.get("written_at"),
        }

    def _read_ocr_manifest(self, ocr_run_id: str) -> dict[str, object] | None:
        if not ocr_run_id:
            return None
        path = self.settings.output_root / ocr_run_id / "manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def router(self):
        router = APIRouter()
        router.add_api_route(
            "/api/document-ir/jobs", self.list_document_ir_jobs, methods=["GET"]
        )
        router.add_api_route(
            "/api/document-ir/review-worklist",
            self.list_document_ir_review_worklist,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}", self.get_document_ir_job, methods=["GET"]
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/manifest",
            self.get_document_ir_manifest,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/quality-report",
            self.get_document_ir_quality_report,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/validation-report",
            self.get_document_ir_validation_report,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/document",
            self.get_document_ir_document,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/pages",
            self.list_document_ir_pages,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/pages/{page_index}",
            self.get_document_ir_page,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/tables",
            self.list_document_ir_tables,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/tables/{table_id}",
            self.get_document_ir_table,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/logical-tables",
            self.list_document_ir_logical_tables,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/logical-tables/{logical_table_id}",
            self.get_document_ir_logical_table,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/figures",
            self.list_document_ir_figures,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/structure-edges",
            self.get_document_ir_structure_edges,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/correction-patches",
            self.get_document_ir_correction_patches,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/model-calls",
            self.get_document_ir_model_calls,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/reviewer-results",
            self.get_document_ir_reviewer_results,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/atomic-patches",
            self.get_document_ir_atomic_patches,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/patch-transactions",
            self.get_document_ir_patch_transactions,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/guard-results",
            self.get_document_ir_guard_results,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/verifier-results",
            self.get_document_ir_verifier_results,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/final-decisions",
            self.get_document_ir_final_decisions,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/candidate-revisions",
            self.get_document_ir_candidate_revisions,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/reviews/{task_id}",
            self.get_document_ir_review_bundle,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/conflict-groups",
            self.get_document_ir_conflict_groups,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/artifact-index",
            self.get_document_ir_artifact_index,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/revisions/{ocr_run_id}",
            self.list_document_ir_revisions,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/documents",
            self.list_document_ir_documents,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/documents/{document_id}/revisions",
            self.list_document_revisions,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/review-tasks",
            self.get_document_ir_review_tasks,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/human-review/inbox",
            self.get_document_ir_human_review_inbox,
            methods=["GET"],
        )
        router.add_api_route(
            "/api/document-ir/jobs/{run_id}/artifacts",
            self.list_document_ir_artifacts,
            methods=["GET"],
        )
        return router
