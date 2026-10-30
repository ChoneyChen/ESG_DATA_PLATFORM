"""Real isolated SQL and HTTP boundaries; these fixtures are synthetic, not report accuracy evidence."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import io
import json
import os
import shutil
import subprocess
from zipfile import ZipFile
import pytest
from fastapi.testclient import TestClient
from esg_contracts.facts import (SourceFact, QuantitativePayload, QualitativePayload, EvidenceLocation, ReportedField,
    ReportedContext, ClauseMapping, ClauseCoverage)
from esg_contracts.evidence import DocumentReference
from esg_contracts.exports import ExportManifest, ExportFile
from esg_contracts.annotations import property_tables
from esg_reports.contract import ReportResource, Company
from esg_data.hashing import digest
from esg_database.client import DatabaseBridge
from esg_database.api import create_app
from esg_database.ingestion import canonical
from esg_database.projector import ProjectionWorker
from esg_database.projection import TRANSFORM_VERSION


@pytest.fixture
def system(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PLATESG_", "ESG_DATABASE_"))}
    env.update(ESG_DATABASE_DATA_DIR=str(tmp_path / "capture"), ESG_DATABASE_PROFILE="capture", ESG_DATABASE_MODULE_ROOT=str(root))
    subprocess.run([shutil.which("node"), str(root / "runtime/manage.mjs"), "init", "--profile", "capture"], env=env,
                   capture_output=True, text=True, check=True)
    bridge = DatabaseBridge(env).start()
    try:
        with TestClient(create_app(bridge), client=("127.0.0.1", 50000)) as client:
            yield client, bridge
    finally:
        bridge.close()


def field(value, role="table"):
    text = str(value)
    return ReportedField(state="stated", raw_text=text, value=value,
        evidence=[EvidenceLocation(object_id="context-"+text, object_type="text_block", page_index=0, excerpt=text, role=role)])


def archive(source="source-a", periods=("2023", "2024", "2025"), *, known=False, publication=2026,
            amount="1.530140000000000000001", interpretation="one", two_targets=False, statement=None):
    pdf_sha = sha256(source.encode()).hexdigest()
    resource = ReportResource(pdf_id=source, asset_id="pdf-"+pdf_sha[:24], sha256=pdf_sha, original_filename="synthetic.pdf",
        company=Company(name="资源企业"), publication_year=publication)
    document = DocumentReference(document_id=source, ir_run_id="ir-"+source, ir_revision=1, content_digest="content", source_sha256=pdf_sha, report_resource=resource)
    keys = ["TEST.amount", "TEST.other"] if two_targets else ["TEST.amount"]
    targets = {}
    for key in keys:
        contract = {"schema_version": "clause-contract-1.3", "clause_key": key, "data_kind": "qualitative" if statement else "quantitative", "labels": {"zh": "合成测试数量"},
            "definition": "Synthetic amount", "value": {"unit_dimension": "mass"},
            "purposes": {"comparison": {"required_axes": ["reporting_entity", "reporting_boundary", "reporting_period"], "require_complete_scopes": True}}}
        targets[key] = {**contract, "clause_digest": digest(contract)}
    facts, links = [], []
    for index, period in enumerate(periods):
        identity = ReportedContext(reporting_period=field(period, "column_header") if period is not None else ReportedField(),
            reporting_entity=field("实际统计主体") if known else ReportedField(),
            reporting_boundary=field("集团合并") if known else ReportedField())
        fact = SourceFact(source_fact_id=source+"-"+interpretation+"-"+str(index), source_anchor_id="anchor-"+str(index),
            document_id=source, ir_run_id=document.ir_run_id, ir_revision=1, interpretation_id=interpretation, report_resource=resource,
            value=QualitativePayload(statement_raw=statement) if statement else QuantitativePayload(value_raw=amount, value_numeric=amount, unit_raw="吨", parse_status="parsed", comparator="exact"),
            source_row_label="实测数量", identity=identity, quality="grounded",
            evidence=[EvidenceLocation(object_id="cell-"+str(index), object_type="cell", page_index=0, excerpt=statement or amount, role="statement" if statement else "value")])
        facts.append(fact.model_dump(mode="json"))
        links.extend(ClauseMapping(clause_key=key, clause_digest=targets[key]["clause_digest"], source_fact_id=fact.source_fact_id,
            match="match", reason="Synthetic contract test").model_dump(mode="json") for key in keys)
    properties, evidence = property_tables(facts)
    members = {"facts": facts, "properties": properties, "evidence": evidence, "relations": links,
        "targets": targets, "coverage": [ClauseCoverage(clause_key=key, clause_digest=targets[key]["clause_digest"], state="covered", fact_count=len(facts)).model_dump(mode="json") for key in keys],
        "document": document.model_dump(mode="json"), "diagnostics": []}
    content = {role: canonical(body) for role, body in members.items()}
    files = [ExportFile(name=role+".json", role=role, sha256=sha256(data).hexdigest(), bytes=len(data), records=1 if role=="document" else len(members[role])) for role, data in content.items()]
    run = source+"-"+interpretation
    export_id = "export-"+sha256(canonical(["extraction-export-1.1", run, *[f.sha256 for f in sorted(files, key=lambda f:f.role)]])).hexdigest()
    manifest = ExportManifest(authority="facts", export_id=export_id, run_id=run, engine_version="synthetic-test", engine_identity={}, document=document,
        clause_targets={key: body["clause_digest"] for key, body in targets.items()}, execution_state="completed", sealed_at="2026-10-31T00:00:00Z", files=files)
    data = io.BytesIO()
    with ZipFile(data, "w") as zipped:
        zipped.writestr("manifest.json", manifest.model_dump_json())
        for role, body in content.items():zipped.writestr(role+".json", body)
    return export_id, data.getvalue()


def receive(client, bridge, **kwargs):
    export_id, data = archive(**kwargs)
    response = client.post("/v1/ingestion/exports", content=data)
    assert response.status_code == 200, response.text
    receipt = response.json()
    ProjectionWorker(bridge).tick()
    result = client.get(f"/v1/ingestion/exports/{export_id}/projection")
    assert result.status_code == 200 and result.json()["status"] == "completed", result.text
    return receipt, result.json()["projection"]


def preview(client, projections, mode="all_sources"):
    request = {"title": "隔离合成测试", "projection_ids": projections, "policy": {"mode": mode, "relations": ["match"]}}
    response = client.post("/v1/datasets/preview", json=request)
    assert response.status_code == 200, response.text
    return request, response.json()


def publish(client, request, decision):
    response = client.post("/v1/datasets", json={**request, "preview_digest": decision["preview_digest"], "reason": "Isolated synthetic test only"})
    assert response.status_code == 200, response.text
    return response.json()


def test_three_year_sparse_capture_projects_publishes_and_withdraws_without_raw_loss(system):
    client, bridge = system
    receipt, projection = receive(client, bridge)
    assert receipt["accepted_facts"] == 3 and receipt["rejected_facts"] == 0
    assert projection["periods"] == {"2023":1,"2024":1,"2025":1}
    again = client.post(f"/v1/ingestion/exports/{receipt['export_id']}/projection")
    assert again.json()["projection"] == projection
    request, decision = preview(client, [projection["projection_id"]])
    assert decision["selected"] == 3
    dataset = publish(client, request, decision)
    path = "/v2/datasets/"+dataset["dataset_version_id"]
    assert client.get(path).json()["observations"] == 3
    page = client.get(path+"/observations").json()
    assert page["total"] == page["returned"] == 3
    assert {row["period"]["year"] for row in page["items"]} == {2023,2024,2025}
    assert all(row["value"]["value_numeric"] == "1.530140000000000000001" for row in page["items"])
    assert all(not row["comparison"]["eligible"] for row in page["items"])
    assert client.get(path+"/observations?period=2023").json()["total"] == 1
    withdrawal = client.post("/v1/datasets/"+dataset["dataset_version_id"]+"/withdraw", json={"expected_revision":1,"reason":"Synthetic verification finished"})
    assert withdrawal.status_code == 200 and withdrawal.json()["revision"] == 2
    assert client.get(path).status_code == client.get(path+"/observations").status_code == 404
    assert publish(client, request, decision)["state"] == "withdrawn"
    assert client.get(f"/v1/ingestion/exports/{receipt['export_id']}/facts").json()["total"] == 3


def test_unknown_period_and_duplicate_physical_interpretations_remain_visible(system):
    client, bridge = system
    receipt, projection = receive(client, bridge, periods=(None,), two_targets=True)
    assert projection["facts"] == 1 and projection["observations"] == 2 and projection["repeated_occurrences"] == 1
    rows = client.get("/v1/projections/"+projection["projection_id"]+"/observations").json()["items"]
    assert {row["period"]["kind"] for row in rows} == {"unknown"}
    assert len({row["occurrence_id"] for row in rows}) == 1
    assert len({row["observation_id"] for row in rows}) == 2
    request, decision = preview(client, [projection["projection_id"]], "latest_report")
    assert decision["selected"] == 2
    assert {row["reason"] for row in decision["decisions"]} == {"identity_incomplete_keep_source"}


def test_latest_report_and_explicit_restated_policy_are_versioned_and_do_not_overwrite(system):
    client, bridge = system
    _, old = receive(client, bridge, source="older", known=True, publication=2025, periods=("2023",), amount="100")
    _, new = receive(client, bridge, source="newer", known=True, publication=2026, periods=("2023",), amount="110")
    ids = [old["projection_id"],new["projection_id"]]
    request, all_sources = preview(client, ids)
    frozen = publish(client, request, all_sources)
    assert frozen["observations"] == 2
    _, latest = preview(client, ids, "latest_report")
    assert latest["selected"] == 1
    records = bridge.call("product_input", {"projection_ids":ids})["observations"]
    by_source = {row["source"]["document_id"]:row for row in records}
    assert next(row for row in latest["decisions"] if row["selected"])["observation_id"] == by_source["newer"]["observation_id"]
    request, previous = preview(client, ids, "prefer_explicit_restated")
    link = {"newer_observation_id":by_source["newer"]["observation_id"],"older_observation_id":by_source["older"]["observation_id"],"reason":"Reviewed synthetic correction"}
    response = client.post("/v1/restatements", json=link)
    assert response.status_code == 200, response.text
    stale = client.post("/v1/datasets", json={**request,"preview_digest":previous["preview_digest"],"reason":"stale preview"})
    assert stale.status_code == 409
    _, revised = preview(client, ids, "prefer_explicit_restated")
    assert revised["selected"] == 1
    assert client.get("/v2/datasets/"+frozen["dataset_version_id"]+"/observations").json()["total"] == 2
    cycle = client.post("/v1/restatements", json={"newer_observation_id":link["older_observation_id"],"older_observation_id":link["newer_observation_id"],"reason":"must refuse cycle"})
    assert cycle.status_code == 409


def test_public_snapshot_paginates_beyond_one_thousand_without_duplicate_or_loss(system):
    client, bridge = system
    _, projection = receive(client, bridge, periods=tuple("2023" for _ in range(1205)))
    request, decision = preview(client, [projection["projection_id"]])
    dataset = publish(client, request, decision)
    path = "/v2/datasets/"+dataset["dataset_version_id"]+"/observations"
    cursor, seen, pages = None, [], 0
    while True:
        response = client.get(path, params={"limit":200,**({"cursor":cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json();pages += 1
        assert page["total"] == 1205
        seen.extend(row["observation_id"] for row in page["items"])
        cursor = page["next_cursor"]
        if not cursor:break
        assert client.get(path,params={"limit":100,"cursor":cursor}).status_code == 422
    assert pages == 7 and len(seen) == len(set(seen)) == 1205


def test_research_csv_and_parquet_have_exact_decimals_typed_years_and_frozen_definitions(system):
    from decimal import Decimal
    import csv
    import pyarrow as pa
    import pyarrow.parquet as pq
    client, bridge = system
    _, projection = receive(client, bridge)
    base="/v1/projections/"+projection["projection_id"]
    result=client.get(base+"/export?format=csv")
    assert result.status_code==200,result.text
    with ZipFile(io.BytesIO(result.content)) as zipped:
        manifest=json.loads(zipped.read("manifest.json"))
        assert manifest["complete"] and manifest["observations"]==3 and not manifest["aggregation_performed"]
        rows=list(csv.DictReader(io.StringIO(zipped.read("facts.csv").decode())))
        assert {row["year"] for row in rows}=={"2023","2024","2025"}
        assert {row["value_numeric"] for row in rows}=={"1.530140000000000000001"}
        assert json.loads(zipped.read("definitions.json"))[0]["clause_key"]=="TEST.amount"
        properties=list(csv.DictReader(io.StringIO(zipped.read("properties.csv").decode())))
        assert all(row["value_type"] not in {"object","array"} for row in properties)
    result=client.get(base+"/export?format=parquet")
    assert result.status_code==200,result.text
    with ZipFile(io.BytesIO(result.content)) as zipped:
        table=pq.read_table(io.BytesIO(zipped.read("facts.parquet")))
        assert pa.types.is_decimal(table.schema.field("value_numeric").type)
        assert pa.types.is_integer(table.schema.field("year").type)
        assert pa.types.is_date32(table.schema.field("period_start").type)
        assert set(table.column("value_numeric").to_pylist())=={Decimal("1.530140000000000000001")}
        relations=pq.read_table(io.BytesIO(zipped.read("relations.parquet")))
        assert pa.types.is_boolean(relations.schema.field("comparison_eligible").type)
    too_precise="0."+"1"*80
    _, huge = receive(client, bridge, source="wide-precision",periods=("2023",),amount=too_precise)
    assert client.get("/v1/projections/"+huge["projection_id"]+"/export?format=csv").status_code==200
    assert client.get("/v1/projections/"+huge["projection_id"]+"/export?format=parquet").status_code==413


def test_qualitative_projection_preserves_unknown_period_and_literal_statement(system):
    client, bridge = system
    statement="2023年至2025年持续实施环境监测；未声明具体统计边界。"
    _, projection=receive(client,bridge,source="statement",periods=(None,),statement=statement)
    rows=client.get("/v1/projections/"+projection["projection_id"]+"/observations").json()["items"]
    assert len(rows)==1 and rows[0]["value"]["kind"]=="assertion"
    assert rows[0]["value"]["statement_raw"]==statement and rows[0]["period"]["kind"]=="unknown"


def test_projection_failure_does_not_change_receipt_and_can_be_retried(system,monkeypatch):
    client, bridge = system
    export_id,data=archive()
    receipt=client.post("/v1/ingestion/exports",content=data).json()
    import esg_database.projector as module
    original=module.project_export
    def unavailable(*args):raise ValueError("Simulated projection defect")
    monkeypatch.setattr(module,"project_export",unavailable)
    ProjectionWorker(bridge).tick()
    status=client.get(f"/v1/ingestion/exports/{export_id}/projection").json()
    assert status["status"]=="failed" and not status["retryable"]
    assert client.get(f"/v1/ingestion/receipts/{export_id}").json()==receipt
    monkeypatch.setattr(module,"project_export",original)
    assert client.post(f"/v1/ingestion/exports/{export_id}/projection").json()["status"]=="completed"
