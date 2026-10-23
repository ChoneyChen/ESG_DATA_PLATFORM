from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from PlatESG.backend.main import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    directory=tmp_path_factory.mktemp("platform-api")
    app=create_app({"PLATESG_DATA_DIR":str(directory/"data"),"PLATESG_AUTO_PUBLISH":"0",
        "PLATESG_REVIEW_TOKEN":"integration-review-token","PLATESG_ALLOW_DEMO_SEED":"1","PLATESG_LIFECYCLE_STORE_SCOPE":"isolated"})
    with TestClient(app) as client:
        client.app.state.bridge.call("seed_demo",timeout=120)
        yield client


def test_all_major_original_ids_survive_and_decimal_api_is_exact(client):
    metrics=client.get("/v1/metrics").json()
    assert len(metrics["items"])==55
    assert sum(m["readiness"]=="ready" for m in client.get("/internal/v1/metrics",headers={"Authorization":"Bearer integration-review-token"}).json()["items"])==38
    assert "E1-6_11" in {m["source_datapoint_id"] for m in metrics["items"]}
    rows=client.get("/v1/observations").json()
    # Ready metrics × two demo entities × two years.
    assert len(rows["items"])==140
    assert all(isinstance(r["value_numeric"],str) for r in rows["items"] if r["value_numeric"] is not None)
    ratio=next(r for r in rows["items"] if r["source_datapoint_id"]=="E1-5_09")
    assert ratio["value_numeric"]=="0.25125"
    assert ratio["canonical_unit"]=="ratio"
    detail=client.get(f'/v1/observations/{ratio["observation_id"]}',params={"dataset_version":rows["dataset_version"]}).json()
    source=detail["sources"][0]
    assert source["raw_value_text"]=="25.125" and source["raw_unit"]=="%"
    assert "rule_version" not in source["unit_conversion"]
    assert source["unit_conversion"]["multiplier"]=="0.01"
    assert source["unit_conversion"]["divisor"]=="1"
    assert all(r["is_demo"] for r in rows["items"])
    statement=next(r for r in rows["items"] if r["source_datapoint_id"]=="E2-4_09")
    assert statement["value_numeric"] is None
    assert statement["value_role"]=="context"
    assert statement["statement_text"]
    for entity in ["demo-alpha", "demo-beta"]:
        for period in ["2024-01-01", "2025-01-01"]:
            energy={r["source_datapoint_id"]:r for r in rows["items"] if r["entity_ref"]==entity and r["period_start"]==period}
            assert Decimal(energy["E1-5_05"]["value_numeric"])/Decimal(energy["E1-5_01"]["value_numeric"])==Decimal(energy["E1-5_09"]["value_numeric"])


def test_comparison_requires_one_explicit_cohort(client):
    # Synthetic GHG rows do not establish a gross amount basis. New scientific
    # definitions must exclude them rather than silently invent that meaning.
    unknown=client.post("/v1/comparisons",json={"metric_id":"ghg.scope1","entity_refs":["demo-alpha","demo-beta"]}).json()
    assert unknown['rows']==[]
    assert any('comparison_dimension_mismatch:amount_basis' in row['reasons'] for row in unknown['excluded'])
    request={"metric_id":"energy.total_consumption","entity_refs":["demo-alpha","demo-beta"]}
    ambiguous=client.post("/v1/comparisons",json=request).json()
    assert ambiguous["rows"]==[]
    assert len(ambiguous["available_cohorts"])==2
    assert all("comparison_contract_required:multiple_cohorts" in r["reasons"] for r in ambiguous["excluded"])
    request.update(period_start="2024-01-01",period_end="2024-12-31")
    exact=client.post("/v1/comparisons",json=request).json()
    assert len(exact["rows"])==2
    assert all(r["period_start"]=="2024-01-01" for r in exact["rows"])
    qualitative=client.post("/v1/comparisons",json={**request,"metric_id":"pollution.measurement_methodology"}).json()
    assert qualitative["rows"]==[]
    assert any("structured_display_only" in r["reasons"] for r in qualitative["excluded"])


def test_auth_and_strict_review_contract(client):
    assert client.get("/internal/v1/candidates").status_code==401
    assert client.post("/internal/v1/reviews",json={"decision":"approved"}).status_code==401
    auth={"Authorization":"Bearer integration-review-token"}
    candidate=client.get("/internal/v1/candidates",headers=auth).json()["items"][0]
    response=client.post("/internal/v1/reviews",headers=auth,json={"mapping_id":candidate["mapping_id"],"decision":"approved",
        "operator_ref":"reviewer","reason":"checked"})
    assert response.status_code==422
    assert "requires an explicit" in str(response.json())


