"""Lifecycle of report-processing records; stage files have separate owners.

Discard releases only this record's reusable-artifact registrations and pins.
OCR/IR source files stay in their dependency-aware storage inventory. Archive
retains all references. Both record changes are reversible and audited.
"""

import json
import hashlib
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from esg_runtime.lifecycle import (
    LifecycleBlocked,
    SourceReferenceGuard,
    blocker,
    require_reason,
)
from esg_pipeline.source_references import extraction_references
from esg_pipeline.contracts import utc_now
from esg_document.storage.package_layout import require_package_dir_name


class ProcessingLifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=4000)
    expected_revision: int | None = Field(default=None, ge=0)


class ProcessingLifecycle:
    def __init__(self, coordinator, guard=None):
        self.coordinator = coordinator
        self.guard = guard or SourceReferenceGuard()

    @staticmethod
    def _scope(state, pins=()):
        return {
            "job_ids": [],
            "ir_run_ids": sorted(
                {pin["ir_run_id"] for pin in pins}
                | ({state["ir_run_id"]} if state.get("ir_run_id") else set())
            ),
            "source_sha256": state["source_sha256"],
        }

    @staticmethod
    def _owned_children(state, db):
        prefix = state["processing_id"] + ":"
        rows = db.execute(
            "SELECT * FROM pipeline_tasks WHERE substr(CASE WHEN json_valid(payload_json) THEN json_extract(payload_json,'$._plan_step_key') ELSE NULL END,1,?)=?",
            (len(prefix), prefix),
        ).fetchall()
        # The dispatch key is durable even if a crash preceded the parent's
        # step checkpoint. Only keys from this parent's OCR/IR attempts belong
        # to it; imported plans keep their own ownership.
        return [
            row
            for row in rows
            if re.fullmatch(
                re.escape(prefix) + r"(?:ocr|ir):\d+",
                json.loads(row["payload_json"])["_plan_step_key"],
            )
        ]

    def _owned_pins(self, state, db):
        owners = {state["processing_id"]}
        for child in self._owned_children(state, db):
            owners.add(child["task_id"])
            owners.add(json.loads(child["payload_json"])["_plan_step_key"])
        placeholders = ",".join("?" for _ in owners)
        return [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM ir_input_pins WHERE owner_id IN ({placeholders})",
                tuple(owners),
            )
        ]

    def _idle_blockers(self, state, db):
        result = []
        try:
            reservations = extraction_references(
                db, processing_id=state["processing_id"]
            )
            if reservations:
                result.append(
                    blocker(
                        "extraction_processing_reference",
                        "提取仍需要此处理记录；先结束或取消相关计划。",
                        session_ids=sorted(
                            {r["session_id"] for r in reservations}
                        ),
                    )
                )
        except ValueError:
            result.append(
                blocker(
                    "extraction_reference_check_unavailable",
                    "提取引用无法核实，暂不能整理处理记录。",
                )
            )
        if state["status"] in {"queued", "running", "cancelling"}:
            result.append(
                blocker("processing_active", "报告处理尚未停止；先取消并等待执行退出。")
            )
        ids = {
            step.get("task_id")
            for step in state.get("steps", {}).values()
            if step.get("task_id")
        }
        ids.update(child["task_id"] for child in self._owned_children(state, db))
        ids.update(
            history["task_id"]
            for step in state.get("steps", {}).values()
            for history in step.get("history", [])
            if history.get("task_id")
        )
        if ids:
            placeholders = ",".join("?" for _ in ids)
            rows = db.execute(
                f"SELECT task_id FROM pipeline_tasks WHERE task_id IN ({placeholders}) AND status IN ('queued','running')",
                tuple(ids),
            ).fetchall()
            if rows:
                result.append(
                    blocker(
                        "processing_child_active",
                        "处理子任务尚未停止。",
                        task_ids=[r[0] for r in rows],
                    )
                )
        return result

    def describe(self, state, *, check_references=False):
        with self.coordinator.store._connect() as db:
            blockers = self._idle_blockers(state, db)
            pins = self._owned_pins(state, db)
        lifecycle = state.get(
            "lifecycle", {"state": "archived" if state.get("archived_at") else "active"}
        )
        references = (
            self.guard.check(self._scope(state, pins)) if check_references else None
        )
        if references:
            blockers.extend(references.get("blockers", []))
        actions = []
        if not self._idle_blockers_cached(blockers):
            action = "restore" if lifecycle["state"] != "active" else "archive"
            actions.append(self._action(state, action))
            if lifecycle["state"] != "discarded" and not (
                references and references.get("blockers")
            ):
                actions.append(self._action(state, "discard"))
        return {
            "schema_version": "lifecycle-v1",
            **lifecycle,
            "revision": state["revision"],
            "available_actions": actions,
            "blockers": blockers,
            "dependencies": references.get("dependencies", []) if references else [],
        }

    @staticmethod
    def _idle_blockers_cached(blockers):
        return any(
            row["code"]
            in {
                "processing_active",
                "processing_child_active",
                "extraction_processing_reference",
                "extraction_reference_check_unavailable",
            }
            for row in blockers
        )

    @staticmethod
    def _action(state, action):
        return {
            "action": action,
            "label": {
                "archive": "归档处理记录",
                "restore": "恢复处理记录",
                "discard": "检查并丢弃处理记录（保留来源文件）",
            }[action],
            "endpoint": f"/api/report-processing/runs/{state['processing_id']}/{action}",
            "method": "POST",
            "payload": {"expected_revision": state["revision"]},
            "requires_reason": True,
            "requires_reference_check": action == "discard",
            "preflight_endpoint": f"/api/report-processing/runs/{state['processing_id']}/lifecycle",
            "requires_confirmation": action == "discard",
            "calls_model": False,
            "incurs_cost": False,
        }

    def command(self, identity, action, *, reason, expected_revision=None):
        reason = require_reason(reason)
        coordinator = self.coordinator
        with coordinator._lock, coordinator.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state_json FROM report_processing_runs WHERE processing_id=?",
                (identity,),
            ).fetchone()
            if not row:
                raise KeyError(identity)
            state = json.loads(row[0])
            if expected_revision is not None and state["revision"] != expected_revision:
                raise LifecycleBlocked(
                    [blocker("revision_conflict", "处理记录已变化；请刷新后操作。")]
                )
            blockers = self._idle_blockers(state, db)
            if blockers:
                raise LifecycleBlocked(blockers)
            lifecycle = state.get("lifecycle", {"state": "active"})
            if action == "discard" and lifecycle["state"] != "discarded":
                pins = self._owned_pins(state, db)
                references = self.guard.check(self._scope(state, pins))
                if not references.get("verified") or references.get("blockers"):
                    raise LifecycleBlocked(
                        references.get("blockers")
                        or [
                            blocker("reference_check_unavailable", "来源引用无法确认。")
                        ]
                    )
                artifacts = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM report_processing_artifacts WHERE processing_id=?",
                        (identity,),
                    )
                ]
                for pin in pins:
                    require_package_dir_name(pin["ir_run_id"])
                    manifest = (
                        coordinator.roots["ir"] / pin["ir_run_id"] / "manifest.json"
                    )
                    pin["manifest_sha256"] = (
                        hashlib.sha256(manifest.read_bytes()).hexdigest()
                        if manifest.is_file()
                        else None
                    )
                lifecycle = {
                    "state": "discarded",
                    "previous_status": state["status"],
                    "artifacts": artifacts,
                    "pins": pins,
                }
                db.execute(
                    "DELETE FROM report_processing_artifacts WHERE processing_id=?",
                    (identity,),
                )
                db.executemany(
                    "DELETE FROM ir_input_pins WHERE owner_id=? AND ir_run_id=?",
                    [(pin["owner_id"], pin["ir_run_id"]) for pin in pins],
                )
                state["status"] = "discarded"
            elif action == "restore" and lifecycle["state"] == "discarded":
                for artifact in lifecycle["artifacts"]:
                    path = (
                        coordinator.roots[artifact["stage"]]
                        / artifact["run_id"]
                        / "manifest.json"
                    )
                    if (
                        not path.is_file()
                        or hashlib.sha256(path.read_bytes()).hexdigest()
                        != artifact["manifest_sha256"]
                    ):
                        raise LifecycleBlocked(
                            [
                                blocker(
                                    "processing_source_unavailable",
                                    "处理来源已移入回收区或发生变化；先恢复对应来源文件。",
                                    run_id=artifact["run_id"],
                                )
                            ]
                        )
                    try:
                        coordinator._verify_manifest(
                            state,
                            artifact["stage"],
                            json.loads(path.read_text(encoding="utf-8")),
                        )
                        coordinator.integrity_check(
                            artifact["stage"], artifact["run_id"]
                        )
                    except (ValueError, OSError) as exc:
                        raise LifecycleBlocked(
                            [
                                blocker(
                                    "processing_source_unavailable",
                                    "处理来源完整性无法确认，恢复已停止。",
                                    run_id=artifact["run_id"],
                                )
                            ]
                        ) from exc
                for pin in lifecycle["pins"]:
                    require_package_dir_name(pin["ir_run_id"])
                    if "manifest_sha256" not in pin:
                        continue  # Earlier lifecycle receipts rely on artifact snapshots above.
                    manifest = (
                        coordinator.roots["ir"] / pin["ir_run_id"] / "manifest.json"
                    )
                    current = (
                        hashlib.sha256(manifest.read_bytes()).hexdigest()
                        if manifest.is_file()
                        else None
                    )
                    if current != pin["manifest_sha256"]:
                        raise LifecycleBlocked(
                            [
                                blocker(
                                    "processing_source_unavailable",
                                    "处理历史来源已变化；先恢复原始证据版本。",
                                    run_id=pin["ir_run_id"],
                                )
                            ]
                        )
                for artifact in lifecycle["artifacts"]:
                    db.execute(
                        "INSERT OR IGNORE INTO report_processing_artifacts VALUES(?,?,?,?,?,?,?)",
                        tuple(
                            artifact[k]
                            for k in (
                                "processing_id",
                                "stage",
                                "run_id",
                                "source_sha256",
                                "config_digest",
                                "manifest_sha256",
                                "created_at",
                            )
                        ),
                    )
                for pin in lifecycle["pins"]:
                    db.execute(
                        "INSERT OR IGNORE INTO ir_input_pins VALUES(?,?,?)",
                        (pin["owner_id"], pin["ir_run_id"], pin["created_at"]),
                    )
                state["status"] = lifecycle["previous_status"]
                lifecycle = {"state": "active"}
            elif action == "archive":
                if lifecycle["state"] == "discarded":
                    raise ValueError("丢弃记录应先恢复。")
                lifecycle = {"state": "archived"}
            elif action == "restore":
                lifecycle = {"state": "active"}
            elif action != "discard":
                raise ValueError("未知生命周期操作。")
            state.update(
                lifecycle={**lifecycle, "reason": reason},
                revision=state["revision"] + 1,
                updated_at=utc_now(),
            )
            if lifecycle["state"] == "active":
                state.pop("archived_at", None)
            else:
                state["archived_at"] = utc_now()
            text = json.dumps(state, ensure_ascii=False)
            db.execute(
                "UPDATE report_processing_runs SET status=?,state_json=? WHERE processing_id=?",
                (state["status"], text, identity),
            )
            db.execute(
                "INSERT INTO report_processing_events(processing_id,created_at,stage,state_json) VALUES(?,?,?,?)",
                (identity, utc_now(), "lifecycle", text),
            )
        return coordinator.detail(state)


def processing_lifecycle_router(service):
    router = APIRouter()

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, "报告处理记录不存在") from exc
        except ValueError as exc:
            raise HTTPException(
                409,
                {
                    "code": "lifecycle_blocked",
                    "message": str(exc),
                    "blockers": exc.blockers
                    if isinstance(exc, LifecycleBlocked)
                    else [],
                },
            ) from exc

    @router.get("/api/report-processing/runs/{processing_id}/lifecycle")
    def description(processing_id: str):
        return call(
            service.describe,
            call(service.coordinator.get, processing_id),
            check_references=True,
        )

    def endpoint(action):
        def command(processing_id: str, request: ProcessingLifecycleCommand):
            return call(service.command, processing_id, action, **request.model_dump())

        return command

    for action in ("archive", "restore", "discard"):
        router.add_api_route(
            f"/api/report-processing/runs/{{processing_id}}/{action}",
            endpoint(action),
            methods=["POST"],
            name=f"processing_{action}",
        )
    return router
