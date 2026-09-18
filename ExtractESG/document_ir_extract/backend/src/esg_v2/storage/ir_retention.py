from __future__ import annotations

import fcntl
import json
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from esg_v2.control_plane.store import PipelineQueueStore
from esg_v2.storage.package_layout import package_dir, require_package_dir_name


RESOLVED_REVIEW_STATUSES = {"auto_resolved", "reviewed", "done"}
ACTIVE_JOB_STATUSES = {"queued", "running"}
READINESS_RANK = {
    "failed": 0,
    "repair_required": 1,
    "auto_review_pending": 2,
    "review_required": 3,
    "ready_with_warnings": 4,
    "ready": 5,
}


class DocumentIrRetentionManager:
    """Keep one best self-contained Document IR package for each OCR run.

    Packages stay immutable while present. A newly written package is promoted only
    when it is the strongest candidate for its OCR run; older packages are then
    removed through a rollback-capable filesystem transaction.
    """

    def __init__(
        self,
        *,
        ir_output_root: Path,
        ir_state_root: Path,
        cleanup_root: Path,
        pipeline_queue_db: Path | None = None,
        enabled: bool = True,
    ) -> None:
        self.ir_output_root = ir_output_root
        self.ir_state_root = ir_state_root
        self.cleanup_root = cleanup_root
        self.pipeline_queue_db = pipeline_queue_db
        self.enabled = enabled

    def reconcile_safely(self, produced_run_id: str) -> dict[str, Any]:
        try:
            return self.reconcile(produced_run_id)
        except Exception as exc:
            result = self._result(
                "cleanup_failed",
                produced_run_id,
                produced_run_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            audit_error = self._append_audit_safely(result)
            if audit_error:
                result["audit_error"] = audit_error
            return result

    def reconcile(self, produced_run_id: str) -> dict[str, Any]:
        require_package_dir_name(produced_run_id)
        if not self.enabled:
            return self._result("disabled", produced_run_id, produced_run_id)

        with self._exclusive_lock():
            produced = self._profile(produced_run_id)
            if produced is None:
                raise FileNotFoundError(
                    f"Completed Document IR manifest not found: {produced_run_id}"
                )
            candidates = self._profiles_for_ocr(str(produced["ocr_run_id"]))
            winner = max(candidates, key=self._profile_sort_key)
            if winner["run_id"] != produced_run_id:
                self._write_current_record(winner, [], [])
                result = self._result(
                    "candidate_not_promoted",
                    produced_run_id,
                    str(winner["run_id"]),
                    candidate_count=len(candidates),
                    produced_quality=produced["quality"],
                    retained_quality=winner["quality"],
                )
                audit_error = self._append_audit_safely(result)
                if audit_error:
                    result["audit_error"] = audit_error
                return result

            superseded = [
                str(item["run_id"])
                for item in candidates
                if item["run_id"] != produced_run_id
            ]
            if not superseded:
                self._write_current_record(produced, [], [])
                result = self._result(
                    "retained_only",
                    produced_run_id,
                    produced_run_id,
                    candidate_count=1,
                    produced_quality=produced["quality"],
                )
                audit_error = self._append_audit_safely(result)
                if audit_error:
                    result["audit_error"] = audit_error
                return result

            queue = self._queue_store()
            rebased_tasks: list[str] = []
            protected: set[str] = set()
            if queue is not None:
                rebased_tasks = queue.rebase_queued_ir_references(
                    superseded,
                    produced_run_id,
                    exclude_native_job_id=produced_run_id,
                )
                protected.update(
                    queue.active_ir_references(
                        superseded,
                        exclude_native_job_id=produced_run_id,
                    )
                )
            protected.update(self._active_native_runs(superseded))
            prunable = [run_id for run_id in superseded if run_id not in protected]
            pruned = self._prune_transaction(
                prunable,
            )
            self._write_current_record(produced, pruned, sorted(protected))
            result = self._result(
                "promoted_and_pruned" if pruned else "promoted_cleanup_deferred",
                produced_run_id,
                produced_run_id,
                candidate_count=len(candidates),
                pruned_run_ids=pruned,
                protected_run_ids=sorted(protected),
                rebased_task_ids=rebased_tasks,
                produced_quality=produced["quality"],
            )
            audit_error = self._append_audit_safely(
                result | {"ocr_run_id": produced["ocr_run_id"]}
            )
            if audit_error:
                result["audit_error"] = audit_error
            return result

    def _profiles_for_ocr(self, ocr_run_id: str) -> list[dict[str, Any]]:
        profiles = []
        if not self.ir_output_root.exists():
            return profiles
        for manifest_path in self.ir_output_root.glob("*/manifest.json"):
            profile = self._profile(manifest_path.parent.name)
            if profile and profile["ocr_run_id"] == ocr_run_id:
                profiles.append(profile)
        return profiles

    def _profile(self, run_id: str) -> dict[str, Any] | None:
        root = package_dir(self.ir_output_root, run_id)
        manifest = self._read_object(root / "manifest.json")
        if not manifest:
            return None
        if str(manifest.get("run_id") or run_id) != run_id:
            return None
        review_index = self._read_object(root / "review" / "index.json") or {}
        validation = self._read_object(root / "quality" / "validation-report.json") or {}
        tasks = review_index.get("tasks") if isinstance(review_index.get("tasks"), list) else []
        unresolved = [
            item
            for item in tasks
            if isinstance(item, dict)
            and str(item.get("status") or "") not in RESOLVED_REVIEW_STATUSES
        ]
        unresolved_blocking = sum(bool(item.get("blocking")) for item in unresolved)
        human_resolved = sum(
            str(item.get("status") or "") == "reviewed"
            for item in tasks
            if isinstance(item, dict)
        )
        metrics = validation.get("metrics") if isinstance(validation.get("metrics"), dict) else {}
        issues = validation.get("issues") if isinstance(validation.get("issues"), list) else []
        blocking_issues = sum(
            isinstance(item, dict) and item.get("severity") == "blocking" for item in issues
        )
        error_issues = sum(
            isinstance(item, dict) and item.get("severity") == "error" for item in issues
        )
        readiness = str(manifest.get("readiness") or validation.get("readiness") or "failed")
        evidence_policy = manifest.get("evidence_policy") or {}
        limited_evidence = bool(
            manifest.get("can_build_limited_evidence")
            and evidence_policy.get("mode") == "limited"
        )
        quality = {
            "can_build_evidence": bool(manifest.get("can_build_evidence", False)),
            "can_build_limited_evidence": limited_evidence,
            "available_page_count": (
                int(evidence_policy.get("available_page_count") or 0)
                if limited_evidence else 0
            ),
            "readiness": readiness,
            "blocking_issue_count": blocking_issues,
            "error_issue_count": error_issues,
            "blocking_unresolved_count": unresolved_blocking,
            "total_unresolved_count": len(unresolved),
            "optional_unresolved_count": int(metrics.get("optional_unresolved_count") or 0),
            "human_resolved_count": human_resolved,
        }
        return {
            "run_id": run_id,
            "ocr_run_id": str(manifest.get("ocr_run_id") or ""),
            "document_id": manifest.get("document_id"),
            "lineage_id": manifest.get("lineage_id"),
            "ir_revision": int(manifest.get("ir_revision") or 1),
            "written_at": str(manifest.get("written_at") or ""),
            "quality": quality,
        }

    @staticmethod
    def _profile_sort_key(profile: dict[str, Any]) -> tuple[Any, ...]:
        quality = profile["quality"]
        return (
            int(quality["can_build_evidence"]),
            int(quality["can_build_limited_evidence"]),
            int(quality["available_page_count"]),
            -int(quality["blocking_issue_count"]),
            -int(quality["error_issue_count"]),
            -int(quality["blocking_unresolved_count"]),
            READINESS_RANK.get(str(quality["readiness"]), -1),
            -int(quality["total_unresolved_count"]),
            -int(quality["optional_unresolved_count"]),
            int(quality["human_resolved_count"]),
            int(profile["ir_revision"]),
            str(profile["written_at"]),
            str(profile["run_id"]),
        )

    def _active_native_runs(self, run_ids: list[str]) -> set[str]:
        active = set()
        for run_id in run_ids:
            state = self._read_object(package_dir(self.ir_state_root, run_id) / "state.json")
            if state and str(state.get("status") or "") in ACTIVE_JOB_STATUSES:
                active.add(run_id)
        return active

    def _prune_transaction(
        self,
        run_ids: list[str],
    ) -> list[str]:
        if not run_ids:
            return []
        transaction_id = (
            f"ir-retention-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
            f"{uuid.uuid4().hex[:10]}"
        )
        trash_root = self.cleanup_root / "trash" / transaction_id
        moved: list[tuple[Path, Path]] = []
        try:
            trash_root.mkdir(parents=True, exist_ok=False)
            for run_id in run_ids:
                require_package_dir_name(run_id)
                for role, source in (
                    ("package", package_dir(self.ir_output_root, run_id)),
                    ("state", package_dir(self.ir_state_root, run_id)),
                ):
                    if not source.exists():
                        continue
                    destination = trash_root / f"{run_id}-{role}"
                    shutil.move(str(source), str(destination))
                    moved.append((source, destination))
        except Exception:
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination), str(source))
            shutil.rmtree(trash_root, ignore_errors=True)
            raise
        shutil.rmtree(trash_root, ignore_errors=True)
        return list(run_ids)

    def _queue_store(self) -> PipelineQueueStore | None:
        if self.pipeline_queue_db is None or not self.pipeline_queue_db.exists():
            return None
        return PipelineQueueStore(self.pipeline_queue_db)

    def _write_current_record(
        self,
        profile: dict[str, Any],
        pruned_run_ids: list[str],
        protected_run_ids: list[str],
    ) -> None:
        current_root = self.cleanup_root / "ir-retention" / "current"
        current_root.mkdir(parents=True, exist_ok=True)
        ocr_run_id = str(profile["ocr_run_id"])
        require_package_dir_name(ocr_run_id)
        payload = {
            "schema_version": "document-ir-retention-current-v1",
            "ocr_run_id": ocr_run_id,
            "retained_run_id": profile["run_id"],
            "document_id": profile.get("document_id"),
            "lineage_id": profile.get("lineage_id"),
            "ir_revision": profile.get("ir_revision"),
            "quality": profile["quality"],
            "pruned_run_ids": pruned_run_ids,
            "protected_run_ids": protected_run_ids,
            "updated_at": self._now(),
        }
        target = current_root / f"{ocr_run_id}.json"
        temporary = target.with_suffix(f".tmp-{uuid.uuid4().hex}")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)

    def _append_audit(self, payload: dict[str, Any]) -> None:
        self.cleanup_root.mkdir(parents=True, exist_ok=True)
        audit = self.cleanup_root / "ir-retention-audit.jsonl"
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            )

    def _append_audit_safely(self, payload: dict[str, Any]) -> str | None:
        try:
            self._append_audit(payload)
        except OSError as exc:
            return f"{type(exc).__name__}: {exc}"
        return None

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.cleanup_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.cleanup_root / "ir-retention.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_object(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _result(
        action: str,
        produced_run_id: str,
        retained_run_id: str,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "schema_version": "document-ir-retention-result-v1",
            "action": action,
            "produced_run_id": produced_run_id,
            "retained_run_id": retained_run_id,
            "pruned_run_ids": [],
            "protected_run_ids": [],
            "rebased_task_ids": [],
            "evaluated_at": DocumentIrRetentionManager._now(),
            **extra,
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