def test_neutral_source_archive_is_read_authorized_and_bounds_its_query(client,monkeypatch):
    assert client.get('/internal/v1/source-facts').status_code == 401
    auth={'Authorization':'Bearer integration-review-token'}
    calls=[]
    def source_query(command,request):
        calls.append((command,request))
        return {'items':[{'snapshot_id':'fixture-source','upstream_system':'synthetic-demo',
            'mapping_state':'unmapped','identity_raw':{'business_segment':'合成业务范围'}}],
            'has_more':True}
    monkeypatch.setattr(client.app.state.bridge,'call',source_query)
    response=client.get('/internal/v1/source-facts',params={'report_ref':'fixture-report','limit':1},headers=auth)
    assert response.status_code == 200
    assert calls == [('source_facts',{'report_ref':'fixture-report','limit':1,'cursor':None})]
    assert response.json()['items'][0]['mapping_state'] == 'unmapped'
    assert response.json()['items'][0]['evidence_location']['state'] == 'synthetic'
    assert response.json()['has_more'] is True
    assert client.get('/internal/v1/source-facts',params={'limit':1001},headers=auth).status_code == 422
    assert len(calls) == 1


def test_property_review_selection_cannot_supply_its_own_report_value(client):
    auth={"Authorization":"Bearer integration-review-token"}
    candidate=client.get("/internal/v1/candidates",headers=auth).json()["items"][0]
    context={"mapping_id":candidate["mapping_id"],"entity_ref":"demo-alpha","period_start":"2025-01-01",
        "period_end":"2025-12-31","boundary_code":"own_operations","reason":"核对报告补充信息",
        "property_selections":[{"source_field":"identity_raw.business_segment","label_zh":"业务范围",
            "value_raw":"前端伪造属性值"}]}
    response=client.post("/internal/v1/measurement-contexts",headers=auth,json=context)
    assert response.status_code==422
    assert any(item["type"]=="extra_forbidden" and item["loc"][-1]=="value_raw" for item in response.json()["detail"])


def test_public_properties_survive_response_models_and_versioned_csv(client,monkeypatch):
    import csv,io
    original=client.app.state.bridge.call
    prop={"field_key":"business_segment","label_zh":"业务范围","value_raw":["炼化","化工"],
        "value_type":"text_array","unit_raw":None,"evidence_scope":"source",
        "evidence":[{"page_number":90,"source_quote":"炼化、化工业务"}],"report_title":"隔离属性合同样例"}
    def with_properties(command,params=None,**kwargs):
        result=original(command,params,**kwargs)
        if command=="observations":
            for row in result["items"]: row["reported_properties"]=[prop]
        return result
    monkeypatch.setattr(client.app.state.bridge,"call",with_properties)
    data=client.get("/v1/observations",params={"limit":1}).json()
    assert data["items"][0]["reported_properties"][0]["value_raw"]==["炼化","化工"]
    export=client.get("/v1/exports",params={"format":"csv","dataset_version":data["dataset_version"]})
    rows=list(csv.DictReader(io.StringIO(export.text.lstrip("\ufeff"))))
    assert json.loads(rows[0]["reported_properties"])==data['items'][0]['reported_properties']
    assert int(rows[0]["dataset_version"])==data["dataset_version"]


def test_withdrawal_changes_current_version_and_preserves_original_evidence(client):
    auth={"Authorization":"Bearer integration-review-token"}
    result=client.get("/v1/observations",params={"metric_id":"ghg.scope1","entity_ref":"demo-alpha"}).json()
    version=result["dataset_version"]
    row=result["items"][0]
    original=client.get(f'/v1/observations/{row["observation_id"]}',params={"dataset_version":version}).json()
    internal=client.get(f'/internal/v1/observations/{row["observation_id"]}',params={"dataset_version":version},headers=auth).json()
    fact_id=internal["sources"][0]["disclosure_id"]
    assert client.post("/internal/v1/reviews",headers=auth,json={"disclosure_id":fact_id,"decision":"rejected",
        "operator_ref":"fixture-reviewer","reason":"合成撤回测试"}).status_code==200
    assert client.post("/internal/v1/publication-process",headers=auth).status_code==200
    assert client.get(f'/v1/observations/{row["observation_id"]}').status_code==404
    old=client.get(f'/v1/observations/{row["observation_id"]}',params={"dataset_version":version}).json()
    assert old["sources"]==original["sources"]
    assert old==original
    assert "review" not in old and "history" not in old
    assert old["dataset_version"]==version


