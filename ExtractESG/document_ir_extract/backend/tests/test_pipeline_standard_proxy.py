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
