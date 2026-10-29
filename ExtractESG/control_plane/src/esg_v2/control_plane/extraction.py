"""PDF preparation → independent clause run. Orchestration owns neither reader nor clauses."""
import json,threading
from uuid import uuid4
import requests
from pydantic import BaseModel,ConfigDict,Field,model_validator
from typing import Literal
from esg_targeted.pipeline_contracts import RunRequest,PipelineBudget
from esg_v2.document.search import PageSearchIntent
from .report_processing import ProcessingRequest,ProcessingConfig,ProcessingActionRequest
from .context import dispatch_key

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['pdf','document_ir']
    asset_id:str | None=None
    ir_run_id:str | None=None
    @model_validator(mode='after')
    def selected(self):
        if self.kind=='pdf' and (not self.asset_id or self.ir_run_id):raise ValueError('PDF input needs only an asset identity')
        if self.kind=='document_ir' and (not self.ir_run_id or self.asset_id):raise ValueError('IR input needs only an IR identity')
        return self

class ExtractionPlanRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    input:Input
    clause_keys:list[str]=Field(default_factory=list)
    selection:str | None=None
    name:str=Field(default='条款提取',min_length=1,max_length=120)
    budget:PipelineBudget=Field(default_factory=PipelineBudget)
    expected_target_digest:str | None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    @model_validator(mode='after')
    def targets(self):
        if bool(self.clause_keys)==bool(self.selection):raise ValueError('Select independent clauses or a named set')
        return self
    def native(self,ir_id):return RunRequest(ir_run_id=ir_id,clause_keys=self.clause_keys,selection=self.selection,name=self.name,budget=self.budget)

