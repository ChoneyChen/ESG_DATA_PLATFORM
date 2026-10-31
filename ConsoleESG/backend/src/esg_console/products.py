"""Session-facing status is composed once on the backend, reused by page and monitor."""
import threading
import time
from urllib.parse import quote
from typing import Literal
from pydantic import BaseModel
from esg_database.http_client import ProductClient,ProductServiceError
from esg_database.products_contracts import CaptureProductState


class SessionProducts(BaseModel):
    schema_version: Literal["console-session-products-1.0"]="console-session-products-1.0"
    session_id: str
    export_id: str | None=None
    observation: Literal["fresh","pending","unavailable"]="pending"
    stage_label: str="等待封存"
    state: CaptureProductState | None=None


class ProductStatus:
    def __init__(self,client=None):
        self.client=client or ProductClient()
        self.cache={}
        self.lock=threading.Lock()

    def state(self,export_id):
        with self.lock:
            previous=self.cache.get(export_id)
            if previous and time.monotonic()-previous[0]<1.2:return previous[1]
            result=self.client.call("/v1/ingestion/exports/"+quote(export_id,safe="")+"/products",model=CaptureProductState)
            if len(self.cache)>128:self.cache.clear()
            self.cache[export_id]=(time.monotonic(),result)
            return result

    def for_session(self,session):
        run=session.get("run") or {}
        delivery=run.get("delivery") or {}
        export_id=delivery.get("export_id")
        base={"session_id":session["session_id"],"export_id":export_id}
        if not export_id:return SessionProducts(**base)
        try:
            state=self.state(export_id)
        except ProductServiceError as exc:
            return SessionProducts(**base,observation="pending" if exc.status==404 else "unavailable",
                stage_label="等待数据库接收" if exc.status==404 else "数据服务状态未确认")
        projection=state.projection
        label=(f"已发布 · {state.published_dataset_count} 个数据集" if state.published_dataset_count else
            "已接收 · 待安装投影模式" if projection.status=="schema_unavailable" else
            "发布已撤下" if state.datasets and state.datasets.total and not state.published_dataset_count else
            "已投影 · 待发布" if projection.status=="completed" else
            "投影失败 · 原值已保留" if projection.status=="failed" else "已接收 · 等待投影")
        return SessionProducts(**base,observation="fresh",stage_label=label,state=state)
