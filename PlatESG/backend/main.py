"""Current published-data platform. No legacy dictionary, SQL worker or frozen API."""
import os
from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from esg_database.http_client import ProductClient,ProductServiceError
from .runtime import release
from .http_prefix import APIPrefixMiddleware
from .evidence_resolver import EvidenceResolver
from .products import product_router

ROOT=Path(__file__).resolve().parents[1]


def create_app(environment=None):
    config={**os.environ,**(environment or {})}
    endpoint=config.get("ESG_DATABASE_PRODUCTS_URL",config.get("ESG_DATABASE_INGEST_URL","http://127.0.0.1:18280"))
    client=ProductClient(endpoint,timeout=3)
    resolver=EvidenceResolver(config.get("ESG_DOCUMENT_BACKEND_URL","http://127.0.0.1:18080"))
    running=release()
    app=FastAPI(title="PlatESG 已发布数据",version="2.0.0",
        description="独立数据库发布快照的只读平台，保留全部期间和原值；比较资格与展示分开。")
    app.add_middleware(APIPrefixMiddleware)
    app.include_router(product_router(endpoint,resolver))

    def availability():
        try:
            state=client.call("/health")
            return bool(state.get("products_ready")),None
        except ProductServiceError:
            return False,"独立数据服务暂不可用"

    @app.get("/health")
    def health():
        ready,error=availability()
        return JSONResponse(status_code=200 if ready else 503,content={"status":"ok" if ready else "not_ready",
            "service":"platesg","profile":"published_products","products_ready":ready,"auto_publish":False,
            "detail":error or (None if ready else "数据服务的发布模式尚未安装")})

    @app.get("/health/compatibility")
    def compatibility():
        state=running.snapshot();ready,error=availability()
        blockers=(["平台代码已更新，需要重启"] if state["restart_required"] else [])+([error or "发布模式尚未就绪"] if not ready else [])
        return {**state,"readiness":{"ready":not blockers,"blockers":blockers}}

    @app.api_route("/v1/{rest:path}",methods=["GET","POST","PUT","PATCH","DELETE"],include_in_schema=False)
    @app.api_route("/internal/v1/{rest:path}",methods=["GET","POST","PUT","PATCH","DELETE"],include_in_schema=False)
    @app.api_route("/review/{rest:path}",methods=["GET","POST"],include_in_schema=False)
    @app.api_route("/review",methods=["GET","POST"],include_in_schema=False)
    def retired(rest=""):
        raise HTTPException(410,"旧指标字典、快照与审核接口已清除；当前数据使用 /v2/datasets")

    if (ROOT/"frontend/dist").is_dir():
        app.mount("/",StaticFiles(directory=ROOT/"frontend/dist",html=True),name="platform")
    return app


app=create_app()
