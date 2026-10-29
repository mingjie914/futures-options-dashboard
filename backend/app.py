from pathlib import Path
from threading import Lock
from datetime import datetime, timezone
from urllib.parse import urlsplit
import os
import json
import subprocess
import hashlib
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal, Optional
from .data import ROOT, normalize, historical_volatility
from .pricing import scenario, exit_analysis
from futures.backend.transport import ByteCache, ResearchHTTP, fingerprint, memory_size

app=FastAPI(title='期权分析 · 本地截面',version='0.1.0')
lock=Lock();state={'data':None,'revision':None}
cache=ByteCache(int(os.environ.get('OPTIONS_CACHE_MB', '32')) * 1024**2)
runtime=Path(os.environ.get('OPTIONS_RUNTIME') or os.environ.get('DASHBOARD_RUNTIME_ROOT') or ROOT/'runtime').resolve()



def _iv_history(path, modified, code, end, window):
    key=('iv',path,modified,code,end,window)
    result=cache.get(key)
    if result is None:
        result=historical_volatility(json.loads(Path(path).read_text('utf-8')),code,end,window)
        cache[key]=result
    return result


@app.get('/api/options/v1/iv-history')
def iv_history(code:str, window:Literal['20','60']='60', snapshot_id:str=''):
    snapshot=current()
    if snapshot_id and snapshot_id!=snapshot['snapshot_id']:raise HTTPException(409,'截面已变化，请刷新页面')
    if not any(r['code']==code for r in snapshot['rows']):raise HTTPException(404,'当前截面无此合约')
    path=_active_folder()/'history-raw.json'
    if not path.exists():return dict(code=code,points=[],n=0,reason='尚未保存该合约的日线 IV 历史')
    try:return _iv_history(str(path),fingerprint([path]),code,snapshot['trade_date'],int(window))
    except (ValueError,KeyError,OSError,TypeError) as exc:raise HTTPException(503,'历史数据不可用，请检查已保存的日线') from exc


def _read_pointer():
    pointer=runtime/'options-active.json'
    if not pointer.exists():return {}
    return json.loads(pointer.read_text('utf-8'))


def _active_folder():
    pointer=_read_pointer();folder=pointer.get('folder')
    if folder:
        base=_raw_root() if pointer.get('path_base')=='options_data_root' else ROOT
        return _path_value(folder,base)
    configured=os.environ.get('OPTIONS_SNAPSHOT_DIR')
    return _path_value(configured) if configured else _raw_root()/'legacy/im_analysis_20260909'


def _raw_root():
    return _path_value(os.environ.get('OPTIONS_DATA_ROOT') or 'data/raw')


def current(required=True):
    try:
        folder=_active_folder()
        revision=fingerprint([folder, runtime/'options-active.json', *folder.glob('*.json')])
        if state['data'] is None or ('revision' in state and state['revision']!=revision):
            if not folder.exists() and not (runtime/'options-active.json').exists() and not os.environ.get('OPTIONS_SNAPSHOT_DIR'):
                if not required:return None
                raise HTTPException(503,'尚未接入期权数据，请配置已保存快照。',headers={'X-Snapshot-State':'empty'})
            data=normalize(folder)
            if memory_size(data)>int(os.environ.get('OPTIONS_ACTIVE_MB','16'))*1024**2:
                raise ValueError('当前截面超过内存预算')
            state.update(data=data,revision=revision)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise HTTPException(503,'已保存期权快照不可用，请检查归档完整性与数据配置。') from exc
    return state['data']


def _month_stats(snapshot):
    rows_by_month={}
    for row in snapshot.get('rows',[]):
        month=row.get('month')
        if month is not None:rows_by_month.setdefault(str(month),[]).append(row)
    stats={}
    for month in snapshot.get('months',[]):
        month_id=month.get('id')
        if month_id is None:continue
        key=str(month_id);rows=rows_by_month.get(key,[])
        def count(value, fallback):
            try:return int(value)
            except (TypeError,ValueError):return fallback
        stats[key]=dict(meta=month,rows=rows,
                        contracts=count(month.get('contracts'),len(rows)),
                        quoted=count(month.get('quoted'),sum(r.get('bid') is not None and r.get('ask') is not None for r in rows)))
    for key,rows in rows_by_month.items():
        if key in stats:continue
        stats[key]=dict(meta={},rows=rows,contracts=len(rows),
                        quoted=sum(r.get('bid') is not None and r.get('ask') is not None for r in rows))
    return stats


def _snapshot_date(value, label):
    try:return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError,ValueError):raise ValueError('快照质量验收失败：'+label+'不可解析')


