import pytest
from backend.analytics import seasonal_position, contract_history


def row(day, symbol, position):
    return dict(trade_date=day, symbol=symbol, position=position)


def test_totals_calendar_leap_and_no_fill():
    result = seasonal_position([row('2024-02-29', 'RB2405', 10),
                                row('2024-02-29', 'RB2410', 20),
                                row('2025-03-01', 'RB2505', 40),
                                row('2025-03-01', 'RB8888', 999)])
    days = {r['month_day']: r for r in result['series']}
    assert len(days) == 366
    assert days['02-29']['2024'] == 30
    assert days['02-29']['2025'] is None
    assert days['03-01']['2025'] == 40
    assert days['03-02']['2025'] is None


def test_missing_is_not_partial_sum_and_zero_valid():
    result = seasonal_position([row('2025-01-02', 'RB2505', 10),
                                row('2025-01-02', 'RB2510', None),
                                row('2025-01-03', 'RB2505', 0)])
    assert result['observations'][0]['position'] is None
    assert result['observations'][1]['position'] == 0


def test_duplicates_rejected():
    with pytest.raises(ValueError):
        seasonal_position([row('2025-01-02', 'RB2505', 1)]*2)


def history_row(day, symbol, position=10, close=100, volume=5):
    return dict(trade_date=day, symbol=symbol, position=position,
                close=close, settle_price=None, volume=volume)


def test_same_month_relative_year_and_same_day_share():
    rows = [history_row('2023-12-01', 'RB2405', 10),
            history_row('2023-12-01', 'RB2410', 30),
            history_row('2024-12-01', 'RB2505', 20),
            history_row('2024-12-01', 'RB2510', 20)]
    result = contract_history(rows, 'same_month', 2025, 2, 5, metric='position_share')
    assert result['lines'][0]['points'][0]['x'] == 'Y-1 12-01'
    assert [l['points'][0]['value'] for l in result['lines']] == [.25, .5]
    assert result['lines'][0]['points'][0]['trade_date'] == '2023-12-01'


def test_fixed_spread_rolls_far_year_and_never_fills_missing_leg():
    rows = [history_row('2024-09-02', 'RB2410', close=110),
            history_row('2024-09-02', 'RB2501', close=120),
            history_row('2024-09-03', 'RB2410', close=130)]
    result = contract_history(rows, 'spread', 2024, 1, 10, 1, 1)
    assert result['lines'][0]['label'] == 'RB2410 − RB2501'
    assert [p['value'] for p in result['lines'][0]['points']] == [-10, None]
    assert result['lines'][0]['points'][0]['near_price'] == 110
    extended = contract_history(rows+[history_row('2025-01-02', 'RB2501', close=99)],
                                'spread', 2024, 1, 10, 1, 1)
    assert extended['axis'] == ['Y+0 09-02']
    with pytest.raises(ValueError):
        contract_history(rows, 'spread', 2024, 1, 10, 1, 0)


def test_history_settle_missing_denominator_missing_synthetic_and_duplicates():
    rows = [history_row('2024-02-29', 'RB2405'),
            history_row('2024-02-29', 'RB2410', position=None),
            history_row('2024-02-29', 'RB9999')]
    share = contract_history(rows, 'same_month', 2024, 1, 5, metric='position_share')
    assert share['lines'][0]['points'][0]['value'] is None
    price = contract_history(rows, 'same_month', 2024, 1, 5, metric='price', price_type='settle')
    assert price['lines'][0]['points'][0]['value'] is None
    assert 'Y+0 02-29' in price['axis']
    overview = contract_history(rows, 'overview', 2024, 1, 5)
    assert len(overview['lines']) == 2
    with pytest.raises(ValueError):
        contract_history(rows+rows[:1], 'same_month', 2024, 1, 5)


def test_history_empty_and_no_future_peak_normalization():
    assert contract_history([], 'same_month', 2026, 5, 10)['lines'] == []
    rows = [history_row('2024-09-02', 'RB2410', position=10),
            history_row('2024-09-02', 'RB2501', position=30)]
    past = contract_history(rows, 'same_month', 2024, 1, 10, metric='position_share')
    rows.append(history_row('2024-09-03', 'RB2410', position=1000000))
    future = contract_history(rows, 'same_month', 2024, 1, 10, metric='position_share')
    assert past['lines'][0]['points'][0] == future['lines'][0]['points'][0]


