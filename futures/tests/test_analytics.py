import json
import unittest
from datetime import datetime, timedelta, timezone

from backend.analytics import (quote_spread, rollover_series, spread_cost_scenario,
                               spread_series, spread_risk, term_structure, term_structure_comparison)


def radar_rows(n=90):
    return [dict(trade_date=(datetime(2026,1,1)+timedelta(days=i)).date().isoformat(),
        symbol=s,close=100+(i%7 if s=='IM2612' else 0),settle_price=100,
        volume=3000,position=10000) for i in range(n) for s in ('IM2612','IM2703')]


def test_index_basis_same_day_sign_ratio_and_internal_gaps():
    from backend.analytics import index_basis
    rows=[{'symbol':'IM2612','trade_date':d,'close':v,'settle_price':9999} for d,v in
          [('2026-09-01',99),('2026-09-02',100),('2026-09-03',101),('2026-09-05',102)]]
    points=[{'observation_date':d,'raw_value':100} for d in
            ['2026-08-31','2026-09-01','2026-09-02','2026-09-04','2026-09-05','2026-09-06']]
    result=index_basis(rows,points,'IM2612')
    assert [p['reference_spread'] for p in result['series']]==[1,0,None,None,-2]
    assert [p['basis_ratio'] for p in result['series']]==[.01,0,None,None,-.02]
    assert result['matched']==3 and result['unmatched']==2 and not result['execution_eligible']
    assert result['latest']['trade_date']=='2026-09-05'


def test_reference_spread_same_date_only_zero_and_missing():
    from backend.analytics import reference_basis
    rows=[row('2026-09-01','CU2610',100),row('2026-09-02','CU2610',101),row('2026-09-04','CU2610',None)]
    points=[{'observation_date':d,'raw_value':v} for d,v in [('2026-09-01',100),('2026-09-03',120),('2026-09-04',130)]]
    result=reference_basis(rows,points,'CU2610')
    assert result['latest']['reference_spread']==0
    assert result['matched']==1 and result['unmatched']==2
    assert all(p['trade_date']!='2026-09-02' for p in result['series'])
    assert result['execution_eligible'] is False
    assert reference_basis(rows,points,'CU2610','settle')['latest']['reference_spread']==-1
    import pytest
    with pytest.raises(ValueError,match='重复'):
        reference_basis(rows,points+[points[0]],'CU2610')


def test_single_position_exact_contract_and_gap_reset():
    from backend.analytics import contract_position
    rows=[row('2026-09-01','IM2612',100),row('2026-09-02','IM2703',100),row('2026-09-03','IM2612',100)]
    rows[-1]['position']=0
    d=contract_position(rows,'IM2612')
    assert [p['position'] for p in d['series']]==[20,None,0]
    assert d['latest']['change'] is None and d['latest']['position']==0


def test_radar_extremes_prior_only_and_family_deduplication():
    from backend.analytics import radar_product
    rows=radar_rows()
    rows[-2]['close']=150
    d=radar_product(rows,rows[-1]['trade_date'])
    assert d['level']=='普通观察' and d['family_count']==1
    event=next(e for e in d['events'] if e['kind']=='spread_level')
    assert event['value']==50 and event['n']==89 and event['percentile']==100
    assert {'spread_1','spread_5'} <= {e['kind'] for e in d['events']}
    # Same prices under settlement must not inherit close-price anomalies.
    assert not radar_product(rows,rows[-1]['trade_date'],'settle')['events']


def test_radar_short_samples_and_liquidity_are_not_strong_signals():
    from backend.analytics import radar_product
    rows=radar_rows(38)
    rows[-2]['close']=150
    assert radar_product(rows,rows[-1]['trade_date'])['level']=='短样本观察'
    rows[-1]['volume']=None
    d=radar_product(rows,rows[-1]['trade_date'])
    assert not d['events'] and d['examined_pairs']==0 and d['skipped_pairs']==1
    assert d['quality']


