"""Plat consumes published-only HTTP products; no capture database or producer directory."""
from urllib.parse import urlencode,quote
from urllib.error import HTTPError,URLError
from fastapi import APIRouter,HTTPException,Query,Path
from fastapi.responses import Response
from esg_database.http_client import ProductClient,ProductServiceError
from esg_database.products_contracts import DatasetPage,DatasetVersion,ObservationPage,ObservationSource,ProductDefinition,Observation


def product_router(base_url,resolver):
    router=APIRouter(prefix="/v2")
    client=ProductClient(base_url,timeout=15)
    def call(path,model=None):
        try:return client.call(path,model=model)
        except ProductServiceError as exc:raise HTTPException(exc.status,"Published data is temporarily unavailable" if exc.status>=500 else exc.code) from exc
    def dataset_path(id):return "/v2/datasets/"+quote(id,safe="")

    @router.get("/datasets",response_model=DatasetPage)
    def datasets(limit:int=Query(100,ge=1,le=1000),cursor:str|None=Query(None,max_length=4096)):
        return call("/v2/datasets?"+urlencode({"limit":limit,**({"cursor":cursor} if cursor else {})}),DatasetPage)

    @router.get("/datasets/{id}",response_model=DatasetVersion)
    def dataset(id:str):return call(dataset_path(id),DatasetVersion)

    @router.get("/datasets/{id}/observations",response_model=ObservationPage)
    def observations(id:str,limit:int=Query(100,ge=1,le=1000),cursor:str|None=Query(None,max_length=4096),period:str|None=Query(None,max_length=160),clause_key:str|None=Query(None,max_length=160),relation:str|None=Query(None,pattern="^(match|uncertain|different)$")):
        query={key:value for key,value in {"limit":limit,"cursor":cursor,"period":period,"clause_key":clause_key,"relation":relation}.items() if value is not None}
        return call(dataset_path(id)+"/observations?"+urlencode(query),ObservationPage)

    @router.get("/datasets/{id}/definitions",response_model=list[ProductDefinition])
    def definitions(id:str):return call(dataset_path(id)+"/definitions")

    @router.get("/datasets/{id}/observations/{observation_id}",response_model=Observation)
    def observation(id:str,observation_id:str):return call(dataset_path(id)+"/observations/"+quote(observation_id,safe=""),Observation)

    @router.get("/datasets/{id}/sources",response_model=list[ObservationSource])
    def sources(id:str):return call(dataset_path(id)+"/sources")

    @router.get("/datasets/{id}/export")
    def export(id:str,format:str=Query("csv",pattern="^(csv|parquet)$"),period:str|None=Query(None,max_length=160),clause_key:str|None=Query(None,max_length=160)):
        query={key:value for key,value in {"format":format,"period":period,"clause_key":clause_key}.items() if value is not None}
        try:body,_=ProductClient(base_url,timeout=90).download(dataset_path(id)+"/export?"+urlencode(query))
        except ProductServiceError as exc:raise HTTPException(exc.status,exc.code) from exc
        return Response(body,media_type="application/zip",headers={"Content-Disposition":'attachment; filename="published-research-'+id+'.zip"'})

    @router.get("/sources/{pdf_sha256}/pdf")
    def pdf(pdf_sha256:str=Path(pattern=r"^[a-f0-9]{64}$")):
        call("/v2/sources/"+pdf_sha256,ObservationSource)
        try:body=resolver.pdf(pdf_sha256)
        except HTTPError as exc:raise HTTPException(404 if exc.code==404 else 503,"Published PDF source is unavailable") from exc
        except (URLError,OSError) as exc:raise HTTPException(503,"Document source service is unavailable") from exc
        except ValueError as exc:raise HTTPException(409,"Published PDF identity could not be verified") from exc
        return Response(body,media_type="application/pdf",headers={"Content-Disposition":'inline; filename="source-'+pdf_sha256+'.pdf"'})

    return router
