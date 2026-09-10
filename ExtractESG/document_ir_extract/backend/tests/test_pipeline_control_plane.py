from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from esg_v2.assets.report_catalog import CATALOG_FILENAME, ReportAssetCatalog
from esg_v2.control_plane.contracts import (
    PipelineTaskStatus,
    PipelineTaskType,
    TargetedExtractionBatchPipelineTaskRequest,
    TargetedExtractionPipelineTaskRequest,
)
from esg_v2.control_plane.scheduler import PipelineScheduler, PipelineTaskHooks, WorkerCommand
from esg_v2.control_plane.hooks import PlatformTaskHooks
from esg_v2.control_plane.secrets import RuntimeSecretVault
from esg_v2.control_plane.store import PipelineQueueStore
from esg_v2.utils.hashing import sha256_file


def _create_task(store: PipelineQueueStore, task_id: str, title: str = "Task"):
    return store.create(
        task_id=task_id,
        task_type=PipelineTaskType.OCR,
        operation="ocr",
        title=title,
        payload={"asset_id": "pdf-test"},
        native_job_id=f"ocr-{task_id}",
    )


def test_queue_reorder_cancel_and_restart_recovery(tmp_path: Path) -> None:
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    _create_task(store, "pt-a")
    _create_task(store, "pt-b")
    _create_task(store, "pt-c")

    reordered = store.reorder(["pt-c", "pt-a"])
    assert [item.task_id for item in reordered] == ["pt-c", "pt-a", "pt-b"]

    cancelled = store.request_cancel("pt-a")
    assert cancelled.status == PipelineTaskStatus.CANCELLED
    claimed = store.claim_next()
    assert claimed is not None and claimed.task_id == "pt-c"
    assert store.recover_interrupted() == ["pt-c"]
    assert store.get("pt-c").status == PipelineTaskStatus.INTERRUPTED


def test_queue_create_many_is_atomic_and_keeps_package_order(tmp_path: Path) -> None:
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    specifications = [
        {
            "task_id": f"pt-batch-{index}",
            "task_type": PipelineTaskType.TARGETED_EXTRACTION,
            "operation": "run",
            "title": package_id,
            "payload": {"package_id": package_id},
            "native_job_id": f"tx-batch-{index}",
        }
        for index, package_id in enumerate(("e1-5", "e1-6", "e2-4"), start=1)
    ]

    tasks = store.create_many(specifications)

    assert [task.payload["package_id"] for task in tasks] == ["e1-5", "e1-6", "e2-4"]
    assert [task.position for task in tasks] == [1, 2, 3]
    assert all(store.events(task.task_id)[0]["stage"] == "queued" for task in tasks)

    duplicate = [dict(specifications[0]), dict(specifications[0])]
    duplicate[0]["task_id"] = "pt-duplicate"
    duplicate[1]["task_id"] = "pt-duplicate"
    try:
        store.create_many(duplicate)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate batch should fail atomically")
    assert all(task.task_id != "pt-duplicate" for task in store.list(100))


def test_clear_history_preserves_active_queue_and_business_artifacts(tmp_path: Path) -> None:
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    queued = _create_task(store, "pt-queued")
    completed = _create_task(store, "pt-completed")
    failed = _create_task(store, "pt-failed")
    store.update(completed.task_id, status=PipelineTaskStatus.COMPLETED)
    store.update(failed.task_id, status=PipelineTaskStatus.FAILED)
    artifact = tmp_path / "business-artifact.json"
    artifact.write_text("preserve me", encoding="utf-8")

    deleted = store.clear_history()

    assert set(deleted) == {completed.task_id, failed.task_id}
    assert store.get(queued.task_id).status == PipelineTaskStatus.QUEUED
    assert artifact.read_text(encoding="utf-8") == "preserve me"


