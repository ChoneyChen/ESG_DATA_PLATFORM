"""Current service boundary; no SQL files, old fixtures or running services."""
from hashlib import sha256
import io
import pytest
from fastapi.testclient import TestClient
from esg_database.http_client import ProductServiceError
from PlatESG.backend import main,products
from PlatESG.backend.evidence_resolver import EvidenceResolver


@pytest.fixture
def platform(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,*args,**kwargs):pass
        def call(self,path,model=None):
            calls.append(path)
            if path=="/health":return {"products_ready":True}
            if path.startswith("/v2/datasets?"):return {"items":[],"total":0,"returned":0,"query_digest":"empty"}
            if path=="/v2/sources/"+"a"*64:return {"document_id":"synthetic","pdf_sha256":"a"*64}
            raise ProductServiceError(404,"not_found")
        def download(self,path):calls.append(path);return b"synthetic-export","application/zip"
    monkeypatch.setattr(main,"ProductClient",Client);monkeypatch.setattr(products,"ProductClient",Client)
    with TestClient(main.create_app(),client=("127.0.0.1",5000)) as client:yield client,calls


def test_current_api_prefix_and_published_only_boundary(platform):
    client,calls=platform
    result=client.get("/api/v2/datasets?limit=7&cursor=next")
    assert result.status_code==200 and result.json()["total"]==0
    assert calls[-1]=="/v2/datasets?limit=7&cursor=next"
    assert client.get("/health").json()["auto_publish"] is False
    assert client.post("/api/v2/datasets",json={}).status_code==405
    assert client.get("/api/v2/datasets?limit=1001").status_code==422


@pytest.mark.parametrize("path",["/api/v1/metrics","/v1/datasets","/internal/v1/review","/review"])
def test_old_interfaces_are_gone(platform,path):
    client,calls=platform
    assert client.get(path).status_code==410
    assert not calls


def test_pdf_authorization_precedes_source_read(platform,monkeypatch):
    client,calls=platform
    reads=[]
    monkeypatch.setattr(EvidenceResolver,"pdf",lambda self,sha:reads.append(sha) or b"%PDF synthetic")
    assert client.get("/api/v2/sources/"+"b"*64+"/pdf").status_code==404
    assert reads==[]
    result=client.get("/api/v2/sources/"+"a"*64+"/pdf")
    assert result.status_code==200 and result.content==b"%PDF synthetic"
    assert reads==["a"*64]


def test_research_export_forwards_filter_without_reinterpreting_values(platform):
    client,calls=platform
    result=client.get("/api/v2/datasets/synthetic/export?format=parquet&period=2023&clause_key=TEST")
    assert result.content==b"synthetic-export"
    assert calls[-1]=="/v2/datasets/synthetic/export?format=parquet&period=2023&clause_key=TEST"


def test_document_port_verifies_source_bytes_and_never_opens_a_local_directory(monkeypatch):
    body=b"%PDF synthetic-source";identity=sha256(body).hexdigest()
    class Opener:
        def open(self,url,timeout):
            assert url.endswith("/api/report-sources/"+identity+"/file") or url.endswith("/api/report-sources/"+"f"*64+"/file")
            return io.BytesIO(body)
    monkeypatch.setattr("PlatESG.backend.evidence_resolver.build_opener",lambda *args:Opener())
    resolver=EvidenceResolver("http://127.0.0.1:18080")
    assert resolver.pdf(identity)==body
    with pytest.raises(ValueError):resolver.pdf("f"*64)
