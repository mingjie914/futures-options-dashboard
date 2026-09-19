"""Read-only integration check against the running local terminal; no Wind call."""
import json
from pathlib import Path
from time import perf_counter
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8790'


def request(path,body=None):
    data=json.dumps(body).encode() if body is not None else None
    req=Request(BASE+path,data=data,headers={'Content-Type':'application/json','Origin':BASE})
    with urlopen(req,timeout=20) as response:return json.load(response)


snapshot=request('/api/options/v1/snapshot')
rows=[r for r in snapshot['rows'] if r['iv'] is not None and r['t']>0 and r['cp']=='C']
row=min(rows,key=lambda r:(r['month'],abs(r['strike']-snapshot['spot'])))
body=dict(snapshot_id=snapshot['snapshot_id'],legs=[dict(code=row['code'],side='buy',quantity=1)],
          price_min=snapshot['spot']*.95,price_max=snapshot['spot']*1.05,price_steps=15,
          horizon_days=min(7,row['t']*365),time_steps=8,iv_shift_pp=0,target=snapshot['spot']*1.02,
          profit_target=5000,fee_roundtrip=10,slippage_points=.2,stress_spread_multiplier=2)
started=perf_counter();result=request('/api/options/v1/exit-analysis',body)
assert len(result['cells'])==120
assert all(c['pnl'] is not None and c['stress_pnl']<=c['pnl']+1e-8 for c in result['cells'])
assert result['entry']['fee_total']==10
assert result['entry']['slippage_cost']>0
elapsed=perf_counter()-started
listing=request('/api/options/v1/history');history_checked=0
for item in listing['items']:
    archived=request('/api/options/v1/history/'+item['key'])
    old_row=next((r for r in archived['rows'] if r['code']==row['code'] and r['iv'] is not None),None)
    if old_row is None:continue
    past={**body,'snapshot_id':archived['snapshot_id'],'history_key':item['key'],
          'horizon_days':min(body['horizon_days'],old_row['t']*365)}
    assert request('/api/options/v1/exit-analysis',past)['snapshot_id']==archived['snapshot_id']
    history_checked+=1
assert request('/api/options/v1/snapshot')['snapshot_id']==snapshot['snapshot_id']
report=dict(asof=snapshot['asof'],contract=row['code'],cells=120,seconds=round(elapsed,4),
            history_count=len(listing['items']),history_checked=history_checked,
            fee_test_value=10,slippage_test_value=.2,global_snapshot_unchanged=True)
(ROOT/'runtime/exit-analysis-validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
