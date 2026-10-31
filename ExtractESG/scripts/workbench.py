#!/usr/bin/env python3
"""Local workbench controller. Detached services, explicit ownership and no database migration."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[2]
for relative in ['ExtractESG/shared_runtime/src','ExtractESG/contracts/src']:
    sys.path.insert(0,str(ROOT/relative))
from esg_runtime.release import release_identity, check_compatibility

DATA = Path(os.environ.get('ESG_DATA_ROOT',str(ROOT/'.local/data'))).expanduser().resolve()
CONTROL = DATA/'control/workbench'
LOGS = DATA/'logs'
REGISTRY = CONTROL/'launcher.json'
SERVICES = {
    'database':(18280,'/health','database-ingestion','database'),
    'extraction':(18180,'/api/compatibility','extraction-backend','extraction'),
    'document':(18080,'/health/compatibility','document-backend','document'),
    'console':(18081,'/api/compatibility','workbench-frontend','console'),
}
OPEN = build_opener(ProxyHandler({}))


def identity(pid):
    result=subprocess.run(['ps','-p',str(pid),'-o','lstart='],capture_output=True,text=True)
    return result.stdout.strip() if result.returncode==0 else None


def belongs(record):
    return bool(record.get('identity') and identity(record['pid'])==record['identity'])


def controller_digest():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def load():
    try:return json.loads(REGISTRY.read_text())
    except (OSError,ValueError):return {}


def save(state):
    CONTROL.mkdir(parents=True,exist_ok=True)
    temporary=REGISTRY.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(REGISTRY)


@contextmanager
def lock(name,nonblocking=False):
    CONTROL.mkdir(parents=True,exist_ok=True)
    with (CONTROL/name).open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|(fcntl.LOCK_NB if nonblocking else 0))
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def get(port,path,timeout=3):
    with OPEN.open(f'http://127.0.0.1:{port}{path}',timeout=timeout) as response:
        return json.load(response)


def listening(port):
    with socket.socket() as connection:
        connection.settimeout(.3)
        return connection.connect_ex(('127.0.0.1',port))==0


def health(name):
    port,path,service,_=SERVICES[name]
    try:
        body=get(port,path)
        if name=='database':
            ready=body.get('status')=='ok' and body.get('export_contract') == 'extraction-export-1.1' and not body.get('restart_required')
        else:
            ready=not check_compatibility(body,release_identity(ROOT/'ExtractESG',service),service)
        return {'port':port,'ready':ready,'restart_required':bool(body.get('restart_required'))}
    except (OSError,ValueError):return {'port':port,'ready':False,'restart_required':False}


def snapshot():
    state=load()
    owner=state.get('supervisor',{})
    services={name:health(name) for name in SERVICES}
    return {'schema_version':'workbench-launcher-1.0','services':services,
        'ready':all(s['ready'] for s in services.values()),'managed':belongs(owner),
        'controller_current':state.get('controller_digest')==controller_digest(),
        'data_root':str(DATA),'logs':str(LOGS)}


def active():
    try:return get(18080,'/api/console/monitor')['active_count']
    except (OSError,ValueError,KeyError):
        if listening(18080):raise RuntimeError('无法确认运行中的任务；服务未停止，请先恢复文档API。')
        return 0


def environment():
    env={**{key:value for key,value in os.environ.items() if key not in {
        'PLATESG_REVIEW_TOKEN','PLATESG_OPERATORS_JSON','PLATESG_DATABASE_URL','PLATESG_DATA_DIR'}},
         'PLATESG_AUTO_PUBLISH':'0','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
         'PIP_NO_INDEX':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1',
         'ESG_DATA_ROOT':str(DATA),'ESG_PROJECT_ROOT':str(ROOT),'ESG_DATABASE_INGEST_URL':'http://127.0.0.1:18280'}
    # Terminal double-click may not load nvm; use an already installed Node only.
    node=shutil.which('node')
    if not node:
        candidates=sorted((Path.home()/'.nvm/versions/node').glob('v*/bin/node'))
        node=str(candidates[-1]) if candidates else None
    if not node:raise RuntimeError('找不到已安装的Node.js；需要Node 20+，启动器不下载依赖。')
    major=int(subprocess.check_output([node,'--version'],text=True).strip().lstrip('v').split('.')[0])
    if major<20:raise RuntimeError('需要Node.js 20或更高版本。')
    env['PATH']=str(Path(node).parent)+os.pathsep+env.get('PATH','')
    return env


def prepare(env):
    python=Path(env.get('ESG_DOCUMENT_PYTHON',str(ROOT/'ExtractESG/.local/python/bin/python')))
    model=Path(env.get('ESG_MODEL_PYTHON',str(Path.home()/'Desktop/model/.runtime/venv/bin/python')))
    if not python.is_file() or not model.is_file():raise RuntimeError('本地Python环境缺失；请配置ESG_DOCUMENT_PYTHON和ESG_MODEL_PYTHON。')
    if not (ROOT/'ConsoleESG/frontend/node_modules').is_dir():raise RuntimeError('Console依赖缺失；请先运行 npm --prefix ConsoleESG/frontend ci。')
    # Editable SDK installation is local and offline; it never installs model dependencies.
    subprocess.run(['bash',str(ROOT/'ExtractESG/scripts/install_local.sh')],env=env,check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['npm','--prefix',str(ROOT/'ConsoleESG/frontend'),'run','build'],env=env,check=True)
    database=Path(env.get('ESG_DATABASE_DATA_DIR',str(DATA/'databases/capture')))
    if any(database.resolve().is_relative_to(ROOT/rel) for rel in ['PlatESG/.local/data','PlatESG/.local/demo-data']):
        raise RuntimeError('工作台原值接收器不能使用现有Plat正式或演示库。')
    if not database.is_dir():
        raise RuntimeError('原值库尚未配置。请先对一个新的隔离目录显式运行 database/runtime/manage.mjs init；启动器不会初始化或迁移数据库。')


def stop_owned(state):
    for record in reversed(list(state.get('services',{}).values())):
        if record.get('owned') and belongs(record):
            try:os.killpg(record['pid'],signal.SIGTERM)
            except ProcessLookupError:pass


def supervise():
    with lock('supervisor.lock',nonblocking=True):
        env=environment()
        state={'schema_version':'workbench-launcher-1.0','root':str(ROOT),'controller_digest':controller_digest(),
            'supervisor':{'pid':os.getpid(),'identity':identity(os.getpid())},'services':{},'started_at':datetime.now(timezone.utc).isoformat()}
        save(state)
        children={}
        stopping=False
        def finish(*_):
            nonlocal stopping
            stopping=True
        for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,finish)
        try:
            for name,(port,_,_,argument) in SERVICES.items():
                if health(name)['ready']:
                    state['services'][name]={'owned':False};save(state);continue
                if listening(port):raise RuntimeError(f'端口{port}被未通过当前版本检查的服务占用；未停止该服务。')
                log=LOGS/(name+'.log')
                with log.open('ab') as output:
                    process=subprocess.Popen(['bash',str(ROOT/'ExtractESG/scripts/run_service.sh'),argument],env=env,
                        cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
                children[name]=process
                state['services'][name]={'owned':True,'pid':process.pid,'identity':identity(process.pid)}
                save(state)
            while not stopping:
                for name,process in children.items():
                    if process.poll() is not None:raise RuntimeError(f'{name}进程退出；任务状态与原值保留，请查看对应日志。')
                time.sleep(1)
        except Exception as exc:
            state['error']=str(exc);save(state)
        finally:
            stop_owned(state)
            for process in children.values():
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    # This process group belongs to this exact supervisor; never kill by port/name.
                    if belongs(state['services'][next(key for key,value in children.items() if value is process)]):
                        os.killpg(process.pid,signal.SIGKILL)
            state['stopped_at']=datetime.now(timezone.utc).isoformat();save(state)


def stop():
    state=load()
    if state.get('root') and Path(state['root']) != ROOT:
        raise RuntimeError('此数据根由另一工作区托管；未停止它的进程。请为本工作区配置独立ESG_DATA_ROOT。')
    if not belongs(state.get('supervisor',{})):
        print('没有本入口托管的运行进程；未停止其他服务。');return
    if active():raise RuntimeError('仍有任务运行，停止未执行。请在Console结束/取消任务后重试。')
    os.kill(state['supervisor']['pid'],signal.SIGTERM)
    for _ in range(25):
        if not belongs(state['supervisor']):return
        time.sleep(1)
    raise RuntimeError('托管进程仍在退出；未强制终止其他进程。')


def start(no_browser=False):
    current=snapshot()
    if current['ready'] and (not current['managed'] or current['controller_current']):
        print('四个服务已运行并通过当前版本检查。')
    else:
        state=load()
        if belongs(state.get('supervisor',{})):stop()
        for name,(port,*_) in SERVICES.items():
            if listening(port) and not health(name)['ready']:
                raise RuntimeError(f'端口{port}由旧入口或其他服务占用；请关闭原启动窗口后再双击。不会按端口误停其他进程。')
        env=environment();prepare(env)
        LOGS.mkdir(parents=True,exist_ok=True)
        with (LOGS/'launcher.log').open('ab') as log:
            daemon=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'supervise'],cwd=ROOT,env=env,
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        print('正在启动文档、提取、Console和原值接收器…',flush=True)
        for tick in range(180):
            if daemon.poll() is not None:raise RuntimeError('后台启动器退出，请查看 '+str(LOGS/'launcher.log'))
            result=snapshot()
            if result['ready']:break
            state=load()
            if state.get('error'):raise RuntimeError(state['error'])
            if tick and tick%15==0:print('等待：'+ '、'.join(k for k,v in result['services'].items() if not v['ready']),flush=True)
            time.sleep(1)
        else:raise RuntimeError('服务准备超过180秒；后台进程保留，请使用status检查，不重复提交提取。')
    # Warm the actual proxy/report path before presenting the page.
    get(18081,'/document/api/report-resources',timeout=15)
    print('Console：http://127.0.0.1:18081/\n数据：'+str(DATA)+'\n日志：'+str(LOGS)+'\n服务在后台运行，关闭终端不会停止。')
    if not no_browser:
        subprocess.run(['open','http://127.0.0.1:18081/?launch='+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')],check=False)


def main():
    parser=argparse.ArgumentParser(description='ESG新版一键工作台：start / status / stop / restart')
    parser.add_argument('command',nargs='?',choices=['start','status','stop','restart','supervise'],default='start')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args()
    if args.command=='supervise':supervise();return
    with lock('command.lock'):
        if args.command=='status':
            result=snapshot()
            if args.json:print(json.dumps(result,ensure_ascii=False))
            else:
                for name,value in result['services'].items():print(f"{name:12} {value['port']} · "+('就绪' if value['ready'] else '未就绪'))
                print('后台托管：'+('是' if result['managed'] else '否')+'\n数据：'+str(DATA))
            return
        if args.command in {'stop','restart'}:stop()
        if args.command in {'start','restart'}:start(args.no_browser or os.getenv('ESG_SKIP_BROWSER')=='1')


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,subprocess.SubprocessError) as exc:
        print('启动未完成：'+str(exc),file=sys.stderr);sys.exit(1)
