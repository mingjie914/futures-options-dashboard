import json

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import app, validate_snapshot
from backend.pricing import price, scenario


def _pricing_snapshot(rows):
    return {'snapshot_id': 'pricing-snapshot', 'spot': 7600, 'rows': rows}


def _pricing_row(code, cp, strike, *, t=1 / 365, bid=10, ask=12, forward=7610):
    return dict(code=code, month='202609', cp=cp, strike=strike, t=t,
                bid=bid, ask=ask, iv=.2, forward=forward, multiplier=100)


def _quality_snapshot(*, contracts=10, quoted=10, iv=True, oi=True,
                      volume=True, mode='replay', trade_date='2026-09-10',
                      expiry='2026-09-18', month='202609', snapshot_id='s'):
    rows=[]
    for index in range(contracts):
        rows.append(dict(month=month, bid=1 if index < quoted else None,
                         ask=2 if index < quoted else None,
                         iv=.2 if iv else None,
                         oi=100 if oi else None,
                         volume=200 if volume else None))
    return dict(snapshot_id=snapshot_id, mode=mode, trade_date=trade_date,
                asof=trade_date+'T11:40:00+08:00',
                months=[dict(id=month, expiry=expiry, contracts=contracts,
                             quoted=quoted)], rows=rows,
                coverage={'contracts': contracts, 'quoted': quoted})


def _write_collection(folder, *, trade_date, asof):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'collection.json').write_text(json.dumps({
        'trade_date': trade_date, 'asof': asof,
    }), encoding='utf-8')


def test_expiry_call_put_and_spread_use_target_intrinsic_value():
    call=_pricing_row('C7700', 'C', 7700, bid=10, ask=12)
    put=_pricing_row('P7700', 'P', 7700, bid=3, ask=4)
    snapshot=_pricing_snapshot([call, put])

    call_result=scenario(snapshot, [dict(code='C7700', side='buy', quantity=1)],
                         target=7800, days=1, iv_shift_pp=0)
    put_result=scenario(snapshot, [dict(code='P7700', side='buy', quantity=1)],
                         target=7800, days=1, iv_shift_pp=0)
    assert call_result['legs'][0]['exit']==100
    assert call_result['pnl']==8800
    assert put_result['legs'][0]['exit']==0
    assert put_result['pnl']==-400
    assert any('到期结算' in warning for warning in call_result['warnings'])
    assert not any('提前退出' in warning for warning in call_result['warnings'])

    spread_rows=[_pricing_row('C7700', 'C', 7700, bid=10, ask=12),
                 _pricing_row('C7900', 'C', 7900, bid=4, ask=6)]
    spread=scenario(_pricing_snapshot(spread_rows), [
        dict(code='C7700', side='buy', quantity=1),
        dict(code='C7900', side='sell', quantity=1),
    ], target=7800, days=1, iv_shift_pp=0)
    assert spread['cost']==800
    assert spread['pnl']==9200
    assert [leg['exit'] for leg in spread['legs']]==[100, 0]


def test_early_exit_keeps_fixed_basis_and_half_spread():
    row=_pricing_row('C7700', 'C', 7700, t=10 / 365, bid=10, ask=12,
                     forward=7610)
    result=scenario(_pricing_snapshot([row]),
                    [dict(code='C7700', side='buy', quantity=1)],
                    target=7800, days=1, iv_shift_pp=0)
    remaining=9 / 365
    expected=price(7610 + 7800 - 7600, 7700, remaining, .2, 'C') - 1
    assert result['legs'][0]['exit']==pytest.approx(expected)
    assert any('提前退出' in warning and '固定基差' in warning
               for warning in result['warnings'])


def test_validate_snapshot_rejects_contract_coverage_loss():
    old=_quality_snapshot()
    new=_quality_snapshot(contracts=7, quoted=7, snapshot_id='new')
    with pytest.raises(ValueError, match='合约覆盖率'):
        validate_snapshot(new, old)


