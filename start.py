"""One local command; independent APIs, frozen futures code, no automatic Wind."""
from pathlib import Path
import argparse,os,subprocess,time,json,socket
from urllib.request import build_opener, ProxyHandler

ROOT=Path(__file__).resolve().parent
ENTRY='http://127.0.0.1:8790'


def local_environment(runtime):
    path=runtime/'local-settings.json'
    settings=json.loads(path.read_text('utf-8')) if path.exists() else {}
    return {**{k:str((ROOT/Path(v)).resolve()) for k,v in settings.items()
               if k in ('FUTURES_DATA_ROOT','OPTIONS_DATA_ROOT','OPTIONS_SNAPSHOT_DIR','SEATS_RUNTIME','SEATS_SNAPSHOT_PATH')}, **os.environ}


def entry_ready():
    opener=build_opener(ProxyHandler({}))
    try:
        for path in ['/health','/api/futures/v1/health','/api/options/v1/health','/api/seats/v1/health']:
            with opener.open(ENTRY+path,timeout=2) as response:
                data=json.load(response)
            if data.get('status')!='ok':return False
            if path=='/health' and data.get('service')!='unified-local-entry':return False
        return True
    except (OSError,ValueError):return False


def show_entry(module, reused=False):
    print(('研究看板已运行，复用现有服务：' if reused else '研究看板：')+ENTRY+'/'+module+'/',flush=True)
    print('顶部切换席位 / 期货 / 期权(Beta)。',flush=True)


def main():
    parser=argparse.ArgumentParser(description='启动统一研究终端，不自动采集或更新发布版本')
    parser.add_argument('--module',choices=['futures','options','seats'],default='futures')
    args=parser.parse_args()
    if entry_ready():
        show_entry(args.module,reused=True)
        return
    runtime=Path(os.environ.get('DASHBOARD_RUNTIME_ROOT') or ROOT/'runtime').resolve()
    pointer=runtime/'frontend-release.json'
    dist=runtime/json.loads(pointer.read_text('utf-8'))['release'] if pointer.exists() else ROOT/'frontend/dist'
    if not (dist/'index.html').exists():raise SystemExit('请先构建并发布前端')
    if not (runtime/'futures-release.json').is_file():
        raise SystemExit('请先执行 python tools/freeze_backend.py 准备本地期货发布版本')
    config=json.loads((runtime/'futures-release.json').read_text('utf-8'))
    base=runtime if config.get('path_base')=='runtime_root' else ROOT
    config.update({key:str(base/Path(config[key])) for key in ['runtime','release']})
    ports=[8790,8794,8795,8796]
    for port in ports:
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1',port))==0:raise SystemExit(f'{port}已占用；不重复启动或停止现有服务')
    env={**local_environment(runtime),'PYTHONUTF8':'1',
         'FUTURES_RUNTIME':config['runtime'],
         'OPTIONS_RUNTIME':str(Path(os.environ.get('OPTIONS_RUNTIME') or runtime).resolve())}
    env.setdefault('FUTURES_DATA_ROOT',str(ROOT/'futures/data'))
    executable='Scripts/python.exe' if os.name=='nt' else 'bin/python'
    python=os.environ.get('OPTIONS_PYTHON',str(ROOT/'.venv'/executable))
    futures_python=os.environ.get('FUTURES_PYTHON',str(ROOT/'.venv-futures'/executable))
    seats_python=os.environ.get('SEATS_PYTHON',str(ROOT/'.venv-seats'/executable))
    for name,exe in [('OPTIONS_PYTHON',python),('FUTURES_PYTHON',futures_python),('SEATS_PYTHON',seats_python)]:
        if not Path(exe).is_file():raise SystemExit(f'{name}解释器不存在：{exe}；请准备虚拟环境或配置完整路径')
    runtime.mkdir(parents=True,exist_ok=True)
    services=[('futures',futures_python,config['release'],'backend.api:app',8795),
              ('options',python,str(ROOT),'backend.app:app',8794),
              ('seats',seats_python,str(ROOT/'seats'),'seat_dashboard.main:app',8796),
              ('gateway',python,str(ROOT),'backend.gateway:app',8790)]
    children=[];logs=[]
    try:
        for name,exe,cwd,app,port in services:
            log=open(runtime/(name+'.log'),'a',encoding='utf-8');logs.append(log)
            service_env=env
            extra=[]
            if name=='seats':
                service_env={**env,'DASHBOARD_RUNTIME_ROOT':env.get('SEATS_RUNTIME',str(runtime/'seats'))}
                if env.get('SEATS_SNAPSHOT_PATH'):service_env['DASHBOARD_SNAPSHOT_PATH']=env['SEATS_SNAPSHOT_PATH']
                extra=['--app-dir',str(ROOT/'seats/backend/src')]
            proc=subprocess.Popen([exe,'-m','uvicorn',app,'--host','127.0.0.1','--port',str(port),*extra],cwd=cwd,env=service_env,stdout=log,stderr=log,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            children.append(proc)
        deadline=time.monotonic()+30
        while not entry_ready():
            failed=[name for (name,*_),proc in zip(services,children) if proc.poll() is not None]
            if failed or time.monotonic()>=deadline:
                raise RuntimeError('服务未就绪：'+(','.join(failed) or '启动超时')+'，请查看runtime日志')
            time.sleep(.2)
        show_entry(args.module)
        print('Ctrl+C停止本次启动的进程；重复启动只复用，不停止已有服务。',flush=True)
        print('期货独立版本 '+config['version']+'；Wind仅在手动刷新时调用',flush=True)
        (runtime/'session.json').write_text(json.dumps({'launcher':os.getpid(),'children':[p.pid for p in children]}),encoding='utf-8')
        while True:
            if children[-1].poll() is not None:raise RuntimeError('统一入口退出，见gateway.log')
            time.sleep(1)
    except KeyboardInterrupt:pass
    finally:
        for proc in children:
            if proc.poll() is None:
                if os.name=='nt':
                    # Windows venv launchers spawn another Python process.
                    # Stop only the tree created by this launcher, including it.
                    subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                else:proc.terminate()
        for proc in children:
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill()
        for log in logs:log.close()


if __name__=='__main__':main()
