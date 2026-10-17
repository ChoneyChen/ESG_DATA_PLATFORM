"""Authenticated library endpoints. Authorization is supplied by the app."""
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
import os
from pydantic import BaseModel, ConfigDict, Field
from esg_runtime.lifecycle import LifecycleBlocked


class LibraryDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PdfDeleteRequest(LibraryDeleteRequest):
    expected_revision: int = Field(ge=0, strict=True)


def library_deletion_router(service, authorize):
    router = APIRouter()
    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, {"code": "library_item_not_found", "message": "库文件不存在，请刷新列表。"}) from exc
        except LifecycleBlocked as exc:
            raise HTTPException(409, {"code": "deletion_blocked", "message": str(exc), "blockers": exc.blockers}) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(409, {"code": "deletion_conflict", "message": str(exc)}) from exc
        except OSError as exc:
            raise HTTPException(500, {"code": "deletion_incomplete", "message": "文件删除未完整完成，请刷新后检查。"}) from exc

    @router.get("/api/pdf-library")
    def pdf_library():
        return call(service.pdf_library)

    @router.get("/api/report-library")
    def report_library():
        return call(service.report_library)

    @router.on_event('startup')
    def start_deletions():
        if not os.getenv('PYTEST_CURRENT_TEST'):service.start()

    @router.on_event('shutdown')
    def stop_deletions():
        service.stop()

    def deletion_result(value):
        return JSONResponse(value,status_code=202) if value.get('status') == 'deletion_pending' else value

    @router.get("/api/ocr-library")
    def library(authorization: str | None = Header(default=None)):
        return call(service.ocr_library)

    @router.get("/api/ocr-library/{run_id}")
    def description(run_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization, "read")
        return call(service.ocr_description, run_id)

    @router.delete("/api/ocr-library/{run_id}")
    def delete_ocr(run_id: str, request: LibraryDeleteRequest, authorization: str | None = Header(default=None)):
        authorize(authorization, "admin")
        return deletion_result(call(service.delete_ocr, run_id, **request.model_dump()))

    @router.get('/api/document-ir/jobs/{run_id}/deletion')
    def ir_description(run_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization,'admin')
        return call(service.ir_description,run_id)

    @router.delete('/api/document-ir/jobs/{run_id}')
    def delete_ir(run_id: str, request: LibraryDeleteRequest, authorization: str | None = Header(default=None)):
        authorize(authorization,'admin')
        return deletion_result(call(service.delete_ir,run_id,**request.model_dump()))

    @router.get("/api/report-assets/{asset_id}/deletion")
    def pdf_description(asset_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization, "admin")
        return call(service.pdf_description, asset_id)

    @router.delete("/api/report-assets/{asset_id}")
    def delete_pdf(asset_id: str, request: PdfDeleteRequest, authorization: str | None = Header(default=None)):
        authorize(authorization, "admin")
        return deletion_result(call(service.delete_pdf, asset_id, **request.model_dump()))
    return router