def test_report_asset_catalog_tracks_successful_ocr_by_hash(tmp_path: Path) -> None:
    asset_root = tmp_path / "pdf"
    ocr_root = tmp_path / "ocr"
    catalog = ReportAssetCatalog(asset_root, ocr_root)
    asset = catalog.add("报告.pdf", io.BytesIO(b"%PDF-1.4\nfixture"))
    assert asset["ocr_status"] == "unprocessed"
    assert (asset_root / CATALOG_FILENAME).is_file()

    run_root = ocr_root / "ocr-20260101T000000Z-aaaaaaaaaaaa"
    run_root.mkdir(parents=True)
    (run_root / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_root.name,
                "ocr_provider": "local_paddleocr",
                "page_count": 1,
                "source": {"sha256": sha256_file(asset_root / "报告.pdf")},
            }
        ),
        encoding="utf-8",
    )
    refreshed = catalog.refresh()
    tracked = refreshed["assets"][0]
    assert tracked["ocr_status"] == "processed"
    assert tracked["ocr_runs"][0]["run_id"] == run_root.name


def test_report_asset_delete_requires_no_typed_filename(tmp_path: Path) -> None:
    catalog = ReportAssetCatalog(tmp_path / "pdf", tmp_path / "ocr")
    asset = catalog.add("报告.pdf", io.BytesIO(b"%PDF-1.4\nfixture"))

    result = catalog.delete(asset["asset_id"])

    assert result["deleted"] is True
    assert not (tmp_path / "pdf" / "报告.pdf").exists()


class _FakeHooks(PipelineTaskHooks):
    def __init__(self) -> None:
        self.interrupted_ids: list[str] = []

    def command(self, task, secrets):
        delay = float(task.payload.get("delay", 0.01))
        code = (
            "import json,time;"
            "print('ESG_PIPELINE_EVENT '+json.dumps({'kind':'log','stage':'work','message':'running'}),flush=True);"
            f"time.sleep({delay});"
            "print('ESG_PIPELINE_EVENT '+json.dumps({'kind':'result','payload':{'ok':True}}),flush=True)"
        )
        return WorkerCommand([sys.executable, "-c", code], Path.cwd(), os.environ.copy())

    def interrupted(self, task) -> None:
        self.interrupted_ids.append(task.task_id)


def _wait_for_status(
    store: PipelineQueueStore,
    task_id: str,
    statuses: set[PipelineTaskStatus],
    timeout: float = 5,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = store.get(task_id)
        if task.status in statuses:
            return task
        time.sleep(0.02)
    raise AssertionError(f"Task {task_id} did not enter {statuses}")


def test_scheduler_runs_one_isolated_worker_and_terminates_process_group(tmp_path: Path) -> None:
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    first = store.create(
        task_id="pt-first",
        task_type=PipelineTaskType.OCR,
        operation="ocr",
        title="first",
        payload={"delay": 5},
        native_job_id="ocr-first",
    )
    _create_task(store, "pt-second")
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), _FakeHooks(), poll_interval_seconds=0.02)
    scheduler.start()
    try:
        running = _wait_for_status(store, first.task_id, {PipelineTaskStatus.RUNNING})
        assert running.worker_pid
        store.request_cancel(first.task_id)
        scheduler.wake()
        _wait_for_status(store, first.task_id, {PipelineTaskStatus.CANCELLED})
        second = _wait_for_status(store, "pt-second", {PipelineTaskStatus.COMPLETED})
        assert second.started_at
        assert store.get(first.task_id).finished_at <= second.started_at
    finally:
        scheduler.stop()


def test_scheduler_shutdown_marks_active_task_interrupted(tmp_path: Path) -> None:
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = store.create(
        task_id="pt-shutdown",
        task_type=PipelineTaskType.DOCUMENT_IR,
        operation="build",
        title="shutdown",
        payload={"delay": 5},
        native_job_id="ir-shutdown",
    )
    hooks = _FakeHooks()
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), hooks, poll_interval_seconds=0.02)
    scheduler.start()
    _wait_for_status(store, task.task_id, {PipelineTaskStatus.RUNNING})
    scheduler.stop()

    interrupted = store.get(task.task_id)
    assert interrupted.status == PipelineTaskStatus.INTERRUPTED
    assert interrupted.worker_pid is None
    assert hooks.interrupted_ids == [task.task_id]


