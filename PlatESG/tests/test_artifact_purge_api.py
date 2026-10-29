from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PlatESG.backend.main import create_app
from PlatESG.tests.frozen_fixture import seed_frozen_database

HEADERS = {"Authorization": "Bearer purge-review-token"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    directory = tmp_path_factory.mktemp("artifact-purge")
    app = create_app({"PLATESG_DATA_DIR": str(directory / "data"), "PLATESG_AUTO_PUBLISH": "0",
                      "PLATESG_REVIEW_TOKEN": "purge-review-token", "PLATESG_ALLOW_DEMO_SEED": "1",
                      "PLATESG_LIFECYCLE_STORE_SCOPE": "isolated"})
    seed_frozen_database(app.state.bridge.environment)
    with TestClient(app) as client:
        yield client


def test_inventory_reports_artifacts_and_chain(client):
    inventory = client.get("/internal/v1/artifact-inventory", headers=HEADERS).json()
    assert inventory["artifact_rows"] > 0
    assert inventory["chain"]["esg.metric_definition_versions"] > 0
    assert inventory["chain"]["esg.standard_requirements"] > 0


def test_dry_run_and_refusals_leave_the_database_alone(client):
    before = client.get("/internal/v1/artifact-inventory", headers=HEADERS).json()
    dry = client.post("/internal/v1/artifact-purge", headers=HEADERS, json={"apply": False}).json()
    assert dry["dry_run"] is True and dry["applied"] is False
    for payload in ({"apply": True, "reason": "缺少确认"},
                    {"apply": True, "confirm": "artifact-purge-v1"},
                    {"apply": True, "confirm": "wrong-confirm", "reason": "错误确认"}):
        response = client.post("/internal/v1/artifact-purge", headers=HEADERS, json=payload)
        assert response.status_code >= 400 or response.json().get("applied") is False
    after = client.get("/internal/v1/artifact-inventory", headers=HEADERS).json()
    assert after["artifact_rows"] == before["artifact_rows"]


def test_applied_purge_empties_artifacts_and_keeps_the_chain(client):
    chain_before = client.get("/internal/v1/artifact-inventory", headers=HEADERS).json()["chain"]
    result = client.post("/internal/v1/artifact-purge", headers=HEADERS,
                         json={"apply": True, "confirm": "artifact-purge-v1", "reason": "测试产物清理"}).json()
    assert result["applied"] is True and result["chain_preserved"] is True
    assert result["removed_rows"] > 0
    after = client.get("/internal/v1/artifact-inventory", headers=HEADERS).json()
    assert after["artifact_rows"] == 0
    assert after["chain"] == chain_before
    assert client.get("/v1/observations", params={"limit": 10}).json()["items"] == []
    # The empty baseline dataset is bound to the current dictionary, so the
    # metric catalog stays readable while no observation is published.
    assert len(client.get("/internal/v1/metrics", headers=HEADERS).json()["items"]) > 0


def test_a_second_purge_is_idempotent(client):
    result = client.post("/internal/v1/artifact-purge", headers=HEADERS,
                         json={"apply": True, "confirm": "artifact-purge-v1", "reason": "重复清理"}).json()
    assert result["applied"] is True and result["removed_rows"] == 0
    assert result["chain_preserved"] is True
