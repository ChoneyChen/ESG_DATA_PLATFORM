from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import pytest
import time
from pathlib import Path
from types import SimpleNamespace

from esg_document.assets.report_catalog import CATALOG_FILENAME, ReportAssetCatalog
from esg_pipeline.contracts import (
    PipelineTaskStatus,
    PipelineTaskType,
)
from esg_pipeline.scheduler import PipelineScheduler, PipelineTaskHooks, WorkerCommand
from esg_pipeline.hooks import PlatformTaskHooks
from esg_pipeline.secrets import RuntimeSecretVault
from esg_pipeline.store import PipelineQueueStore
from esg_document.utils.hashing import sha256_file


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
    except (sqlite3.IntegrityError, ValueError):
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


def test_report_asset_legacy_delete_cannot_pretend_to_delete(tmp_path: Path) -> None:
    catalog = ReportAssetCatalog(tmp_path / "pdf", tmp_path / "ocr")
    asset = catalog.add("报告.pdf", io.BytesIO(b"%PDF-1.4\nfixture"))

    import pytest
    with pytest.raises(ValueError,match="LibraryDeletionService"):
        catalog.delete(asset["asset_id"])

    assert (tmp_path / "pdf" / "报告.pdf").exists()
    assert catalog.refresh()["assets"]


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
    *,
    require_worker_pid: bool = False,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = store.get(task_id)
        if task.status in statuses and (not require_worker_pid or task.worker_pid):
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
        # Claiming reserves the queue slot as RUNNING with stage=starting;
        # the process identity is published only after Popen succeeds.
        running = _wait_for_status(
            store, first.task_id, {PipelineTaskStatus.RUNNING}, require_worker_pid=True
        )
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


def test_restart_reconnects_live_worker_without_clearing_ownership(tmp_path: Path) -> None:
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    _create_task(store, "pt-live")
    store.claim_next()
    store.update("pt-live", worker_pid=os.getpid())
    assert store.recover_interrupted() == []
    assert store.get("pt-live").worker_pid == os.getpid()
    assert store.get("pt-live").status == PipelineTaskStatus.RUNNING


def test_restart_recovers_explicit_worker_result_without_http_probe(tmp_path: Path) -> None:
    class Hooks(_FakeHooks):
        def __init__(self):
            super().__init__()
            self.results = []

        def completed(self, task, result):
            self.results.append(result)

    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    _create_task(store, "pt-finished-offline")
    store.claim_next()
    result = {"manifest_path": str(tmp_path / "ocr-manifest.json"), "page_count": 5}
    log = tmp_path / "tasks" / "pt-finished-offline" / "worker.log"
    log.parent.mkdir(parents=True)
    log.write_text("diagnostic only\nESG_PIPELINE_EVENT " + json.dumps({"kind": "result", "payload": result}) + "\n", encoding="utf-8")
    hooks = Hooks()
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), hooks, poll_interval_seconds=0.1)
    scheduler.start()
    try:
        assert store.get("pt-finished-offline").status == PipelineTaskStatus.COMPLETED
        assert hooks.results == [result]
        assert hooks.interrupted_ids == []
    finally:
        scheduler.stop()


def test_worker_log_does_not_infer_success_from_nonterminal_targeted_status(tmp_path: Path) -> None:
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = store.create(task_id="pt-running", task_type=PipelineTaskType.TARGETED_EXTRACTION,
                        operation="run", title="not finished", payload={}, native_job_id="tx-running")
    log = tmp_path / "tasks" / task.task_id / "worker.log"
    log.parent.mkdir(parents=True)
    log.write_text("ESG_PIPELINE_EVENT " + json.dumps({"kind": "result", "payload": {"status": "running"}}) + "\n", encoding="utf-8")
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), _FakeHooks())
    assert scheduler._read_worker_result(task) is None


def test_worker_log_does_not_reuse_terminal_result_from_previous_attempt(tmp_path: Path) -> None:
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    task = store.create(task_id="pt-resumed", task_type=PipelineTaskType.TARGETED_EXTRACTION,
                        operation="run", title="resumed", payload={}, native_job_id="tx-resumed")
    log = tmp_path / "tasks" / task.task_id / "worker.log"
    log.parent.mkdir(parents=True)
    log.write_text("ESG_PIPELINE_EVENT " + json.dumps({"kind": "result", "payload": {"status": "completed"}}) + "\n", encoding="utf-8")
    store.add_event(task.task_id, "running", "info", "New attempt started", {"worker_log_offset": log.stat().st_size})
    with log.open("a", encoding="utf-8") as output:
        output.write("Second attempt failed before producing a result\n")
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), _FakeHooks())
    assert scheduler._read_worker_result(task) is None


def test_repeated_probe_does_not_append_identical_progress_events(tmp_path: Path) -> None:
    class Hooks(_FakeHooks):
        def probe(self, task):
            return {"stage": "fake-read", "message": "Reading one table", "progress_current": 0, "progress_total": 1}

    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    store.create(task_id="pt-probe", task_type=PipelineTaskType.OCR, operation="ocr", title="probe", payload={"delay": 2.2}, native_job_id="ocr-probe")
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), Hooks(), poll_interval_seconds=0.1)
    scheduler.start()
    try:
        _wait_for_status(store, "pt-probe", {PipelineTaskStatus.COMPLETED})
        probes = [event for event in store.events('pt-probe') if event['stage'] == 'fake-read'
            and event.get('detail', {}).get('changes', {}).get('progress_json')
            and json.loads(event['detail']['changes']['progress_json'])['status'] == 'running']
        assert len(probes) == 1
        # A measured page count remains valid even if the producer emits an
        # unregistered phase; the task type must not invent its phase label.
        assert probes[0]['message'] == '执行阶段待识别'
        progress = json.loads(probes[0]['detail']['changes']['progress_json'])
        assert (progress['stage_code'], progress['unit'], progress['current'], progress['total']) == ('fake-read', 'pages', 0, 1)
    finally:
        scheduler.stop()