def test_validate_snapshot_rejects_quote_iv_and_live_field_losses():
    old=_quality_snapshot(mode='live')
    with pytest.raises(ValueError, match='报价覆盖率'):
        validate_snapshot(_quality_snapshot(mode='live', quoted=7, snapshot_id='quote'), old)
    with pytest.raises(ValueError, match='有效IV'):
        validate_snapshot(_quality_snapshot(mode='live', iv=False, snapshot_id='iv'), old)
    with pytest.raises(ValueError, match='OI覆盖率'):
        validate_snapshot(_quality_snapshot(mode='live', oi=False, snapshot_id='oi'), old)


def test_validate_snapshot_allows_expired_old_and_new_month():
    old=_quality_snapshot(expiry='2026-09-09', month='202609')
    new=_quality_snapshot(month='202610', expiry='2026-10-16', snapshot_id='new')
    assert validate_snapshot(new, old) is True


def test_refresh_quality_rejection_keeps_pointer_and_state(monkeypatch, tmp_path):
    runtime=tmp_path/'runtime';runtime.mkdir()
    old_folder=tmp_path/'data/raw/old';old_folder.mkdir(parents=True)
    pointer={'folder':str(old_folder), 'snapshot_id':'old-pointer'}
    pointer_path=runtime/'options-active.json'
    pointer_path.write_text(json.dumps(pointer), encoding='utf-8')
    old=_quality_snapshot(snapshot_id='old')
    bad=_quality_snapshot(contracts=7, quoted=7, snapshot_id='bad')
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    monkeypatch.setattr(app_module, 'runtime', runtime)
    monkeypatch.setattr(app_module, 'state', {'data':old})
    monkeypatch.setattr(app_module, '_previous_cache', {'snapshot_id':None, 'result':None})
    monkeypatch.setattr(app_module, 'normalize', lambda folder: bad)
    monkeypatch.setattr(app_module.subprocess, 'run', lambda *args, **kwargs:
                        type('Result', (), {'returncode':0, 'stderr':''})())

    with TestClient(app) as client:
        response=client.post('/api/options/v1/refresh')
    assert response.status_code==503
    assert '合约覆盖率' in response.json()['detail']
    assert json.loads(pointer_path.read_text(encoding='utf-8'))==pointer
    assert app_module.state['data'] is old


def test_refresh_pointer_keeps_previous_folder(monkeypatch, tmp_path):
    runtime=tmp_path/'runtime';runtime.mkdir()
    old_folder=tmp_path/'data/raw/old';old_folder.mkdir(parents=True)
    pointer_path=runtime/'options-active.json'
    pointer_path.write_text(json.dumps({'folder':str(old_folder),
                                        'snapshot_id':'old-pointer'}), encoding='utf-8')
    old=_quality_snapshot(snapshot_id='old')
    new=_quality_snapshot(snapshot_id='new')
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    monkeypatch.setattr(app_module, 'runtime', runtime)
    monkeypatch.setattr(app_module, 'state', {'data':old})
    monkeypatch.setattr(app_module, '_previous_cache', {'snapshot_id':None, 'result':None})
    monkeypatch.setattr(app_module, 'normalize', lambda folder: new)
    monkeypatch.setattr(app_module.subprocess, 'run', lambda *args, **kwargs:
                        type('Result', (), {'returncode':0, 'stderr':''})())

    with TestClient(app) as client:
        response=client.post('/api/options/v1/refresh')
    assert response.status_code==200
    published=json.loads(pointer_path.read_text(encoding='utf-8'))
    assert published['previous_folder']==str(old_folder)
    assert app_module.state['data'] is new


@pytest.mark.parametrize('quoted,iv',[(10,True),(0,True),(10,False)])
def test_empty_install_is_healthy_and_first_refresh_requires_valid_data(monkeypatch,tmp_path,quoted,iv):
    runtime=tmp_path/'runtime';runtime.mkdir()
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    monkeypatch.setattr(app_module,'runtime',runtime)
    monkeypatch.setattr(app_module,'state',{'data':None})
    with TestClient(app) as client:
        assert client.get('/api/options/v1/status').json()==dict(status='ok',mode='empty',asof=None,snapshot_id=None)
        assert client.get('/api/options/v1/snapshot').status_code==503
        new=_quality_snapshot(quoted=quoted,iv=iv,snapshot_id='first')
        monkeypatch.setattr(app_module,'normalize',lambda folder:new)
        monkeypatch.setattr(app_module.subprocess,'run',lambda *args,**kwargs:
                            type('Result',(),{'returncode':0,'stderr':''})())
        response=client.post('/api/options/v1/refresh')
    if quoted and iv:
        assert response.status_code==200
        assert app_module.state['data'] is new
        pointer=json.loads((runtime/'options-active.json').read_text('utf-8'))
        assert pointer['snapshot_id']=='first' and pointer['previous_folder'] is None
    else:
        assert response.status_code==503
        assert app_module.state['data'] is None
        assert not (runtime/'options-active.json').exists()


