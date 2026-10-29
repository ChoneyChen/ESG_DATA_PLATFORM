"""Composition root: filesystem, HTTP and model paths stop here."""
import os
from pathlib import Path
from esg_runtime.layout import EXTRACT_ROOT
from esg_clauses.repository import ClauseRepository
from .state import RunStore
from .adapters.artifacts import FileArtifactPort
from .adapters.document import HttpDocumentPort

def resources():
    root=Path(os.getenv('ESG_EXTRACTION_DATA_ROOT',str(EXTRACT_ROOT/'extraction/.local')))
    return (ClauseRepository(os.getenv('ESG_CLAUSE_ROOT',str(EXTRACT_ROOT/'standard'))),
        RunStore(root/'runs.sqlite3'),FileArtifactPort(root/'artifacts'),
        HttpDocumentPort(os.getenv('ESG_DOCUMENT_BACKEND_URL','http://127.0.0.1:18080')))

def engine_digest():
    import hashlib
    paths=[path for module in ('extraction','contracts','shared_runtime') for path in (EXTRACT_ROOT/module/'src').rglob('*.py')]
    value=hashlib.sha256()
    for path in sorted(paths):
        value.update(path.relative_to(EXTRACT_ROOT).as_posix().encode());value.update(b'\0');value.update(path.read_bytes());value.update(b'\0')
    return value.hexdigest()

def execute(run_id,*,model=None):
    from esg_targeted.pipeline_contracts import RunRequest
    from .adapters.local_model import LocalQwenModel
    from .engine import ExtractionBackbone,PipelineCancelled
    repo,store,artifacts,documents=resources();state=store.claim(run_id)
    model=model or LocalQwenModel(Path(os.getenv('QWEN_VLM_MODEL_PATH',str(Path.home()/'Desktop/model/Qwen3.5-9B-4bit'))))
    model.progress_callback=lambda event:store.update(run_id,current_call=event)
    try:
        fingerprint=engine_digest()
        if state.get('engine_fingerprint')!=fingerprint:raise ValueError('Extraction code changed after run creation; submit a new run')
        actual=documents.document(state['request']['ir_run_id']).model_dump(mode='json')
        if actual!=state['document']:raise ValueError('Document evidence changed after run creation; submit a new run')
        body=ExtractionBackbone(documents,model,artifacts,engine_identity={"version":"clause-backbone-1.0","fingerprint":fingerprint}).run(run_id,RunRequest.model_validate(state['request']),repo.thaw(state['targets']),
            cancel_check=lambda:store.get(run_id)['cancel_requested'],on_progress=lambda event:store.update(run_id,progress=event))
        status='completed' if all(c['state']=='covered' for c in body['coverage']) else 'partial'
        return store.update(run_id,status=status,summary=body['summary'],finished_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())
    except PipelineCancelled:
        return store.update(run_id,status='cancelled')
    except Exception as exc:
        store.update(run_id,status='failed',error=f'{type(exc).__name__}: {str(exc)[:1000]}');raise
    finally:
        model.close()
