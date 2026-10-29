"""Current run state. Atomic transitions, frozen targets and no historical-route adapter."""
import json,sqlite3
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4

def now():return datetime.now(timezone.utc).isoformat()

class RunStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30);db.execute('PRAGMA busy_timeout=30000');return db
    def create(self,request,snapshot,document,submission_key=None,engine_fingerprint=None):
        state={'run_id':'extract-'+(__import__('hashlib').sha256(submission_key.encode()).hexdigest()[:32] if submission_key else uuid4().hex),'schema_version':'extraction-run-1.0','status':'queued',
            'name':request.name,'engine_fingerprint':engine_fingerprint,'request':request.model_dump(mode='json'),'targets':snapshot,'document':document,
            'created_at':now(),'updated_at':now(),'progress':{'current':0,'total':len(snapshot['clauses'])},'cancel_requested':False}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM runs WHERE run_id=?',(state['run_id'],)).fetchone()
            if row:
                old=json.loads(row[0])
                if any(old[k]!=state[k] for k in ('request','targets','document','engine_fingerprint')):raise ValueError('Submission key already belongs to different input')
                return self.public(old)
            db.execute('INSERT INTO runs VALUES (?,?)',(state['run_id'],json.dumps(state,ensure_ascii=False)))
        return self.public(state)
    def get(self,run_id):
        with self.connect() as db:row=db.execute('SELECT body FROM runs WHERE run_id=?',(run_id,)).fetchone()
        if row is None:raise KeyError(run_id)
        return json.loads(row[0])
    def update(self,run_id,**patch):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT body FROM runs WHERE run_id=?',(run_id,)).fetchone()
            if not row:raise KeyError(run_id)
            state=json.loads(row[0]);state.update(patch,updated_at=now())
            db.execute('UPDATE runs SET body=? WHERE run_id=?',(json.dumps(state,ensure_ascii=False),run_id))
        return self.public(state)
    def claim(self,run_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT body FROM runs WHERE run_id=?',(run_id,)).fetchone()
            if not row:raise KeyError(run_id)
            state=json.loads(row[0])
            if state['status']!='queued':raise ValueError('Run is not queued; start a new run with an explicit target snapshot')
            state.update(status='running',started_at=now(),updated_at=now())
            db.execute('UPDATE runs SET body=? WHERE run_id=?',(json.dumps(state,ensure_ascii=False),run_id))
        return state
    def list(self):
        with self.connect() as db:rows=db.execute('SELECT body FROM runs ORDER BY rowid DESC').fetchall()
        return [self.public(json.loads(row[0])) for row in rows]
    @staticmethod
    def public(state):
        return {k:v for k,v in state.items() if k!='targets'}|{'clause_keys':list(state.get('targets',{}).get('clauses',{})),
            'target_digest':state.get('targets',{}).get('digest'), 'artifacts_url':f"/api/runs/{state['run_id']}/facts"}