def test_cursor_and_export_freeze_dataset_and_do_not_turn_missing_into_zero(client):
    first=client.get("/v1/observations",params={"limit":3}).json()
    second=client.get("/v1/observations",params={"limit":3,"dataset_version":first["dataset_version"],"cursor":first["next_cursor"]}).json()
    assert not ({r["observation_id"] for r in first["items"]}&{r["observation_id"] for r in second["items"]})
    wrong=client.get("/v1/observations",params={"limit":3,"entity_ref":"demo-alpha","cursor":first["next_cursor"]})
    assert wrong.status_code==422
    assert wrong.json()["code"]=="invalid_cursor"
    export=client.get("/v1/exports",params={"format":"json","dataset_version":first["dataset_version"]}).json()
    assert export["dataset_version"]==first["dataset_version"]
    coverage=client.get("/v1/coverage",params={"entity_ref":"demo-alpha","dataset_version":first["dataset_version"]}).json()
    assert sum(r["status"]=="not_available" for r in coverage["items"])>=20
    assert client.get("/v1/datasets/999999").status_code==404
    assert client.get("/v1/observations",params={"dataset_version":999999}).status_code==404


def test_failed_delivery_retry_is_audited_and_does_not_reapprove_facts(client):
    """Simulate a lost delivery acknowledgement after the candidate commit."""
    auth={"Authorization":"Bearer integration-review-token"}
    # The canonical compiled package name comes from the dictionary, not a
    # fixture-specific assumption about naming conventions.
    metric=next(m for m in client.get("/internal/v1/metrics",headers=auth).json()["items"] if m["source_datapoint_id"]=="E1-5_01")
    bundle=ROOT/"PlatESG/.local/demo-bundles/demo-alpha-2024"/metric["standard_binding"]["package_id"]
    body={"bundle_path":str(bundle),"report_metadata":{"upstream_system":"synthetic-demo","organization_ref":"demo-alpha",
        "report_ref":"synthetic-report-demo-alpha-2024","report_version_ref":"synthetic-report-demo-alpha-2024",
        "source_artifact_ref":"demo://synthetic/synthetic-report-demo-alpha-2024",
        "source_sha256":hashlib.sha256(b"synthetic-report-demo-alpha-2024").hexdigest(),
        "report_title":"合成测试报告（虚构）demo-alpha 2024","language_code":"zh",
        "report_period_start":"2024-01-01","report_period_end":"2024-12-31"}}
    receipt_id=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
    client.app.state.bridge.call("receipt",{"receipt_id":receipt_id,"status":"failed",
        "detail":{"message":"synthetic acknowledgement loss after candidate commit","request":body}})
    baseline=client.get("/v1/observations").json()
    url=f"/internal/v1/import-receipts/{receipt_id}/retry"
    request={"operator_ref":"delivery-reviewer","reason":"核验合成批次后重试候选交付"}
    assert client.post(url,json=request).status_code==401
    response=client.post(url,json=request,headers=auth)
    assert response.status_code==200,response.text
    assert response.json()["current_status"]=="imported"
    receipt=client.get("/internal/v1/import-receipts",params={"receipt_id":receipt_id},headers=auth).json()
    assert receipt["status"]=="failed" and receipt["current_status"]=="imported"
    assert len(receipt["attempts"])==1
    assert receipt["attempts"][0]["reason"]==request["reason"]
    assert receipt["attempts"][0]["detail"]["idempotent"] is True
    assert client.post(url,json=request,headers=auth).status_code==200
    assert len(client.get("/internal/v1/import-receipts",params={"receipt_id":receipt_id},headers=auth).json()["attempts"])==1
    assert client.get("/v1/observations").json()==baseline


def test_frontend_api_prefix_and_local_review_page(client):
    assert client.get("/api/v1/metrics").json()==client.get("/v1/metrics").json()
    assert client.get("/api/internal/v1/candidates",headers={"Authorization":"Bearer 无效凭据".encode("utf-8")}).status_code==401
    page=client.get("/review/")
    assert page.status_code==200
    assert "password" in page.text


def test_lifecycle_reference_api_is_authenticated_and_protects_published_sources(client):
    payload={"job_ids":[],"ir_run_ids":["synthetic-ir-demo-alpha-2024"]}
    assert client.post("/internal/v1/lifecycle/references",json=payload).status_code==401
    auth={"Authorization":"Bearer integration-review-token"}
    checked=client.post("/internal/v1/lifecycle/references",json=payload,headers=auth)
    assert checked.status_code==200
    assert checked.json()["verified"] is True
    assert checked.json()["blockers"][0]["code"]=="published_source_reference"
    denied=client.post("/internal/v1/lifecycle/reserve",json={**payload,"reason":"正式来源不能回收"},headers=auth)
    assert denied.status_code==200 and not denied.json().get("reservation_id")
    assert client.post("/internal/v1/lifecycle/references",json={},headers=auth).status_code==422