def validate_snapshot(new,old):
    """Reject an incomplete refresh while retaining the currently published snapshot."""
    new_trade_date=_snapshot_date(new.get('trade_date'),'新截面trade_date')
    new_stats=_month_stats(new);old_stats=_month_stats(old);errors=[]
    for month_id,old_month in old_stats.items():
        old_rows=old_month['rows'];expiry=old_month['meta'].get('expiry')
        if expiry is None and old_rows:expiry=old_rows[0].get('expiry')
        if expiry is None:
            errors.append('旧月份'+month_id+'缺少expiry，无法判断是否仍在市')
            continue
        if _snapshot_date(expiry,'旧月份'+month_id+' expiry')<new_trade_date:
            continue
        new_month=new_stats.get(month_id,{'rows':[],'contracts':0,'quoted':0})
        old_contracts=old_month['contracts'];new_contracts=new_month['contracts']
        if old_contracts and new_contracts/old_contracts<.8:
            errors.append('月份'+month_id+'合约覆盖率为'+f'{new_contracts/old_contracts:.1%}'+'，低于80%')
        if old_month['quoted']>0 and old_contracts:
            old_quote_rate=old_month['quoted']/old_contracts
            new_quote_rate=new_month['quoted']/new_contracts if new_contracts else 0
            if old_quote_rate-new_quote_rate>.2+1e-12:
                errors.append('月份'+month_id+'报价覆盖率由'+f'{old_quote_rate:.1%}'+'降至'+f'{new_quote_rate:.1%}'+'，下降超过20个百分点')
        old_iv=sum(row.get('iv') is not None for row in old_rows)
        new_iv=sum(row.get('iv') is not None for row in new_month['rows'])
        if old_iv and not new_iv:
            errors.append('月份'+month_id+'旧截面有有效IV，新截面没有有效IV')
        if new.get('mode')=='live':
            for field,label in (('oi','OI'),('volume','成交量')):
                if any(row.get(field) is not None for row in old_rows):
                    new_count=sum(row.get(field) is not None for row in new_month['rows'])
                    new_rate=new_count/new_contracts if new_contracts else 0
                    if new_rate<.8:
                        errors.append('live月份'+month_id+label+'覆盖率为'+f'{new_rate:.1%}'+'，低于80%')
    if errors:raise ValueError('快照质量验收失败：'+'；'.join(errors))
    return True


def _parse_asof(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (TypeError,ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _collection_meta(folder):
    path=folder/'collection.json'
    try:
        payload=json.loads(path.read_text('utf-8'))
        return payload if isinstance(payload,dict) else None
    except (OSError,ValueError,TypeError):
        return None


def _path_value(value,base=None):
    path=Path(value)
    return path if path.is_absolute() else (base if base is not None else ROOT)/path


def _find_previous_folder(snapshot,current_asof):
    candidates=[];trade_date=str(snapshot.get('trade_date'))
    for collection in _raw_root().glob('*/collection.json'):
        meta=_collection_meta(collection.parent);asof=_parse_asof(meta.get('asof')) if meta else None
        if not meta or str(meta.get('trade_date'))!=trade_date or asof is None or not asof<current_asof:continue
        candidates.append((asof,collection.parent))
    return max(candidates,key=lambda item:item[0])[1] if candidates else None


def _previous_result(snapshot):
    current_asof=_parse_asof(snapshot.get('asof'))
    if current_asof is None:return {'snapshot':None,'reason':'当前截面asof不可解析，无法定位上一截面'}
    current_trade_date=str(snapshot.get('trade_date'))
    pointer=_read_pointer();value=pointer.get('previous_folder')
    if value:
        base=_raw_root() if pointer.get('path_base')=='options_data_root' else ROOT
        folder=_path_value(value,base);meta=_collection_meta(folder)
        if meta is None:return {'snapshot':None,'reason':'上一截面指针所指目录缺少有效collection.json'}
        if str(meta.get('trade_date'))!=current_trade_date:
            return {'snapshot':None,'reason':'上一截面交易日为'+str(meta.get('trade_date'))+'，与当前截面'+current_trade_date+'不同'}
        previous_asof=_parse_asof(meta.get('asof'))
        if previous_asof is None:return {'snapshot':None,'reason':'上一截面asof不可解析，无法比较'}
        if not previous_asof<current_asof:
            return {'snapshot':None,'reason':'上一截面asof不早于当前截面，无法比较'}
    else:
        folder=_find_previous_folder(snapshot,current_asof)
        if folder is None:return {'snapshot':None,'reason':'暂无同一交易日更早的行情截面'}
    try:previous=normalize(folder)
    except (OSError,ValueError,TypeError) as exc:
        return {'snapshot':None,'reason':'上一截面不可用：'+str(exc)}
    if str(previous.get('trade_date'))!=current_trade_date:
        return {'snapshot':None,'reason':'上一截面交易日与当前截面不同，无法比较'}
    previous_asof=_parse_asof(previous.get('asof'))
    if previous_asof is None or not previous_asof<current_asof:
        return {'snapshot':None,'reason':'上一截面asof不早于当前截面，无法比较'}
    return {'snapshot':previous}


def source_version(path):
    folder=_active_folder()
    files=[folder, runtime/'options-active.json', *folder.glob('*.json')]
    if path.endswith(('/previous','/history')):
        files += list(_raw_root().rglob('*.json'))
    return fingerprint(files)


code_version=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))
    + Path(__file__).parents[1].joinpath('futures/backend/transport.py').read_bytes()).hexdigest()
