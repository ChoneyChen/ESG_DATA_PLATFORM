"""Internal control and published-only read ports over the same immutable products."""
import os
from fastapi import APIRouter, HTTPException, Request, Query, Path
from fastapi.responses import Response
from esg_runtime.local_operator import require_local_origin, LOOPBACK_HOSTS
from .projection import TRANSFORM_VERSION
from .selection import preview_dataset, publish_dataset, record_restatement, preview_observations
from .products_contracts import (ProjectionStatus, ProjectionPage, DatasetRequest, DatasetPreview,
    PublishRequest, WithdrawalRequest, RestatementRequest, DatasetVersion, DatasetPage, ObservationPage, ProductDefinition, CaptureProductState, PreviewQuery, ObservationSource, Observation)
from .research import contract as research_contract, archive as research_archive, observation_stream, ResearchCapacityError
from .operator import require_operator


def product_router(bridge, release, projector):
    router = APIRouter()

    def operator(request):
        return require_operator(request,release)

    def status(export_id):
        if not bridge.call("ingestion_health", {}).get("products_ready"):
            return ProjectionStatus(export_id=export_id, status="schema_unavailable", error_code="product_schema_unavailable")
        return bridge.call("projection_status", {"export_id": export_id, "transform_version": TRANSFORM_VERSION})

    def export(snapshot_id, query, format):
        try:
            definitions=bridge.call("product_definitions", query)
            body=research_archive(observation_stream(bridge, **query),snapshot_id,format=format,definitions=definitions,query=query)
            return Response(body,media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="research-{snapshot_id}-{format}.zip"'})
        except ImportError as exc:
            raise HTTPException(503,"Install the database research dependencies for Parquet export") from exc
        except ResearchCapacityError as exc:
            raise HTTPException(413,str(exc)) from exc

    @router.get("/v1/research-contract")
    def research_schema():
        return research_contract()

    @router.get("/v1/ingestion/exports/{export_id}/projection", response_model=ProjectionStatus)
    def projection(export_id: str):
        return status(export_id)

    @router.get("/v1/ingestion/exports/{export_id}/products",response_model=CaptureProductState)
    def capture_products(export_id: str):
        return bridge.call("capture_product_state",{"export_id":export_id,"transform_version":TRANSFORM_VERSION})

    @router.post("/v1/ingestion/exports/{export_id}/projection", response_model=ProjectionStatus)
    def retry_projection(export_id: str, http: Request):
        operator(http)
        if not bridge.call("ingestion_health", {}).get("products_ready"):
            raise HTTPException(503, "Install the product schema explicitly")
        projector.project(export_id)
        return status(export_id)

    @router.get("/v1/projections", response_model=ProjectionPage)
    def projections(limit: int = Query(100, ge=1, le=1000), cursor: str | None = Query(None, max_length=4096)):
        return bridge.call("projection_catalog", {"limit": limit, "cursor": cursor})

    @router.get("/v1/projections/{projection_id}/observations", response_model=ObservationPage)
    def projection_observations(projection_id: str, limit: int = Query(200, ge=1, le=1000), cursor: str | None = Query(None, max_length=4096),
                                period: str | None = Query(None, max_length=160), clause_key: str | None = Query(None, max_length=160),
                                relation: str | None = Query(None, pattern="^(match|uncertain|different)$")):
        return bridge.call("observation_page", {"projection_id": projection_id, "limit": limit, "cursor": cursor, "period": period, "clause_key": clause_key, "relation": relation})

    @router.get("/v1/projections/{projection_id}/definitions", response_model=list[ProductDefinition])
    def projection_definitions(projection_id: str):
        return bridge.call("product_definitions", {"projection_id":projection_id})

    @router.get("/v1/projections/{projection_id}/export")
    def export_projection(projection_id: str, format: str = Query("csv",pattern="^(csv|parquet)$"),
                          period: str | None = Query(None,max_length=160),clause_key: str | None = Query(None,max_length=160)):
        return export(projection_id,{"projection_id":projection_id,"period":period,"clause_key":clause_key},format)

    @router.post("/v1/datasets/preview", response_model=DatasetPreview)
    def preview(body: DatasetRequest, http: Request):
        operator(http)
        try:
            return preview_dataset(bridge, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/v1/datasets/preview/observations",response_model=ObservationPage)
    def preview_rows(body: PreviewQuery,http: Request,limit: int=Query(200,ge=1,le=1000),cursor: str | None=Query(None,max_length=4096)):
        operator(http)
        try:return preview_observations(bridge,body,limit,cursor)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @router.post("/v1/datasets", response_model=DatasetVersion)
    def publish(body: PublishRequest, http: Request):
        actor = operator(http)
        try:
            return publish_dataset(bridge, body, actor)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/v1/datasets", response_model=DatasetPage)
    def internal_datasets(limit: int = Query(100, ge=1, le=1000), cursor: str | None = Query(None, max_length=4096),export_id: str | None=Query(None,max_length=160)):
        return bridge.call("dataset_catalog", {"published_only": False, "limit": limit, "cursor": cursor,"export_id":export_id})

    @router.get("/v1/datasets/{dataset_version_id}", response_model=DatasetVersion)
    def internal_dataset(dataset_version_id: str):
        return bridge.call("dataset_version", {"dataset_version_id": dataset_version_id, "published_only": False})

    @router.get("/v1/datasets/{dataset_version_id}/observations",response_model=ObservationPage)
    def internal_observations(dataset_version_id: str,limit: int=Query(200,ge=1,le=1000),cursor: str | None=Query(None,max_length=4096),
                              period: str | None=Query(None,max_length=160),clause_key: str | None=Query(None,max_length=160)):
        return bridge.call("observation_page",{"dataset_version_id":dataset_version_id,"published_only":False,"limit":limit,"cursor":cursor,"period":period,"clause_key":clause_key})

    @router.get("/v1/datasets/{dataset_version_id}/export")
    def export_internal_dataset(dataset_version_id: str,format: str=Query("csv",pattern="^(csv|parquet)$"),
                                period: str | None=Query(None,max_length=160),clause_key: str | None=Query(None,max_length=160)):
        return export(dataset_version_id,{"dataset_version_id":dataset_version_id,"published_only":False,"period":period,"clause_key":clause_key},format)

    @router.post("/v1/datasets/{dataset_version_id}/withdraw", response_model=DatasetVersion)
    def withdraw(dataset_version_id: str, body: WithdrawalRequest, http: Request):
        return bridge.call("withdraw_dataset", {"dataset_version_id": dataset_version_id, **body.model_dump(), "actor": operator(http)})

    @router.post("/v1/restatements")
    def restatement(body: RestatementRequest, http: Request):
        actor = operator(http)
        try:
            return record_restatement(bridge, body, actor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/v2/datasets", response_model=DatasetPage)
    def public_datasets(limit: int = Query(100, ge=1, le=1000), cursor: str | None = Query(None, max_length=4096)):
        return bridge.call("dataset_catalog", {"published_only": True, "limit": limit, "cursor": cursor})

    @router.get("/v2/sources/{pdf_sha256}",response_model=ObservationSource)
    def source(pdf_sha256: str=Path(pattern=r"^[a-f0-9]{64}$")):
        return bridge.call("public_source",{"pdf_sha256":pdf_sha256})

    @router.get("/v2/datasets/{dataset_version_id}", response_model=DatasetVersion)
    def public_dataset(dataset_version_id: str):
        return bridge.call("dataset_version", {"dataset_version_id": dataset_version_id, "published_only": True})

    @router.get("/v2/datasets/{dataset_version_id}/observations", response_model=ObservationPage)
    def public_observations(dataset_version_id: str, limit: int = Query(200, ge=1, le=1000), cursor: str | None = Query(None, max_length=4096),
                            period: str | None = Query(None, max_length=160), clause_key: str | None = Query(None, max_length=160),
                            relation: str | None = Query(None, pattern="^(match|uncertain|different)$")):
        return bridge.call("observation_page", {"dataset_version_id": dataset_version_id, "published_only": True,
            "limit": limit, "cursor": cursor, "period": period, "clause_key": clause_key, "relation": relation})

    @router.get("/v2/datasets/{dataset_version_id}/definitions",response_model=list[ProductDefinition])
    def dataset_definitions(dataset_version_id: str):
        return bridge.call("product_definitions",{"dataset_version_id":dataset_version_id,"published_only":True})

    @router.get("/v2/datasets/{dataset_version_id}/observations/{observation_id}",response_model=Observation)
    def observation(dataset_version_id:str,observation_id:str):
        return bridge.call("observation_detail",{"dataset_version_id":dataset_version_id,"observation_id":observation_id})

    @router.get("/v2/datasets/{dataset_version_id}/sources",response_model=list[ObservationSource])
    def sources(dataset_version_id:str):
        return bridge.call("dataset_sources",{"dataset_version_id":dataset_version_id})

    @router.get("/v2/datasets/{dataset_version_id}/export")
    def export_dataset(dataset_version_id: str,format: str = Query("csv",pattern="^(csv|parquet)$"),
                       period: str | None = Query(None,max_length=160),clause_key: str | None = Query(None,max_length=160)):
        return export(dataset_version_id,{"dataset_version_id":dataset_version_id,"published_only":True,"period":period,"clause_key":clause_key},format)

    return router
