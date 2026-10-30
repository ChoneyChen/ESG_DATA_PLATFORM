"""Independent local ingestion service. Receipt precedes any review/publication."""
from contextlib import asynccontextmanager
import os
from zipfile import BadZipFile
from pathlib import Path
from esg_runtime.release import ModuleRelease
from fastapi import FastAPI, Request, HTTPException
from .client import DatabaseBridge, DatabaseError, module_release
from .ingestion import decode_archive, MAX_ARCHIVE_BYTES


def create_app(bridge=None):
    owned = bridge is None
    bridge = bridge or DatabaseBridge()
    release = module_release()

    @asynccontextmanager
    async def lifespan(app):
        if owned:
            bridge.start()
        yield
        if owned:
            bridge.close()

    app = FastAPI(title="Independent extraction ingestion", lifespan=lifespan)

    @app.get("/health")
    def health():
        ready = bridge.call("ingestion_health", {}).get("ready")
        if not ready:
            raise HTTPException(503, "Install the capture schema with the explicit management command")
        return {**release.snapshot(), "status": "ok", "export_contract": "extraction-export-1.0", "auto_publish": False}

    @app.post("/v1/ingestion/exports")
    async def ingest(request: Request):
        if release.snapshot()["restart_required"]:
            raise HTTPException(503, "Restart the updated receiver before ingesting")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_ARCHIVE_BYTES:
                raise HTTPException(413, "Export exceeds receiver capacity")
        try:
            payload = decode_archive(bytes(data))
            return bridge.call("ingest_export", payload)
        except DatabaseError as exc:
            raise HTTPException(409 if exc.code == "export_identity_conflict" else 503, exc.code) from exc
        except (ValueError, KeyError, TypeError, BadZipFile) as exc:
            raise HTTPException(422, "Export archive is invalid: " + str(exc)[:200]) from exc

    @app.get("/v1/ingestion/receipts/{export_id}")
    def receipt(export_id: str):
        import re
        if not re.fullmatch(r"export-[a-f0-9]{64}", export_id):
            raise HTTPException(404, "Unknown export")
        result = bridge.call("export_receipt", {"export_id": export_id})
        if result is None:
            raise HTTPException(404, "Receipt not found")
        return result

    @app.get("/v1/ingestion/exports/{export_id}/facts")
    def facts(export_id: str):
        receipt(export_id)
        return bridge.call("capture_facts", {"export_id": export_id})

    return app


app = create_app()
