"""Source lifecycle read/reservation port; no producer directories or credentials."""
from fastapi import APIRouter,Request
from .operator import require_operator
from .source_contracts import SourceScope,SourceReferences,ReserveSource,UpdateSourceReservation


def sources_router(bridge,release):
    router=APIRouter(prefix="/v1/sources/lifecycle")
    @router.post("/references",response_model=SourceReferences)
    def references(body:SourceScope):return bridge.call("source_references",body.model_dump(mode="json"))
    @router.post("/reserve")
    def reserve(body:ReserveSource,request:Request):
        return bridge.call("reserve_source",{**body.model_dump(mode="json"),"actor":require_operator(request,release)})
    @router.post("/reservations/{reservation_id}")
    def update(reservation_id:str,body:UpdateSourceReservation,request:Request):
        require_operator(request,release)
        return bridge.call("update_source_reservation",{"reservation_id":reservation_id,**body.model_dump()})
    return router
