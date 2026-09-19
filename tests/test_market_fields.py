import json
from backend.data import normalize


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
    assert any('盘中WSQ' in warning for warning in snapshot['warnings'])


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