def test_targeted_cloud_secret_is_not_persisted_and_restart_requires_environment(
    tmp_path: Path, monkeypatch
) -> None:
    request = TargetedExtractionPipelineTaskRequest(
        ir_run_id="ir-1",
        package_id="package-1",
        package_version="1.0.0",
        semantic_provider="qiniu_vlm",
        semantic_model="qwen3.5-397b-a17b",
        qiniu_api_key="secret-value",
    )
    payload = request.model_dump(mode="json")
    assert "qiniu_api_key" not in payload
    assert payload["semantic_provider"] == "qiniu_vlm"
    assert payload["semantic_model"] == "qwen3.5-397b-a17b"

    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = store.create(
        task_id="pt-targeted-cloud",
        task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="run",
        title="targeted",
        payload=payload,
        native_job_id="tx-1",
        requires_runtime_secret=True,
    )
    monkeypatch.delenv("QINIU_API_KEY", raising=False)
    assert not PipelineScheduler._configured_secret_available(task)
    monkeypatch.setenv("QINIU_API_KEY", "environment-secret")
    assert PipelineScheduler._configured_secret_available(task)


def test_targeted_batch_requires_unique_packages_and_shared_execution() -> None:
    shared = {
        "ir_run_id": "ir-shared",
        "package_version": "1.0.0",
        "semantic_provider": "local_nuextract",
    }
    batch = TargetedExtractionBatchPipelineTaskRequest(
        requests=[
            TargetedExtractionPipelineTaskRequest(
                **shared,
                package_id="esrs.e1-5",
                metric_ids=["e1-5.dp01", "e1-5.dp01", "e1-5.dp02"],
            ),
            TargetedExtractionPipelineTaskRequest(
                **shared,
                package_id="esrs.e1-6",
                metric_ids=["e1-6.dp07"],
            ),
            TargetedExtractionPipelineTaskRequest(
                **shared,
                package_id="esrs.e2-4",
                metric_ids=["e2-4.dp02"],
            ),
        ]
    )
    assert [len(item.metric_ids) for item in batch.requests] == [2, 1, 1]

    for requests, message in (
        (
            [
                TargetedExtractionPipelineTaskRequest(
                    **shared, package_id="esrs.e1-5", metric_ids=["metric-1"]
                ),
                TargetedExtractionPipelineTaskRequest(
                    **shared, package_id="esrs.e1-5", metric_ids=["metric-2"]
                ),
            ],
            "Duplicate standard package",
        ),
        (
            [
                TargetedExtractionPipelineTaskRequest(
                    **shared, package_id="esrs.e1-5", metric_ids=["metric-1"]
                ),
                TargetedExtractionPipelineTaskRequest(
                    **{**shared, "ir_run_id": "ir-other"},
                    package_id="esrs.e1-6",
                    metric_ids=["metric-2"],
                ),
            ],
            "share one IR",
        ),
        (
            [
                TargetedExtractionPipelineTaskRequest(
                    **shared, package_id="esrs.e1-5", metric_ids=[]
                )
            ],
            "select at least one metric",
        ),
    ):
        try:
            TargetedExtractionBatchPipelineTaskRequest(requests=requests)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"batch validation should fail: {message}")


def test_targeted_cloud_provider_survives_queue_to_worker_command(tmp_path: Path) -> None:
    request = TargetedExtractionPipelineTaskRequest(
        ir_run_id="ir-cloud",
        package_id="esrs.test",
        package_version="1.0.0",
        metric_ids=["metric-1"],
        semantic_provider="qiniu_vlm",
        semantic_model="qwen-cloud-vlm",
        retrieval_object_top_n=5,
        qiniu_api_key="runtime-only",
    )
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = store.create(
        task_id="pt-cloud-command",
        task_type=PipelineTaskType.TARGETED_EXTRACTION,
        operation="run",
        title="cloud extraction",
        payload=request.model_dump(mode="json"),
        native_job_id="tx-cloud",
        requires_runtime_secret=True,
    )
    hooks = object.__new__(PlatformTaskHooks)
    hooks.settings = SimpleNamespace(
        targeted_runtime_python=Path(sys.executable),
        targeted_app_root=tmp_path / "targeted_table_extract",
    )

    command = hooks.command(task, {"QINIU_API_KEY": "runtime-only"})

    provider_index = command.argv.index("--semantic-provider")
    model_index = command.argv.index("--semantic-model")
    object_top_n_index = command.argv.index("--retrieval-object-top-n")
    assert command.argv[provider_index + 1] == "qiniu_vlm"
    assert command.argv[model_index + 1] == "qwen-cloud-vlm"
    assert command.argv[object_top_n_index + 1] == "5"
    assert command.environment["QINIU_API_KEY"] == "runtime-only"
    assert "runtime-only" not in json.dumps(task.payload)
