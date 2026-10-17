"""Small read projection for the global processing window, no execution owner."""
from fastapi import APIRouter


def processing_status_router(store):
    router = APIRouter()
    def project(identity):
        fields = ("task_id", "task_type", "operation", "title", "status", "stage", "progress", "timing", "error_detail", "revision")
        task = store.get(identity).model_dump(mode="json", include=set(fields) | {"available_actions"})
        result = {key: task.get(key) for key in fields}
        result["available_actions"] = [a for a in task.get("available_actions", []) if a.get("action") == "cancel"]
        return result
    @router.get("/api/processing-status")
    def status():
        with store._connect() as db:
            counts = dict(db.execute("SELECT status,COUNT(*) FROM pipeline_tasks WHERE archived_at IS NULL GROUP BY status"))
            active = [r[0] for r in db.execute("SELECT task_id FROM pipeline_tasks WHERE status IN ('running','queued') AND archived_at IS NULL ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,created_at LIMIT 50")]
            recent = [r[0] for r in db.execute("SELECT task_id FROM pipeline_tasks WHERE status NOT IN ('running','queued') AND archived_at IS NULL ORDER BY updated_at DESC LIMIT 8")]
        def retained(ids):
            result=[]
            for identity in ids:
                try: result.append(project(identity))
                except KeyError: continue
            return result
        return {"schema_version": "processing-status-v1", "running_count": counts.get("running", 0),
            "queued_count": counts.get("queued", 0), "active_tasks": retained(active), "recent_tasks": retained(recent),
            "active_truncated": counts.get("running", 0)+counts.get("queued", 0)>50}
    return router
