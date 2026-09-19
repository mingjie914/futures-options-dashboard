import json
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
import backend.app as module
from backend.pricing import scenario, exit_analysis


def snapshot():
    return {'snapshot_id':'sample','asof':'2026-09-10T11:00:00+08:00','spot':100.,'rows':[
        {'code':'C100','month':'202609','cp':'C','strike':100.,'t':10/365,
         'bid':4.,'ask':6.,'iv':.3,'forward':101.,'multiplier':100.},
        {'code':'C110','month':'202609','cp':'C','strike':110.,'t':10/365,
         'bid':1.,'ask':2.,'iv':.3,'forward':101.,'multiplier':100.}]}


LEGS=[{'code':'C100','side':'buy','quantity':2}]


def test_roundtrip_fee_units_and_entry_slippage_at_expiry():
    result=scenario(snapshot(),LEGS,110,10,0,fee_roundtrip=3,slippage_points=.5)
    assert result['cost']==1306  # 2 * 100 * (ask6+0.5) + 2 * fee3
    assert result['fee_total']==6
    assert result['slippage_cost']==100
    assert result['pnl']==694  # expiry10 * 2 * 100 - 1306
    assert result['gross_pnl']==800
    assert result['friction_cost']==106
    assert result['max_loss']==1306
    assert result['legs'][0]['exit']==10  # no exit slippage at settlement


def test_spread_fee_counts_both_legs_and_stress_does_not_improve_pnl():
    legs=[{'code':'C100','side':'buy','quantity':1},{'code':'C110','side':'sell','quantity':1}]
    normal=scenario(snapshot(),legs,108,2,0,3,.2,1)
    stress=scenario(snapshot(),legs,108,2,0,3,.2,2)
    assert normal['fee_total']==6
    assert normal['max_loss']==pytest.approx(546)
    assert normal['max_gain']==pytest.approx(454)
    assert stress['pnl']<=normal['pnl']
    assert stress['friction_cost']>=normal['friction_cost']
    assert normal['gross_pnl']==pytest.approx(scenario(snapshot(),legs,108,2,0)['pnl'])


def test_grid_matches_scalar_and_reverse_scans_without_extrapolation():
    s=snapshot()
    result=exit_analysis(s,LEGS,90,120,7,10,3,0,110,500,3,.5,2)
    assert result['calculation_version']=='exit-v1'
    assert result['entry']['risk_status']=='same_expiry'
    assert result['entry']['max_gain'] is None  # long call has no expiry upside cap
    assert len(result['cells'])==21
    for cell in result['cells']:
        day=result['days'][cell['day_index']];price=result['prices'][cell['price_index']]
        expected=scenario(s,LEGS,price,day,0,3,.5,1)
        assert cell['pnl']==pytest.approx(expected['pnl'])
        assert cell['stress_pnl']<=cell['pnl']+1e-9
    assert result['target_rows'][-1]['normal']['pnl']==694
    assert result['reverse_rows'][-1]['normal_price']==110
    unavailable=exit_analysis(s,LEGS,90,95,3,1,2,0,110,100000)
    assert all(row['normal_price'] is None for row in unavailable['reverse_rows'])


def test_grid_cannot_cross_first_expiry_or_mutate_source():
    s=snapshot();before=deepcopy(s)
    with pytest.raises(ValueError,match='最早到期'):
        exit_analysis(s,LEGS,90,120,5,11,3,0,110,500)
    with pytest.raises(ValueError,match='价格上限'):
        exit_analysis(s,LEGS,120,90,5,1,3,0,110,500)
    assert s==before


def test_api_limits_snapshot_version_and_historical_isolation(monkeypatch,tmp_path):
    s=snapshot()
    monkeypatch.setattr(module,'state',{'data':s})
    raw=tmp_path/'data/raw';folder=raw/'saved';folder.mkdir(parents=True)
    for name in ('chain','quotes'):(folder/(name+'.json')).write_text('{}')
    (folder/'collection.json').write_text(json.dumps({'asof':s['asof'],'trade_date':'2026-09-10'}))
    monkeypatch.setattr(module,'ROOT',tmp_path)
    historical={**s,'snapshot_id':'historical'}
    monkeypatch.setattr(module,'_archived_snapshot',lambda path:historical)
    body=dict(snapshot_id='sample',legs=LEGS,price_min=90,price_max=120,price_steps=7,
              horizon_days=5,time_steps=3,target=110,profit_target=500)
    with TestClient(module.app) as client:
        assert client.post('/api/options/v1/exit-analysis',json=body).status_code==200
        assert client.post('/api/options/v1/exit-analysis',json={**body,'price_steps':1000}).status_code==422
        assert client.post('/api/options/v1/exit-analysis',json={**body,'snapshot_id':'old'}).status_code==409
        items=client.get('/api/options/v1/history').json()['items'];assert len(items)==1
        key=items[0]['key']
        assert client.get('/api/options/v1/history/'+key).json()['snapshot_id']=='historical'
        assert client.post('/api/options/v1/exit-analysis',json={**body,'snapshot_id':'historical','history_key':key}).status_code==200
        assert client.get('/api/options/v1/history/not-a-key').status_code==404
        assert client.get('/api/options/v1/snapshot').json()['snapshot_id']=='sample'