def test_lifecycle_reservation_identity_is_strict_and_recovery_is_idempotent(client):
    from uuid import uuid4
    auth={"Authorization":"Bearer integration-review-token"}
    identity=str(uuid4())
    request={"job_ids":["fixture-timeout-job"],"reservation_id":identity,"reason":"可恢复的确定请求"}
    first=client.post("/internal/v1/lifecycle/reserve",json=request,headers=auth)
    assert first.status_code==200 and first.json()["reservation_id"]==identity
    assert client.post("/internal/v1/lifecycle/reserve",json=request,headers=auth).json()["reservation_id"]==identity
    released=client.post(f"/internal/v1/lifecycle/reservations/{identity}",json={"state":"released","reason":"恢复"},headers=auth)
    assert released.status_code==200 and released.json()["state"]=="released"
    assert client.post("/internal/v1/lifecycle/reserve",json={**request,"reservation_id":"unsafe"},headers=auth).status_code==422


def test_lifecycle_does_not_treat_empty_preview_as_proof_about_other_stores(tmp_path,monkeypatch):
    app=create_app({"PLATESG_DATA_DIR":str(tmp_path/"isolated-preview"),"PLATESG_REVIEW_TOKEN":"fixture-coverage-token",
        "PLATESG_LIFECYCLE_STORE_SCOPE":"all_known_local_stores"})
    actual_is_dir=Path.is_dir
    def is_dir(path):
        if str(path).endswith("ExtractESG/esg_database/.local/pglite"):return True
        return actual_is_dir(path)
    monkeypatch.setattr(Path,"is_dir",is_dir)
    monkeypatch.setattr(app.state.bridge,"call",lambda *args,**kwargs:{"schema_version":"lifecycle-references-v1","verified":True,"blockers":[],"dependencies":[]})
    temporary_client=TestClient(app)
    result=temporary_client.post("/internal/v1/lifecycle/references",json={"job_ids":["fixture-old-job"]},
        headers={"Authorization":"Bearer fixture-coverage-token"}).json()
    assert result["verified"] is False
    assert result["blockers"][0]["code"]=="additional_source_store_unverified"
    assert result["store_coverage"]["unverified_store_roots"]
    reserved=temporary_client.post("/internal/v1/lifecycle/reserve",json={"job_ids":["fixture-old-job"],"reason":"不能用预览库授权清理"},
        headers={"Authorization":"Bearer fixture-coverage-token"}).json()
    assert reserved["verified"] is False and not reserved.get("reservation_id")


def test_public_responses_exclude_internal_state_recursively(client):
    denied={"operator_ref","context_reviewer","context_review_reason","fact_review_decision_id",
        "mapping_review_decision_id","disclosure_id","mapping_id","context_id","source_sha256",
        "source_artifact_ref","source_node_id","standard_binding","readiness","source_digest",
        "file_sha256","content_digest","review","history","normalization_rule_version","rule_version","rule_digest"}
    def inspect(value):
        if isinstance(value,dict):
            assert not (set(value)&denied), set(value)&denied
            for item in value.values(): inspect(item)
        elif isinstance(value,list):
            for item in value: inspect(item)
    rows=client.get('/v1/observations').json()
    for url in ['/v1/entities','/v1/metrics','/v1/observations','/v1/coverage','/v1/datasets','/v1/sources','/v1/exports?format=json']:
        response=client.get(url);assert response.status_code==200,response.text
        inspect(response.json())
    inspect(client.get('/v1/observations/'+rows['items'][0]['observation_id']).json())
    assert client.get('/internal/v1/metrics').status_code==401
    assert client.get('/internal/v1/publication-status').status_code==401
    assert client.get('/v1/extraction-profiles').status_code==404


def test_operator_identity_and_roles_are_server_controlled(tmp_path,monkeypatch):
    app=create_app({'PLATESG_REVIEW_TOKEN':'','PLATESG_AUTO_PUBLISH':'0','PLATESG_OPERATORS_JSON':json.dumps([
        {'token':'reader-only','operator_ref':'verified-reader','roles':['read']},
        {'token':'reviewer-only','operator_ref':'verified-reviewer','roles':['read','review']}])})
    calls=[]
    monkeypatch.setattr(app.state.bridge,'call',lambda method,params=None,**kwargs: calls.append((method,params)) or {'items':[]})
    c=TestClient(app)
    reader={'Authorization':'Bearer reader-only'};reviewer={'Authorization':'Bearer reviewer-only'}
    assert c.get('/internal/v1/me',headers=reader).json()['operator_ref']=='verified-reader'
    body={'disclosure_id':'00000000-0000-0000-0000-000000000001','decision':'approved','operator_ref':'spoofed-admin','reason':'检查原文'}
    assert c.post('/internal/v1/reviews',json=body,headers=reader).status_code==403
    assert c.post('/internal/v1/publication-process',headers=reviewer).status_code==403
    assert c.post('/internal/v1/reviews',json=body,headers=reviewer).status_code==200
    assert calls[-1][1]['operator_ref']=='verified-reviewer'
    assert c.post('/internal/v1/lifecycle/reserve',json={'job_ids':['test-job'],'reason':'权限检查'},headers=reader).status_code==403