def test_reconnected_worker_terminal_state_is_synchronized(tmp_path: Path) -> None:
    class Hooks(_FakeHooks):
        def probe(self, task):
            return {"status": "partial", "stage": "finished", "message": "Partial extraction", "progress_current": 1, "progress_total": 2, "summary": {"record_counts": {"observation": 3}}}

    process = __import__("subprocess").Popen([sys.executable, "-c", "import time; time.sleep(0.25)"], start_new_session=True)
    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    _create_task(store, "pt-existing")
    store.claim_next()
    store.update("pt-existing", worker_pid=process.pid)
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), Hooks(), poll_interval_seconds=0.1)
    scheduler.start()
    try:
        process.wait(timeout=3)
        result = _wait_for_status(store, "pt-existing", {PipelineTaskStatus.PARTIAL})
        assert result.worker_pid is None
        assert result.progress_current == 1
        assert any("no model restart" in event["message"] for event in store.events("pt-existing"))
    finally:
        scheduler.stop()
        if process.poll() is None:
            process.kill()
            process.wait()


def test_restart_honors_a_finished_native_job_before_interrupting(tmp_path: Path) -> None:
    class Hooks(_FakeHooks):
        def probe(self, task):
            return {"status": "completed", "stage": "finished", "progress_current": 2, "progress_total": 2, "summary": {"record_counts": {"observation": 2}}}

    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    _create_task(store, "pt-finished-offline")
    store.claim_next()  # Simulate lost API ownership with no remaining process.
    hooks = Hooks()
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), hooks, poll_interval_seconds=0.1)
    scheduler.start()
    try:
        assert store.get("pt-finished-offline").status == PipelineTaskStatus.COMPLETED
        assert hooks.interrupted_ids == []
    finally:
        scheduler.stop()


def test_scheduler_leaves_task_queued_while_shared_slot_is_busy(tmp_path: Path) -> None:
    from esg_pipeline.execution_slot import ExecutionSlot

    store = PipelineQueueStore(tmp_path / "queue.sqlite3")
    _create_task(store, "pt-slot-busy")
    slot = ExecutionSlot(tmp_path / "model-execution.lock").acquire()
    scheduler = PipelineScheduler(store, RuntimeSecretVault(), _FakeHooks(), poll_interval_seconds=0.1)
    scheduler.start()
    try:
        time.sleep(0.2)
        assert store.get("pt-slot-busy").status == PipelineTaskStatus.QUEUED
        assert store.get("pt-slot-busy").worker_pid is None
        slot.close()
        _wait_for_status(store, "pt-slot-busy", {PipelineTaskStatus.COMPLETED})
    finally:
        slot.close()
        scheduler.stop()








def test_backbone_worker_has_only_run_identity_and_local_runtime(tmp_path):
    hooks=object.__new__(PlatformTaskHooks)
    hooks.settings=SimpleNamespace(extraction_python=Path(sys.executable),extraction_root=tmp_path/'targeted_table_extract')
    store=PipelineQueueStore(tmp_path/'queue.db')
    task=store.create(task_id='pt-one',task_type=PipelineTaskType.TARGETED_EXTRACTION,operation='clause_extraction',title='提取',payload={'run_id':'extract-one'},native_job_id='extract-one')
    command=hooks.command(task,{})
    assert command.argv[-2:]==['esg_extraction.pipeline.worker','extract-one']
    assert command.environment['HF_HUB_OFFLINE']=='1'
    assert 'standards' not in command.environment['PYTHONPATH']
    assert str(tmp_path.parent/'ClauseESG/src') in command.environment['PYTHONPATH']
    task.operation='run'
    with pytest.raises(ValueError,match='Retired'):hooks.command(task,{})


@pytest.mark.parametrize('native_status',['completed','partial'])
def test_current_extraction_worker_result_is_consumed_by_real_scheduler(tmp_path,monkeypatch,capsys,native_status):
    from esg_extraction.pipeline import worker
    monkeypatch.setattr(sys,'argv',['esg-extraction','extract-protocol-fixture'])
    monkeypatch.setattr(worker,'execute',lambda _:{'run_id':'extract-protocol-fixture','status':native_status,'progress':{'current':1,'total':1},'summary':{'selected_clauses':1,'processed_clauses':1,'source_facts':1}})
    monkeypatch.setattr(worker.signal,'signal',lambda *_:None)
    worker.main()
    emitted=capsys.readouterr().out.strip()
    class Hooks(_FakeHooks):
        def command(self,task,secrets):
            return WorkerCommand([sys.executable,'-c','import sys; print(sys.argv[1],flush=True)',emitted],tmp_path,os.environ.copy())
    store=PipelineQueueStore(tmp_path/'protocol.sqlite3')
    store.create(task_id='protocol',task_type=PipelineTaskType.TARGETED_EXTRACTION,operation='clause_extraction',title='Protocol boundary',payload={},native_job_id='extract-protocol-fixture')
    scheduler=PipelineScheduler(store,RuntimeSecretVault(),Hooks(),poll_interval_seconds=0.02)
    scheduler.start()
    try:
        task=_wait_for_status(store,'protocol',{PipelineTaskStatus.COMPLETED,PipelineTaskStatus.PARTIAL,PipelineTaskStatus.FAILED})
        assert task.status.value==native_status,task.error
        assert (task.progress_current,task.progress_total)==(1,1)
        assert task.error is None
    finally:
        scheduler.stop()
