"""Pure daily analytics; no source access, order execution or forward filling.

Percentiles use a mid-rank convention, in percent. Historical statistics exclude
the current point; 20 prior observations are required before statistical signals.
Contract dates inferred here are month anchors, never authoritative expiry dates.
"""

import math
import re
import statistics
from datetime import date, datetime

FORMULA_VERSION = 'daily-v1'
MIN_HISTORY = 20


def fundamental_statistics(points, as_of, frequency):
    """Original-unit, current-vintage descriptive statistics; no causal signal."""
    cutoff = date.fromisoformat(as_of)
    selected = sorted((p for p in points if p['observation_date'] <= as_of), key=lambda p:p['observation_date'])
    if len({p['observation_date'] for p in selected}) != len(selected):
        raise ValueError('基本面存在重复观测日期')
    valid = [p for p in selected if p['raw_value'] is not None and math.isfinite(p['raw_value'])]
    latest, previous = (valid[-1] if valid else None), (valid[-2] if len(valid)>1 else None)
    baseline = [p['raw_value'] for p in valid[:-1]][-252:]
    percentile = (100 * (sum(v < latest['raw_value'] for v in baseline) +
        .5 * sum(v == latest['raw_value'] for v in baseline)) / len(baseline)) if latest and len(baseline)>=20 else None
    lines = {}
    for p in selected:
        day = date.fromisoformat(p['observation_date'])
        x = day.strftime('%m-%d')
        lines.setdefault(day.year, []).append({'x':x, 'value':p['raw_value'], 'trade_date':p['observation_date']})
    return {'latest':latest, 'previous':previous,
        'change':latest['raw_value']-previous['raw_value'] if previous else None,
        'age_days':(cutoff-date.fromisoformat(latest['observation_date'])).days if latest else None,
        'freshness':'release_calendar_unverified', 'historical_percentile':percentile, 'history_n':len(baseline),
        'seasonal':{'mode':'seasonal','metric':'price','frequency':frequency,
            'axis':sorted({p['x'] for values in lines.values() for p in values}),
            'lines':[{'year':year,'label':str(year),'points':values} for year,values in sorted(lines.items())]},
        'formula_version':'fundamental-observation-v1'}


def contract_position(rows, symbol):
    indexed = _index(rows, {symbol})
    if not indexed:
        raise ValueError('当前窗口未观测到所选合约')
    first = min(day for day, _ in indexed)
    days = sorted({r['trade_date'] for r in rows if r['trade_date'] >= first})
    points, previous = [], None
    for day in days:
        row = indexed.get((day, symbol), {})
        oi, volume = _number(row.get('position')), _number(row.get('volume'))
        oi = oi if oi is not None and oi >= 0 else None
        volume = volume if volume is not None and volume >= 0 else None
        points.append(dict(trade_date=day,position=oi,volume=volume,
            change=oi-previous if oi is not None and previous is not None else None))
        previous = oi
    return dict(symbol=symbol,series=points,latest=next((p for p in reversed(points) if p['position'] is not None),None),
        warnings=['所选真实合约，不拼接主力；缺失持仓不填零',
            '增减仓为相邻品种观测日差值，缺口后不计算；不能据此判断多空方向'])


def reference_basis(rows, points, symbol, price_type='close'):
    """Observation-date reference only; never carry spot forward or claim availability."""
    indexed = _index(rows, {symbol})
    field = _field(price_type)
    seen, series = set(), []
    for point in sorted(points,key=lambda p:p['observation_date']):
        day = point['observation_date']
        if day in seen:
            raise ValueError('参考价观测日期重复')
        seen.add(day)
        spot = _number(point['raw_value'])
        future = _number(indexed.get((day,symbol),{}).get(field))
        series.append(dict(trade_date=day,reference_price=spot,futures_price=future,
            reference_spread=spot-future if spot is not None and future is not None else None))
    matched = [p for p in series if p['reference_spread'] is not None]
    return dict(series=series,matched=len(matched),unmatched=len(series)-len(matched),
        latest=matched[-1] if matched else None,formula='reference_price - futures_price',
        comparison_basis='same_observation_date_only',execution_eligible=False)


def index_basis(rows, points, symbol):
    """S-F close basis of one real contract; retain gaps inside its observed lifetime."""
    contract = [r for r in rows if r['symbol']==symbol]
    if not contract:
        raise ValueError('当前窗口未观测到所选合约')
    first, last = min(r['trade_date'] for r in contract), max(r['trade_date'] for r in contract)
    by_day = {}
    for point in points:
        day = point['observation_date']
        if day in by_day:
            raise ValueError('指数观测日期重复')
        by_day[day] = point['raw_value']
    days = sorted(d for d in set(by_day)|{r['trade_date'] for r in rows} if first<=d<=last)
    aligned = [{'observation_date':d,'raw_value':by_day.get(d)} for d in days]
    result = reference_basis(rows,aligned,symbol,'close')
    for p in result['series']:
        spot, future = p['reference_price'], p['futures_price']
        if (spot is not None and spot<=0) or (future is not None and future<=0):
            p['reference_spread'] = None
        p['basis_ratio'] = p['reference_spread']/spot if p['reference_spread'] is not None and spot else None
    valid = [p for p in result['series'] if p['reference_spread'] is not None]
    result.update(matched=len(valid),unmatched=len(days)-len(valid),latest=valid[-1] if valid else None)
    return result