def test_radar_gaps_do_not_turn_multiday_changes_into_daily_jumps():
    from backend.analytics import radar_product
    rows=radar_rows()
    rows[-2]['close']=150
    rows[-4]['close']=None
    d=radar_product(rows,rows[-1]['trade_date'])
    assert 'spread_level' in {e['kind'] for e in d['events']}
    assert not {'spread_1','spread_5'} & {e['kind'] for e in d['events']}


def test_radar_position_volume_and_stale_date():
    from backend.analytics import radar_product
    rows=radar_rows()
    rows[-1]['position']=15000
    rows[-1]['volume']=15000
    d=radar_product(rows,rows[-1]['trade_date'])
    assert {'oi_total','oi_contract','migration','volume'} <= {e['kind'] for e in d['events']}
    assert d['level']=='重点关注'
    volume=next(e for e in d['events'] if e['kind']=='volume')
    assert volume['n']==20 and volume['baseline']==6000 and volume['value']==3
    assert radar_product(rows,'2026-05-01')['level']=='数据待检查'


def test_radar_contract_set_changes_suppress_total_flow_and_volume():
    from backend.analytics import radar_product
    rows=radar_rows()
    rows.append({**rows[-1],'symbol':'IM2706','position':100000,'volume':100000})
    d=radar_product(rows,rows[-1]['trade_date'])
    assert not {'oi_total','volume'} & {e['kind'] for e in d['events']}
    assert any('合约覆盖变化' in q for q in d['quality'])


def test_radar_duplicate_keys_rejected():
    import pytest
    from backend.analytics import radar_product
    rows=radar_rows()
    with pytest.raises(ValueError,match='重复'):
        radar_product(rows+[rows[-1]],rows[-1]['trade_date'])


def test_radar_level_matches_detail_history_with_old_internal_gaps():
    from backend.analytics import radar_product
    rows=radar_rows(290)
    for i in range(55,75):
        rows[2*i]['close']=None
    rows[-2]['close']=150
    detail=spread_series(rows,'IM2612','IM2703')['summary']['latest']
    radar=radar_product(rows,rows[-1]['trade_date'])
    e=next(e for e in radar['events'] if e['kind']=='spread_level')
    assert e['n']==detail['history_n']==252
    assert e['percentile']==detail['historical_percentile']


def row(day, symbol, price, **values):
    return dict(trade_date=day, symbol=symbol, close=price,
                settle_price=price + 1 if price is not None else None,
                volume=10, position=20, **values)