class ExtractionCoordinator:
    def __init__(self,store,processing,queue,cancel_task,base_url):
        self.store,self.processing,self.queue,self.cancel_task=store,processing,queue,cancel_task
        self.base_url=base_url.rstrip('/');self._stop=threading.Event();self._thread=None;self._lock=threading.RLock()
        with store._connect() as db:db.execute('CREATE TABLE IF NOT EXISTS clause_extraction_plans (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    def remote(self,method,path,payload=None):
        with requests.Session() as session:
            session.trust_env=False;response=session.request(method,self.base_url+path,json=payload,timeout=120)
            if response.status_code>=400:raise ValueError(f'Extraction service rejected request ({response.status_code}): '+response.text[:1000])
            return response.json()
    def get(self,key):
        with self.store._connect() as db:row=db.execute('SELECT body FROM clause_extraction_plans WHERE id=?',(key,)).fetchone()
        if not row:raise KeyError(key)
        return json.loads(row[0])
    def save(self,state):
        with self.store._connect() as db:db.execute('INSERT INTO clause_extraction_plans VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',(state['session_id'],json.dumps(state,ensure_ascii=False)))
    def list(self):
        with self.store._connect() as db:rows=db.execute('SELECT body FROM clause_extraction_plans ORDER BY rowid DESC').fetchall()
        return [self.detail(json.loads(row[0])) for row in rows]
    def preview(self,request):
        native=request.native(request.input.ir_run_id or 'awaiting-document-preparation')
        snapshot=self.remote('POST','/api/targets/freeze',native.model_dump(mode='json'))
        if request.expected_target_digest and request.expected_target_digest!=snapshot['digest']:
            raise ValueError('Selected clauses changed after preview; reload the target selection')
        asset=self.processing.assets.get(request.input.asset_id) if request.input.kind=='pdf' else None
        if request.input.kind=='document_ir':self.remote('POST','/api/runs/preview',native.model_dump(mode='json'))
        return {'targets':snapshot,'clause_count':len(snapshot['clauses']),'output_count':len(snapshot['clauses']),
            'source_sha256':asset['sha256'] if asset else None,'calls_model':False,'budget':request.budget.model_dump()}
    def create(self,request):
        preview=self.preview(request);key='extract-plan-'+uuid4().hex
        state={'session_id':key,'name':request.name,'status':'queued','stage':'preparing' if request.input.kind=='pdf' else 'extracting',
            'request':request.model_dump(mode='json'),'target_snapshot':preview['targets'],'source_sha256':preview['source_sha256'],
            'ir_run_id':request.input.ir_run_id,'processing_id':None,'run_id':None,'queue_task_id':None}
        self.save(state);return self.detail(state)
    def detail(self,state):
        state={**state};state.pop('target_snapshot',None)
        if state.get('run_id'):
            run=self.remote('GET',f"/api/runs/{state['run_id']}");state['run']=run;state['status']=run['status'];state['progress']=run.get('progress',{})
        state['execution_status']='finished' if state['status'] in {'completed','partial'} else state['status']
        state['result_status']='usable' if state['status']=='completed' else 'partial' if state['status']=='partial' else 'not_evaluated'
        state['available_actions']=[]
        if state['status'] in {'queued','running'}:state['available_actions']=[{'action':'cancel','label':'取消','endpoint':f"/api/report-sessions/{state['session_id']}/cancel",'method':'POST'}]
        return state
    def cancel(self,key):
        with self._lock:
            state=self.get(key)
            if state.get('queue_task_id'):self.cancel_task(state['queue_task_id'])
            elif state.get('processing_id'):self.processing.action(state['processing_id'],'cancel',ProcessingActionRequest())
            if state.get('run_id'):self.remote('POST',f"/api/runs/{state['run_id']}/cancel")
            state['status']='cancelled';self.save(state);return self.detail(state)
    def tick(self,state):
        if state['status'] not in {'queued','running'}:return
        request=ExtractionPlanRequest.model_validate(state['request'])
        if not state['ir_run_id']:
            if not state['processing_id']:
                clauses=state['target_snapshot']['clauses']
                targets=[PageSearchIntent(target_id=k,contract_digest=c['clause_digest'],data_kind=c['data_kind'],positive_terms=c['retrieval']['positive_terms'],negative_terms=c['retrieval']['negative_terms'],supporting_terms=c['retrieval']['context_terms']) for k,c in clauses.items()]
                process=self.processing.create(ProcessingRequest(asset_id=request.input.asset_id,mode='reprocess',source_sha256=state['source_sha256'],submission_key=state['session_id'],config=ProcessingConfig(page_selection='clauses',page_search_targets=targets,ocr_provider='local_paddleocr',allow_cloud_ocr=False,execute_required_reviews=False,execute_optional_reviews=False,review_provider='local_qwen')))
                state['processing_id']=process['processing_id'];state['status']='running';self.save(state)
            process=self.processing.get(state['processing_id'])
            if process['status'] in {'failed','cancelled','interrupted','needs_attention'}:
                state.update(status='failed',error='Document preparation did not produce admissible evidence',processing_status=process['status']);self.save(state);return
            if process['status'] not in {'ready','ready_with_limits'}:return
            state['ir_run_id']=process['ir_run_id'];self.save(state)
        if not state['run_id']:
            self.store.pin_ir(state['session_id'],state['ir_run_id'])
            native=request.native(state['ir_run_id']).model_copy(update={'clause_keys':list(state['target_snapshot']['clauses']),'selection':None})
            run=self.remote('POST','/api/runs',{'request':native.model_dump(mode='json'),'target_snapshot':state['target_snapshot'],'submission_key':state['session_id']})
            state.update(run_id=run['run_id'],stage='extracting');self.save(state)
        if not state['queue_task_id']:
            token=dispatch_key.set(state['session_id'])
            try:task=self.queue(state['run_id'],request.name,request.budget.timeout_seconds)
            finally:dispatch_key.reset(token)
            state['queue_task_id']=task.task_id;state['status']='running';self.save(state)
        run=self.remote('GET',f"/api/runs/{state['run_id']}")
        if run['status'] in {'completed','partial','failed','cancelled'}:state['status']=run['status'];self.save(state)
    def start(self):
        if self._thread and self._thread.is_alive():return
        self._stop.clear()
        def loop():
            while not self._stop.is_set():
                with self.store._connect() as db:rows=db.execute("SELECT body FROM clause_extraction_plans WHERE json_extract(body,'$.status') IN ('queued','running')").fetchall()
                for row in rows:
                    with self._lock:
                        state=json.loads(row[0])
                        try:self.tick(state)
                        except Exception as exc:
                            state.update(status='failed',error=f'{type(exc).__name__}: {str(exc)[:1000]}');self.save(state)
                self._stop.wait(1)
        self._thread=threading.Thread(target=loop,daemon=True);self._thread.start()
    def stop(self):
        self._stop.set()
        if self._thread:self._thread.join(timeout=2)
