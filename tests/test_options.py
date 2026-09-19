import pytest
from fastapi.testclient import TestClient
from backend.pricing import price, implied, greeks, payoff_bounds, scenario
from backend.data import normalize,ROOT
from backend.app import app,current
import backend.app as app_module


@pytest.mark.parametrize('f,k,t,v,cp,want',[
    (7600,7700,9/365,.28,'C',90.0267710093161),
    (7600,7500,37/365,.26,'P',202.13921330260044),
    (100,100,1,.2,'C',7.846975606033844)])
def test_scipy_reference(f,k,t,v,cp,want):
    assert price(f,k,t,v,cp)==pytest.approx(want,abs=1e-9)
    assert implied(want,f,k,t,cp)==pytest.approx(v,abs=1e-9)


def test_delta_gamma_finite_difference():
    f,k,t,v=7600,7700,9/365,.28;h=.1
    g=greeks(f,k,t,v,'C',100)
    assert g['delta']==pytest.approx((price(f+h,k,t,v,'C')-price(f-h,k,t,v,'C'))/(2*h),rel=1e-6)
    assert g['gamma']==pytest.approx((price(f+h,k,t,v,'C')-2*price(f,k,t,v,'C')+price(f-h,k,t,v,'C'))/h**2,rel=1e-5)


def test_payoff_bounds_spread_and_naked():
    legs=[dict(cp='C',strike=7600,quantity=1,multiplier=100,sign=1),dict(cp='C',strike=8000,quantity=1,multiplier=100,sign=-1)]
    assert payoff_bounds(legs,12000)==(12000,28000)
    assert payoff_bounds([legs[1]],-2000)==(None,2000)


def test_archived_unquoted_months_remain_empty():
    folder=ROOT/'data/raw/legacy/im_analysis_20260909'
    if not (folder/'chain.json').exists():
        pytest.skip('可选真实归档验收：本机未配置私有 Wind 归档')
    s=normalize(folder)
    assert len(s['months'])==6
    assert s['coverage']=={'contracts':288,'quoted':130}
    assert all(r['iv'] is None and r['bid'] is None for r in s['rows'] if r['month']=='202703')
    r=next(r for r in s['rows'] if r['code']=='MO2609-C-7700.CFE')
    assert r['oi_change']==848


def test_real_snapshot_prices_reproduce_quotes():
    if not (app_module._active_folder()/'chain.json').exists():
        pytest.skip('可选真实盘口验收：本机未配置私有 Wind 快照')
    s=current()
    assert s['coverage']['quoted']>0
    for r in s['rows']:
        if r['iv'] is not None:
            assert price(r['forward'],r['strike'],r['t'],r['iv'],r['cp'])==pytest.approx((r['bid']+r['ask'])/2,abs=1e-6)


def test_scenario_snapshot_and_invalid_legs(monkeypatch):
    monkeypatch.setattr(app_module,'state',{'data':dict(snapshot_id='unit-snapshot',spot=7600,rows=[
        dict(code='MO2609-C-7700.CFE',month='202609',cp='C',strike=7700,
             t=9/365,bid=89,ask=91,iv=.28,forward=7600,multiplier=100)])})
    with TestClient(app) as c:
        s=c.get('/api/options/v1/snapshot').json()
        body=dict(snapshot_id=s['snapshot_id'],legs=[dict(code='MO2609-C-7700.CFE',side='buy',quantity=1)],target=7700,days=1,iv_shift_pp=0)
        r=c.post('/api/options/v1/scenarios',json=body)
        assert r.status_code==200
        result=r.json();assert result['max_loss']==result['cost'];assert result['max_gain'] is None
        assert result['fees_included'] is False
        body['days']=100
        assert c.post('/api/options/v1/scenarios',json=body).status_code==422
        body['snapshot_id']='old'
        assert c.post('/api/options/v1/scenarios',json=body).status_code==409


def test_origin():
    with TestClient(app) as c:
        assert c.post('/api/options/v1/refresh',headers={'origin':'https://unrelated.test'}).status_code==403


def test_gateway_unknown_and_independent_health():
    from backend.gateway import app as gateway
    with TestClient(gateway) as c:
        assert c.get('/health').status_code==200
        assert c.get('/api/no-such-route').status_code==404
        assert c.get('/api/unknown/v1/test').status_code==404


def test_gateway_forwards_research_plan_patch():
    import httpx
    from backend.gateway import app as gateway
    payload={'rationale':'验证统一入口保存研究计划'}
    def transport(request):
        assert request.method=='PATCH'
        assert request.url.path=='/api/watchlists/7'
        assert request.url.port==8795
        import json
        assert json.loads(request.content)==payload
        return httpx.Response(200,json={'id':7,'plan':payload})
    with TestClient(gateway) as c:
        gateway.state.client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        result=c.patch('/api/futures/v1/watchlists/7',json=payload)
        assert result.status_code==200
        assert result.json()['plan']==payload


def test_gateway_module_failure_does_not_break_other_module():
    import httpx
    from backend.gateway import app as gateway
    def transport(request):
        if request.url.port==8795:
            raise httpx.ConnectError('futures stopped',request=request)
        assert request.url.path=='/api/options/v1/status'
        return httpx.Response(200,json={'status':'ok'})
    with TestClient(gateway) as c:
        gateway.state.client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        assert c.get('/api/futures/v1/products').status_code==503
        assert c.get('/api/options/v1/status').json()=={'status':'ok'}
