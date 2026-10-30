"""Report resource metadata and batch intake. The report module owns these files."""

from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from esg_reports.contract import ReportResource, ReportList
from esg_reports.repository import ReportRepository
from esg_reports.importer import import_folder, import_upload
import json
from esg_runtime.local_operator import require_local_origin


class Update(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: ReportResource
    expected_revision: int = Field(ge=1)


class Import(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(min_length=1)
    preview: bool = True


def resource_router(assets, repository):
    router = APIRouter(prefix="/api/report-resources")

    def sync():
        current = assets.refresh(include_archived=False)["assets"]
        for asset in current:
            repository.ensure_source(asset["sha256"], asset["file_name"])
        return {asset["asset_id"] for asset in current}

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (FileNotFoundError, KeyError) as exc:
            raise HTTPException(404, "Report resource does not exist") from exc
        except ValidationError as exc:
            raise HTTPException(
                422,
                [
                    {k: error[k] for k in ("loc", "type", "msg") if k in error}
                    for error in exc.errors()
                ],
            ) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("")
    def reports(
        company: str | None = None,
        industry: str | None = None,
        publication_year: int | None = None,
        reporting_year: int | None = None,
        include_unavailable: bool = False,
    ) -> ReportList:
        available = sync()
        rows = repository.list(
            company=company,
            industry=industry,
            publication_year=publication_year,
            reporting_year=reporting_year,
        )
        return ReportList.model_validate(
            {
                "reports": [
                    {
                        **r.model_dump(mode="json"),
                        "missing_metadata": r.missing_metadata,
                        "asset_available": r.asset_id in available,
                    }
                    for r in rows
                    if include_unavailable or r.asset_id in available
                ]
            }
        )

    @router.post("/imports")
    def intake(request: Import, http: Request):
        require_local_origin(http)
        result = call(
            import_folder, request.folder, repository.library, preview=request.preview
        )
        if not request.preview:
            assets.refresh()
        return result

    @router.post("/uploads", status_code=201)
    def upload(http: Request, file: UploadFile = File(...), metadata: str = Form(...)):
        require_local_origin(http)
        try:
            fields = json.loads(metadata)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "Report metadata must be valid JSON") from exc
        result = call(
            import_upload, file.file, file.filename, fields, repository.library
        )
        assets.refresh()
        return result

    @router.get("/{pdf_id}")
    def get(pdf_id: str):
        sync()
        return call(repository.get, pdf_id)

    @router.put("/{pdf_id}")
    def update(pdf_id: str, request: Update, http: Request):
        require_local_origin(http)
        if pdf_id != request.resource.pdf_id:
            raise HTTPException(409, "PDF number differs from URL")
        return call(
            repository.save,
            request.resource,
            expected_revision=request.expected_revision,
        )

    return router