class AnalyticsTests(unittest.TestCase):
    def test_spread_plot_restores_gaps_and_resets_ma(self):
        from backend.analytics import spread_chart_series
        days=[(datetime(2026,1,1)+timedelta(days=i)).date().isoformat() for i in range(43)]
        rows=[row(day,'RB2605',i) for i,day in enumerate(days)]
        points=[{'trade_date':day,'spread':float(i)} for i,day in enumerate(days) if i!=21]
        plotted=spread_chart_series(rows,{'series':points})
        self.assertEqual(plotted[19]['ma20'],9.5)
        self.assertIsNone(plotted[21]['spread'])
        self.assertIsNone(plotted[40]['ma20'])
        self.assertEqual(plotted[41]['ma20'],31.5)
        self.assertEqual(points[0],{'trade_date':days[0],'spread':0.0})

    def risk_fixture(self, values):
        days = [(datetime(2025, 1, 1)+timedelta(days=i)).date().isoformat() for i in range(len(values))]
        return [dict(trade_date=d, spread=v) for d, v in zip(days, values)], days

    def test_risk_volatility_sample_std_full_window_and_zero(self):
        series, days = self.risk_fixture([i*(i+1)/2 for i in range(21)])
        risk = spread_risk(series, days)
        self.assertAlmostEqual(risk['volatility'][0]['value'], 35**.5)
        self.assertIsNone(risk['volatility'][1]['value'])
        self.assertEqual(risk['volatility'][0]['n'], 20)
        series, days = self.risk_fixture([-5]*61)
        self.assertEqual([x['value'] for x in spread_risk(series, days)['volatility']], [0, 0])

    def test_risk_adverse_is_entry_to_path_not_endpoint_or_drawdown(self):
        series, days = self.risk_fixture([-10,-14,-7,-12,-9,-10])
        h = spread_risk(series, days)['horizons'][0]
        self.assertEqual(h['n'], 1)
        self.assertEqual((h['buy']['value'], h['sell']['value']), (4, 3))
        self.assertEqual(h['buy']['adverse_date'], days[1])
        self.assertEqual(h['sell']['adverse_date'], days[2])
        self.assertEqual(h['buy']['end_date'], days[5])

    def test_risk_missing_observation_breaks_windows_and_differences(self):
        series, days = self.risk_fixture(list(range(21)))
        series = [p for p in series if p['trade_date'] != days[10]]
        risk = spread_risk(series, days)
        self.assertIsNone(risk['volatility'][0]['value'])
        self.assertEqual(risk['volatility'][0]['n'], 18)
        self.assertEqual(risk['horizons'][0]['skipped'], 6)
        self.assertEqual(risk['horizons'][1]['n'], 0)
        self.assertIsNone(risk['horizons'][1]['buy'])

    def test_risk_cutoff_and_bounded_lookback(self):
        series, days = self.risk_fixture(list(range(300)))
        risk = spread_risk(series, days[:-1])
        self.assertEqual(risk, spread_risk(series[:-1], days[:-1]))
        self.assertEqual(risk['observed_dates'], 253)
        self.assertEqual([h['n'] for h in risk['horizons']], [248,233])
        self.assertEqual(risk['horizons'][0]['sell']['value'], 5)
        self.assertEqual(risk['horizons'][0]['buy']['value'], 0)

    def test_risk_empty_missing_and_nonfinite_json_safe(self):
        for values in ([], [None], [float('nan')]*6):
            series, days = self.risk_fixture(values)
            risk = spread_risk(series, days)
            json.dumps(risk, allow_nan=False)
            self.assertIsNone(risk['volatility'][0]['value'])
            self.assertEqual(risk['horizons'][0]['n'], 0)

    def test_risk_pair_window_starts_at_first_common_history(self):
        days = [(datetime(2025, 1, 1)+timedelta(days=i)).date().isoformat() for i in range(9)]
        series = [dict(trade_date=days[i], spread=float(i)) for i in (3, 4, 6)]
        risk = spread_risk(series, days)
        self.assertEqual(risk['product_window'], dict(start=days[0], end=days[-1], n=9))
        self.assertEqual(risk['effective_pair_window'], dict(start=days[3], end=days[-1], n=6))
        self.assertEqual(risk['excluded_pre_history'], 3)
        self.assertEqual(risk['missing_dates'], [days[5], days[7], days[8]])

    def test_comparable_spread_cross_year_and_strict_cutoff(self):
        from backend.analytics import comparable_spread
        rows = [
            row('2026-09-06', 'RB2610', 140), row('2026-09-06', 'RB2701', 100),
            row('2026-09-07', 'RB2610', 999), row('2026-09-07', 'RB2701', 100),
            row('2025-09-06', 'RB2510', 130), row('2025-09-06', 'RB2601', 100),
            row('2024-09-06', 'RB2410', 120), row('2024-09-06', 'RB2501', 100),
            row('2023-09-06', 'RB2310', 110), row('2023-09-06', 'RB2401', 100),
            row('2027-09-06', 'RB2710', 700), row('2027-09-06', 'RB2801', 100),
        ]
        result = comparable_spread(rows, 'RB2610', 'RB2701', '2026-09-07')
        self.assertEqual(result['current'], {'trade_date': '2026-09-07', 'spread': 899})
        self.assertFalse(result['current_stale'])
        self.assertEqual(result['years'], [2023, 2024, 2025])
        self.assertEqual([(p['near'], p['far'], p['trade_date'], p['spread']) for p in result['samples']], [
            ('RB2310', 'RB2401', '2023-09-06', 10),
            ('RB2410', 'RB2501', '2024-09-06', 20),
            ('RB2510', 'RB2601', '2025-09-06', 30),
        ])
        self.assertEqual(result['median'], 20)
        self.assertEqual(result['percentile'], 100)
        self.assertIn('±15', result['window_rule'])

    def test_comparable_spread_missing_leg_does_not_fill_or_duplicate_year(self):
        from backend.analytics import comparable_spread
        rows = [
            row('2026-09-06', 'RB2610', 140), row('2026-09-06', 'RB2701', 100),
            row('2025-09-01', 'RB2510', 130), row('2025-09-01', 'RB2601', 100),
            row('2025-09-06', 'RB2510', 999),  # far leg is missing on the closest date
            row('2024-09-06', 'RB2410', 120),  # far leg missing for the whole window
        ]
        result = comparable_spread(rows, 'RB2610', 'RB2701', '2026-09-07')
        self.assertEqual(result['years'], [2025])
        self.assertEqual(result['samples'][0]['trade_date'], '2025-09-01')
        self.assertEqual(result['samples'][0]['spread'], 30)
        self.assertEqual(len(result['years']), 1)
        self.assertIsNone(result['percentile'])
        self.assertTrue(result['current_stale'])
        self.assertIn('current_observation_before_as_of', result['quality'])
        self.assertTrue(any(item['year'] == 2024 for item in result['excluded']))

    def test_comparable_spread_uses_same_day_when_available_and_percentile_needs_three_years(self):
        from backend.analytics import comparable_spread
        rows = [
            row('2026-09-06', 'RB2610', 140), row('2026-09-06', 'RB2701', 100),
            row('2025-09-05', 'RB2510', 130), row('2025-09-05', 'RB2601', 100),
            row('2025-09-06', 'RB2510', 150), row('2025-09-06', 'RB2601', 100),
            row('2025-09-07', 'RB2510', 170), row('2025-09-07', 'RB2601', 100),
        ]
        result = comparable_spread(rows, 'RB2610', 'RB2701', '2026-09-07')
        self.assertEqual(result['samples'][0]['trade_date'], '2025-09-06')
        self.assertEqual(result['samples'][0]['spread'], 50)
        self.assertEqual(len(result['years']), 1)
        self.assertIsNone(result['percentile'])

    def test_comparable_spread_target_keeps_relative_delivery_year_offset(self):
        from backend.analytics import comparable_spread
        rows = [
            row('2026-09-07', 'LH2701', 110), row('2026-09-07', 'LH2703', 100),
            row('2025-09-07', 'LH2601', 120), row('2025-09-07', 'LH2603', 100),
            row('2026-09-07', 'LH2601', 300), row('2026-09-07', 'LH2603', 100),
        ]
        result = comparable_spread(rows, 'LH2701', 'LH2703', '2026-09-08')
        self.assertEqual(result['years'], [2026])
        self.assertEqual(result['samples'][0]['target_date'], '2025-09-07')
        self.assertEqual(result['samples'][0]['trade_date'], '2025-09-07')
        self.assertEqual(result['samples'][0]['spread'], 20)

    def test_pair_only_same_day_no_fill(self):
        rows = [row('2026-01-01', 'RB2610', 100), row('2026-01-01', 'RB2701', 105),
                row('2026-01-02', 'RB2610', 110)]
        result = spread_series(rows, 'RB2610', 'RB2701')
        self.assertEqual(result['series'][0]['spread'], -5)
        self.assertEqual(len(result['series']), 1)
        self.assertEqual(result['summary']['missing_dates'], ['2026-01-02'])
        self.assertIn('missing_leg_or_price', result['summary']['quality_flags'])

    def test_history_excludes_current_and_future(self):
        rows = []
        for i in range(22):
            day = f'2026-01-{i+1:02d}'
            rows.extend([row(day, 'RB2610', 100+i), row(day, 'RB2701', 100)])
        result = spread_series(rows, 'RB2610', 'RB2701', window=20)
        latest = result['series'][-1]
        self.assertEqual(latest['history_n'], 20)
        self.assertEqual(latest['historical_percentile'], 100)
        self.assertLess(latest['descriptive_percentile'], 100)
        self.assertAlmostEqual(latest['z_score'], (21-10.5)/(35**0.5))
        self.assertEqual(result['series'][:21], spread_series(rows[:-2], 'RB2610', 'RB2701', window=20)['series'])

    def test_zero_variance_and_negative_spreads_json_safe(self):
        rows = [r for i in range(22) for r in
                [row(f'2026-01-{i+1:02d}', 'RB2610', 100), row(f'2026-01-{i+1:02d}', 'RB2701', 105)]]
        result = spread_series(rows, 'RB2610', 'RB2701')
        self.assertIsNone(result['series'][-1]['z_score'])
        json.dumps(result, allow_nan=False)

    def test_invalid_identity_and_duplicates(self):
        for near, far in [('RB9999', 'RB2701'), ('RB2610', 'CU2701'), ('RB2701', 'RB2610'), ('SR601', 'SR701')]:
            with self.assertRaises(ValueError):
                spread_series([], near, far)
        rows = [row('2026-01-01', 'RB2610', 100)] * 2
        with self.assertRaises(ValueError):
            spread_series(rows, 'RB2610', 'RB2701')

    def test_missing_price_not_zero(self):
        rows = [row('2026-01-01', 'RB2610', None), row('2026-01-01', 'RB2701', 100)]
        self.assertEqual(spread_series(rows, 'RB2610', 'RB2701')['series'], [])
        with self.assertRaises(ValueError):
            spread_series(rows, 'RB2610', 'RB2701', 'high')

    def test_settlement_explicit_and_missing_not_close_fallback(self):
        rows = [row('2026-01-01', 'RB2610', 100), row('2026-01-01', 'RB2701', 105)]
        rows[0]['settle_price'] = 110
        self.assertEqual(spread_series(rows, 'RB2610', 'RB2701', 'settle')['series'][0]['spread'], 4)
        rows[0]['settle_price'] = None
        self.assertEqual(spread_series(rows, 'RB2610', 'RB2701', 'settle')['series'], [])
        for invalid in (1, 5001, 2.5, True):
            with self.assertRaises(ValueError):
                spread_series(rows, 'RB2610', 'RB2701', window=invalid)

    def test_empty_inputs_and_nonfinite_price_are_json_safe(self):
        self.assertIsNone(spread_series([], 'RB2610', 'RB2701')['summary']['latest'])
        self.assertEqual(term_structure([], '2026-01-01')['nodes'], [])
        rows = [row('2026-01-01', 'RB2610', float('nan')), row('2026-01-01', 'RB2701', 105)]
        json.dumps(spread_series(rows, 'RB2610', 'RB2701'), allow_nan=False)

    def test_curve_unequal_date_spacing_and_missing_node(self):
        rows = [row('2026-01-01', 'RB2602', 100), row('2026-01-01', 'RB2603', 110),
                row('2026-01-01', 'RB2605', 140), row('2026-01-01', 'RB2606', None),
                row('2026-01-01', 'RB9999', 120)]
        result = term_structure(rows, '2026-01-01')
        self.assertEqual(len(result['nodes']), 4)
        self.assertAlmostEqual(result['curvature'][0]['residual'], 110-(100+40*28/89))
        self.assertIsNone(result['adjacent'][0]['annualized_price_slope'])
        self.assertIsNone(result['nodes'][-1]['price'])

    def test_curve_comparison_aligns_current_symbols_and_observed_days(self):
        days = ['2026-01-02', '2026-01-05', '2026-01-06', '2026-01-08', '2026-01-09']
        rows = [row(day, 'RB2602', 90+i) for i, day in enumerate(days)]
        rows += [row(days[-1], 'RB2603', None), row(days[-1], 'RB2601', 500),
                 row('2026-01-10', 'CU2602', 1), row('2026-01-10', 'RB9999', 1000),
                 row('2026-01-12', 'RB2602', 100), row('2026-01-12', 'RB2603', 110),
                 row('2026-01-12', 'RB2604', 120)]
        result = term_structure_comparison(rows, '2026-01-12')
        previous, five = result['comparisons']
        self.assertEqual((previous['as_of'], five['as_of']), (days[-1], days[0]))
        self.assertEqual([n['symbol'] for n in previous['nodes']], ['RB2602','RB2603','RB2604'])
        self.assertEqual([n['price'] for n in previous['nodes']], [94,None,None])
        self.assertEqual([n['change'] for n in previous['nodes']], [6,None,None])
        self.assertEqual(five['nodes'][0]['change'], 10)
        rows += [row('2026-01-13', 'RB2602', 999)]
        self.assertEqual(term_structure_comparison(rows, '2026-01-12'), result)

    def test_curve_comparison_missing_history_and_price_type(self):
        rows = [row('2026-01-02', 'RB2602', 90), row('2026-01-05', 'RB2602', 100)]
        rows[0]['settle_price'] = None
        rows[1]['settle_price'] = 120
        result = term_structure_comparison(rows, '2026-01-05', 'settle')
        self.assertIsNone(result['comparisons'][0]['nodes'][0]['price'])
        self.assertIsNone(result['comparisons'][0]['nodes'][0]['change'])
        self.assertEqual(result['comparisons'][1]['status'], 'insufficient_observed_dates')
        self.assertIsNone(result['comparisons'][1]['as_of'])
        self.assertEqual(result['nodes'][0]['price'], 120)
        self.assertEqual(term_structure_comparison([], '2026-01-05')['nodes'], [])

    def test_rollover_pair_denominator_and_sign(self):
        rows = [row('2026-01-01', 'RB2610', 100), row('2026-01-01', 'RB2701', 95)]
        rows[0].update(volume=30, position=60)
        rows[1].update(volume=10, position=40)
        point = rollover_series(rows, 'RB2610', 'RB2701')['series'][0]
        self.assertEqual(point['new_oi_share'], .4)
        self.assertEqual(point['new_volume_share'], .25)
        self.assertEqual(point['long_roll_estimate'], -5)
        self.assertEqual(point['short_roll_estimate'], 5)
        rows[0]['position'] = rows[1]['position'] = 0
        self.assertIsNone(rollover_series(rows, 'RB2610', 'RB2701')['series'][0]['new_oi_share'])

    def test_cost_hand_calculations(self):
        for direction, entry, exit_, breakeven in [('buy',51,61,53), ('sell',49,39,47)]:
            result = spread_cost_scenario(direction, entry, exit_, 2, 10, 40)
            self.assertEqual(result['gross_pnl'], 200)
            self.assertEqual(result['net_pnl'], 160)
            self.assertEqual(result['breakeven_exit_spread'], breakeven)
        self.assertIsNone(spread_cost_scenario('buy', 0, 1, 2, 10)['net_pnl'])
        self.assertIsNone(spread_cost_scenario('buy', 0, 1, 2, None)['gross_pnl'])
        with self.assertRaises(ValueError):
            spread_cost_scenario('buy', 0, 1, 0, 10, 40)

    def quotes(self):
        now = datetime(2026,9,7,21,0,1,tzinfo=timezone(timedelta(hours=8)))
        base = dict(trade_date='2026-09-08', observed_at=now.isoformat(),
                    market_status='trading', bid_size=3, ask_size=2)
        return dict(base,symbol='RB2610',bid=3500,ask=3501), dict(base,symbol='RB2701',bid=3450,ask=3451), now

    def test_quote_directions_and_night_session(self):
        near, far, now = self.quotes()
        result = quote_spread(near, far, now)
        self.assertEqual(result['buy_spread'], 51)
        self.assertEqual(result['sell_spread'], 49)
        self.assertEqual(result['long_roll_cost'], -49)
        self.assertEqual(result['short_roll_cost'], 51)
        self.assertEqual(result['buy_capacity'], 2)
        self.assertEqual(result['status'], 'indicative')
        self.assertFalse(result['executable'])

    def test_quote_gates(self):
        mutations = [dict(observed_at=None), dict(bid=None), dict(bid_size=0),
                     dict(market_status='unknown'), dict(trade_date='2026-09-07'),
                     dict(observed_at='2026-09-07T20:59:00+08:00'),
                     dict(observed_at='2026-09-07T21:00:20+08:00'),
                     dict(bid=4000)]
        for mutation in mutations:
            near, far, now = self.quotes()
            far.update(mutation)
            result = quote_spread(near, far, now)
            self.assertEqual(result['status'], 'unavailable', mutation)
            self.assertIsNone(result['buy_spread'])
            self.assertTrue(result['quality_flags'])
        near, far, now = self.quotes()
        far['observed_at'] = '2026-09-07T20:59:56+08:00'
        self.assertIn('quote_time_skew', quote_spread(near, far, now)['quality_flags'])