@pytest.mark.parametrize('broken',['pointer','archive','missing_pointed_archive'])
def test_existing_snapshot_failure_is_not_treated_as_empty(monkeypatch,tmp_path,broken):
    runtime=tmp_path/'runtime';runtime.mkdir()
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    monkeypatch.setattr(app_module,'runtime',runtime)
    monkeypatch.setattr(app_module,'state',{'data':None})
    if broken=='archive':
        folder=tmp_path/'data/raw/legacy/im_analysis_20260909';folder.mkdir(parents=True)
        (folder/'chain.json').write_text('{',encoding='utf-8')
    else:
        content='{' if broken=='pointer' else json.dumps({'folder':str(tmp_path/'missing')})
        (runtime/'options-active.json').write_text(content,encoding='utf-8')
    monkeypatch.setattr(app_module.subprocess,'run',lambda *args,**kwargs:pytest.fail('不得对损坏的已有数据触发 Wind 刷新'))
    with TestClient(app) as client:
        for method,path in [('get','status'),('get','snapshot'),('post','refresh')]:
            response=getattr(client,method)('/api/options/v1/'+path)
            assert response.status_code==503
            assert '已保存期权快照不可用' in response.json()['detail']
    assert app_module.state['data'] is None


def test_previous_endpoint_fallback_and_cache(monkeypatch, tmp_path):
    runtime=tmp_path/'runtime';runtime.mkdir()
    candidate=tmp_path/'data/raw/old';_write_collection(
        candidate, trade_date='2026-09-10', asof='2026-09-10T11:30:00+08:00')
    current=_quality_snapshot(snapshot_id='current')
    previous_snapshot=_quality_snapshot(snapshot_id='previous')
    previous_snapshot['asof']='2026-09-10T11:30:00+08:00'
    (runtime/'options-active.json').write_text(json.dumps({
        'folder':str(tmp_path/'data/raw/current'), 'snapshot_id':'current',
    }), encoding='utf-8')
    calls=[]
    def fake_normalize(folder):
        calls.append(folder)
        return previous_snapshot
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    monkeypatch.setattr(app_module, 'runtime', runtime)
    monkeypatch.setattr(app_module, 'state', {'data':current})
    monkeypatch.setattr(app_module, '_previous_cache', {'snapshot_id':None, 'result':None})
    monkeypatch.setattr(app_module, 'normalize', fake_normalize)

    with TestClient(app) as client:
        first=client.get('/api/options/v1/previous')
        second=client.get('/api/options/v1/previous')
    assert first.status_code==200
    assert first.json()=={'snapshot':previous_snapshot}
    assert second.json()==first.json()
    assert calls==[candidate]


def test_previous_endpoint_returns_reason_for_different_trade_date(monkeypatch, tmp_path):
    runtime=tmp_path/'runtime';runtime.mkdir()
    candidate=tmp_path/'data/raw/old';_write_collection(
        candidate, trade_date='2026-09-09', asof='2026-09-09T15:00:00+08:00')
    current=_quality_snapshot(snapshot_id='current')
    (runtime/'options-active.json').write_text(json.dumps({
        'folder':str(tmp_path/'data/raw/current'), 'snapshot_id':'current',
        'previous_folder':str(candidate),
    }), encoding='utf-8')
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    monkeypatch.setattr(app_module, 'runtime', runtime)
    monkeypatch.setattr(app_module, 'state', {'data':current})
    monkeypatch.setattr(app_module, '_previous_cache', {'snapshot_id':None, 'result':None})

    with TestClient(app) as client:
        response=client.get('/api/options/v1/previous')
    body=response.json()
    assert body['snapshot'] is None
    assert '不同' in body['reason']