app.add_middleware(ResearchHTTP,version=source_version,cache=cache,code_version=code_version,
    concurrency=int(os.environ.get('OPTIONS_HTTP_CONCURRENCY','1')),
    paths=['/api/options/v1/'+name for name in ('snapshot','iv-history','previous','history')])


@app.middleware('http')
async def origin_check(request:Request,call_next):
    origin=request.headers.get('origin')
    if (origin and urlsplit(origin).netloc!=request.headers.get('host')) or request.headers.get('sec-fetch-site')=='cross-site':
        return JSONResponse({'detail':'仅允许同源本地访问'},403)
    return await call_next(request)


@app.get('/api/options/v1/version')
def version():
    try:
        if not any(_active_folder().glob('*.json')):
            raise HTTPException(503, '数据版本暂不可用')
        value=hashlib.sha256((code_version+source_version('/snapshot')).encode()).hexdigest()
        return JSONResponse({'version':value},headers={'Cache-Control':'no-store'})
    except (OSError,ValueError,TypeError) as exc:
        raise HTTPException(503,'数据版本暂不可用') from exc


@app.get('/api/options/v1/health')
def health():return {'status':'ok'}


@app.get('/api/options/v1/status')
def status():
    s=current(required=False)
    return {'status':'ok','mode':s['mode'] if s else 'empty',
            'asof':s['asof'] if s else None,'snapshot_id':s['snapshot_id'] if s else None}


@app.get('/api/options/v1/snapshot')
def snapshot():return current()


@app.get('/api/options/v1/previous')
def previous():
    snapshot=current();key=('previous',snapshot['snapshot_id'],source_version('/previous'))
    result=cache.get(key)
    if result is None:
        result=_previous_result(snapshot)
        cache[key]=result
    return result


def _archive_entries():
    raw=_raw_root();entries=[]
    for path in raw.glob('*/collection.json'):
        folder=path.parent
        if not (folder/'chain.json').exists() or not (folder/'quotes.json').exists():continue
        meta=_collection_meta(folder)
        if not meta or _parse_asof(meta.get('asof')) is None:continue
        key=hashlib.sha256(folder.name.encode('utf-8')).hexdigest()[:16]
        entries.append(dict(key=key,folder=folder,asof=meta['asof'],trade_date=meta.get('trade_date')))
    return sorted(entries,key=lambda x:_parse_asof(x['asof']),reverse=True)


def _archived_snapshot(folder):
    key=('archive',folder,fingerprint([Path(folder),*Path(folder).glob('*.json')]))
    result=cache.get(key)
    if result is None:
        result=normalize(Path(folder))
        cache[key]=result
    return result


def _history_snapshot(key):
    entry=next((entry for entry in _archive_entries() if entry['key']==key),None)
    if not entry:raise HTTPException(404,'历史截面不存在')
    try:return _archived_snapshot(str(entry['folder']))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise HTTPException(422,'历史截面数据不完整，无法分析') from exc


@app.get('/api/options/v1/history')
def history():
    entries=_archive_entries();active=_active_folder()
    return {'items':[{k:e[k] for k in ('key','asof','trade_date')} for e in entries],
            'current_key':next((e['key'] for e in entries if e['folder']==active),None)}


@app.get('/api/options/v1/history/{key}')
def history_snapshot(key:str):return _history_snapshot(key)


