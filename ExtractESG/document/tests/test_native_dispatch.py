"""Fault injection before durable queue commit; no OCR/model execution."""
from contextlib import contextmanager
import json

import pytest

from esg_pipeline.contracts import DocumentIrPipelineTaskRequest, OcrPipelineTaskRequest
from esg_pipeline.native_dispatch import reserve_native_run
from esg_pipeline.context import dispatch_key
from esg_pipeline.store import PipelineQueueStore
from esg_document.document.contracts import DocumentIrBuildRequest
from current_api_fixture import prepare_api


@contextmanager
def fixed_step(key):
    token = dispatch_key.set(key)
    try:
        yield
    finally:
        dispatch_key.reset(token)


@pytest.mark.parametrize("kind", ["ocr", "ir"])
def test_native_created_before_queue_crash_reuses_same_native_identity(tmp_path, monkeypatch, kind):
    api, queue = prepare_api(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "_require_processing_ready", lambda: None)
    if kind == "ocr":
        monkeypatch.setattr(api.report_assets, "get", lambda _: {"file_name": "source.pdf", "sha256": "a" * 64}, raising=False)
        monkeypatch.setattr(api.report_assets, "resolve", lambda _: tmp_path / "source.pdf", raising=False)
        submit = lambda: api.queue_ocr_task(OcrPipelineTaskRequest(asset_id="pdf-fixture", ocr_provider="local_paddleocr", allow_api_fallback=False, token="temporary-secret"))
        native_store = api.ocr_store
    else:
        ocr_root = api.settings.output_root / "ocr-fixture"
        ocr_root.mkdir()
        (ocr_root / "manifest.json").write_text(json.dumps({"source": {"sha256": "a" * 64}}))
        submit = lambda: api.queue_document_ir_task(DocumentIrPipelineTaskRequest(build_request=DocumentIrBuildRequest(ocr_run_id="ocr-fixture", review_provider="local_qwen")))
        native_store = api.ir_store
    real_queue = api._queue_task
    def fail_after_native(**kwargs):
        raise RuntimeError("fault after native state, before queue commit")
    monkeypatch.setattr(api, "_queue_task", fail_after_native)
    with fixed_step(f"processing-fixture:{kind}:0"):
        with pytest.raises(RuntimeError, match="before queue"):
            submit()
        natives = native_store.list()
        assert len(natives) == 1
        reserved_id = natives[0].run_id
        assert queue.list() == []
        monkeypatch.setattr(api, "_queue_task", real_queue)
        queued = submit()
    assert queued.native_job_id == reserved_id
    assert len(native_store.list()) == 1
    assert len(queue.list()) == 1
    assert "temporary-secret" not in queued.model_dump_json()
    with queue._connect() as db:
        reservation = db.execute("SELECT * FROM pipeline_native_reservations").fetchall()
    assert len(reservation) == 1
    assert "temporary-secret" not in str([dict(row) for row in reservation])


def test_native_reservation_survives_restart_and_refuses_changed_frozen_request(tmp_path):
    queue = PipelineQueueStore(tmp_path / "queue.sqlite")
    first, fingerprint = reserve_native_run(queue, "ocr", {"asset_id": "a", "provider": "local"}, "processing-a:ocr:0")
    restarted = PipelineQueueStore(queue.path)
    second, repeated = reserve_native_run(restarted, "ocr", {"provider": "local", "asset_id": "a"}, "processing-a:ocr:0")
    assert first == second
    assert fingerprint == repeated
    with pytest.raises(ValueError, match="固定请求已变化"):
        reserve_native_run(restarted, "ocr", {"asset_id": "b", "provider": "local"}, "processing-a:ocr:0")
    with pytest.raises(ValueError, match="阶段"):
        reserve_native_run(restarted, "ir", {"asset_id": "a", "provider": "local"}, "processing-a:ocr:0")
