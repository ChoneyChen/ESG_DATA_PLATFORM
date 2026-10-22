"""Real HTTP contracts over isolated stores; no model or queue is executed."""
import os
from pathlib import Path
import subprocess
import sys


def test_stale_runtime_rejects_create_before_persisting_any_request(tmp_path):
    extract_root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    environment.update(ESG_TARGETED_STATE_DB=str(tmp_path / 'jobs.sqlite3'),
        ESG_TARGETED_OUTPUT_ROOT=str(tmp_path / 'results'), ESG_TARGETED_INDEX_CACHE=str(tmp_path / 'indexes'),
        ESG_TARGETED_IR_ROOTS=str(tmp_path / 'ir'),
        PYTHONPATH=os.pathsep.join(str(p) for p in [extract_root / 'targeted_table_extract/backend/src',
            extract_root / 'standard_packages/src', extract_root / 'shared_runtime/src']))
    program = """
from fastapi.testclient import TestClient
from esg_targeted.api import main
import esg_targeted.runtime_compatibility as compatibility
compatibility.runtime_release.snapshot = lambda: {'restart_required': True}
client = TestClient(main.app)
standard = {'package_id':'esrs.2023-set1.e1-6', 'package_version':'2.1.1', 'metric_ids':['esrs.2023-set1.e1-6.dp07']}
job = client.post('/api/jobs', json={'ir_run_id':'fixture-ir', **standard})
session = client.post('/api/report-sessions?enqueue=false', json={'input':{'kind':'document_ir','ir_run_id':'fixture-ir'},'standards':[standard]})
assert job.status_code == session.status_code == 409, (job.text, session.text)
assert job.json()['detail']['code'] == session.json()['detail']['code'] == 'runtime_incompatible'
assert not main.job_store.list()
assert not main.report_sessions.list()
assert client.get('/api/standards').status_code == 200
assert len(client.get('/api/standards').json()) == 99
print('stale creates blocked; no persisted jobs; read-only catalog remains available')
"""
    result = subprocess.run([sys.executable, '-B', '-c', program], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'no persisted jobs' in result.stdout
