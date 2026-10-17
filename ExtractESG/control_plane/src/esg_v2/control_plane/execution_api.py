"""Queue history and resumable event transport; no worker is owned by a stream."""
from __future__ import annotations

import asyncio
import json

from fastapi import HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


class HistoryCommand(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int | None = Field(default=None, ge=0)


def register_execution_api(app, store):
    @app.get("/api/pipeline/history")
    def history(limit: int = Query(300, ge=1, le=1000)):
        return {"schema_version": "queue-history-v1", "items": [task.model_dump(mode="json")
            for task in store.list(limit, include_archived=True) if task.archived_at]}

    def command(task_id, request, restore):
        try:
            action = store.restore if restore else store.archive
            return action(task_id, request.reason, expected_revision=request.expected_revision)
        except KeyError as exc:
            raise HTTPException(404, "任务记录不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, {"code": "lifecycle_conflict", "message": str(exc)}) from exc

    @app.post("/api/pipeline/tasks/{task_id}/archive")
    def archive_task(task_id: str, request: HistoryCommand):
        return command(task_id, request, False)

    @app.post("/api/pipeline/tasks/{task_id}/restore")
    def restore_task(task_id: str, request: HistoryCommand):
        return command(task_id, request, True)

    @app.get("/api/pipeline/events/stream")
    async def event_stream(request: Request, after: int = Query(0, ge=0), task_id: str | None = None):
        if task_id:
            try:
                store.get(task_id)
            except KeyError as exc:
                raise HTTPException(404, "任务记录不存在") from exc
        try:
            cursor = max(after, int(request.headers.get("last-event-id") or 0))
        except ValueError as exc:
            raise HTTPException(400, "事件游标无效") from exc

        async def stream():
            nonlocal cursor
            heartbeat = 0
            while not await request.is_disconnected():
                events = await asyncio.to_thread(store.events, task_id, cursor, limit=200)
                for event in events:
                    cursor = event["event_id"]
                    yield f"id: {cursor}\nevent: execution\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if not events:
                    heartbeat += 1
                    if heartbeat % 10 == 0:
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
