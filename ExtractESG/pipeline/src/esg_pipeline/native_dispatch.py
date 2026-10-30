"""Close the native-state → queue crash gap without changing artifact IDs.

Only an opaque request digest is persisted. A reserved identity may reconcile
its own unstarted native state, but never replaces a historical/running job.
"""

from hashlib import sha256
import json

from esg_pipeline.contracts import utc_now
from esg_document.storage.package_layout import new_run_id, require_run_id


def reserve_native_run(store, kind, request, step_key, *, preferred_run_id=None):
    if not step_key:
        return preferred_run_id or new_run_id(kind), None
    fingerprint = sha256(
        json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    with store._lock, store._connect() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS pipeline_native_reservations (step_key TEXT PRIMARY KEY, stage TEXT NOT NULL, request_digest TEXT NOT NULL, native_run_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)"
        )
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT stage,request_digest,native_run_id FROM pipeline_native_reservations WHERE step_key=?",
            (step_key,),
        ).fetchone()
        if existing:
            if existing[0] != kind or existing[1] != fingerprint:
                raise ValueError(
                    "同一处理动作的阶段或固定请求已变化，不能复用原生任务身份"
                )
            return existing[2], fingerprint
        run_id = preferred_run_id or new_run_id(kind)
        require_run_id(run_id, kind)
        db.execute(
            "INSERT INTO pipeline_native_reservations VALUES (?,?,?,?,?)",
            (step_key, kind, fingerprint, run_id, utc_now()),
        )
        return run_id, fingerprint


def ensure_native_state(native_store, state, step_key, request_digest):
    if step_key:
        state.summary = {
            **state.summary,
            "_plan_step_key": step_key,
            "_native_request_digest": request_digest,
        }
    try:
        native_store.create(state)
    except FileExistsError:
        existing = native_store.get(state.run_id)
        if (
            not step_key
            or not existing
            or existing.status != "queued"
            or (
                existing.summary.get("_plan_step_key"),
                existing.summary.get("_native_request_digest"),
            )
            != (step_key, request_digest)
        ):
            raise
        # This is our durable reservation's prequeue checkpoint, not a new job.
        return existing
    return state
