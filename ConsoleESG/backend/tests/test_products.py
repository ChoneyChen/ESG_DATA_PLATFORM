"""One backend workflow projection is reused by session details and the monitor."""
from esg_console.products import ProductStatus
from esg_console.monitor import monitor_snapshot
from esg_database.products_contracts import CaptureProductState
from esg_database.http_client import ProductServiceError


def test_shared_projection_does_not_recompute_or_guess_a_dataset_state():
    calls=[]
    state=CaptureProductState.model_validate({"export_id":"export-one","receipt":{"export_id":"export-one","input_digest":"digest","receipt_id":"receipt-one","status":"accepted","accepted_facts":3,"rejected_facts":0,"dispositions":[],"publication_status":"not_evaluated","received_at":"2026-10-31"},"captured_facts":3,"capture_periods":[{"period":"2023","facts":1},{"period":"2024","facts":1},{"period":"2025","facts":1}],"projection":{"export_id":"export-one","status":"failed","error_code":"synthetic_projection_error"},"published_dataset_count":0})
    class Client:
        def call(self,path,model=None):calls.append(path);return state
    products=ProductStatus(Client())
    session={"session_id":"session-one","name":"报告","status":"completed","current_phase":"finished","phase_label":"执行结束","run":{"delivery":{"export_id":"export-one"}}}
    detail=products.for_session(session)
    monitor=monitor_snapshot([session],products).runs[0].products
    assert detail==monitor and len(calls)==1
    assert detail.stage_label=="投影失败 · 原值已保留" and detail.state.captured_facts==3


def test_unavailable_reception_is_not_displayed_as_delivered_or_published():
    class Client:
        def call(self,*args,**kwargs):raise ProductServiceError(503,"unavailable")
    result=ProductStatus(Client()).for_session({"session_id":"session","run":{"delivery":{"export_id":"export-one"}}})
    assert result.state is None and result.observation=="unavailable"
    assert result.stage_label=="数据服务状态未确认"
