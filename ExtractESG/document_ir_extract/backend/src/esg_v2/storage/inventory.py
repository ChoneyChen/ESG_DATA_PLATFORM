from __future__ import annotations

import hashlib
import json
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from esg_v2.document.identity import (
    legacy_document_id,
    manifest_document_id,
    manifest_document_label,
    normalize_sha256,
)
from esg_v2.storage.package_layout import require_package_dir_name


RunKind = Literal["ocr", "ir"]
TargetKind = Literal["ocr", "ir", "document", "incomplete"]
ACTIVE_JOB_STATUSES = {"queued", "running"}
INCOMPLETE_JOB_STATUSES = {"failed", "interrupted", "cancelled"}
DELETION_PLAN_TTL_SECONDS = 15 * 60


class StorageTarget(BaseModel):
    kind: TargetKind
    target_id: str


class DeletionPlanRequest(BaseModel):
    targets: list[StorageTarget] = Field(min_length=1)
    cascade: bool = False


class StorageInventoryService:
    """Derived inventory and dependency-aware cleanup for local run artifacts."""

    def __init__(
        self,
        *,
        ocr_output_root: Path,
        ir_output_root: Path,
        ocr_state_root: Path,
        ir_state_root: Path,
        upload_root: Path,
        cleanup_root: Path,
    ) -> None:
        self.ocr_output_root = ocr_output_root
        self.ir_output_root = ir_output_root
        self.ocr_state_root = ocr_state_root
        self.ir_state_root = ir_state_root
        self.upload_root = upload_root
        self.cleanup_root = cleanup_root
        self._plans: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def inventory(self) -> dict[str, Any]:
        ocr_runs = self._discover_ocr_runs()
        ir_runs = self._discover_ir_runs(ocr_runs)
        ir_by_ocr: dict[str, list[dict[str, Any]]] = {}
        for row in ir_runs.values():
            ir_by_ocr.setdefault(str(row.get("ocr_run_id") or ""), []).append(row)

        document_groups: dict[str, dict[str, Any]] = {}
        for row in ocr_runs.values():
            group = self._document_group(document_groups, row)
            group["ocr_runs"].append({**row, "ir_runs": []})
        for row in ir_runs.values():
            group = self._document_group(document_groups, row)
            if not any(item["run_id"] == row.get("ocr_run_id") for item in group["ocr_runs"]):
                group["unlinked_ir_runs"].append(row)

        for group in document_groups.values():
            for ocr in group["ocr_runs"]:
                ocr["ir_runs"] = sorted(
                    ir_by_ocr.get(ocr["run_id"], []),
                    key=self._run_sort_key,
                    reverse=True,
                )
            group["ocr_runs"].sort(key=self._run_sort_key, reverse=True)
            group["unlinked_ir_runs"].sort(key=self._run_sort_key, reverse=True)
            all_runs = [
                *group["ocr_runs"],
                *[ir for ocr in group["ocr_runs"] for ir in ocr["ir_runs"]],
                *group["unlinked_ir_runs"],
            ]
            group["file_count"] = sum(int(item.get("file_count") or 0) for item in all_runs)
            group["size_bytes"] = sum(int(item.get("size_bytes") or 0) for item in all_runs)
            group["ocr_run_count"] = len(group["ocr_runs"])
            group["ir_run_count"] = sum(len(item["ir_runs"]) for item in group["ocr_runs"]) + len(
                group["unlinked_ir_runs"]
            )
            group["incomplete_count"] = sum(bool(item.get("cleanup_candidate")) for item in all_runs)
            group["latest_activity_at"] = max(
                (str(item.get("updated_at") or "") for item in all_runs),
                default="",
            )

        documents = sorted(
            document_groups.values(),
            key=lambda item: (str(item.get("latest_activity_at") or ""), str(item["document_id"])),
            reverse=True,
        )
        all_runs = [*ocr_runs.values(), *ir_runs.values()]
        return {
            "available": True,
            "generated_at": self._now(),
            "summary": {
                "document_count": len(documents),
                "ocr_run_count": len(ocr_runs),
                "ir_run_count": len(ir_runs),
                "incomplete_count": sum(bool(item.get("cleanup_candidate")) for item in all_runs),
                "active_count": sum(bool(item.get("active")) for item in all_runs),
                "file_count": sum(int(item.get("file_count") or 0) for item in all_runs),
                "size_bytes": sum(int(item.get("size_bytes") or 0) for item in all_runs),
            },
            "documents": documents,
        }

    def create_deletion_plan(self, request: DeletionPlanRequest) -> dict[str, Any]:
        with self._lock:
            self._prune_expired_plans()
            plan = self._compile_plan(request)
            self._plans[plan["plan_id"]] = plan
            return self._public_plan(plan)

    def execute_deletion_plan(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            stored = self._plans.get(plan_id)
            if stored is None:
                raise KeyError("Deletion plan not found or backend restarted; compile a new plan")
            if datetime.fromisoformat(str(stored["expires_at"])) <= datetime.now(timezone.utc):
                self._plans.pop(plan_id, None)
                raise KeyError("Deletion plan expired; compile a new plan")
            if not stored["can_execute"]:
                raise ValueError("Deletion plan is blocked and cannot be executed")

            current = self._compile_plan(DeletionPlanRequest.model_validate(stored["request"]), plan_id=plan_id)
            if current["fingerprint"] != stored["fingerprint"]:
                raise RuntimeError("Storage changed after planning; compile a fresh deletion plan")

            cleanup_id = f"cleanup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:10]}"
            trash_root = self.cleanup_root / "trash" / cleanup_id
            moved: list[tuple[Path, Path]] = []
            try:
                trash_root.mkdir(parents=True, exist_ok=False)
                for index, entry in enumerate(current["path_entries"]):
                    source = Path(entry["absolute_path"])
                    if not source.exists():
                        continue
                    self._require_owned_path(source)
                    destination = trash_root / f"{index:04d}-{entry['role']}-{source.name}"
                    shutil.move(str(source), str(destination))
                    moved.append((source, destination))
                result = {
                    "cleanup_id": cleanup_id,
                    "executed_at": self._now(),
                    "deleted_runs": current["selected_runs"],
                    "deleted_path_count": len(moved),
                    "deleted_file_count": current["file_count"],
                    "deleted_size_bytes": current["size_bytes"],
                }
                self._append_audit({**result, "plan_id": plan_id, "targets": current["request"]["targets"]})
            except Exception:
                for source, destination in reversed(moved):
                    if destination.exists() and not source.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(destination), str(source))
                shutil.rmtree(trash_root, ignore_errors=True)
                raise

            shutil.rmtree(trash_root, ignore_errors=True)
            self._plans.pop(plan_id, None)
            return result

    def _compile_plan(self, request: DeletionPlanRequest, *, plan_id: str | None = None) -> dict[str, Any]:
        ocr_runs = self._discover_ocr_runs()
        ir_runs = self._discover_ir_runs(ocr_runs)
        selected_ocr: set[str] = set()
        selected_ir: set[str] = set()
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        for target in request.targets:
            if target.kind == "ocr":
                if target.target_id not in ocr_runs:
                    blockers.append(self._message("target_not_found", target.target_id, "OCR Run 不存在。"))
                else:
                    selected_ocr.add(target.target_id)
            elif target.kind == "ir":
                if target.target_id not in ir_runs:
                    blockers.append(self._message("target_not_found", target.target_id, "Document IR Run 不存在。"))
                else:
                    selected_ir.add(target.target_id)
            elif target.kind == "document":
                matches_ocr = {key for key, row in ocr_runs.items() if row["document_id"] == target.target_id}
                matches_ir = {key for key, row in ir_runs.items() if row["document_id"] == target.target_id}
                if not matches_ocr and not matches_ir:
                    blockers.append(self._message("target_not_found", target.target_id, "报告资产不存在。"))
                elif not request.cascade:
                    blockers.append(
                        self._message("document_requires_cascade", target.target_id, "删除整份报告必须开启级联删除。")
                    )
                else:
                    selected_ocr.update(matches_ocr)
                    selected_ir.update(matches_ir)
            elif target.kind == "incomplete":
                selected_ocr.update(key for key, row in ocr_runs.items() if row["cleanup_candidate"])
                selected_ir.update(key for key, row in ir_runs.items() if row["cleanup_candidate"])

        if request.cascade:
            selected_ir.update(
                key for key, row in ir_runs.items() if str(row.get("ocr_run_id") or "") in selected_ocr
            )
            changed = True
            while changed:
                changed = False
                for key, row in ir_runs.items():
                    if row.get("parent_ir_run_id") in selected_ir and key not in selected_ir:
                        selected_ir.add(key)
                        changed = True

        for run_id in sorted(selected_ocr):
            row = ocr_runs[run_id]
            if row["active"]:
                blockers.append(self._message("active_job", run_id, "OCR 任务仍在运行，禁止清理。"))
            dependencies = sorted(
                key for key, ir in ir_runs.items() if str(ir.get("ocr_run_id") or "") == run_id and key not in selected_ir
            )
            if dependencies:
                blockers.append(
                    self._message(
                        "ocr_has_ir_dependencies",
                        run_id,
                        f"该 OCR 仍被 {len(dependencies)} 个 IR 包使用；请开启级联删除。",
                    )
                )
        for run_id in sorted(selected_ir):
            row = ir_runs[run_id]
            if row["active"]:
                blockers.append(self._message("active_job", run_id, "Document IR 任务仍在运行，禁止清理。"))
            children = sorted(
                key for key, child in ir_runs.items() if child.get("parent_ir_run_id") == run_id and key not in selected_ir
            )
            if children:
                blockers.append(
                    self._message(
                        "ir_has_child_revisions",
                        run_id,
                        f"该 IR 仍有 {len(children)} 个子修订；请开启级联删除。",
                    )
                )

        if selected_ir:
            warnings.append(
                self._message(
                    "downstream_dependencies_unverified",
                    ",".join(sorted(selected_ir)),
                    "当前服务无法验证独立定向抽取服务中的引用；删除 IR 前请确认下游任务不再使用这些 revision。",
                )
            )
        if not selected_ocr and not selected_ir and not blockers:
            blockers.append(self._message("nothing_to_delete", "", "没有符合条件的产物或任务记录。"))

        selected_runs = [
            *({"kind": "ocr", "run_id": key, "status": ocr_runs[key]["status"]} for key in sorted(selected_ocr)),
            *({"kind": "ir", "run_id": key, "status": ir_runs[key]["status"]} for key in sorted(selected_ir)),
        ]
        path_entries = []
        for run_id in sorted(selected_ocr):
            path_entries.extend(self._owned_path_entries("ocr", run_id))
        for run_id in sorted(selected_ir):
            path_entries.extend(self._owned_path_entries("ir", run_id))
        file_count, size_bytes = self._path_entries_stats(path_entries)
        fingerprint_payload = {
            "request": request.model_dump(mode="json"),
            "selected_runs": selected_runs,
            "paths": [
                {"role": item["role"], "path": item["display_path"], **self._path_stats(Path(item["absolute_path"]))}
                for item in path_entries
            ],
            "blockers": blockers,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest()
        created_at = datetime.now(timezone.utc)
        return {
            "plan_id": plan_id or f"delete-plan-{uuid.uuid4().hex}",
            "created_at": created_at.isoformat(),
            "expires_at": (created_at + timedelta(seconds=DELETION_PLAN_TTL_SECONDS)).isoformat(),
            "request": request.model_dump(mode="json"),
            "can_execute": not blockers,
            "selected_runs": selected_runs,
            "path_entries": path_entries,
            "file_count": file_count,
            "size_bytes": size_bytes,
            "blockers": blockers,
            "warnings": warnings,
            "fingerprint": fingerprint,
        }

    def _discover_ocr_runs(self) -> dict[str, dict[str, Any]]:
        run_ids = self._child_directory_names(self.ocr_output_root)
        run_ids.update(self._child_directory_names(self.ocr_state_root))
        run_ids.update(self._child_directory_names(self.upload_root))
        rows: dict[str, dict[str, Any]] = {}
        for run_id in sorted(run_ids):
            manifest, manifest_health = self._read_object(self.ocr_output_root / run_id / "manifest.json")
            state, state_health = self._read_object(self.ocr_state_root / run_id / "state.json")
            source = manifest.get("source") if manifest else {}
            source = source if isinstance(source, dict) else {}
            summary = state.get("summary") if state else {}
            summary = summary if isinstance(summary, dict) else {}
            identity_manifest = manifest or {
                "run_id": run_id,
                "document_id": summary.get("document_id"),
                "document_label": summary.get("document_label"),
            }
            paths = self._owned_path_entries("ocr", run_id)
            file_count, size_bytes = self._path_entries_stats(paths)
            status = str((state or {}).get("status") or ("done" if manifest else "incomplete"))
            updated_at = self._latest_timestamp(
                manifest.get("written_at") if manifest else None,
                state.get("updated_at") if state else None,
                *(Path(item["absolute_path"]) for item in paths),
            )
            rows[run_id] = {
                "kind": "ocr",
                "run_id": run_id,
                "document_id": manifest_document_id(identity_manifest),
                "document_label": manifest_document_label(identity_manifest),
                "source_pdf_sha256": normalize_sha256(source.get("sha256")),
                "status": status,
                "active": status in ACTIVE_JOB_STATUSES,
                "cleanup_candidate": status not in ACTIVE_JOB_STATUSES
                and (status in INCOMPLETE_JOB_STATUSES or not manifest),
                "package_state": self._package_state(manifest_health, state_health),
                "manifest_health": manifest_health,
                "state_health": state_health,
                "page_count": int((manifest or {}).get("page_count") or (state or {}).get("page_count") or 0),
                "model": (manifest or {}).get("model") or summary.get("model"),
                "ocr_provider": (manifest or {}).get("ocr_provider") or summary.get("ocr_provider"),
                "provider_route": (manifest or {}).get("provider_route") or summary.get("provider_route"),
                "file_count": file_count,
                "size_bytes": size_bytes,
                "updated_at": updated_at,
                "has_upload": (self.upload_root / run_id).is_dir(),
            }
        return rows

    def _discover_ir_runs(self, ocr_runs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        run_ids = self._child_directory_names(self.ir_output_root)
        run_ids.update(self._child_directory_names(self.ir_state_root))
        rows: dict[str, dict[str, Any]] = {}
        for run_id in sorted(run_ids):
            manifest, manifest_health = self._read_object(self.ir_output_root / run_id / "manifest.json")
            state, state_health = self._read_object(self.ir_state_root / run_id / "state.json")
            ocr_run_id = str((manifest or {}).get("ocr_run_id") or (state or {}).get("ocr_run_id") or "")
            linked_ocr = ocr_runs.get(ocr_run_id)
            identity_manifest = manifest or {
                "run_id": run_id,
                "ocr_run_id": ocr_run_id,
                "document_id": (linked_ocr or {}).get("document_id"),
                "document_label": (linked_ocr or {}).get("document_label"),
            }
            paths = self._owned_path_entries("ir", run_id)
            file_count, size_bytes = self._path_entries_stats(paths)
            status = str((state or {}).get("status") or ("done" if manifest else "incomplete"))
            updated_at = self._latest_timestamp(
                manifest.get("written_at") if manifest else None,
                state.get("updated_at") if state else None,
                *(Path(item["absolute_path"]) for item in paths),
            )
            retention = self._retention_record(ocr_run_id)
            rows[run_id] = {
                "kind": "ir",
                "run_id": run_id,
                "ocr_run_id": ocr_run_id,
                "document_id": manifest_document_id(identity_manifest),
                "document_label": manifest_document_label(identity_manifest),
                "status": status,
                "active": status in ACTIVE_JOB_STATUSES,
                "cleanup_candidate": status not in ACTIVE_JOB_STATUSES
                and (status in INCOMPLETE_JOB_STATUSES or not manifest),
                "package_state": self._package_state(manifest_health, state_health),
                "manifest_health": manifest_health,
                "state_health": state_health,
                "lineage_id": (manifest or {}).get("lineage_id"),
                "ir_revision": int((manifest or {}).get("ir_revision") or 0),
                "parent_ir_run_id": (manifest or {}).get("parent_ir_run_id"),
                "readiness": (manifest or {}).get("readiness"),
                "can_build_evidence": bool((manifest or {}).get("can_build_evidence", False)),
                "schema_version": (manifest or {}).get("schema_version"),
                "pipeline_version": (manifest or {}).get("pipeline_version"),
                "retention_status": (
                    "current_best"
                    if retention and retention.get("retained_run_id") == run_id
                    else "candidate"
                    if retention
                    else "untracked"
                ),
                "retention_updated_at": (retention or {}).get("updated_at"),
                "file_count": file_count,
                "size_bytes": size_bytes,
                "updated_at": updated_at,
            }
        children: dict[str, list[str]] = {}
        for row in rows.values():
            parent = str(row.get("parent_ir_run_id") or "")
            if parent:
                children.setdefault(parent, []).append(row["run_id"])
        for row in rows.values():
            row["child_ir_run_ids"] = sorted(children.get(row["run_id"], []))
        return rows

    def _retention_record(self, ocr_run_id: str) -> dict[str, Any] | None:
        if not ocr_run_id:
            return None
        try:
            require_package_dir_name(ocr_run_id)
        except ValueError:
            return None
        record, _ = self._read_object(
            self.cleanup_root / "ir-retention" / "current" / f"{ocr_run_id}.json"
        )
        return record

    def _owned_path_entries(self, kind: RunKind, run_id: str) -> list[dict[str, str]]:
        require_package_dir_name(run_id)
        specs = (
            [
                ("ocr_package", self.ocr_output_root / run_id, self.ocr_output_root),
                ("ocr_job_state", self.ocr_state_root / run_id, self.ocr_state_root),
                ("source_upload", self.upload_root / run_id, self.upload_root),
            ]
            if kind == "ocr"
            else [
                ("ir_package", self.ir_output_root / run_id, self.ir_output_root),
                ("ir_job_state", self.ir_state_root / run_id, self.ir_state_root),
            ]
        )
        entries = []
        for role, path, root in specs:
            if path.is_dir():
                entries.append(
                    {
                        "role": role,
                        "absolute_path": str(path),
                        "display_path": f"{root.name}/{run_id}",
                    }
                )
        return entries

    def _require_owned_path(self, path: Path) -> None:
        resolved = path.resolve()
        for root in self._owned_roots():
            try:
                relative = resolved.relative_to(root.resolve())
            except ValueError:
                continue
            if len(relative.parts) == 1 and relative.name not in {"", ".", ".."}:
                return
        raise ValueError(f"Refusing to delete path outside an owned run directory: {path}")

    def _owned_roots(self) -> tuple[Path, ...]:
        return (
            self.ocr_output_root,
            self.ir_output_root,
            self.ocr_state_root,
            self.ir_state_root,
            self.upload_root,
        )

    def _append_audit(self, payload: dict[str, Any]) -> None:
        self.cleanup_root.mkdir(parents=True, exist_ok=True)
        path = self.cleanup_root / "cleanup-audit.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _prune_expired_plans(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            plan_id
            for plan_id, plan in self._plans.items()
            if datetime.fromisoformat(str(plan["expires_at"])) <= now
        ]
        for plan_id in expired:
            self._plans.pop(plan_id, None)

    @staticmethod
    def _read_object(path: Path) -> tuple[dict[str, Any] | None, str]:
        if not path.exists():
            return None, "missing"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, "invalid"
        return (payload, "valid") if isinstance(payload, dict) else (None, "invalid")

    @staticmethod
    def _child_directory_names(root: Path) -> set[str]:
        if not root.exists():
            return set()
        names = set()
        for path in root.iterdir():
            if not path.is_dir():
                continue
            try:
                require_package_dir_name(path.name)
            except ValueError:
                continue
            names.add(path.name)
        return names

    @staticmethod
    def _path_stats(path: Path) -> dict[str, int]:
        if not path.exists():
            return {"file_count": 0, "size_bytes": 0}
        file_count = 0
        size_bytes = 0
        for item in path.rglob("*"):
            if item.is_file() and item.name != ".DS_Store":
                file_count += 1
                try:
                    size_bytes += item.stat().st_size
                except OSError:
                    continue
        return {"file_count": file_count, "size_bytes": size_bytes}

    @classmethod
    def _path_entries_stats(cls, entries: list[dict[str, str]]) -> tuple[int, int]:
        stats = [cls._path_stats(Path(item["absolute_path"])) for item in entries]
        return sum(item["file_count"] for item in stats), sum(item["size_bytes"] for item in stats)

    @staticmethod
    def _package_state(manifest_health: str, state_health: str) -> str:
        if manifest_health == "valid" and state_health == "valid":
            return "package_and_state"
        if manifest_health == "valid":
            return "package_only"
        if state_health == "valid":
            return "state_only"
        return "partial_or_invalid"

    @staticmethod
    def _latest_timestamp(*values: Any) -> str | None:
        candidates = []
        for value in values:
            if isinstance(value, Path):
                if value.exists():
                    candidates.append(datetime.fromtimestamp(value.stat().st_mtime, timezone.utc).isoformat())
            elif value:
                candidates.append(str(value))
        return max(candidates, default=None)

    @staticmethod
    def _document_group(groups: dict[str, dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
        document_id = str(row.get("document_id") or legacy_document_id(str(row["run_id"])))
        group = groups.setdefault(
            document_id,
            {
                "document_id": document_id,
                "document_label": row.get("document_label") or f"Document {document_id[-12:]}",
                "source_pdf_sha256": row.get("source_pdf_sha256"),
                "ocr_runs": [],
                "unlinked_ir_runs": [],
            },
        )
        if row.get("document_label"):
            group["document_label"] = row["document_label"]
        if row.get("source_pdf_sha256"):
            group["source_pdf_sha256"] = row["source_pdf_sha256"]
        return group

    @staticmethod
    def _run_sort_key(item: dict[str, Any]) -> tuple[str, str]:
        return str(item.get("updated_at") or ""), str(item.get("run_id") or "")

    @staticmethod
    def _message(code: str, target_id: str, message: str) -> dict[str, str]:
        return {"code": code, "target_id": target_id, "message": message}

    @staticmethod
    def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in plan.items() if key not in {"path_entries", "fingerprint", "request"}}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
