#!/usr/bin/env python3
"""Install SDK wheels outside the checkout and start the current HTTP consumer."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'docs/verification/standalone.json')
    output=parser.parse_args().output
    with tempfile.TemporaryDirectory(prefix='esg-standalone-') as td:
        work=Path(td)
        env={key:value for key,value in os.environ.items() if not key.startswith(('ESG_','PLATESG_','PYTHONPATH'))}
        env['PYTHONDONTWRITEBYTECODE']='1'
        wheels=work/'wheels';wheels.mkdir()
        modules=['ExtractESG/contracts','ExtractESG/shared_runtime','ClauseESG','database']
        subprocess.run([sys.executable,'-m','pip','wheel','--no-deps','--no-build-isolation','-w',str(wheels),*[str(ROOT/path) for path in modules]],check=True,capture_output=True,env=env)
        target=work/'installed'
        subprocess.run([sys.executable,'-m','pip','install','--no-deps','--no-compile','--target',str(target),*[str(path) for path in wheels.glob('*.whl')]],check=True,capture_output=True,env=env)
        assets=work/'database';assets.mkdir()
        for name in ('runtime','queries','scripts','migrations'):shutil.copytree(ROOT/'database'/name,assets/name)
        shutil.copy2(ROOT/'database/package.json',assets/'package.json')
        # Reuse only third-party Node dependencies, never another module's code or data.
        (assets/'node_modules').symlink_to(ROOT/'database/node_modules',target_is_directory=True)
        platform=work/'PlatESG';platform.mkdir()
        shutil.copytree(ROOT/'PlatESG/backend',platform/'backend',ignore=shutil.ignore_patterns('__pycache__'))
        with socket.socket() as port:port.bind(('127.0.0.1',0));number=port.getsockname()[1]
        env.update(PYTHONPATH=str(target)+os.pathsep+str(work),ESG_DATABASE_MODULE_ROOT=str(assets),ESG_DATABASE_DATA_DIR=str(work/'capture'),
            ESG_DATABASE_INGEST_URL=f'http://127.0.0.1:{number}',PLATESG_AUTO_PUBLISH='0')
        subprocess.run([shutil.which('node'),str(assets/'runtime/manage.mjs'),'init','--profile','capture'],check=True,capture_output=True,cwd=work,env=env)
        script='''
import json,subprocess,sys,time
from urllib.request import urlopen
import esg_clauses,esg_contracts,esg_database
from esg_clauses.templates import new_clause
from fastapi.testclient import TestClient
from PlatESG.backend.main import create_app
new_clause('LOCAL_01','LOCAL','quantitative','Isolated measurement').verify()
server=subprocess.Popen([sys.executable,'-m','uvicorn','esg_database.api:app','--host','127.0.0.1','--port',sys.argv[1]],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
try:
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:
            with urlopen('http://127.0.0.1:'+sys.argv[1]+'/health',timeout=1) as response:assert json.load(response)['products_ready']
            break
        except Exception:
            if server.poll() is not None:raise RuntimeError(server.stderr.read().decode()[-2000:])
            time.sleep(.1)
    else:raise RuntimeError('Isolated database readiness timed out')
    with TestClient(create_app()) as client:
        health=client.get('/health');assert health.status_code==200,health.text
        assert client.get('/api/v2/datasets').json()['total']==0
        assert client.get('/internal/v1/metrics').status_code==410
    print(json.dumps({'clause_sdk_import':esg_clauses.__file__,'database_sdk_import':esg_database.__file__,
        'products_ready':True,'published_catalog':'empty','retired_api':410,'requires_extract_checkout':False,'live_database_opened':False}))
finally:
    server.terminate()
    try:server.communicate(timeout=8)
    except subprocess.TimeoutExpired:server.kill();server.communicate()
'''
        result=subprocess.run([sys.executable,'-c',script,str(number)],capture_output=True,text=True,cwd=work,env=env,timeout=55)
        if result.returncode:raise RuntimeError(result.stderr[-5000:])
        body=json.loads(result.stdout.splitlines()[-1])
        assert all(str(target) in body[key] for key in ('clause_sdk_import','database_sdk_import'))
        for key in ('clause_sdk_import','database_sdk_import'):body.pop(key)
        body['installed_sdk_modules']=[path.name for path in wheels.glob('*.whl')]
        body['deployment_boundary']='SDK wheels + independent DB Node assets + HTTP; no legacy dictionary or extraction checkout'
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(body,ensure_ascii=False))


if __name__=='__main__':main()