def radar_product(rows, as_of, price_type='close', min_volume=1000, min_position=1000):
    """Latest-only summaries, prior-only baselines; no price/OI filling or flow inference."""
    field = _field(price_type)
    daily = {}
    for r in rows:
        day = daily.setdefault(r['trade_date'], {})
        if r['symbol'] in day:
            raise ValueError('日线合约日期重复')
        day[r['symbol']] = r
    days = sorted(daily)
    latest_day = days[-1]
    current = daily[latest_day]
    quality = []
    if latest_day != as_of:
        quality.append('日期落后于全市场最新观测')
    events, examined = [], 0

    def number(row, key):
        v = _number(row.get(key))
        return v if v is not None and (key not in ('volume', 'position') or v >= 0) else None

    def liquid(symbol):
        r = current[symbol]
        return (number(r, 'volume') is not None and number(r, 'volume') >= min_volume
                and number(r, 'position') is not None and number(r, 'position') >= min_position)

    def add(kind, family, label, value, n, near=None, far=None, **extra):
        events.append(dict(kind=kind, family=family, label=label, value=value, n=n,
            short=n<60, near=near, far=far, **extra))

    def changes(values, lag=1):
        return [round(values[i]-values[i-lag], 10) if all(v is not None for v in values[i-lag:i+1]) else None
                for i in range(lag, len(values))]

    def jump(values, kind, family, label, near=None, far=None):
        history = [abs(v) for v in values[:-1] if v is not None][-252:]
        value = values[-1] if values else None
        if value is not None and len(history)>=20 and abs(value)>0:
            p = _percentile(abs(value), history)
            if p>=97.5:
                add(kind, family, label, value, len(history), near, far, percentile=p)

    symbols = sorted(current)
    active = sorted((s for s in symbols if liquid(s)), key=lambda s: (-current[s]['position'], s))[:3]
    pairs = set(zip(symbols, symbols[1:]))
    pairs.update(tuple(sorted((a,b))) for i,a in enumerate(active) for b in active[i+1:])
    skipped = 0
    for near, far in sorted(pairs):
        if not liquid(near) or not liquid(far):
            skipped += 1
            continue
        values = []
        for day in days:
            a,b = number(daily[day].get(near,{}),field),number(daily[day].get(far,{}),field)
            values.append(round(a-b,10) if a is not None and b is not None else None)
        if values[-1] is None:
            quality.append(f'{near}/{far} 当日价格缺失')
            continue
        examined += 1
        history = [v for v in values[:-1] if v is not None][-252:]
        if len(history)>=20:
            p = _percentile(values[-1],history)
            if p<=5 or p>=95:
                add('spread_level','spread','价差低位' if p<=5 else '价差高位',values[-1],len(history),near,far,percentile=p)
        jump(changes(values), 'spread_1','spread','价差1观测日突变',near,far)
        jump(changes(values,5), 'spread_5','spread','价差5观测日突变',near,far)
        # Pair share only: does not claim investor transfers or whole-product share.
        shares=[]
        for day in days:
            a,b=number(daily[day].get(near,{}),'position'),number(daily[day].get(far,{}),'position')
            shares.append(100*b/(a+b) if a is not None and b is not None and a+b>0 else None)
        delta=changes(shares)
        if delta and delta[-1] is not None and abs(delta[-1])>=3:
            jump(delta,'migration','position','远月两腿持仓占比突变 / 百分点',near,far)

    # Total changes only where observed contract sets match; listing/expiry isn't a flow signal.
    totals = {'position':[], 'volume':[]}
    for day in days:
        for key in totals:
            vals=[number(r,key) for r in daily[day].values()]
            totals[key].append(sum(vals) if all(v is not None for v in vals) else None)
    for key, vals in totals.items():
        if vals[-1] is None:
            quality.append('当日'+('持仓' if key=='position' else '成交')+'字段缺失')
    oi=totals['position']
    delta=changes(oi)
    for i in range(len(delta)):
        if set(daily[days[i]])!=set(daily[days[i+1]]):
            delta[i]=None
    if len(days)>1 and set(daily[days[-2]])!=set(current):
        quality.append('合约覆盖变化：不报总持仓变动')
    if delta and delta[-1] is not None and oi[-2] and abs(delta[-1])>=1000 and abs(delta[-1]/oi[-2])>=0.03:
        jump(delta,'oi_total','position','品种总持仓突变 / 手')
    # Important single contracts, using their own daily absolute and relative change.
    for symbol in active:
        vals=[number(daily[d].get(symbol,{}),'position') for d in days]
        delta=changes(vals)
        if delta and delta[-1] is not None and vals[-2] and abs(delta[-1])>=1000 and abs(delta[-1]/vals[-2])>=0.05:
            jump(delta,'oi_contract','position','活跃合约持仓突变 / 手',symbol)
    volumes=totals['volume']
    if len(days)>=61 and all(v is not None for v in volumes[-21:]) and all(set(daily[d])==set(current) for d in days[-21:]):
        base=statistics.median(volumes[-21:-1])
        if base>0 and volumes[-1]>=max(1000,min_volume) and volumes[-1]/base>=2:
            add('volume','volume','品种成交放大 / 倍',volumes[-1]/base,20,baseline=base)
            events[-1]['short']=False  # Ratio rule, not a percentile; product has >=60 prior days.
    elif len(days)>=21:
        quality.append('成交放大基准不可比：覆盖变化或字段缺失')
    if len(days)<61:
        quality.append('品种历史不足60个前序观测')
    for e in events:
        selected=[current[s] for s in (e['near'],e['far']) if s]
        e['weak_volume']=min((number(r,'volume') for r in selected), default=None)
        e['weak_position']=min((number(r,'position') for r in selected), default=None)
    families={e['family'] for e in events if not e['short']}
    level='重点关注' if len(families)>=2 else '普通观察' if families else '短样本观察' if events else '未命中'
    if latest_day!=as_of:
        level='数据待检查'
    return dict(as_of=latest_day,level=level,events=events,quality=list(dict.fromkeys(quality)),
        examined_pairs=examined,skipped_pairs=skipped,observations=len(days),total_position=oi[-1],
        total_volume=volumes[-1],family_count=len(families))


