from fastapi.testclient import TestClient


def test_pipeline_standard_catalog_is_exposed_on_document_backend(monkeypatch):
    from esg_v2.api import main as api_main

    requested = []

    def fake_catalog_request(path: str):
        requested.append(path)
        if path == "/api/standards":
            return [
                {
                    "package_id": "esrs.2023-set1.e1-6",
                    "package_version": "1.2.0",
                }
            ]
        return {
            "manifest": {
                "package_id": "esrs.2023-set1.e1-6",
                "package_version": "1.2.0",
            },
            "metrics": [],
        }

    monkeypatch.setattr(api_main, "_targeted_catalog_request", fake_catalog_request)
    client = TestClient(api_main.app)

    listing = client.get("/api/pipeline/standards")
    package = client.get(
        "/api/pipeline/standards/esrs.2023-set1.e1-6/1.2.0"
    )

    assert listing.status_code == 200
    assert listing.json()[0]["package_version"] == "1.2.0"
    assert package.status_code == 200
    assert package.json()["manifest"]["package_id"] == "esrs.2023-set1.e1-6"
    assert requested == [
        "/api/standards",
        "/api/standards/esrs.2023-set1.e1-6/1.2.0",
    ]


def test_catalog_read_retries_a_targeted_backend_busy_with_extraction(monkeypatch):
    import requests
    from fastapi import HTTPException
    from esg_v2.api import main as api_main

    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"items": []}

    class Session:
        trust_env = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, timeout):
            calls.append((url, timeout))
            if len(calls) < 3:
                raise requests.Timeout("extraction holds the backend")
            return Response()

    monkeypatch.setattr(api_main.requests, "Session", Session)
    monkeypatch.setattr(api_main.time, "sleep", lambda *_: None)
    assert api_main._targeted_catalog_request("/api/standards/catalog") == {"items": []}
    assert len(calls) == 3 and calls[0][1] == 30

    calls.clear()

    class AlwaysBusy(Session):
        def get(self, url, timeout):
            calls.append(url)
            raise requests.Timeout("still busy")

    monkeypatch.setattr(api_main.requests, "Session", AlwaysBusy)
    try:
        api_main._targeted_catalog_request("/api/standards/catalog")
        raise AssertionError("a permanently busy backend must not be reported as a catalog")
    except HTTPException as exc:
        assert exc.status_code == 503 and "正忙" in str(exc.detail)
    assert len(calls) == 3