if __name__ == '__main__':
    unittest.main()
def test_fundamental_statistics_cutoff_zero_and_seasonal():
    from backend.analytics import fundamental_statistics
    points = [{'observation_date':f'2026-01-{day:02d}', 'raw_value':float(day)} for day in range(1,22)]
    points += [{'observation_date':'2026-01-22','raw_value':0.0}, {'observation_date':'2026-01-23','raw_value':999.0}]
    result = fundamental_statistics(points, '2026-01-22', 'D')
    assert result['latest']['raw_value'] == 0 and result['change'] == -21
    assert result['historical_percentile'] == 0 and result['history_n'] == 21
    assert result['age_days'] == 0 and len(result['seasonal']['lines'][0]['points']) == 22
    assert fundamental_statistics(points,'2025-01-01','D')['latest'] is None


def test_rollover_progress_fixed_pairs_expiry_alignment_and_missing_values():
    from backend.analytics import rollover_progress
    def row(day, symbol, oi, price):
        return dict(trade_date=day,symbol=symbol,position=oi,close=price)
    rows=[row('2026-09-17','IM2609',10,100),row('2026-09-17','IM2612',20,90),
          row('2026-09-18','IM2609',0,101),row('2026-09-18','IM2612',30,None),
          row('2026-10-29','IM2612',100,95),row('2026-10-29','IM2703',80,80),
          row('2026-10-29','IM2706',80,75),row('2026-10-29','IM2611',900,100),
          row('2026-10-30','IM2706',9999,80)]
    terms={'contracts':[dict(symbol=s,last_trade_date=d,status='rds_recorded') for s,d in
           [('IM2609','2026-09-18'),('IM2612','2026-12-18'),('IM2703','2027-03-19')]]}
    result=rollover_progress(rows,'IM2612','IM2609',terms,'2026-10-29')
    assert result['candidate']=='IM2703'  # Later month only; ties prefer nearest, no future observation.
    current,previous=result['rounds']
    assert current['series'][-1]==dict(day=-50,trade_date='2026-10-29',outgoing=100,incoming=80,spread=15)
    assert previous['series'][-1]==dict(day=0,trade_date='2026-09-18',outgoing=0,incoming=30,spread=None)
    assert previous['series'][0]['day']==-1 and previous['series'][0]['spread'] is None
    assert all(p['trade_date']<='2026-10-29' for r in result['rounds'] for p in r['series'])
    terms['contracts'][0]['status']='calendar_conflict'
    assert rollover_progress(rows,'IM2612','IM2609',terms,'2026-10-29')['rounds'][1]['series']==[]
    assert all(not r['series'] for r in rollover_progress(rows,None,None,terms,'2026-10-29')['rounds'])


def test_rollover_reuses_existing_pair_spread(monkeypatch):
    from backend import analytics
    rows=[dict(symbol=s,trade_date='2026-10-29',position=p,close=c) for s,p,c in [('IM2612',100,100),('IM2703',80,90)]]
    result=analytics.spread_series(rows,'IM2612','IM2703')
    def unexpected(*args,**kwargs):
        raise AssertionError('existing selected-pair spread must not be recomputed')
    monkeypatch.setattr(analytics,'spread_series',unexpected)
    assert analytics.rollover_series(rows,'IM2612','IM2703',result=result)['series'][0]['spread']==10
    terms={'contracts':[dict(symbol='IM2612',last_trade_date='2026-12-18',status='rds_recorded')]}
    assert analytics.rollover_progress(rows,'IM2612',None,terms,'2026-10-29',result)['rounds'][0]['series'][0]['spread']==10