def seasonal_position(rows):
    """Observed real contracts summed by day; calendar-year overlays, no filling."""
    from datetime import timedelta
    daily, seen = {}, set()
    for row in rows:
        symbol = row['symbol']
        if symbol[-4:] in {'8888', '9998', '9999'}:
            continue
        _contract(symbol)
        day = date.fromisoformat(row['trade_date'])
        key = (day, symbol)
        if key in seen:
            raise ValueError('Duplicate daily contract key')
        seen.add(key)
        entry = daily.setdefault(day, {'position': 0, 'contracts': 0, 'missing': 0})
        value = _number(row.get('position'))
        entry['contracts'] += 1
        if value is None or value < 0:
            entry['missing'] += 1
        else:
            entry['position'] += value
    years = sorted({d.year for d in daily})
    grid = []
    for offset in range(366):
        md = (date(2000, 1, 1) + timedelta(days=offset)).strftime('%m-%d')
        point = {'month_day': md}
        for year in years:
            point[str(year)] = None
        grid.append(point)
    by_md = {r['month_day']: r for r in grid}
    observations = []
    for day, entry in sorted(daily.items()):
        value = None if entry['missing'] else entry['position']
        by_md[day.strftime('%m-%d')][str(day.year)] = value
        observations.append({'trade_date': day.isoformat(), **entry, 'position': value})
    return {'series': grid, 'years': years, 'observations': observations,
            'lines': [{'label': f'{year}年', 'year': year, 'points': [
                {'x': r['trade_date'][5:], 'trade_date': r['trade_date'], 'value': r['position']}
                for r in observations if r['trade_date'].startswith(str(year))]} for year in years],
            'scope': 'observed_real_contracts_total', 'unit': '手',
            'warnings': ['按自然月日对齐；无观测日仅跨空档连线，不生成数值；字段缺失仍断开',
                         '以品种已观测日期作交易日代理，无法识别整个品种同时缺数的日期',
                         '合计仅覆盖已观测真实月合约，缺失合约无法从日线单独识别',
                         '任一已观测合约持仓缺失则当日合计为空；未调整历史统计口径变化']}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _seasonal_spread_summary(lines, anchor_year, years, end):
    """One backward-only seasonal observation per prior cohort, not a signal."""
    cutoff = date.fromisoformat(end)
    by_year = {line['year']: line for line in lines}
    current_line = by_year.get(anchor_year, {})
    current_points = [p for p in current_line.get('points', [])
                      if p['trade_date'] <= end and _number(p['value']) is not None]
    result = dict(cutoff=end, current=None, samples=[], excluded=[], n=0,
                  median=None, min=None, max=None, tolerance_days=7,
                  status='no_current_observation',
                  warnings=['每个历史年份最多一个同季节观测，仅描述所选年份，不代表胜率或置信区间',
                            '仅取目标日及之前7个自然日内有效值；非闰年无2月29日则剔除',
                            '历史口径及基本面变化未调整；不是可执行套利信号'])
    if not current_points:
        return result
    current = max(current_points, key=lambda p: p['trade_date'])
    current_day = date.fromisoformat(current['trade_date'])
    result['current'] = dict(year=anchor_year, label=current_line['label'],
                             trade_date=current['trade_date'], value=current['value'],
                             lag_days=(cutoff-current_day).days)
    for year in range(anchor_year-years+1, anchor_year):
        try:
            target = current_day.replace(year=current_day.year+year-anchor_year)
        except ValueError:
            result['excluded'].append(dict(year=year, reason='invalid_calendar_date'))
            continue
        line = by_year.get(year)
        if line is None:
            result['excluded'].append(dict(year=year, target_date=target.isoformat(),
                                           reason='missing_cohort'))
            continue
        candidates = [p for p in line['points'] if _number(p['value']) is not None
                      and 0 <= (target-date.fromisoformat(p['trade_date'])).days <= 7
                      and p['trade_date'] <= end]
        if not candidates:
            result['excluded'].append(dict(year=year, target_date=target.isoformat(),
                                           reason='no_observation_within_7_days'))
            continue
        selected = max(candidates, key=lambda p: p['trade_date'])
        result['samples'].append(dict(year=year, label=line['label'],
                                      target_date=target.isoformat(),
                                      trade_date=selected['trade_date'], value=selected['value'],
                                      lag_days=(target-date.fromisoformat(selected['trade_date'])).days))
    values = [p['value'] for p in result['samples']]
    result.update(n=len(values), median=statistics.median(values) if values else None,
                  min=min(values) if values else None, max=max(values) if values else None,
                  status='available' if values else 'no_historical_sample')
    return result


