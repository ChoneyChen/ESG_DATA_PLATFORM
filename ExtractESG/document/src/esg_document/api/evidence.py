from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from esg_contracts.evidence import SearchIntent, ObjectReference, ObjectSearchPage, DocumentReference, EvidenceFrame
from esg_document.document.evidence_service import DocumentEvidenceService
from esg_contracts.search import PageSearchIntent


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: ObjectReference
    chunk_index: int = Field(default=0, ge=0)


class PageLocationRequest(BaseModel):
    target: PageSearchIntent
    excluded_pages: list[int] = Field(default_factory=list)
    limit: int = Field(default=16, ge=1, le=100)


def evidence_router(resolver, resource_lookup=None, pdf_resolver=None):
    router = APIRouter(prefix="/api/evidence")
    service = DocumentEvidenceService(resolver, resource_lookup)

    def call(fn, *args):
        try:
            return fn(*args)
        except (FileNotFoundError, KeyError) as exc:
            raise HTTPException(404, "Document evidence does not exist") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{ir_run_id}", response_model=DocumentReference)
    def document(ir_run_id: str):
        return call(service.document, ir_run_id)

    @router.post("/{ir_run_id}/search", response_model=ObjectSearchPage)
    def search(ir_run_id: str, request: SearchIntent):
        return call(service.search_page, ir_run_id, request)

    @router.post("/{ir_run_id}/read", response_model=EvidenceFrame)
    def read(ir_run_id: str, request: ReadRequest):
        return call(service.read, ir_run_id, request.reference, request.chunk_index)

    @router.post("/{ir_run_id}/locate-pages")
    def locate_pages(ir_run_id: str, request: PageLocationRequest):
        from esg_document.ocr.page_selection import ClausePageSelector
        document = call(service.document, ir_run_id)
        if pdf_resolver is None:
            raise HTTPException(404, "Original PDF is not attached")
        path = call(pdf_resolver, document)
        selection = ClausePageSelector(search_targets=[request.target.model_dump(mode="json")]).select(path)
        excluded = set(request.excluded_pages) | set((document.search_scope or {}).get("prepared_pages", []))
        candidates = selection.page_indices if selection.page_indices is not None else range(selection.source_page_count)
        remaining = [index for index in candidates if index not in excluded]
        return {"page_indices": sorted(remaining[:request.limit]), "remaining": len(remaining),
            "source_page_count": selection.source_page_count, "reason": selection.reason}

    return router