def test_history_observed_missing_day_is_explicit_but_weekend_is_not():
    rows = [history_row('2026-09-04', 'RB2610'),
            history_row('2026-09-07', 'RB2610'),
            history_row('2026-09-08', 'RB2701'),
            history_row('2026-09-09', 'RB2610')]
    for mode in ('same_month','overview'):
        result = contract_history(rows, mode, 2026, 1, 10)
        points = result['lines'][0]['points']
        assert [p['trade_date'] for p in points] == ['2026-09-04','2026-09-07','2026-09-08','2026-09-09']
        assert [p['value'] for p in points] == [10,10,None,10]


def test_seasonal_chart_lines_keep_only_observed_days_and_explicit_null():
    result = seasonal_position([row('2026-09-04','RB2610',10),
                                row('2026-09-07','RB2610',None),
                                row('2026-09-08','RB2610',20)])
    assert [p['x'] for p in result['lines'][0]['points']] == ['09-04','09-07','09-08']
    assert [p['value'] for p in result['lines'][0]['points']] == [10,None,20]


def spread_rows(day, year, spread):
    return [history_row(day, f'RB{year % 100:02d}10', close=100+spread),
            history_row(day, f'RB{(year+1) % 100:02d}01', close=100)]


def test_seasonal_summary_backward_only_and_excludes_current_cohort():
    rows = (spread_rows('2026-09-07', 2026, 40)
            + spread_rows('2026-09-08', 2026, 999)
            + spread_rows('2025-09-05', 2025, 10)
            + spread_rows('2025-09-08', 2025, 999)
            + spread_rows('2024-08-31', 2024, 0)
            + spread_rows('2024-08-30', 2024, 999))
    summary = contract_history(rows, 'spread', 2026, 3, 10, end='2026-09-07')['seasonal_summary']
    assert summary['current'] == dict(year=2026, label='RB2610 − RB2701',
                                      trade_date='2026-09-07', value=40, lag_days=0)
    assert (summary['n'], summary['median'], summary['min'], summary['max']) == (2, 5, 0, 10)
    assert [p['trade_date'] for p in summary['samples']] == ['2024-08-31', '2025-09-05']
    assert [p['lag_days'] for p in summary['samples']] == [7, 2]
    assert all(p['target_date'].endswith('09-07') for p in summary['samples'])


def test_seasonal_summary_relative_year_missing_and_stale_current():
    rows = (spread_rows('2025-12-02', 2026, 20)
            + spread_rows('2024-12-01', 2025, 10)
            + spread_rows('2023-11-24', 2024, 30))
    summary = contract_history(rows, 'spread', 2026, 4, 10, end='2025-12-05')['seasonal_summary']
    assert summary['current']['lag_days'] == 3
    assert summary['samples'][0]['target_date'] == '2024-12-02'
    assert summary['n'] == 1
    assert summary['excluded'] == [
        dict(year=2023, target_date='2022-12-02', reason='missing_cohort'),
        dict(year=2024, target_date='2023-12-02', reason='no_observation_within_7_days')]


def test_seasonal_summary_leap_date_no_clamp_or_forward_observation():
    rows = (spread_rows('2024-02-29', 2024, 20)
            + spread_rows('2023-02-28', 2023, 30)
            + spread_rows('2020-03-01', 2020, 99))
    summary = contract_history(rows, 'spread', 2024, 5, 10, end='2024-02-29')['seasonal_summary']
    assert summary['status'] == 'no_historical_sample'
    assert summary['n'] == 0 and summary['median'] is None
    assert summary['excluded'][0]['reason'] == 'no_observation_within_7_days'
    assert all(p['reason'] == 'invalid_calendar_date' for p in summary['excluded'][1:])


def test_seasonal_summary_missing_current_never_substitutes_prior_year():
    rows = spread_rows('2025-09-07', 2025, 40)
    result = contract_history(rows, 'spread', 2026, 3, 10, end='2026-09-07')
    assert result['seasonal_summary']['status'] == 'no_current_observation'
    assert result['seasonal_summary']['current'] is None
    assert contract_history(rows, 'spread', 2026, 3, 10)['seasonal_summary'] is None
    assert contract_history(rows, 'same_month', 2026, 3, 10, end='2026-09-07')['seasonal_summary'] is None
