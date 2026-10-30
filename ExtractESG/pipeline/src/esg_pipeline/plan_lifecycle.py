"""Durable parent lifecycle commands across the platform and extraction domain.

The intent is saved before the remote command. A retry reconciles the remote
state before changing local visibility. Queue rows and evidence pins remain
available for recovery and audit; discard only targets guarded native outputs.
"""

from __future__ import annotations

import json
import hashlib
from esg_pipeline.contracts import utc_now
from esg_pipeline.task_references import resolve_task_references


class PlanLifecycle:
    def __init__(self, coordinator, native_request):
        self.coordinator, self.store, self.native_request = (
            coordinator,
            coordinator.store,
            native_request,
        )
        with self.store._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS pipeline_plan_events "
                "(event_id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL, created_at TEXT NOT NULL, detail_json TEXT NOT NULL)"
            )

    def _plan(self, plan_id):
        plan = next(
            (p for p in self.coordinator.list() if p["plan_id"] == plan_id), None
        )
        if plan is None:
            raise KeyError(plan_id)
        return plan

    def _tasks(self, plan):
        return resolve_task_references(self.store, plan)["tasks"]

    def _manifest_fingerprint(self, run_id):
        manifest = self.coordinator.inspect_ir(run_id)
        return (
            hashlib.sha256(
                json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if manifest
            else None
        )

    def describe(self, plan, native=None):
        value = {
            "schema_version": "lifecycle-v1",
            "state": "active",
            "revision": 0,
            **plan.get("lifecycle", {}),
            "available_actions": [],
            "blockers": [],
            "dependencies": [],
        }
        references = resolve_task_references(self.store, plan)
        tasks = references["tasks"]
        active = []
        for task in tasks:
            try:
                alive = self.store.worker_alive(task.task_id)
            except KeyError:
                if task.task_id not in references["missing_task_ids"]:
                    references["missing_task_ids"].append(task.task_id)
                continue  # A row can disappear between the two read snapshots.
            if task.status.value in {"queued", "running"} or alive:
                active.append(task.task_id)
        integrity = references["reference_integrity"]
        if references["missing_task_ids"] and integrity["status"] == "complete":
            integrity["status"] = "missing"
        integrity["resolved_task_count"] = integrity["expected_task_count"] - len(
            references["missing_task_ids"]
        )
        value.update(
            reference_integrity=integrity,
            missing_task_ids=references["missing_task_ids"],
            invalid_reference_paths=references["invalid_reference_paths"],
        )
        if references["missing_task_ids"]:
            value["blockers"].append(
                {
                    "code": "task_reference_missing",
                    "message": "部分队列记录已缺失；报告和已保存成果仍可查看，缺失任务不会自动重新调度，记录仍可删除。",
                    "task_ids": references["missing_task_ids"],
                }
            )
        if references["invalid_reference_paths"]:
            value["blockers"].append(
                {
                    "code": "task_reference_invalid",
                    "message": "部分任务引用或已删除阶段无法确认；可以查看和删除记录，继续执行需先核对任务。",
                    "paths": references["invalid_reference_paths"],
                }
            )
        if plan.get("status") in {"active", "cancelling"} or active:
            value["blockers"].append(
                {
                    "code": "parent_active",
                    "message": "先取消任务并等待执行退出，再归档或丢弃。",
                    "task_ids": active,
                }
            )
        if plan.get("lifecycle_intent"):
            value["pending_command"] = plan["lifecycle_intent"]
        if native:
            value["dependencies"] = native.get("dependencies", [])
            value["reference_check"] = native.get("reference_check")
        if not value["blockers"]:
            commands = ["restore"] if value["state"] != "active" else ["archive"]
            documents = plan.get("documents") or []
            native_id = (
                documents[0].get("native_session_id")
                if documents and isinstance(documents[0], dict)
                else None
            )
            if native_id and value["state"] != "discarded":
                commands.append("discard")
            for command in commands:
                value["available_actions"].append(
                    {
                        "action": command,
                        "label": {
                            "archive": "归档报告任务",
                            "restore": "恢复报告任务",
                            "discard": "丢弃未入库成果（进入回收区）",
                        }[command],
                        "endpoint": f"/api/report-sessions/{plan['plan_id']}/{command}",
                        "method": "POST",
                        "payload": {"expected_revision": value["revision"]},
                        "requires_reason": True,
                        "requires_confirmation": command == "discard",
                        "calls_model": False,
                        "incurs_cost": False,
                    }
                )
        return value

    def operate(self, plan_id, action, reason, expected_revision=None):
        if action not in {"archive", "restore", "discard"}:
            raise ValueError("不支持此生命周期操作")
        if not reason.strip():
            raise ValueError("操作原因不能为空")
        target = {"archive": "archived", "restore": "active", "discard": "discarded"}[
            action
        ]
        with self.coordinator._lock:
            plan = self._plan(plan_id)
            lifecycle = self.describe(plan)
            if (
                expected_revision is not None
                and expected_revision != lifecycle["revision"]
            ):
                raise ValueError("记录已变化，请刷新后重新操作")
            if lifecycle["blockers"]:
                raise ValueError(lifecycle["blockers"][0]["message"])
            if lifecycle["state"] == target and not plan.get("lifecycle_intent"):
                return lifecycle
            if action == "archive" and lifecycle["state"] == "discarded":
                raise ValueError("已丢弃成果应先恢复")
            pending = plan.get("lifecycle_intent")
            if pending and pending["action"] != action:
                raise ValueError("上次操作尚待协调完成，请重试该操作")
            native_id = plan["documents"][0].get("native_session_id")
            if action == "discard" and not native_id:
                raise ValueError(
                    "尚无独立提取成果可丢弃；可归档记录，来源由报告处理管理"
                )
            tasks = self._tasks(plan)
            owners = [plan_id, *[task.task_id for task in tasks]]
            released_pins = lifecycle.get("released_pins", [])
            if action == "restore":
                missing = sorted(
                    {
                        pin["ir_run_id"]
                        for pin in released_pins
                        if not self._manifest_fingerprint(pin["ir_run_id"])
                        or self._manifest_fingerprint(pin["ir_run_id"])
                        != pin.get("manifest_sha256")
                    }
                )
                if missing:
                    raise ValueError(
                        "请先恢复原始证据版本；当前来源缺失或已变化："
                        + "、".join(missing)
                    )
            with self.store._connect() as connection:
                pins = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM ir_input_pins WHERE owner_id IN (%s)"
                        % ",".join("?" for _ in owners),
                        owners,
                    )
                ]
            if action == "discard" and not pending:
                pins = [
                    {
                        **pin,
                        "manifest_sha256": self._manifest_fingerprint(pin["ir_run_id"]),
                    }
                    for pin in pins
                ]
                if any(not pin["manifest_sha256"] for pin in pins):
                    raise ValueError(
                        "存在无法确认的证据版本，请先检查来源；记录仍可归档"
                    )
            plan["lifecycle_intent"] = pending or {
                "action": action,
                "reason": reason,
                "created_at": utc_now(),
                "queue_ids_to_archive": [
                    task.task_id for task in tasks if not task.archived_at
                ],
                "pins_to_release": pins,
            }
            self.coordinator.save(plan)
            native = None
            if native_id:
                try:
                    native = self.native_request(
                        "GET", f"/api/report-sessions/{native_id}/lifecycle"
                    )
                    if native.get("state") != target:
                        native = self.native_request(
                            "POST",
                            f"/api/report-sessions/{native_id}/{action}",
                            {
                                "reason": reason,
                                "expected_revision": native.get("revision", 0),
                            },
                        )
                    if native.get("state") != target:
                        raise ValueError(
                            "提取后端尚未确认生命周期结果，请刷新并重试原操作"
                        )
                except Exception as exc:
                    # A definite rejection has no remote effect. Unknown network
                    # outcomes retain the intent for safe reconciliation.
                    if getattr(exc, "status_code", None) in {400, 404, 409, 422}:
                        plan.pop("lifecycle_intent", None)
                        self.coordinator.save(plan)
                    raise
            intent = plan["lifecycle_intent"]
            archived_ids = list(
                dict.fromkeys(
                    [
                        *lifecycle.get("archived_queue_ids", []),
                        *intent.get("queue_ids_to_archive", []),
                    ]
                )
            )
            for task in tasks:
                if action == "restore":
                    if task.task_id in archived_ids and task.archived_at:
                        self.store.restore(task.task_id, reason)
                elif not task.archived_at:
                    self.store.archive(task.task_id, reason)
                    if task.task_id not in archived_ids:
                        archived_ids.append(task.task_id)
            if action == "discard":
                released_pins = intent.get("pins_to_release", [])
            elif action == "restore":
                released_pins = []
            plan["lifecycle"] = {
                "state": target,
                "revision": lifecycle["revision"] + 1,
                "reason": reason,
                "updated_at": utc_now(),
                "archived_queue_ids": archived_ids,
                "released_pins": released_pins,
            }
            plan.pop("lifecycle_intent", None)
            plan["updated_at"] = utc_now()
            with self.store._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if action == "discard":
                    connection.execute(
                        "DELETE FROM ir_input_pins WHERE owner_id IN (%s)"
                        % ",".join("?" for _ in owners),
                        owners,
                    )
                elif action == "restore":
                    for pin in lifecycle.get("released_pins", []):
                        connection.execute(
                            "INSERT OR IGNORE INTO ir_input_pins VALUES(?,?,?)",
                            (pin["owner_id"], pin["ir_run_id"], utc_now()),
                        )
                connection.execute(
                    "UPDATE pipeline_plans SET state_json=? WHERE plan_id=?",
                    (json.dumps(plan, ensure_ascii=False), plan_id),
                )
                connection.execute(
                    "INSERT INTO pipeline_plan_events(plan_id,created_at,detail_json) VALUES(?,?,?)",
                    (
                        plan_id,
                        utc_now(),
                        json.dumps(
                            {"action": action, **plan["lifecycle"]}, ensure_ascii=False
                        ),
                    ),
                )
            return self.describe(plan, native)
