"""Synthetic owned files only; no live report, model or production deletion."""
import io
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from esg_runtime.lifecycle import LifecycleBlocked
from esg_document.assets.report_catalog import ReportAssetCatalog
from esg_document.assets.library_deletion import LibraryDeletionService
from esg_document.api.library_deletion import library_deletion_router
from esg_document.api.processing_status import processing_status_router
from esg_pipeline.store import PipelineQueueStore
from esg_pipeline.contracts import PipelineTaskType
from test_storage_inventory import _service, _write_chain, OCR_RUN, IR_ROOT, IR_CHILD


class Guard:
    def __init__(self): self.scopes=[]
    def check(self, scope): return {"verified":True,"blockers":[],"dependencies":[]}
    def reserve(self, scope, reason): self.scopes.append(scope);return "synthetic-reservation"
    def update_reservation(self, *args): return {}


def test_pdf_delete_removes_file_and_catalog_without_archive(tmp_path):
    guard=Guard();catalog=ReportAssetCatalog(tmp_path/'pdf',tmp_path/'ocr',reference_guard=guard)
    asset=catalog.add('synthetic.pdf',io.BytesIO(b'%PDF synthetic standalone'))
    service=LibraryDeletionService(catalog)
    result=service.delete_pdf(asset['asset_id'],expected_revision=0,identity_sha256=asset['sha256'])
    assert result['deleted'] is True and result['status']=='deleted' and result['recoverable'] is False
    assert not catalog.refresh(include_archived=True)['assets']
    assert not (catalog.root/'synthetic.pdf').exists() and not catalog.trash.list()


def test_stale_pdf_identity_keeps_source_bytes(tmp_path):
    catalog=ReportAssetCatalog(tmp_path/'pdf',tmp_path/'ocr',reference_guard=Guard())
    asset=catalog.add('synthetic.pdf',io.BytesIO(b'%PDF synthetic preserved'))
    with pytest.raises(LifecycleBlocked):
        LibraryDeletionService(catalog).delete_pdf(asset['asset_id'],expected_revision=1,identity_sha256=asset['sha256'])
    assert catalog.resolve(asset['asset_id']).exists()


def test_ocr_library_flat_list_and_permanent_delete_keep_pdf_scope_reusable(tmp_path):
    _write_chain(tmp_path);inventory=_service(tmp_path);guard=Guard()
    inventory.reference_guard=guard
    catalog=ReportAssetCatalog(tmp_path/'pdf',inventory.ocr_output_root,reference_guard=guard)
    service=LibraryDeletionService(catalog,inventory)
    library=service.ocr_library()
    assert library['schema_version']=='ocr-library-v1' and library['items'][0]['run_id']==OCR_RUN
    assert library['items'][0]['available_actions'][0]['method']=='DELETE'
    description=service.ocr_description(OCR_RUN)
    result=service.delete_ocr(OCR_RUN,identity_sha256=description['identity_sha256'])
    assert result['deleted'] is True and result['source_pdf_deleted'] is False
    assert not (inventory.ocr_output_root/OCR_RUN).exists()
    assert not (inventory.ir_output_root/IR_ROOT).exists() and not (inventory.ir_output_root/IR_CHILD).exists()
    assert 'source_sha256' not in guard.scopes[0] and not inventory.trash.list()


def test_status_projection_counts_queue_without_returning_payloads(tmp_path):
    store=PipelineQueueStore(tmp_path/'queue.sqlite3')
    store.create(task_id='synthetic-task',task_type=PipelineTaskType.OCR,operation='ocr',title='合成识别任务',payload={'sensitive_fixture':'must not be returned'},native_job_id='ocr-synthetic')
    app=FastAPI();app.include_router(processing_status_router(store))
    result=TestClient(app).get('/api/processing-status').json()
    assert result['queued_count']==1 and result['running_count']==0
    assert result['active_tasks'][0]['task_id']=='synthetic-task'
    assert 'payload' not in result['active_tasks'][0] and 'must not be returned' not in str(result)
