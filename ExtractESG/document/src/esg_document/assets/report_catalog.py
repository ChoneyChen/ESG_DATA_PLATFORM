from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from esg_document.utils.hashing import sha256_file
from esg_runtime.lifecycle import (
    LifecycleBlocked,
    PublicationReferenceGuard,
    blocker,
    require_reason,
)
from esg_runtime.trash import RecoverableTrash


CATALOG_SCHEMA = "esg-report-asset-catalog-v1"
CATALOG_FILENAME = "report-catalog.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportAssetCatalog:
    """Scans one dedicated PDF directory and derives OCR completion from manifests."""

    def __init__(
        self,
        root: Path,
        ocr_output_root: Path,
        *,
        reference_guard=None,
        pipeline_queue_db=None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.ocr_output_root = ocr_output_root.expanduser().resolve()
        self.catalog_path = self.root / CATALOG_FILENAME
        self._lock = threading.RLock()
        self.lifecycle_path = self.root / ".report-lifecycle.json"
        self.reference_guard = reference_guard or PublicationReferenceGuard()
        self.pipeline_queue_db = pipeline_queue_db
        self.trash = RecoverableTrash(
            self.root / ".recycle", [self.root], guard=self.reference_guard
        )
        self.root.mkdir(parents=True, exist_ok=True)

    def refresh(self, *, include_archived: bool = False) -> dict[str, Any]:
        with self._lock:
            previous = self._load_raw()
            previous_by_name = {
                str(item.get("file_name")): item
                for item in previous.get("assets", [])
                if isinstance(item, dict)
            }
            ocr_by_sha = self._ocr_runs_by_sha()
            lifecycle_records = self._read_lifecycle()
            assets: list[dict[str, Any]] = []
            for path in sorted(
                self.root.iterdir(), key=lambda item: item.name.casefold()
            ):
                if not path.is_file() or path.suffix.casefold() != ".pdf":
                    continue
                stat = path.stat()
                cached = previous_by_name.get(path.name) or {}
                if (
                    cached.get("size_bytes") == stat.st_size
                    and cached.get("modified_ns") == stat.st_mtime_ns
                    and cached.get("sha256")
                ):
                    digest = str(cached["sha256"])
                else:
                    digest = sha256_file(path)
                runs = ocr_by_sha.get(digest, [])
                lifecycle = lifecycle_records.get(
                    f"pdf-{digest[:24]}", {"state": "active", "revision": 0}
                )
                assets.append(
                    {
                        "asset_id": f"pdf-{digest[:24]}",
                        "file_name": path.name,
                        "relative_path": path.name,
                        "size_bytes": stat.st_size,
                        "modified_ns": stat.st_mtime_ns,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc
                        ).isoformat(),
                        "sha256": digest,
                        "ocr_status": "processed" if runs else "unprocessed",
                        "ocr_run_count": len(runs),
                        "ocr_runs": runs,
                        "lifecycle": lifecycle,
                        "available_actions": [
                            {
                                "action": "restore"
                                if lifecycle["state"] == "archived"
                                else "archive",
                                "label": "恢复报告"
                                if lifecycle["state"] == "archived"
                                else "归档报告",
                                "endpoint": f"/api/report-assets/pdf-{digest[:24]}/"
                                + (
                                    "restore"
                                    if lifecycle["state"] == "archived"
                                    else "archive"
                                ),
                                "method": "POST",
                                "requires_reason": True,
                                "calls_model": False,
                                "incurs_cost": False,
                            }
                        ],
                    }
                )
            payload = {
                "schema_version": CATALOG_SCHEMA,
                "root": str(self.root),
                "updated_at": _utc_now(),
                "asset_count": len(assets),
                "processed_count": sum(
                    item["ocr_status"] == "processed" for item in assets
                ),
                "unprocessed_count": sum(
                    item["ocr_status"] == "unprocessed" for item in assets
                ),
                "assets": assets,
            }
            self._write_atomic(payload)
            if not include_archived:
                payload = {
                    **payload,
                    "assets": [
                        item
                        for item in assets
                        if item["lifecycle"]["state"] == "active"
                    ],
                }
                payload["asset_count"] = len(payload["assets"])
                payload["archived_count"] = len(assets) - payload["asset_count"]
                payload["processed_count"] = sum(
                    item["ocr_status"] == "processed" for item in payload["assets"]
                )
                payload["unprocessed_count"] = sum(
                    item["ocr_status"] == "unprocessed" for item in payload["assets"]
                )
            return payload

    def get(self, asset_id: str) -> dict[str, Any]:
        payload = self.refresh(include_archived=True)
        asset = next(
            (item for item in payload["assets"] if item["asset_id"] == asset_id),
            None,
        )
        if asset is None:
            raise KeyError(asset_id)
        return asset

    def resolve(self, asset_id: str) -> Path:
        asset = self.get(asset_id)
        path = (self.root / str(asset["relative_path"])).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Report asset escapes the configured directory") from exc
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def add(self, file_name: str, source: BinaryIO) -> dict[str, Any]:
        safe_name = Path(file_name).name
        if not safe_name.casefold().endswith(".pdf"):
            raise ValueError("Only PDF report assets are accepted")
        destination = (self.root / safe_name).resolve()
        try:
            destination.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Invalid report asset name") from exc
        if destination.exists():
            raise FileExistsError(safe_name)
        with self._lock:
            fd, temp_name = tempfile.mkstemp(
                prefix=".upload-", suffix=".pdf", dir=self.root
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    shutil.copyfileobj(source, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                Path(temp_name).replace(destination)
            except Exception:
                Path(temp_name).unlink(missing_ok=True)
                raise
        return self.get(f"pdf-{sha256_file(destination)[:24]}")

    def delete(self, asset_id: str) -> dict[str, Any]:
        raise ValueError("请使用 LibraryDeletionService 删除 PDF 及所属版本。")

    def _read_lifecycle(self):
        if not self.lifecycle_path.exists():
            return {}
        records = json.loads(self.lifecycle_path.read_text(encoding="utf-8"))
        if not isinstance(records, dict):
            raise ValueError("报告生命周期记录损坏，操作已停止。")
        return records

    def _write_lifecycle(self, records):
        temporary = self.lifecycle_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.lifecycle_path)

    def lifecycle_description(self, asset_id):
        from esg_runtime.production_references import production_references

        asset = self.get(asset_id)
        references = self.reference_guard.check(
            {"source_sha256": asset["sha256"], "job_ids": [], "ir_run_ids": []}
        )
        blockers = list(references.get("blockers", []))
        if asset["ocr_runs"]:
            blockers.append(
                blocker(
                    "processed_report_reference",
                    "已有识别成果引用此PDF；先管理相关处理成果。",
                    run_ids=[r["run_id"] for r in asset["ocr_runs"]],
                )
            )
        if self.pipeline_queue_db and Path(self.pipeline_queue_db).is_file():
            try:
                with sqlite3.connect(
                    Path(self.pipeline_queue_db).resolve().as_uri() + "?mode=ro",
                    uri=True,
                ) as conn:
                    active = conn.execute(
                        "SELECT task_id FROM pipeline_tasks WHERE status IN ('queued','running') AND "
                        "(json_extract(payload_json,'$.asset_id')=? OR json_extract(payload_json,'$.source_sha256')=?)",
                        (asset_id, asset["sha256"]),
                    ).fetchall()
                    if active:
                        blockers.append(
                            blocker(
                                "active_report_task",
                                "报告仍被活动任务使用。",
                                task_ids=[r[0] for r in active],
                            )
                        )
                    tables = {
                        r[0]
                        for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    if "report_processing_runs" in tables:
                        runs = conn.execute(
                            "SELECT processing_id,status FROM report_processing_runs WHERE asset_id=? AND status NOT IN ('discarded','purged')",
                            (asset_id,),
                        ).fetchall()
                        if runs:
                            blockers.append(
                                blocker(
                                    "report_processing_reference",
                                    "报告处理记录仍保留此来源。",
                                    processing_ids=[r[0] for r in runs],
                                )
                            )
                    reservations = production_references(
                        conn, asset_id=asset_id, source_sha256=asset["sha256"]
                    )
                    if reservations:
                        blockers.append(
                            blocker(
                                "production_report_reference",
                                "自动生产计划仍引用此PDF；先结束或取消相关计划。",
                                automation_ids=sorted(
                                    {r["automation_id"] for r in reservations}
                                ),
                            )
                        )
            except (sqlite3.Error, ValueError):
                blockers.append(
                    blocker(
                        "report_reference_check_unavailable",
                        "无法核查报告处理依赖，暂不能清理来源。",
                    )
                )
        return {
            "schema_version": "lifecycle-v1",
            **asset["lifecycle"],
            "asset_id": asset_id,
            "blockers": blockers,
            "dependencies": references.get("dependencies", []),
            "available_actions": asset["available_actions"]
            + (
                []
                if blockers
                else [
                    {
                        "action": "discard",
                        "label": "移入回收区",
                        "endpoint": f"/api/report-assets/{asset_id}/discard",
                        "method": "POST",
                        "requires_reason": True,
                        "requires_confirmation": True,
                        "calls_model": False,
                        "incurs_cost": False,
                    }
                ]
            ),
        }

    def lifecycle(self, asset_id, action, *, reason, expected_revision=None):
        reason = require_reason(reason)
        with self._lock:
            records = self._read_lifecycle()
            current = records.get(asset_id, {"state": "active", "revision": 0})
            if (
                expected_revision is not None
                and expected_revision != current["revision"]
            ):
                raise LifecycleBlocked(
                    [blocker("revision_conflict", "报告状态已变化；请刷新后重试。")]
                )
            if action == "restore" and current["state"] == "discarded":
                self.trash.restore(current["cleanup_id"], reason=reason)
            else:
                asset = self.get(asset_id)
                if action == "discard":
                    description = self.lifecycle_description(asset_id)
                    if description["blockers"]:
                        raise LifecycleBlocked(description["blockers"])
                    receipt = self.trash.quarantine(
                        [self.resolve(asset_id)],
                        scope={"source_sha256": asset["sha256"]},
                        reason=reason,
                        metadata={"asset_id": asset_id, "asset": asset},
                    )
                    current = {**current, "cleanup_id": receipt["cleanup_id"]}
            target = {
                "archive": "archived",
                "restore": "active",
                "discard": "discarded",
            }[action]
            records[asset_id] = {
                **current,
                "state": target,
                "reason": reason,
                "revision": current["revision"] + 1,
                "updated_at": _utc_now(),
            }
            try:
                self._write_lifecycle(records)
                payload = self.refresh()
            except Exception:
                if action == "discard" and current.get("cleanup_id"):
                    self.trash.restore(
                        current["cleanup_id"], reason="记录更新失败，恢复PDF来源。"
                    )
                raise
            return {
                "asset_id": asset_id,
                "operation": action,
                "deleted": False,
                "lifecycle": records[asset_id],
                "remaining_asset_count": payload["asset_count"],
            }

    def restore_receipt(self, cleanup_id, *, reason):
        with self._lock:
            receipt = self.trash.get(cleanup_id)
            asset_id = receipt["metadata"]["asset_id"]
            current = self._read_lifecycle().get(asset_id, {"state": "active"})
            if (
                current["state"] == "discarded"
                and current.get("cleanup_id") == cleanup_id
            ):
                return self.lifecycle(asset_id, "restore", reason=reason)
            # A timeout or crash may precede the catalog checkpoint. Recover
            # its journal directly without changing a later archive decision.
            return self.trash.restore(cleanup_id, reason=reason)

    def _ocr_runs_by_sha(self) -> dict[str, list[dict[str, Any]]]:
        output: dict[str, list[dict[str, Any]]] = {}
        if not self.ocr_output_root.is_dir():
            return output
        for path in self.ocr_output_root.glob("*/manifest.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            source = manifest.get("source")
            source = source if isinstance(source, dict) else {}
            digest = str(source.get("sha256") or "")
            if len(digest) != 64:
                continue
            output.setdefault(digest, []).append(
                {
                    "run_id": str(manifest.get("run_id") or path.parent.name),
                    "provider": manifest.get("ocr_provider"),
                    "page_count": int(manifest.get("page_count") or 0),
                    # "targeted" runs OCR'd only page_count of the PDF's pages.
                    "page_selection": (manifest.get("page_selection") or {}).get(
                        "mode", "all"
                    ),
                    "completed": True,
                }
            )
        for runs in output.values():
            runs.sort(key=lambda item: str(item["run_id"]), reverse=True)
        return output

    def _load_raw(self) -> dict[str, Any]:
        if not self.catalog_path.is_file():
            return {"assets": []}
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"assets": []}
        return payload if isinstance(payload, dict) else {"assets": []}

    def _write_atomic(self, payload: dict[str, Any]) -> None:
        temporary = self.catalog_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.catalog_path)
