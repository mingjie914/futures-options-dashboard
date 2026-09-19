"""Research saved Wind responses only; never change the terminal's active snapshot."""
import json
import math
import statistics
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.data import normalize
from backend.pricing import scenario

folder=ROOT/'data/raw/wind-research-20260914T0025'
s=normalize(folder)
lookup={r['code']:r for r in s['rows']}
history=json.loads((folder/'index_history.json').read_text('utf-8'))
closes=history['Data'][0]
returns=[math.log(b/a) for a,b in zip(closes,closes[1:])]

def interpolate(points,target):
    points=sorted(points)
    for (x,y),(xx,yy) in zip(points,points[1:]):
        if x<=target<=xx:return y+(yy-y)*(target-x)/(xx-x)
    return None

months=[]
for month in s['months']:
    rows=[r for r in s['rows'] if r['month']==month['id']]
    valid=[r for r in rows if r['iv'] is not None]
    otm=[r for r in valid if r['cp']==('P' if r['strike']<month['forward'] else 'C')]
    c25=interpolate([(abs(r['delta']),r['iv']) for r in valid if r['cp']=='C'],.25)
    p25=interpolate([(abs(r['delta']),r['iv']) for r in valid if r['cp']=='P'],.25)
    atm=interpolate([(r['strike'],r['iv']) for r in otm],month['forward'])
    totals={cp:{key:sum(r[key] for r in rows if r['cp']==cp and r[key] is not None) for key in ('oi','oi_change','volume')} for cp in ('C','P')}
    months.append({**month,'atm_iv':atm,'c25_iv':c25,'p25_iv':p25,'put_minus_call_25_pp':100*(p25-c25) if p25 and c25 else None,'totals':totals})

specs=[
 ('09单买C7400',[('2609','C',7400,'buy')]),
 ('09单买C7500',[('2609','C',7500,'buy')]),
 ('10单买C7400',[('2610','C',7400,'buy')]),
 ('09单买P7400',[('2609','P',7400,'buy')]),
 ('09牛市C7400/7600',[('2609','C',7400,'buy'),('2609','C',7600,'sell')]),
 ('10牛市C7400/7700',[('2610','C',7400,'buy'),('2610','C',7700,'sell')]),
 ('09熊市P7400/7200',[('2609','P',7400,'buy'),('2609','P',7200,'sell')]),
 ('09跨式7400',[('2609','C',7400,'buy'),('2609','P',7400,'buy')]),
 ('09宽跨P7300+C7500',[('2609','P',7300,'buy'),('2609','C',7500,'buy')]),
 ('10跨式7400',[('2610','C',7400,'buy'),('2610','P',7400,'buy')]),
 ('买10卖09C7400日历',[('2610','C',7400,'buy'),('2609','C',7400,'sell')]),
]
strategies=[]
for name,items in specs:
    legs=[dict(code=f'MO{month}-{cp}-{strike}.CFE',side=side,quantity=1) for month,cp,strike,side in items]
    base=scenario(s,legs,s['spot'],0,0)
    actual_cost=sum((1 if leg['side']=='buy' else -1)*lookup[leg['code']]['multiplier']*lookup[leg['code']]['ask' if leg['side']=='buy' else 'bid'] for leg in legs)
    assert abs(base['cost']-actual_cost)<1e-7
    assert all(lookup[leg['code']]['quote_date']==20260911 for leg in legs)
    pnl={str(day):{str(target):{str(iv):round(scenario(s,legs,target,day,iv)['pnl'],2) for iv in (-3,0,3)} for target in (7200,7300,7400,s['spot'],7500,7600,7700)} for day in (3,5,7)}
    sensitivity={k:sum((1 if leg['side']=='buy' else -1)*lookup[leg['code']][k] for leg in legs) for k in ('delta','gamma','theta','vega')}
    strategies.append(dict(name=name,legs=legs,cost=base['cost'],max_loss=base['max_loss'],max_gain=base['max_gain'],risk_status=base['risk_status'],greeks=sensitivity,pnl=pnl))

report=dict(asof=s['asof'],received_at=json.loads((folder/'collection.json').read_text('utf-8'))['received_at'],spot=s['spot'],day_return_pct=100*(closes[-1]/closes[-2]-1),coverage=s['coverage'],snapshot_id=s['snapshot_id'],rv={str(n):100*statistics.stdev(returns[-n:])*math.sqrt(252) for n in (5,10,20,60)},months=months,strategies=strategies)
out=ROOT/'data/research';out.mkdir(exist_ok=True)
(out/'opportunities-20260914.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ('asof','spot','day_return_pct','rv')},ensure_ascii=False))
for r in strategies:
    print(json.dumps(dict(name=r['name'],cost=r['cost'],loss=r['max_loss'],gain=r['max_gain'],greeks=r['greeks'],day3_iv0={k:v['0'] for k,v in r['pnl']['3'].items()},day3_at7500=r['pnl']['3']['7500']),ensure_ascii=False))
