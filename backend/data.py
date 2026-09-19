"""Read source responses without filling unquoted months with simulated values."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from statistics import median
import hashlib
import json
import math
from .pricing import RATE, implied, greeks

ROOT=Path(__file__).resolve().parents[1]
TZ=timezone(timedelta(hours=8))


def load(folder,name,default=None):
    p=folder/(name+'.json')
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def clean(v):
    if isinstance(v,float) and not math.isfinite(v):return None
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,list):return [clean(x) for x in v]
    return v


def cross(obj):
    if not obj or obj.get('ErrorCode')!=0:return {}
    return {code:dict(zip(obj['Fields'],values)) for code,values in zip(obj['Codes'],zip(*obj['Data']))}


def number(v):
    try:
        x=float(v)
        return x if math.isfinite(x) and x>=0 else None
    except (TypeError,ValueError):return None


def normalize(folder):
    meta=load(folder,'collection',{})
    trade_date=meta.get('trade_date','2026-09-09')
    asof=meta.get('asof',trade_date+'T15:00:00+08:00')
    now=datetime.fromisoformat(asof)
    chain=load(folder,'chain')
    if not chain or chain.get('ErrorCode')!=0:raise ValueError('合约目录不可用')
    contracts=[dict(zip(chain['Fields'],r)) for r in zip(*chain['Data'])]
    q=cross(load(folder,'quotes'))
    daily=cross(load(folder,'daily') or load(folder,'options_'+trade_date.replace('-','')))
    prev=cross(load(folder,'previous') or load(folder,'options_20260908'))
    spot=number(meta.get('spot'))
    if spot is None:
        history=load(folder,'hist_000852.SH')
        if history:spot=number(history['Data'][history['Fields'].index('CLOSE')][-1])
    rows=[];months={};rt_volume_count=0;rt_oi_count=0
    is_live=meta.get('mode','replay')=='live'
    for c in contracts:
        code=c['option_code'];month=str(c['month']);b=q.get(code,{});d=daily.get(code,{});p=prev.get(code,{})
        expiry=str(c['last_tradedate'])[:10]
        t=max(0,(datetime.fromisoformat(expiry+'T15:00:00+08:00')-now).total_seconds()/(365*86400))
        bid=number(b.get('RT_BID1'));ask=number(b.get('RT_ASK1'))
        quote_day=number(b.get('RT_DATE'))
        same_day=quote_day is not None and int(quote_day)==int(trade_date.replace('-',''))
        valid=bid is not None and ask is not None and 0<bid<=ask and same_day
        # Prefer the same-day WSQ cumulative fields for a live snapshot.  The
        # WSS daily row can be unavailable or remain on the last close while
        # the market is open.  Legacy/replay archives without these fields
        # continue to use their daily values.
        oi_rt=number(b.get('RT_OI')) if same_day else None
        volume_rt=number(b.get('RT_VOL')) if same_day else None
        rt_volume_count+=volume_rt is not None
        rt_oi_count+=oi_rt is not None
        oi=oi_rt if oi_rt is not None else (None if is_live else number(d.get('OI')))
        oi_prev=number(p.get('OI'))
        volume=volume_rt if volume_rt is not None else (None if is_live else number(d.get('VOLUME')))
        r=dict(code=code,month=month,strike=float(c['strike_price']),cp='C' if '-C-' in code else 'P',
               expiry=expiry,t=t,multiplier=float(c['multiplier']),bid=bid,ask=ask,
               bid_size=number(b.get('RT_BSIZE1')),ask_size=number(b.get('RT_ASIZE1')),last=number(b.get('RT_LAST')),
               close=number(d.get('CLOSE')),volume=volume,oi=oi,oi_prev=oi_prev,
               oi_change=oi-oi_prev if oi is not None and oi_prev is not None else None,
               iv=None,iv_bid=None,iv_ask=None,delta=None,gamma=None,vega=None,theta=None,forward=None,
               quote_date=b.get('RT_DATE'),quote_time=b.get('RT_TIME'),quality='valid' if valid else 'missing_or_invalid_quote')
        rows.append(r)
        if month not in months:
            td=load(folder,'tdays_'+month[-4:])
            months[month]=dict(id=month,code='MO'+month[2:],expiry=expiry,
                calendar_days=(datetime.fromisoformat(expiry).date()-now.date()).days,
                trading_days=len(td['Times']) if td and td.get('ErrorCode')==0 else None,contracts=0,quoted=0)
        months[month]['contracts']+=1;months[month]['quoted']+=valid
    for month,m in months.items():
        group=[r for r in rows if r['month']==month];pairs={}
        for r in group:
            if r['quality']=='valid':pairs.setdefault(r['strike'],{})[r['cp']]=r
        valid=[(k,x) for k,x in pairs.items() if len(x)==2 and max(x['C']['ask']-x['C']['bid'],x['P']['ask']-x['P']['bid'])<=max(10,spot*.005) ] if spot else []
        nearest=sorted(valid,key=lambda x:abs(x[0]-spot))[:5]
        f=median([k+((x['C']['bid']+x['C']['ask']-x['P']['bid']-x['P']['ask'])/2)*math.exp(RATE*x['C']['t']) for k,x in nearest]) if len(nearest)>=3 else None
        m['forward']=f
        for r in group:
            r['forward']=f
            if r['quality']!='valid' or f is None:continue
            mid=(r['bid']+r['ask'])/2
            vol=implied(mid,f,r['strike'],r['t'],r['cp']);r['iv']=vol
            r['iv_bid']=implied(r['bid'],f,r['strike'],r['t'],r['cp'])
            r['iv_ask']=implied(r['ask'],f,r['strike'],r['t'],r['cp'])
            if vol:r.update(greeks(f,r['strike'],r['t'],vol,r['cp'],r['multiplier']))
            else:r['quality']='iv_unavailable'
    field_warnings=[]
    if rt_volume_count:
        field_warnings.append('成交量优先使用同一盘中WSQ累计值；无该字段时保留为空')
    else:
        field_warnings.append('当前截面未取得盘中累计成交量，保留为空，不以0代替')
    if rt_oi_count:
        field_warnings.append('持仓优先使用同一盘中WSQ值，增减相对前一交易日')
    else:
        field_warnings.append('当前截面未取得盘中WSQ持仓，' + ('持仓及增减保留为空' if is_live else '持仓增减使用日线值'))
    data=dict(mode=meta.get('mode','replay'),asof=asof,trade_date=trade_date,spot=spot,
        source='Wind已保存响应',months=sorted(months.values(),key=lambda m:m['id']),rows=rows,
        rates={'risk_free':RATE},coverage={'contracts':len(rows),'quoted':sum(m['quoted'] for m in months.values())},
        warnings=['手动截面，不是持续实时订阅；逐合约源日期与时间保留',
                  *field_warnings,
                  'IV按有效买卖中价、临近平值配对隐含远期计算；利率假设1.5%',
                  '当前支持MO；IO/HO/AU尚未开放分析，缺失月份不补示例数据'])
    data=clean(data);data['snapshot_id']=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    return data