class Leg(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code:str
    side:Literal['buy','sell']
    quantity:int=Field(ge=1,le=10000)


class Scenario(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    snapshot_id:str
    legs:list[Leg]=Field(min_length=1,max_length=8)
    target:float=Field(gt=0)
    days:float=Field(ge=0,le=365)
    iv_shift_pp:float=Field(ge=-100,le=200)
    fee_roundtrip:float=Field(default=0,ge=0,le=100000)
    slippage_points:float=Field(default=0,ge=0,le=1000)
    exit_spread_multiplier:float=Field(default=1,ge=1,le=5)


@app.post('/api/options/v1/scenarios')
def scenarios(body:Scenario):
    s=current()
    if body.snapshot_id!=s['snapshot_id']:raise HTTPException(409,'行情版本已变化，请刷新后重新比较')
    try:return scenario(s,[x.model_dump() for x in body.legs],body.target,body.days,body.iv_shift_pp,
                        body.fee_roundtrip,body.slippage_points,body.exit_spread_multiplier)
    except ValueError as e:raise HTTPException(422,str(e)) from e


class ExitAnalysis(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    snapshot_id:str
    history_key:Optional[str]=Field(default=None,pattern=r'^[0-9a-f]{16}$')
    legs:list[Leg]=Field(min_length=1,max_length=8)
    price_min:float=Field(gt=0)
    price_max:float=Field(gt=0)
    price_steps:int=Field(default=15,ge=2,le=41)
    horizon_days:float=Field(ge=0,le=365)
    time_steps:int=Field(default=8,ge=2,le=21)
    iv_shift_pp:float=Field(default=0,ge=-100,le=200)
    target:float=Field(gt=0)
    profit_target:float=Field(ge=0)
    fee_roundtrip:float=Field(default=0,ge=0,le=100000)
    slippage_points:float=Field(default=0,ge=0,le=1000)
    stress_spread_multiplier:float=Field(default=2,ge=1,le=5)


@app.post('/api/options/v1/exit-analysis')
def analyze_exit(body:ExitAnalysis):
    s=_history_snapshot(body.history_key) if body.history_key else current()
    if s['snapshot_id']!=body.snapshot_id:raise HTTPException(409,'行情截面已变化，请重新载入后分析')
    payload=body.model_dump(exclude={'snapshot_id','history_key','legs'})
    try:return exit_analysis(s,[leg.model_dump() for leg in body.legs],**payload)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/options/v1/refresh')
def refresh():
    if not lock.acquire(blocking=False):raise HTTPException(409,'当前已有取数任务')
    try:
        old=current(required=False);old_folder=_active_folder() if old else None
        python=os.environ.get('OPTIONS_WIND_PYTHON')
        if not python:raise HTTPException(503,'未配置 OPTIONS_WIND_PYTHON，Wind 手动采集未启用；可继续读取已保存快照')
        runtime.mkdir(parents=True,exist_ok=True)
        folder=_raw_root()/datetime.now().strftime('wind-%Y%m%dT%H%M%S%f')
        try:
            result=subprocess.run([python,str(ROOT/'backend/collect.py'),'--out',str(folder)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,env={**os.environ,'PYTHONUTF8':'1'})
        except OSError as exc:
            raise HTTPException(503,'Wind采集解释器不可用，保留上一截面；请检查 OPTIONS_WIND_PYTHON') from exc
        if result.returncode:
            (runtime/'last-refresh-error.txt').write_text(result.stderr[-4000:],encoding='utf-8')
            raise HTTPException(503,'Wind取数失败，保留上一有效截面；详见本地错误日志')
        new=normalize(folder)
        if not new['coverage']['quoted']:raise HTTPException(503,'没有有效双边报价，保留上一截面')
        if not any(row['iv'] is not None for row in new['rows']):
            raise HTTPException(503,'没有有效 IV，不发布本次截面')
        try:validate_snapshot(new,old or {})
        except ValueError as exc:raise HTTPException(503,str(exc)) from exc
        try:previous=Path(os.path.relpath(old_folder,_raw_root())).as_posix() if old_folder else None
        except ValueError:previous=str(old_folder)  # Existing archive on another Windows drive.
        tmp=runtime/'options-active.tmp';tmp.write_text(json.dumps({'folder':folder.name,'path_base':'options_data_root','snapshot_id':new['snapshot_id'],'previous_folder':previous}),encoding='utf-8');tmp.replace(runtime/'options-active.json')
        state['data']=new
        if 'revision' in state:state['revision']=source_version('/snapshot')
        return {'status':'ok','snapshot_id':new['snapshot_id'],'coverage':new['coverage']}
    except subprocess.TimeoutExpired:raise HTTPException(503,'Wind请求超时，已结束本次子进程；保留上一截面')
    finally:lock.release()