def contract_history(rows, mode, anchor_year, years, near_month,
                     far_month=1, far_year_offset=1, metric='position', price_type='close', end=None):
    """Historical overlays from actual contracts; relative calendar, never expiry estimates."""
    from datetime import timedelta
    if mode not in {'same_month', 'spread', 'overview'}:
        raise ValueError('Unknown history mode')
    if metric not in {'position', 'position_share', 'price', 'volume'}:
        raise ValueError('Unknown history metric')
    if not 1 <= years <= 10 or not 1 <= near_month <= 12 or not 1 <= far_month <= 12:
        raise ValueError('Invalid years or month')
    if far_year_offset not in {0, 1} or (mode == 'spread' and far_year_offset*12+far_month <= near_month):
        raise ValueError('远月必须晚于近月；跨年组合请选择次年')
    field = _field(price_type)
    contracts, daily, product = {}, {}, None
    for row in rows:
        if row['symbol'][-4:] in {'8888', '9998', '9999'}:
            continue
        name, anchor = _contract(row['symbol'])
        if product is not None and name != product:
            raise ValueError('One product required')
        product = name
        day = date.fromisoformat(row['trade_date']).isoformat()
        series = contracts.setdefault(row['symbol'], {})
        if day in series:
            raise ValueError('Duplicate daily contract key')
        series[day] = row
        value = _number(row.get('position'))
        total, valid = daily.get(day, (0, True))
        daily[day] = (total+(value or 0), valid and value is not None and value >= 0)
    def value(row):
        if not row:
            return None
        if metric == 'price':
            return _number(row.get(field))
        number = _number(row.get('position' if metric == 'position_share' else metric))
        if number is None or number < 0:
            return None
        if metric == 'position_share':
            total, valid = daily[row['trade_date']]
            return number/total if valid and total > 0 else None
        return number
    def point(day, year, val, **extra):
        parsed = date.fromisoformat(day)
        relative = parsed.year-year
        return dict(x=f'Y{relative:+d} {parsed:%m-%d}', trade_date=day, value=val, **extra)
    lines, missing = [], []
    for year in (range(anchor_year-years+1, anchor_year+1) if product else []):
        near = f'{product}{year%100:02d}{near_month:02d}'
        if mode == 'overview':
            break
        if mode == 'same_month':
            if near not in contracts:
                missing.append(near)
                continue
            points = [point(day, year, value(row)) for day, row in sorted(contracts[near].items())]
            lines.append(dict(label=near, year=year, points=points))
        else:
            far = f'{product}{(year+far_year_offset)%100:02d}{far_month:02d}'
            a, b = contracts.get(near, {}), contracts.get(far, {})
            if not a or not b:
                missing.append(f'{near} − {far}')
                continue
            points = []
            for day in sorted(a.keys() | b.keys()):
                first, second = _number(a.get(day, {}).get(field)), _number(b.get(day, {}).get(field))
                val = first-second if first is not None and second is not None else None
                points.append(point(day, year, val, near_price=first, far_price=second))
            lines.append(dict(label=f'{near} − {far}', year=year, points=points))
    # Reindex within each observed lifecycle using the product's observed dates.
    # These explicit nulls are real gaps; calendar-only padding is not a gap.
    product_days = sorted(daily)
    for line in lines:
        lookup = {p['trade_date']: p for p in line['points']}
        if lookup:
            first, last = min(lookup), max(lookup)
            line['points'] = [lookup.get(day) or point(day, line['year'], None)
                              for day in product_days if first <= day <= last]
    if mode == 'overview':
        start = f'{anchor_year-years+1}-01-01'
        for symbol, series in sorted(contracts.items()):
            first, last = max(start, min(series)), max(series)
            points = [dict(x=day, trade_date=day, value=value(series.get(day)))
                      for day in product_days if first <= day <= last]
            if points:
                lines.append(dict(label=symbol, points=points))
        axis = sorted({p['x'] for line in lines for p in line['points']})
    else:
        points = [p for line in lines for p in line['points']]
        relevant = [p for p in points if p['value'] is not None] or points
        offsets = [int(p['x'].split()[0][1:]) for p in relevant]
        axis = [f'Y{offset:+d} {(date(2000, 1, 1)+timedelta(days=i)):%m-%d}'
                for offset in range(min(offsets, default=0), max(offsets, default=0)+1)
                for i in range(366)]
        if relevant:
            index = {x: i for i, x in enumerate(axis)}
            bounds = [index[p['x']] for p in relevant]
            axis = axis[min(bounds):max(bounds)+1]
    return dict(mode=mode, metric=metric, axis=axis, lines=lines, missing=missing,
                seasonal_summary=_seasonal_spread_summary(lines, anchor_year, years, end)
                    if mode == 'spread' and end is not None else None,
                alignment='actual_date' if mode == 'overview' else 'calendar_relative_to_near_delivery_year',
                unit='原始报价单位' if mode == 'spread' or metric == 'price' else '比例' if metric == 'position_share' else '手',
                warnings=['仅使用已观测真实月合约，不以连续合约拼接',
                          '无观测日跨空档连线、不填值；品种有观测但本合约缺数则断开',
                          '交易日代理来自品种已观测日期，不能识别整品种同时缺数',
                          '缺失不填值；上市前、停牌与缺数不作相同解释',
                          'Y-1为近月交割年份的前一年，Y+0为交割当年；不是剩余到期日',
                          '占比使用当日已观测品种总持仓，不使用合约未来峰值',
                          '历史统计口径变化未调整；源窗口边界可能截断早期生命周期'])


def _contract(symbol):
    match = re.fullmatch(r'([A-Za-z]+)(\d{4})(?:\.[A-Za-z]+)?', symbol)
    if not match or match[2] in {'8888', '9998', '9999'}:
        raise ValueError('Use an actual, unambiguous four-digit month contract')
    try:
        anchor = date(2000 + int(match[2][:2]), int(match[2][2:]), 1)
    except ValueError as exc:
        raise ValueError('Invalid contract month') from exc
    return match[1].upper(), anchor


