"""Isolated current document API stores; no report producer or historical adapter."""
from types import SimpleNamespace
from esg_pipeline.store import PipelineQueueStore
from esg_document.storage.job_store import JobStore
from esg_contracts.document import OcrJobState
from esg_document.document.contracts import DocumentIrJobState

def prepare_api(tmp_path,monkeypatch):
    from esg_document import config
    settings=config.Settings(output_root=tmp_path/'ocr',document_ir_output_root=tmp_path/'ir',upload_root=tmp_path/'uploads',report_asset_root=tmp_path/'assets',pipeline_queue_db=tmp_path/'queue.db',pipeline_task_root=tmp_path/'tasks',ocr_job_state_root=tmp_path/'jobs/ocr',document_ir_job_state_root=tmp_path/'jobs/ir',storage_cleanup_root=tmp_path/'cleanup')
    monkeypatch.setattr(config,'get_settings',lambda:settings)
    from esg_document.api import main as api
    for path in (settings.output_root,settings.document_ir_output_root,settings.upload_root):path.mkdir(parents=True,exist_ok=True)
    queue=PipelineQueueStore(settings.pipeline_queue_db)
    monkeypatch.setattr(api,'settings',settings);monkeypatch.setattr(api,'pipeline_store',queue)
    monkeypatch.setattr(api,'ocr_store',JobStore(settings.ocr_job_state_root,OcrJobState));monkeypatch.setattr(api,'ir_store',JobStore(settings.document_ir_job_state_root,DocumentIrJobState))
    monkeypatch.setattr(api,'pipeline_scheduler',SimpleNamespace(wake=lambda:None))
    monkeypatch.setattr(api,'report_assets',SimpleNamespace(refresh=lambda:{'assets':[]}))
    return api,queue
