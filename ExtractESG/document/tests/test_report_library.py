"""Generic source lineage and deletion checks using temporary synthetic bytes."""
import io
import json
from esg_document.assets.library_deletion import LibraryDeletionService
from esg_document.assets.report_catalog import ReportAssetCatalog
from esg_pipeline.store import PipelineQueueStore
from test_storage_inventory import _service, _write_chain, _write_json, OCR_RUN, IR_ROOT, IR_CHILD
from test_simple_library_smoke import Guard


def fixture(tmp_path):
    _write_chain(tmp_path);inventory=_service(tmp_path)
    guard=Guard();inventory.reference_guard=guard
    catalog=ReportAssetCatalog(tmp_path/'pdf',inventory.ocr_output_root,reference_guard=guard)
    asset=catalog.add('synthetic.pdf',io.BytesIO(b'%PDF entirely synthetic'))
    path=inventory.ocr_output_root/OCR_RUN/'manifest.json'
    manifest=json.loads(path.read_text());manifest['source']['sha256']=asset['sha256']
    path.write_text(json.dumps(manifest))
    sibling='ocr-synthetic-second'
    _write_json(inventory.ocr_output_root/sibling/'manifest.json',{'run_id':sibling,'source':{'sha256':asset['sha256'],'display_name':'different-name.pdf'},'page_count':1})
    _write_json(inventory.ocr_output_root/'ocr-synthetic-orphan'/'manifest.json',{'run_id':'ocr-synthetic-orphan','source':{'display_name':'synthetic.pdf'},'page_count':1})
    return LibraryDeletionService(catalog,inventory),asset,sibling


def test_hierarchy_uses_explicit_hash_and_preserves_unlinked_versions(tmp_path):
    service,asset,sibling=fixture(tmp_path)
    result=service.report_library()
    assert result['count']==1 and result['items'][0]['ocr_count']==2
    children=result['items'][0]['ocr_versions']
    assert {r['run_id'] for r in children}=={OCR_RUN,sibling}
    assert {r['run_id'] for r in children if r['linked_ir_ids']}=={OCR_RUN}
    assert len(result['unlinked_ocr'])==1  # matching filename is insufficient


def test_delete_pdf_cascades_owned_versions_and_preserves_unrelated_orphan(tmp_path):
    service,asset,sibling=fixture(tmp_path)
    result=service.delete_pdf(asset['asset_id'],expected_revision=0,identity_sha256=asset['sha256'])
    assert result['deleted'] is True
    assert not service.catalog.refresh(include_archived=True)['assets']
    assert not (service.inventory.ocr_output_root/OCR_RUN).exists()
    assert not (service.inventory.ocr_output_root/sibling).exists()
    assert not (service.inventory.ir_output_root/IR_ROOT).exists()
    assert not (service.inventory.ir_output_root/IR_CHILD).exists()
    assert (service.inventory.ocr_output_root/'ocr-synthetic-orphan').exists()




def test_delete_ir_removes_descendants_and_keeps_source_ocr_and_pdf(tmp_path):
    service,asset,sibling=fixture(tmp_path)
    description=service.ir_description(IR_ROOT)
    assert description['available_actions'][0]['method']=='DELETE'
    result=service.delete_ir(IR_ROOT,identity_sha256=description['identity_sha256'])
    assert result['deleted'] is True and result['source_ocr_deleted'] is False
    assert (service.inventory.ocr_output_root/OCR_RUN).exists()
    assert service.catalog.resolve(asset['asset_id']).exists()
    assert not (service.inventory.ir_output_root/IR_CHILD).exists()




def test_native_pending_deletion_is_retried_without_destroying_local_input_early(tmp_path):
    import time
    from esg_runtime.lifecycle import LifecycleBlocked,blocker
    service,asset,sibling=fixture(tmp_path)
    store=PipelineQueueStore(tmp_path/'queue.sqlite3');service.catalog.pipeline_queue_db=store.path
    calls=[]
    def cleanup(db,**kwargs):
        calls.append(kwargs)
        if len(calls)==1:raise LifecycleBlocked([blocker('native_worker_active','Native execution is stopping')])
    service.configure(cancel_task=lambda identity:None,cleanup_records=cleanup)
    try:
        identity=service.ir_description(IR_ROOT)['identity_sha256']
        result=service.delete_ir(IR_ROOT,identity_sha256=identity)
        assert result['status']=='deletion_pending' and (service.inventory.ir_output_root/IR_ROOT).exists()
        path=next(service.journal_root.glob('pending-*.json'));deadline=time.monotonic()+3
        while time.monotonic()<deadline and json.loads(path.read_text()).get('status')!='deleted':time.sleep(.01)
        assert json.loads(path.read_text())['status']=='deleted'
        assert (service.inventory.ocr_output_root/OCR_RUN).exists()
    finally:service.stop()