def _pair(near, far):
    product, first = _contract(near)
    other, second = _contract(far)
    if product != other or first >= second:
        raise ValueError('Legs must be same product, near month strictly before far month')


def _field(price_type):
    if price_type not in {'close', 'settle'}:
        raise ValueError('price_type must be close or settle')
    return 'settle_price' if price_type == 'settle' else 'close'


def _index(rows, symbols):
    indexed = {}
    for row in rows:
        if row['symbol'] not in symbols:
            continue
        day = date.fromisoformat(row['trade_date']).isoformat()
        key = (day, row['symbol'])
        if key in indexed:
            raise ValueError('Duplicate daily contract key: ' + str(key))
        indexed[key] = row
    return indexed


def _percentile(value, history):
    return 100 * (sum(x < value for x in history) + .5 * sum(x == value for x in history)) / len(history)


def spread_chart_series(rows, result):
    """Restore observed-product gaps for plotting; MA20 never bridges missing pairs."""
    points = result['series']
    if not points:
        return []
    by_day = {p['trade_date']: p for p in points}
    days = sorted({r['trade_date'] for r in rows
                   if points[0]['trade_date'] <= r['trade_date'] <= points[-1]['trade_date']})
    output, trailing = [], []
    for day in days:
        point = by_day.get(day)
        if point is None:
            trailing = []
            output.append({'trade_date':day,'spread':None,'ma20':None,
                           'near_price':None,'far_price':None,'historical_percentile':None})
            continue
        trailing = (trailing + [point['spread']])[-20:]
        output.append({**point, 'ma20':statistics.mean(trailing) if len(trailing)==20 else None})
    return output


def spread_series(rows, near, far, price_type='close', window=252):
    """Pair only shared dates; invalid/missing prices produce explicit gaps."""
    _pair(near, far)
    field = _field(price_type)
    if not isinstance(window, int) or isinstance(window, bool) or not 2 <= window <= 5000:
        raise ValueError('window must be an integer in [2, 5000]')
    indexed = _index(rows, {near, far})
    days = sorted({day for day, _ in indexed})
    series, spreads, changes, missing = [], [], [], []
    for day in days:
        first, second = indexed.get((day, near), {}), indexed.get((day, far), {})
        first_price, second_price = _number(first.get(field)), _number(second.get(field))
        if first_price is None or second_price is None:
            missing.append(day)
            continue
        spread = first_price - second_price
        history = spreads[-window:]
        std = statistics.stdev(history) if len(history) >= MIN_HISTORY else None
        change = spread-spreads[-1] if spreads else None
        point = dict(trade_date=day, near_price=first_price, far_price=second_price,
                     spread=spread, change=change, history_n=len(history),
                     historical_percentile=_percentile(spread, history) if len(history) >= MIN_HISTORY else None,
                     descriptive_percentile=_percentile(spread, (history + [spread])[-window:]),
                     z_score=(spread-statistics.mean(history))/std if std else None,
                     change_volatility=statistics.stdev(changes[-window:]) if len(changes) >= MIN_HISTORY else None,
                     near_volume=_number(first.get('volume')), far_volume=_number(second.get('volume')),
                     near_position=_number(first.get('position')), far_position=_number(second.get('position')))
        series.append(point)
        spreads.append(spread)
        if change is not None:
            changes.append(change)
    flags = ['eod_valuation_not_execution', 'contract_eligibility_not_verified']
    if missing:
        flags.append('missing_leg_or_price')
    if not series:
        flags.append('no_paired_data')
    elif series[-1]['history_n'] < MIN_HISTORY:
        flags.append('insufficient_history')
    elif series[-1]['z_score'] is None:
        flags.append('zero_variance')
    return dict(series=series, summary=dict(near=near, far=far, price_type=price_type,
                sample_count=len(series), latest=series[-1] if series else None,
                missing_dates=missing, quality_flags=flags, formula_version=FORMULA_VERSION,
                window=window, min_history=MIN_HISTORY, percentile_method='midrank_percent',
                change_basis='previous_paired_observation', statistics_basis='prior_observations_only'))


