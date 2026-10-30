#!/usr/bin/env python3
"""Build/install lightweight SDK wheels outside the checkout; cold-start an isolated consumer."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    with tempfile.TemporaryDirectory(prefix='esg-standalone-') as td:
        work = Path(td)
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(('ESG_', 'PLATESG_', 'PYTHONPATH')):
                env.pop(key, None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        wheels = work/'wheels'; wheels.mkdir()
        modules = ['ExtractESG/contracts','ExtractESG/shared_runtime','ClauseESG','database']
        subprocess.run([sys.executable,'-m','pip','wheel','--no-deps','--no-build-isolation','-w',str(wheels),
                        *[str(ROOT/p) for p in modules]],check=True,capture_output=True,env=env)
        target = work/'installed'
        subprocess.run([sys.executable,'-m','pip','install','--no-deps','--no-compile','--target',str(target),
                        *[str(p) for p in wheels.glob('*.whl')]],check=True,capture_output=True,env=env)
        assets = work/'database';assets.mkdir()
        for name in ['runtime','queries','scripts','migrations','catalog']:
            shutil.copytree(ROOT/'database'/name,assets/name)
        shutil.copy2(ROOT/'database/package.json',assets/'package.json')
        # Third-party runtime dependencies are deployment assets, not imported application source roots.
        (assets/'node_modules').symlink_to(ROOT/'database/node_modules',target_is_directory=True)
        app = work/'PlatESG';app.mkdir()
        shutil.copytree(ROOT/'PlatESG/backend',app/'backend',ignore=shutil.ignore_patterns('__pycache__'))
        env.update(PYTHONPATH=str(target)+os.pathsep+str(work), ESG_DATABASE_MODULE_ROOT=str(assets),
                   ESG_DATABASE_DATA_DIR=str(work/'capture-db'),PLATESG_DATA_DIR=str(work/'capture-db'),PLATESG_AUTO_PUBLISH='0',
                   PLATESG_LOCAL_OPERATOR='0',PLATESG_REVIEW_TOKEN='isolated-test-only-token')
        subprocess.run([str(ROOT/'database/scripts/run-node.sh'),str(assets/'runtime/manage.mjs'),'init'],check=True,
                       capture_output=True,cwd=work,env=env)
        script = '''
import json
import esg_clauses, esg_contracts, esg_database
from esg_clauses.templates import new_clause
from fastapi.testclient import TestClient
from PlatESG.backend.main import create_app
clause = new_clause('LOCAL_01','LOCAL','quantitative','Isolated raw measurement')
clause.verify()
with TestClient(create_app()) as client:
    health = client.get('/health')
    assert health.status_code == 200, health.text
    assert client.get('/internal/v1/metrics').status_code in {401,403}
print(json.dumps({'clause_sdk_import':esg_clauses.__file__, 'database_sdk_import':esg_database.__file__,
  'consumer_health':health.json(), 'requires_extract_checkout':False, 'live_database_opened':False}))
'''
        result = subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,cwd=work,env=env)
        if result.returncode:
            raise RuntimeError(result.stderr[-5000:])
        body = json.loads(result.stdout.splitlines()[-1])
        assert all(str(target) in body[key] for key in ['clause_sdk_import','database_sdk_import'])
        body['installed_sdk_modules'] = [p.name for p in wheels.glob('*.whl')]
        body['deployment_boundary'] = 'SDK wheels + independent DB Node assets/dependencies; Plat consumer copied outside checkout'
        body.pop('clause_sdk_import');body.pop('database_sdk_import')
        output=ROOT/'docs/implementation/2026-10-30/standalone.json'
        output.write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(body,ensure_ascii=False))


if __name__ == '__main__':main()
