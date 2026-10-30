from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from esg_contracts.evidence import SearchIntent, ObjectReference, ObjectSearchPage, DocumentReference, EvidenceFrame
from esg_document.document.evidence_service import DocumentEvidenceService


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: ObjectReference
    chunk_index: int = Field(default=0, ge=0)


def evidence_router(resolver, resource_lookup=None):
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

    return router