def spread_risk(series, observation_days):
    """Descriptive risk over the product window after the pair's first value.

    ``observation_days`` is the product-level date proxy.  Dates before the
    first valid common pair observation are outside the pair lifecycle and are
    therefore not treated as missing pair observations.  Dates after that
    point remain in the effective window, including internal and trailing
    missing values, so risk windows never bridge a known gap.
    """
    product_days = sorted(set(observation_days))[-253:]
    lookup = {p['trade_date']: _number(p.get('spread')) for p in series}
    pair_dates = sorted(day for day, value in lookup.items() if value is not None)
    pair_start = pair_dates[0] if pair_dates else None
    days = [day for day in product_days if pair_start is not None and day >= pair_start]
    values = [lookup.get(day) for day in days]
    product_window = dict(start=product_days[0] if product_days else None,
                          end=product_days[-1] if product_days else None,
                          n=len(product_days))
    effective_pair_window = dict(start=days[0] if days else None,
                                 end=days[-1] if days else None,
                                 n=len(days))
    excluded_pre_history = sum(day < pair_start for day in product_days) if pair_start else len(product_days)
    volatility = []
    for window in (20, 60):
        tail = values[-(window+1):]
        changes = [b-a for a, b in zip(tail, tail[1:]) if a is not None and b is not None]
        volatility.append(dict(window=window, n=len(changes),
            value=statistics.stdev(changes) if len(changes) == window else None))
    horizons = []
    for horizon in (5, 20):
        candidates, skipped = [], 0
        for i in range(max(0, len(values)-horizon)):
            path = values[i:i+horizon+1]
            if any(v is None for v in path):
                skipped += 1
                continue
            for direction in ('buy', 'sell'):
                adverse = [max(0, path[0]-v if direction == 'buy' else v-path[0]) for v in path]
                peak = max(adverse)
                candidates.append(dict(direction=direction, value=peak, entry_date=days[i],
                    adverse_date=days[i+adverse.index(peak)], end_date=days[i+horizon]))
        worst = {direction: max((c for c in candidates if c['direction'] == direction),
                                key=lambda c: c['value'], default=None) for direction in ('buy', 'sell')}
        horizons.append(dict(horizon=horizon, n=len(candidates)//2, skipped=skipped, **worst))
    return dict(as_of=days[-1] if days else None, start=days[0] if days else None,
                observed_dates=len(days), missing_dates=[d for d, v in zip(days, values) if v is None],
                product_window=product_window,
                effective_pair_window=effective_pair_window,
                excluded_pre_history=excluded_pre_history,
                volatility=volatility, horizons=horizons, unit='原始报价单位', version='spread-risk-v2-pair-window',
                warnings=['截止日收盘后描述性统计，包含截止日；不同于不含当日的历史分位',
                          '最近至多253个品种观测日；对子首个有效共同观测之前的日期不计入有效对子窗口',
                          '有效对子窗口保留生命周期内内部及尾部缺数；整品种同时缺数无法识别，已知缺口不跨越',
                          '波动率为20/60个连续观测日价差变化的样本标准差(ddof=1)，不足完整窗口不报值',
                          '不利变动以各窗口起点为基准，取持有期内最差日终值；不是仅看期末，也不是峰谷回撤',
                          '持有期窗口重叠，不是独立交易样本或策略回测',
                          '不含日内极值、费用与滑点，不换算账户盈亏；历史最差不是未来损失上限'])


def comparable_spread(rows, near, far, as_of, price_type='close'):
    """Compare a fixed near/far month pair on one calendar-season date.

    The current value is the latest valid common observation through
    ``as_of``.  Each earlier near-month delivery year contributes at most one
    actual same-day pair observation strictly before ``as_of``, within +/-15
    natural days of the current observation's month/day.  Contract month
    spacing is preserved, including a far leg in the following calendar year;
    no values are filled or stitched.
    """
    _pair(near, far)
    field = _field(price_type)
    cutoff = date.fromisoformat(as_of)
    product, near_anchor = _contract(near)
    _, far_anchor = _contract(far)
    month_gap = ((far_anchor.year - near_anchor.year) * 12
                 + far_anchor.month - near_anchor.month)
    if month_gap <= 0:
        raise ValueError('远月必须晚于近月')

    # Keep only actual contracts of the requested product and reject duplicate
    # source keys before any historical filtering.  Other products and the
    # synthetic catalogue symbols are outside this pair's fixed-month scope.
    by_symbol = {}
    delivery_years = set()
    for row in rows:
        try:
            row_product, anchor = _contract(row['symbol'])
        except (KeyError, ValueError):
            continue
        if row_product != product:
            continue
        day = date.fromisoformat(row['trade_date']).isoformat()
        series = by_symbol.setdefault(row['symbol'], {})
        if day in series:
            raise ValueError('Duplicate daily contract key: ' + str((day, row['symbol'])))
        series[day] = row
        delivery_years.add(anchor.year)

    def symbol_for(year, month):
        return f'{product}{year % 100:02d}{month:02d}'

    def pair_points(near_symbol, far_symbol):
        near_rows = by_symbol.get(near_symbol, {})
        far_rows = by_symbol.get(far_symbol, {})
        points = []
        for day in sorted(near_rows.keys() & far_rows.keys()):
            first = _number(near_rows[day].get(field))
            second = _number(far_rows[day].get(field))
            if first is not None and second is not None:
                points.append((date.fromisoformat(day), first-second))
        return points

    current_points = [(day, value) for day, value in pair_points(near, far)
                      if day <= cutoff]
    current = None
    if current_points:
        current_day, current_value = max(current_points, key=lambda item: item[0])
        current = dict(trade_date=current_day.isoformat(), spread=current_value)

    samples, excluded = [], []
    if current is not None:
        current_day = date.fromisoformat(current['trade_date'])
        historical_years = sorted(year for year in delivery_years
                                  if year < near_anchor.year)
        for year in historical_years:
            near_symbol = symbol_for(year, near_anchor.month)
            total_month = year * 12 + near_anchor.month - 1 + month_gap
            far_year, far_month = divmod(total_month, 12)
            far_symbol = symbol_for(far_year, far_month + 1)
            target_year = current_day.year + year - near_anchor.year
            try:
                target = current_day.replace(year=target_year)
            except ValueError:
                excluded.append(dict(year=year, near=near_symbol, far=far_symbol,
                                     reason='invalid_calendar_date'))
                continue
            candidates = [(day, value) for day, value in pair_points(near_symbol, far_symbol)
                          if day < cutoff and abs((day-target).days) <= 15]
            if not candidates:
                excluded.append(dict(year=year, near=near_symbol, far=far_symbol,
                                     target_date=target.isoformat(),
                                     reason='no_common_observation_within_15_days'))
                continue
            selected_day, selected_value = min(
                candidates, key=lambda item: (abs((item[0]-target).days), item[0]))
            samples.append(dict(year=year, near=near_symbol, far=far_symbol,
                                target_date=target.isoformat(),
                                trade_date=selected_day.isoformat(),
                                spread=selected_value,
                                distance_days=abs((selected_day-target).days)))

    values = [sample['spread'] for sample in samples]
    percentile = (_percentile(current['spread'], values)
                  if current is not None and len(values) >= 3 else None)
    years = [sample['year'] for sample in samples]
    status = ('no_current_observation' if current is None
              else 'available' if samples else 'no_historical_sample')
    current_stale = current is not None and current['trade_date'] < cutoff.isoformat()
    quality = (['no_current_observation'] if current is None else
               ['current_observation_before_as_of'] if current_stale else [])
    window_rule = ('保持观测年相对近月交割年的偏移，将当前观测月日映射到历史对应年份，'
                   '在前后±15个自然日内取最近真实共同观测；每个历史年度最多1条')
    warnings = [
        '仅按日历季节比较，不是准确到期日调整或剩余到期日比较',
        '当前有效共同观测可含as_of当日；历史样本观测严格早于as_of，且历史近月交割年度严格早于当前近月交割年度，当前及未来交割年度排除',
        '只使用同日双腿有效报价；不前填、不插值、不拼接，不把同一年度多个日期当作独立样本',
        '同季节描述统计不升级雷达方向或交易信号',
    ]
    return dict(near=near, far=far, price_type=price_type, as_of=cutoff.isoformat(),
                current=current, samples=samples, years=years,
                median=statistics.median(values) if values else None,
                percentile=percentile, percentile_method='midrank_percent' if percentile is not None else None,
                excluded=excluded, window_rule=window_rule, window_days=15,
                status=status, current_stale=current_stale, quality=quality,
                warnings=warnings)


def term_structure(rows, as_of, price_type='close'):
    """Observed contract nodes only; absent catalogue nodes cannot be inferred."""
    as_of = date.fromisoformat(as_of)
    field = _field(price_type)
    nodes, products, seen = [], set(), set()
    for row in rows:
        if row['trade_date'] != as_of.isoformat():
            continue
        try:
            product, anchor = _contract(row['symbol'])
        except ValueError:
            continue
        if row['symbol'] in seen:
            raise ValueError('Duplicate term structure contract')
        seen.add(row['symbol'])
        products.add(product)
        nodes.append(dict(symbol=row['symbol'], price=_number(row.get(field)),
                          delivery_anchor_estimate=anchor.isoformat(),
                          anchor_quality='contract_month_first_day_estimate',
                          days_to_anchor_estimate=(anchor-as_of).days,
                          volume=_number(row.get('volume')), position=_number(row.get('position'))))
    if len(products) > 1:
        raise ValueError('Term structure must contain one product')
    nodes.sort(key=lambda node: node['delivery_anchor_estimate'])
    adjacent, curvature = [], []
    for first, second in zip(nodes, nodes[1:]):
        valid = first['price'] is not None and second['price'] is not None
        adjacent.append(dict(near=first['symbol'], far=second['symbol'],
                             spread=first['price']-second['price'] if valid else None,
                             calendar_days_estimate=second['days_to_anchor_estimate']-first['days_to_anchor_estimate'],
                             annualized_price_slope=None))
    for left, middle, right in zip(nodes, nodes[1:], nodes[2:]):
        total = right['days_to_anchor_estimate']-left['days_to_anchor_estimate']
        weight = (middle['days_to_anchor_estimate']-left['days_to_anchor_estimate'])/total if total else None
        valid = weight is not None and all(node['price'] is not None for node in (left,middle,right))
        residual = middle['price'] - ((1-weight)*left['price']+weight*right['price']) if valid else None
        curvature.append(dict(left=left['symbol'], middle=middle['symbol'], right=right['symbol'],
                              residual=residual, anchor_quality='month_anchor_estimate'))
    return dict(nodes=nodes, adjacent=adjacent, curvature=curvature,
                summary=dict(as_of=as_of.isoformat(), price_type=price_type, formula_version=FORMULA_VERSION,
                             quality_flags=['expiry_dates_unverified', 'observed_contracts_only', 'not_trade_return']))


def term_structure_comparison(rows, as_of, price_type='close'):
    """Compare actual current contracts, without substituting expired/new nodes."""
    result = term_structure(rows, as_of, price_type)
    products = {_contract(node['symbol'])[0] for node in result['nodes']}
    days, historical_rows = set(), []
    for row in rows:
        if row['trade_date'] >= as_of:
            continue
        try:
            product, _ = _contract(row['symbol'])
        except ValueError:
            continue
        if product in products:
            days.add(row['trade_date'])
            historical_rows.append(row)
    days = sorted(days)
    comparisons = []
    for lag in (1, 5):
        previous = days[-lag] if len(days) >= lag else None
        historical = {node['symbol']: node for node in term_structure(
            historical_rows, previous, price_type)['nodes']} if previous else {}
        nodes = []
        for current in result['nodes']:
            price = historical.get(current['symbol'], {}).get('price')
            nodes.append(dict(symbol=current['symbol'], price=price,
                change=current['price']-price if current['price'] is not None and price is not None else None))
        comparisons.append(dict(lag=lag, as_of=previous, nodes=nodes,
            status='ok' if previous else 'insufficient_observed_dates'))
    result.update(comparisons=comparisons,
        comparison_basis='product_observed_dates_in_snapshot',
        comparison_warnings=['前1/5日为当前快照内品种观测日，不是已核验交易日历；整品种缺数无法识别',
            '仅比较当日真实合约；历史缺失不填充，不拼接替代合约；价格变化为当日减对照日',
            '快照日期范围不足时不提供对应历史对照；未核验精确到期日，不输出到期年化收益'])
    return result


def rollover_series(rows, old, new, price_type='close'):
    """Shares use the selected pair denominator, not all product open interest."""
    result = spread_series(rows, old, new, price_type)
    series, previous_oi, previous_volume = [], None, None
    for point in result['series']:
        values = {}
        for metric, first, second in [('oi',point['near_position'],point['far_position']),
                                      ('volume',point['near_volume'],point['far_volume'])]:
            valid = first is not None and second is not None and first >= 0 and second >= 0 and first+second > 0
            share = second/(first+second) if valid else None
            previous = previous_oi if metric == 'oi' else previous_volume
            values.update({f'old_{metric}': first, f'new_{metric}': second,
                           f'new_{metric}_share': share, f'old_{metric}_share': 1-share if share is not None else None,
                           f'new_{metric}_share_change': share-previous if share is not None and previous is not None else None})
            if metric == 'oi':
                previous_oi = share
            else:
                previous_volume = share
        series.append(dict(trade_date=point['trade_date'], spread=point['spread'],
                           long_roll_estimate=-point['spread'], short_roll_estimate=point['spread'], **values))
    return dict(series=series, summary=dict(result['summary'], latest=series[-1] if series else None,
                denominator='selected_pair', flow_inference='aggregate_OI_not_investor_transfer'))


def spread_cost_scenario(direction, entry_spread, exit_spread, lots, multiplier, total_cost=None):
    """Equal lots/multiplier only; explicit aggregate opening+closing costs in yuan."""
    if direction not in {'buy', 'sell'}:
        raise ValueError('direction must be buy or sell')
    entry, exit_, quantity, multiple, costs = map(_number, (entry_spread,exit_spread,lots,multiplier,total_cost))
    if entry is None or exit_ is None or quantity is None or quantity <= 0 or not quantity.is_integer():
        raise ValueError('Finite spreads and positive integer lots required')
    if multiplier is not None and (multiple is None or multiple <= 0):
        raise ValueError('multiplier must be positive or unknown')
    if total_cost is not None and (costs is None or costs < 0):
        raise ValueError('total_cost must be nonnegative or unknown')
    sign = 1 if direction == 'buy' else -1
    gross = sign*quantity*multiple*(exit_-entry) if multiple is not None else None
    known = gross is not None and costs is not None
    return dict(direction=direction, entry_spread=entry, exit_spread=exit_, lots=quantity,
                multiplier=multiple, total_cost=costs, gross_pnl=gross,
                net_pnl=gross-costs if known else None,
                breakeven_exit_spread=entry+sign*costs/(quantity*multiple) if known else None,
                quality_flags=[] if known else ['cost_or_multiplier_unknown'],
                status='hypothetical_not_account_pnl', formula_version=FORMULA_VERSION)


def quote_spread(near_quote, far_quote, now, max_age_seconds=10, max_skew_seconds=3):
    """Gate top-of-book estimates; even fresh quotes never guarantee two fills."""
    _pair(near_quote['symbol'], far_quote['symbol'])
    now = datetime.fromisoformat(now) if isinstance(now, str) else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    if max_age_seconds <= 0 or max_skew_seconds < 0:
        raise ValueError('Invalid freshness thresholds')
    flags, times = [], []
    for name, quote in [('near',near_quote),('far',far_quote)]:
        try:
            stamp = datetime.fromisoformat(quote.get('observed_at') or '')
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError('Naive quote timestamp')
            age = (now-stamp).total_seconds()
            if age < 0 or age > max_age_seconds:
                flags.append(name+'_stale_or_future_quote')
            times.append(stamp)
        except (ValueError, TypeError):
            flags.append(name+'_missing_quote_time')
        bid, ask = _number(quote.get('bid')), _number(quote.get('ask'))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
            flags.append(name+'_invalid_book')
        for field in ('bid_size','ask_size'):
            value = _number(quote.get(field))
            if value is None or value <= 0 or not value.is_integer():
                flags.append(name+'_missing_depth')
                break
        if quote.get('market_status') != 'trading':
            flags.append(name+'_not_confirmed_trading')
        try:
            date.fromisoformat(quote.get('trade_date') or '')
        except (ValueError, TypeError):
            flags.append(name+'_missing_trade_date')
    if near_quote.get('trade_date') != far_quote.get('trade_date'):
        flags.append('trade_date_mismatch')
    if len(times) == 2 and abs((times[0]-times[1]).total_seconds()) > max_skew_seconds:
        flags.append('quote_time_skew')
    valid = not flags
    buy = _number(near_quote['ask'])-_number(far_quote['bid']) if valid else None
    sell = _number(near_quote['bid'])-_number(far_quote['ask']) if valid else None
    return dict(buy_spread=buy, sell_spread=sell, long_roll_cost=-sell if valid else None,
                short_roll_cost=buy, buy_capacity=min(_number(near_quote['ask_size']),_number(far_quote['bid_size'])) if valid else None,
                sell_capacity=min(_number(near_quote['bid_size']),_number(far_quote['ask_size'])) if valid else None,
                status='indicative' if valid else 'unavailable', executable=False,
                quality_flags=flags, risk_note='Non-atomic legs; costs, eligibility and fills unverified',
                near_observed_at=near_quote.get('observed_at'), far_observed_at=far_quote.get('observed_at'),
                near_received_at=near_quote.get('received_at'), far_received_at=far_quote.get('received_at'),
                near_trade_date=near_quote.get('trade_date'), far_trade_date=far_quote.get('trade_date'),
                formula_version=FORMULA_VERSION)
