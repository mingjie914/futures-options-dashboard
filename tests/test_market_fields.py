import json
from backend.data import normalize


def test_fixed_contract_iv_history_gaps_cutoff_percentile_and_hv():
    from datetime import date,timedelta
    from statistics import stdev
    import math
    import pytest
    from backend.data import historical_volatility
    from backend.pricing import price
    days=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(30)]
    contracts=[dict(order_book_id=f'MO2603{cp}{k}',strike_price=k,option_type=cp,maturity_date='2026-03-20') for k in [90,100,110] for cp in 'CP']
    prices=[]
    for i,day in enumerate(days):
        t=(date(2026,3,20)-date.fromisoformat(day)).days/365
        prices.append(dict(order_book_id='000852.XSHG',date=day,close=100+(i%3)))
        for c in contracts:
            prices.append(dict(order_book_id=c['order_book_id'],date=day,close=price(100,c['strike_price'],t,.2+i*.001,c['option_type'])))
    raw=dict(source='test',calendar=days,prices=prices,contracts=contracts)
    result=historical_volatility(raw,'MO2603-C-100.CFE',days[24],20)
    assert result['n']==20 and result['latest']['date']==days[24]
    assert result['latest']['iv']==pytest.approx(.224,abs=1e-10)
    assert result['percentile']==97.5
    closes=[100+(i%3) for i in range(4,25)]
    assert result['latest']['hv20']==pytest.approx(stdev([math.log(b/a) for a,b in zip(closes,closes[1:])])*math.sqrt(252))
    for p in prices:
        if p['date']==days[23] and p['order_book_id'] in ('MO2603C100','000852.XSHG'):p['close']=None
    result=historical_volatility(raw,'MO2603-C-100.CFE',days[24],20)
    assert result['n']==19 and result['points'][-2]['iv'] is None
    assert result['latest']['hv20'] is None
    assert historical_volatility(raw,'MO2603-C-100.CFE',days[0],20)['percentile'] is None


def test_iv_history_route_missing_archive_and_snapshot_binding(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import backend.app as api
    snapshot=dict(snapshot_id='v1',trade_date='2026-10-27',rows=[dict(code='MO2611-C-6400.CFE')])
    monkeypatch.setattr(api,'current',lambda:snapshot)
    monkeypatch.setattr(api,'_active_folder',lambda:tmp_path)
    client=TestClient(api.app)
    url='/api/options/v1/iv-history?code=MO2611-C-6400.CFE&window=20'
    result=client.get(url)
    assert result.status_code==200 and result.json()['points']==[]
    assert client.get(url+'&snapshot_id=old').status_code==409
    assert client.get(url.replace('6400','9999')).status_code==404
    assert client.get(url.replace('window=20','window=21')).status_code==422


def _save_response(folder, name, fields, codes, data, times):
    (folder / (name + '.json')).write_text(json.dumps({
        'ErrorCode': 0, 'Codes': codes, 'Fields': fields,
        'Times': times, 'Data': data,
    }, ensure_ascii=False), encoding='utf-8')


def test_same_snapshot_realtime_fields_supply_intraday_volume_and_oi(tmp_path):
    codes = ['MO2609-C-7700.CFE', 'MO2609-P-7700.CFE']
    _save_response(tmp_path, 'chain',
                   ['option_code', 'month', 'strike_price', 'last_tradedate', 'multiplier'],
                   ['C1', 'C2', 'C3', 'C4', 'C5'],
                   [codes, ['202609', '202609'], [7700, 7700],
                    ['2026-09-18', '2026-09-18'], [100, 100]], ['2026-09-10'])
    _save_response(tmp_path, 'quotes',
                   ['RT_BID1', 'RT_ASK1', 'RT_LAST', 'RT_DATE', 'RT_VOL', 'RT_OI'],
                   codes,
                   [[76.4, 184.0], [78.0, 196.4], [76.6, 185.0],
                    [20260910, 20260910], [13314, 6160], [10569, 5179]],
                   ['2026-09-10 11:40:18'])
    _save_response(tmp_path, 'daily', ['CLOSE', 'VOLUME', 'OI', 'AMT'], codes,
                   [[96.4, 173.6], [float('nan'), float('nan')], [10080, 5100],
                    [float('nan'), float('nan')]], ['2026-09-10 11:40:18'])
    _save_response(tmp_path, 'previous', ['OI'], codes, [[10080, 5100]],
                   ['2026-09-09 15:00:00'])
    _save_response(tmp_path, 'tdays_2609', ['date'], ['D1'], [['2026-09-11']],
                   ['2026-09-11'])
    (tmp_path / 'collection.json').write_text(json.dumps({
        'trade_date': '2026-09-10',
        'asof': '2026-09-10T11:40:18+08:00',
        'spot': 7614.09,
        'mode': 'live',
    }), encoding='utf-8')

    snapshot = normalize(tmp_path)
    call = next(row for row in snapshot['rows'] if row['cp'] == 'C')
    put = next(row for row in snapshot['rows'] if row['cp'] == 'P')
    assert call['volume'] == 13314
    assert call['oi'] == 10569
    assert call['oi_change'] == 489
    assert put['oi_change'] == 79
    assert any('源盘口' in warning for warning in snapshot['warnings'])
    assert snapshot['source']=='Wind已保存响应'
    meta_path=tmp_path/'collection.json'
    meta=json.loads(meta_path.read_text('utf-8'))
    meta.update(source='RiceQuant测试样本',test_only=True,warnings=['仅限本次测试'])
    meta_path.write_text(json.dumps(meta),encoding='utf-8')
    test_snapshot=normalize(tmp_path)
    assert test_snapshot['source']=='RiceQuant测试样本' and test_snapshot['test_only']
    assert test_snapshot['rows']==snapshot['rows']
    assert '仅限本次测试' in test_snapshot['warnings']


def test_missing_intraday_volume_is_kept_empty(tmp_path):
    codes = ['MO2609-C-7700.CFE']
    _save_response(tmp_path, 'chain',
                   ['option_code', 'month', 'strike_price', 'last_tradedate', 'multiplier'],
                   ['C1', 'C2', 'C3', 'C4', 'C5'],
                   [codes, ['202609'], [7700], ['2026-09-18'], [100]], ['2026-09-10'])
    _save_response(tmp_path, 'quotes', ['RT_BID1', 'RT_ASK1', 'RT_DATE'], codes,
                   [[76.4], [78.0], [20260910]], ['2026-09-10 11:40:18'])
    _save_response(tmp_path, 'daily', ['VOLUME', 'OI'], codes,
                   [[float('nan')], [10080]], ['2026-09-10 11:40:18'])
    _save_response(tmp_path, 'previous', ['OI'], codes, [[10080]],
                   ['2026-09-09 15:00:00'])
    _save_response(tmp_path, 'tdays_2609', ['date'], ['D1'], [['2026-09-11']],
                   ['2026-09-11'])
    (tmp_path / 'collection.json').write_text(json.dumps({
        'trade_date': '2026-09-10',
        'asof': '2026-09-10T11:40:18+08:00',
        'spot': 7614.09,
        'mode': 'live',
    }), encoding='utf-8')

    snapshot = normalize(tmp_path)
    row = snapshot['rows'][0]
    assert row['volume'] is None
    assert row['oi'] is None
    assert row['oi_change'] is None
    assert '保留为空' in snapshot['warnings'][1]
