"""Local-only HTTP interface. Requests never start Wind or production ingestion."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import json
import math
import os
from hashlib import sha256
from pathlib import Path
import sqlite3
import duckdb
from urllib.parse import urlsplit
from typing import Optional
from threading import Lock
from time import perf_counter
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .transport import ResearchHTTP, fingerprint
from . import analytics
from .data import PRODUCT_NAMES, CONTRACT, FIELDS, INDEX_SERIES, DailyStore, clean, validate_product, fundamental_review, fundamental_analysis, fundamental_update_status, fundamental_candidates, index_daily, contract_terms


class Watch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product: str = Field(pattern=r'^[A-Z]{1,3}$')
    near: str = Field(pattern=r'^[A-Z]{1,3}\d{4}$')
    far: str = Field(pattern=r'^[A-Z]{1,3}\d{4}$')
    price_type: str = Field(default='close', pattern='^(close|settle)$')


class Scenario(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    direction: str = Field(pattern='^(buy|sell)$')
    entry_spread: float
    exit_spread: float
    lots: int = Field(ge=1, le=100000)
    multiplier: Optional[float] = Field(default=None, gt=0, le=1000000)
    total_cost: Optional[float] = Field(default=None, ge=0)


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    direction: Optional[str] = Field(default=None, pattern='^(buy|sell)$')
    rationale: str = Field(default='', max_length=2000)
    reference_spread: Optional[float] = None
    reference_date: Optional[date] = None
    snapshot_id: Optional[str] = Field(default=None, max_length=200)
    entry_spread: Optional[float] = None
    exit_spread: Optional[float] = None
    invalidation: str = Field(default='', max_length=2000)
    lots: int = Field(default=1, ge=1, le=100000)
    multiplier: Optional[float] = Field(default=None, gt=0, le=1000000)
    multiplier_source: Optional[str] = Field(default=None, max_length=300)
    total_cost: Optional[float] = Field(default=None, ge=0)
    review_date: Optional[date] = None


def radar_objects(item, family='all'):
    """Single object-level classification shared by API and UI; not a trade signal."""
    groups = {}
    for event in item['events']:
        if family == 'all' or event['family'] == family:
            groups.setdefault((event.get('near'), event.get('far')), []).append(event)
    if not groups and family == 'all' and not item['events']:
        groups[(None, None)] = []
    result = []
    finite = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    for (near, far), events in groups.items():
        mature = [e for e in events if not e.get('short') and (e.get('n',0)>=60 or e['kind']=='volume')]
        level = next((e for e in mature if e['kind']=='spread_level'), None)
        month = (item.get('as_of') or '').replace('-','')[2:6]
        delivery = bool(month and any(s and s[-4:]<=month for s in (near,far)))
        quality = item.get('quality', [])
        stale = item['level']=='数据待检查'
        blocked = stale or delivery or bool(quality)
        p = level.get('percentile') if level else None
        extreme = finite(p) and (p<=1 or p>=99)
        priority = not blocked and (extreme or len({e['family'] for e in mature})>=2)
        liquid = level and all(finite(level.get(k)) and level[k]>0 for k in ('weak_volume','weak_position'))
        directional = not blocked and liquid and near and far and finite(p) and (p<=5 or p>=95)
        direction = ('sell' if p>=95 else 'buy') if directional else None
        result.append({**item, 'product_summary_level':item['level'],
            'key':item['product']+':'+('/'.join((near or '',far or '')) if events else 'none'),
            'events':events, 'near':near, 'far':far, 'direction_code':direction,
            'bucket':'数据待检查' if stale else '未命中' if not events else '优先核查' if priority else '观察名单',
            'direction':f'卖 {near} / 买 {far}' if direction=='sell' else f'买 {near} / 卖 {far}' if direction=='buy' else '暂无方向',
            'limits':[*quality, *(['已进入合约月份：到期日与持仓资格待核验'] if delivery else []),
                *(['短样本'] if any(e.get('short') for e in events) else []),
                '均值回归假设；到期、成本与可成交性待核验' if direction else '仅凭量仓或价差突变不判断多空']})
    return result


def create_app(store=None):
    store = store or DailyStore()
    app = FastAPI(title='期货量价工作台', version='0.1.0')
    app.state.store = store
    radar_lock = Lock()
    def source_version(path):
        files = list((store.root.parent/'futuresfundamentals').glob('*.parquet')) if path == '/api/inventory' else store._files()
        if not files:
            files = list((store.runtime/'snapshots').glob('*.json'))
        if path.startswith('/api/fundamentals') or path == '/api/basis-reference':
            files += list((store.root.parent/'aindexeodprices').glob('year_month=*/*.parquet'))
            files += [p for name in ('fundamental_lab', 'ceic_lab')
                      for p in (store.runtime/name).rglob('*') if p.is_file()]
        return [date.today().isoformat(), fingerprint(files)]
    code_version = sha256(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))).hexdigest()
    app.add_middleware(ResearchHTTP, version=source_version, cache=store.cache, code_version=code_version, session=store.read_session,
        concurrency=int(os.environ.get('FUTURES_HTTP_CONCURRENCY', '1')),
        paths=['/api/'+name for name in ('products','contracts','dashboard','spread-history',
            'seasonal-position','contract-position','contract-history','basis-reference','inventory','radar','position-peaks',
            'fundamentals/analysis','fundamentals/review','fundamentals/candidates','data/futures_daily/rows')])
    public_hosts = [h.strip().lower() for h in os.environ.get('DASHBOARD_ALLOWED_HOSTS', '').split(',') if h.strip()]
    if any(any(c in h for c in '*:/') for h in public_hosts):
        raise ValueError('DASHBOARD_ALLOWED_HOSTS must contain explicit hostnames, without scheme, port or wildcard')
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver', *public_hosts])

    @app.middleware('http')
    async def local_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != request.headers.get('host', '').lower():
                return JSONResponse({'detail': '仅允许同源访问'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '禁止跨站访问'}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    def read(product, start=None, end=None):
        try:
            return store.read(product, start, end)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, '日线源不可用，且无对应历史快照') from exc

    def latest_contracts(snapshot):
        as_of = snapshot['meta']['as_of']
        return sorted([r for r in snapshot['rows'] if r['trade_date'] == as_of], key=lambda r: r['symbol'])

    def dashboard(product='RB', near=None, far=None, price_type='close', start=None, end=None, view='all'):
        if price_type not in ('close', 'settle'):
            raise ValueError('价格口径只允许 close 或 settle')
        snapshot = read(product, start, end)
        rows, meta = snapshot['rows'], dict(snapshot['meta'])
        contracts = latest_contracts(snapshot)
        known = {r['symbol'] for r in rows}
        for symbol in (near, far):
            if symbol and (not CONTRACT.fullmatch(symbol) or symbol not in known):
                raise ValueError('指定合约不在此品种及日期窗口中')
        liquid = sorted(contracts, key=lambda r: r.get('position') or 0, reverse=True)
        defaults = sorted([r['symbol'] for r in liquid[:2]])
        if near is None:
            near = defaults[0] if defaults else None
        if far is None:
            far = next((s for s in defaults if s != near), next((r['symbol'] for r in contracts if r['symbol'] != near), None))
        if near and far and near >= far:
            raise ValueError('近月必须早于远月，且两腿不能相同')
        meta.update({'price_type': price_type, 'units': {'price': '品种原始报价单位', 'volume': '手', 'position': '手', 'amount': '元'}})
        result = {'meta': meta, 'product': snapshot['product'], 'near': near, 'far': far,
                  'price_type': price_type, 'contracts': contracts}
        if view in ('all', 'spread', 'rollover'):
            spread = analytics.spread_series(rows, near, far, price_type) if near and far else {'series': [], 'summary': {}}
            result['spread'] = spread
        if view in ('all', 'spread'):
            result['risk'] = analytics.spread_risk(spread['series'], [r['trade_date'] for r in rows])
        if view in ('all', 'curve'):
            result['curve'] = analytics.term_structure_comparison(rows, meta['as_of'], price_type) if rows else {'nodes': [], 'adjacent': [], 'curvature': [], 'summary': {}, 'comparisons': []}
        if view in ('all', 'rollover'):
            result['rollover'] = analytics.rollover_series(rows, near, far, price_type) if near and far else {'series': [], 'summary': {}}
        if view in ('all', 'scan'):
            opportunities = []
            for a, b in zip(contracts, contracts[1:]):
                pair = analytics.spread_series(rows, a['symbol'], b['symbol'], price_type)
                trend = analytics.spread_chart_series(rows, pair)[-60:]
                opportunities.append({'near': a['symbol'], 'far': b['symbol'], **pair['summary'],
                    'trend':[{'trade_date':p['trade_date'],'spread':p['spread']} for p in trend]})
            result['opportunities'] = opportunities
        if view == 'all':
            result['contract_terms'] = contract_terms(snapshot['product'],[s for s in (near,far) if s],meta['as_of'])
            result['warnings'] = ['仅日终研究，非实时可成交报价', '合约目录来自已观测日线，未覆盖无成交挂牌合约',
                '基差页分列股指日收盘基差与CU周频参考价差；均非实时套利信号', '费用、保证金、到期日须逐品种核验']
        return clean(result)

    @app.get('/api/radar')
    def radar(end: Optional[date] = None, price_type: str = 'close',
              family: str = Query('all', pattern='^(all|spread|position|volume)$'),
              min_volume: int = Query(1000, ge=0, le=10000000),
              min_position: int = Query(1000, ge=0, le=10000000)):
        if price_type not in ('close','settle'):
            raise ValueError('价格口径只允许 close 或 settle')
        def response(result):
            objects = [o for item in result['items'] for o in radar_objects(item, family)]
            rank = {'优先核查':0,'观察名单':1,'数据待检查':2,'未命中':3}
            objects.sort(key=lambda o:(rank[o['bucket']],o['key']))
            return {**result, 'objects':objects, 'family':family}
        with radar_lock:
            started = perf_counter()
            try:
                snapshot = store.market_snapshot(end.isoformat() if end else None)
            except (OSError, RuntimeError, duckdb.Error) as exc:
                raise HTTPException(503, '全市场源读取失败，不将旧扫描冒充当前结果') from exc
            key = (snapshot['snapshot_id'], price_type, min_volume, min_position)
            cached = store.cache.get('radar-result')
            if cached and cached[0] == key:
                return response(cached[1])
            groups = {}
            for row in snapshot['rows']:
                product = row['symbol'][:-4]
                groups.setdefault(product, []).append(row)
            as_of = max((r['trade_date'] for r in snapshot['rows']), default=None)
            items, failures = [], []
            for product, rows in sorted(groups.items()):
                try:
                    items.append({'product':product, **analytics.radar_product(rows, as_of, price_type, min_volume, min_position)})
                except ValueError as exc:
                    failures.append({'product':product, 'reason':str(exc)})
            rank = {'重点关注':0,'普通观察':1,'短样本观察':2,'数据待检查':3,'未命中':4}
            items.sort(key=lambda r:(rank[r['level']],-r['family_count'],r['product']))
            objects = [o for item in items for o in radar_objects(item)]
            result = clean(dict(items=items,failures=failures,expected=len(groups),scanned=len(items),
                object_counts={'priority':sum(o['bucket']=='优先核查' for o in objects),
                    'abnormal_pairs':sum(bool(o['far'] and o['events'] and o['bucket']!='数据待检查') for o in objects)},
                as_of=as_of,requested_end=snapshot['requested_end'],start=snapshot['start'],
                snapshot_id=snapshot['snapshot_id'],price_type=price_type,min_volume=min_volume,
                min_position=min_position,version='radar-v2-object-rules',cached=False,
                generated_at=datetime.now().astimezone().isoformat(),seconds=round(perf_counter()-started,3),
                warnings=['范围为此前400个日历日已观测真实合约品种，并非交易所完整挂牌目录',
                    '基准排除当日；价差统计最多252个有效样本；不足60为短样本观察',
                    '日期连续性按品种观测日检查，无法识别整个品种同时缺数；未做季节与到期调整',
                    '分位和变动同属价差类，不重复计为多类共振；不提供胜率或买卖建议',
                    '成交比基准20期中位数；持仓绝对变动至少1000手且总量3%/活跃合约5%；远月占比变动至少3个百分点',
                    '仅本地量价初筛，不调用Wind、基本面、生产入口或RDS；无扫描历史状态比较']))
            store.cache['radar-result'] = (key, result)
            return response(result)

    @app.get('/api/position-peaks')
    def position_peaks(end: Optional[date] = None,
                       min_volume: int = Query(1000, ge=0, le=10000000),
                       min_position: int = Query(1000, ge=0, le=10000000)):
        try:
            snapshot = store.market_snapshot(end.isoformat() if end else None)
            as_of = max((r['trade_date'] for r in snapshot['rows']), default=None)
            peaks = store.position_peaks(as_of) if as_of else []
        except (OSError, RuntimeError, duckdb.Error) as exc:
            raise HTTPException(503, '历史持仓读取失败') from exc
        result = analytics.position_peak_ranking(snapshot['rows'], peaks, as_of, min_volume=min_volume, min_position=min_position)
        return clean({**result, 'items':[{**r, 'name':PRODUCT_NAMES.get(r['product'], '')} for r in result['items']],
                      'as_of':as_of, 'years':5, 'snapshot_id':snapshot['snapshot_id']})

    @app.get('/api/version')
    def version():
        try:
            if not store._files() and not any((store.runtime/'snapshots').glob('*.json')):
                raise HTTPException(503, '数据版本暂不可用')
            # Metadata stats only: reuse the API fingerprint, including basis inputs.
            value = sha256(repr([code_version, source_version('/api/basis-reference'), source_version('/api/inventory')]).encode()).hexdigest()
            return JSONResponse({'version': value}, headers={'Cache-Control': 'no-store'})
        except (OSError, ValueError) as exc:
            raise HTTPException(503, '数据版本暂不可用') from exc

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'mode': 'eod_readonly', 'daily_source_exists': store.root.is_dir(),
                'fundamentals': 'isolated_review', 'basis_reference':'index_close_and_CU_review',
                'wind': 'not_started', 'rds': 'not_connected'}

    @app.get('/api/products')
    def products():
        return {'products': [{**p, 'name':PRODUCT_NAMES.get(p['product'], '')} for p in store.products()], 'meta': {'source': 'local_parquet', 'scope': 'latest_partition'}}

    @app.get('/api/fundamentals/review')
    def review_fundamentals(code: Optional[str] = Query(None, pattern=r'^[SG]\d{7}$'),
                            start: Optional[date] = None, end: Optional[date] = None,
                            offset: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=5000)):
        try:
            return fundamental_review(store.runtime / 'fundamental_lab', code,
                start.isoformat() if start else None, end.isoformat() if end else None, offset, limit)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, '隔离基本面快照不可用或证据校验失败；不会自动取数') from exc

    @app.get('/api/fundamentals/analysis')
    def analyze_fundamentals(product: Optional[str] = Query(None, pattern=r'^[A-Z]{1,3}$'),
                             indicator: Optional[str] = Query(None, pattern=r'^(wind:[SG]\d{7}|ceic:\d+)$'),
                             end: Optional[date] = None):
        try:
            return clean(fundamental_analysis(store.runtime, product, indicator, end.isoformat() if end else None))
        except (OSError, RuntimeError, duckdb.Error) as exc:
            raise HTTPException(503, '基本面读取失败；旧数据不会伪装为本次结果，不触发取数') from exc

    @app.get('/api/fundamentals/status')
    def fundamental_status():
        return clean(fundamental_update_status(store.runtime / 'fundamental_lab'))

    @app.get('/api/fundamentals/candidates')
    def candidate_fundamentals(code: Optional[str] = Query(None, pattern=r'^[SG]\d{7}$'),
                               offset: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=5000)):
        try:
            return fundamental_candidates(store.runtime/'fundamental_lab', code, offset, limit)
        except (OSError, ValueError, KeyError, TypeError, duckdb.Error) as exc:
            raise HTTPException(503, '候选原始快照未准备好或校验失败；不自动取数、不影响已审阅数据') from exc

    @app.get('/api/contracts')
    def contracts(product: str = 'RB', start: Optional[date] = None, end: Optional[date] = None):
        snapshot = read(product, start.isoformat() if start else None, end.isoformat() if end else None)
        return {'contracts': latest_contracts(snapshot), 'meta': snapshot['meta']}

    @app.get('/api/dashboard')
    def get_dashboard(product: str = 'RB', near: Optional[str] = None, far: Optional[str] = None,
                      price_type: str = 'close', start: Optional[date] = None, end: Optional[date] = None,
                      view: str = Query('all', pattern='^(all|catalog|spread|curve|rollover|scan)$')):
        return dashboard(product, near, far, price_type, start.isoformat() if start else None, end.isoformat() if end else None, view)

    @app.get('/api/data/catalog')
    def catalog():
        return {'datasets': [{'name': 'futures_daily', 'operations': ['read'], 'fields': FIELDS}]}

    @app.get('/api/spread-history')
    def spread_history(product: str, near: str, far: str, price_type: str = 'close',
                       end: date = Query(...), start: Optional[date] = None,
                       span: str = Query('3m', pattern='^(1m|3m|1y|all|custom)$')):
        from datetime import timedelta
        validate_product(product)
        if any(not CONTRACT.fullmatch(s) or s[:-4] != product for s in (near,far)) or near >= far:
            raise ValueError('须为当前品种的不同真实近远月合约，且近月早于远月')
        if price_type not in ('close','settle') or (span=='custom' and start is None) or (start and start>end):
            raise ValueError('价格口径或日期范围无效')
        try:
            snapshot = store.read_all(product, end.isoformat())
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, '完整本地历史不可用，不以部分窗口冒充共同历史') from exc
        result = analytics.spread_series(snapshot['rows'], near, far, price_type)
        points = analytics.spread_chart_series(snapshot['rows'], result)
        cutoff = (start.isoformat() if span=='custom' else
                  (end-timedelta(days={'1m':30,'3m':90,'1y':365}[span])).isoformat() if span!='all' else snapshot['start'])
        visible = [p for p in points if p['trade_date']>=cutoff]
        valid = [p for p in visible if p['spread'] is not None]
        return clean({'meta':snapshot['meta'], 'near':near,'far':far,'price_type':price_type,
            'comparable':analytics.comparable_spread(snapshot['rows'],near,far,end.isoformat(),price_type),
            'span':span,'series':visible,'paired_count':len(valid),
            'first':valid[0]['trade_date'] if valid else None,'last':valid[-1]['trade_date'] if valid else None,
            'common_first':points[0]['trade_date'] if points else None,
            'common_last':points[-1]['trade_date'] if points else None,
            'warnings':['仅本地已观测的两腿共同历史，不拼接主力或其他合约',
                '近1月/3月/1年分别为30/90/365自然日窗口；分位使用此前最多252个有效配对，最少20个',
                '20期均线含当日；窗口切换保留历史预热；缺失配对断线并重置均线',
                '整品种缺失日期无法由观测日识别；图形不是交易信号']})

    @app.get('/api/inventory')
    def inventory(product: str, end: Optional[date] = None):
        product = validate_product(product)
        files = sorted((store.root.parent/'futuresfundamentals').glob('*.parquet'))
        if not files:
            raise HTTPException(503, '库存仓单数据尚未发布')
        rows = store._query(files, '''SELECT indicator,name,category,freq,unit,trade_date::VARCHAR observation_date,value
            FROM read_parquet(?) WHERE upper(product)=? AND trade_date<=?::DATE
            AND (starts_with(indicator,'F_WR_') OR starts_with(indicator,'F_ST_'))
            ORDER BY indicator,trade_date''', (product, (end or date.today()).isoformat()))
        series = {}
        for row in rows:
            key = row['indicator']
            if not row['unit'] or row['freq'] not in ('D','W'):
                raise HTTPException(503, '库存仓单单位或频率尚未确认')
            item = series.setdefault(key, dict(id=key,name=row['name'],kind=row['category'],unit=row['unit'],
                frequency=row['freq'],source='交易所公开报表 · RDS',points=[]))
            if (item['name'],item['kind'],item['unit'],item['frequency']) != (row['name'],row['category'],row['unit'],row['freq']):
                raise HTTPException(503, '同一库存序列存在口径冲突')
            if item['points'] and item['points'][-1]['date'] == row['observation_date']:
                raise HTTPException(503, '库存仓单存在重复日期')
            item['points'].append({'date':row['observation_date'],'value':row['value']})
        return {'product':product,'as_of':max((r['observation_date'] for r in rows),default=None),
                'preview':False,'history_scope':'all_published','series':list(series.values())}

    @app.get('/api/seasonal-position')
    def seasonal_position(product: str = 'RB', end: Optional[date] = None,
                          years: int = Query(5, ge=2, le=5), all_years: bool = False):
        if end is None:
            latest = read(product)
            if latest['meta']['as_of'] is None:
                raise ValueError('当前品种没有可用日期')
            end = date.fromisoformat(latest['meta']['as_of'])
        if all_years:
            try:
                snapshot = store.read_all(product, end.isoformat())
            except (OSError, RuntimeError) as exc:
                raise HTTPException(503, '全部历史范围不可用，不能按部分数据显示为全部') from exc
        else:
            snapshot = read(product, date(end.year-years+1, 1, 1).isoformat(), end.isoformat())
        return {'meta': {**snapshot['meta'], 'read_start': snapshot['start'],
                         'history_scope': 'all_local' if all_years else 'recent_years'},
                **analytics.seasonal_position(snapshot['rows'])}

    @app.get('/api/contract-position')
    def contract_position(product: str, symbol: str, start: Optional[date] = None, end: Optional[date] = None):
        product=validate_product(product)
        if not CONTRACT.fullmatch(symbol) or symbol[:-4]!=product:
            raise ValueError('所选合约不属于当前品种')
        snapshot=read(product,start.isoformat() if start else None,end.isoformat() if end else None)
        return clean({'meta':snapshot['meta'],'contracts':sorted({r['symbol'] for r in snapshot['rows']}),
            **analytics.contract_position(snapshot['rows'],symbol)})

    @app.get('/api/basis-reference')
    def basis_reference(product: str, symbol: Optional[str] = None, price_type: str = 'close',
                        start: Optional[date] = None, end: Optional[date] = None):
        product=validate_product(product)
        if price_type not in ('close','settle'):
            raise ValueError('价格口径只允许close/settle')
        if start and end and start>end:
            raise ValueError('开始日期不能晚于截止日期')
        is_index = product in INDEX_SERIES
        if product!='CU' and not is_index:
            need=('对应指数现货日线及其代码、日期和点位口径；当前本地目录未接入'
                if product in ('IM','IC','IF','IH') else
                '可交割券价格、应计利息和转换因子；不能使用商品基差公式'
                if product in ('T','TF','TS','TL') else '与期货可比较的现货代码、地区规格、单位及已验收序列')
            return {'status':'unavailable','product':product,'execution_eligible':False,'reason':need,
                'available_products':['CU',*INDEX_SERIES]}
        if is_index and price_type != 'close':
            return {'status':'unavailable','product':product,'execution_eligible':False,
                'reason':'股指基差仅用期货收盘价对指数收盘价；请将顶部价格口径切换为收盘。结算价不可视为同一收盘时点。'}
        if is_index and not (store.root.parent/'aindexeodprices').is_dir() and not (store.runtime/'fundamental_lab/index_review.json').exists():
            return {'status':'unavailable','product':product,'execution_eligible':False,
                'reason':'指数日收盘快照尚未验收；此页面不会自动取数。'}
        if symbol and (not CONTRACT.fullmatch(symbol) or symbol[:-4]!=product):
            raise ValueError('所选合约不属于当前品种')
        snapshot=read(product,start.isoformat() if start else None,end.isoformat() if end else None)
        contracts=latest_contracts(snapshot)
        symbol=symbol or next((r['symbol'] for r in sorted(contracts,key=lambda r:-(r.get('position') or 0))),None)
        if not symbol or symbol not in {r['symbol'] for r in snapshot['rows']}:
            raise ValueError('窗口中无所选真实合约')
        if is_index:
            try:
                review=index_daily(store.runtime/'fundamental_lab',product,snapshot['start'],snapshot['end'],store.root.parent/'aindexeodprices')
            except (OSError,RuntimeError,ValueError,KeyError,duckdb.Error) as exc:
                raise HTTPException(503,'指数快照不可用或校验失败；不会自动取数或替换指标') from exc
            item=next(i for i in review['indicators'] if i['product']==product)
            result=analytics.index_basis(snapshot['rows'],review['points'],symbol)
            return clean(dict(result,status='index_close_basis',product=product,symbol=symbol,
                contract_terms=contract_terms(product,[symbol],snapshot['meta']['as_of']),
                contracts=sorted({r['symbol'] for r in snapshot['rows']}),price_type='close',meta=snapshot['meta'],
                reference=item,reference_snapshot=review['sha256'],
                warnings=['基差 = 指数收盘 S − 期货收盘 F；正值为期货贴水，负值为期货升水',
                    '基差率 = (S−F)/S；不是年化收益率，不含资金、分红、成本或ETF跟踪误差',
                    '只比较同日收盘字段，未验证逐笔收盘同步；不使用期货结算价',
                    '真实单合约，不拼接主力；已观测生命周期内单边缺失保留断点',
                    '不参与雷达方向或排名；历史下载版本不等于当时可得，不作为无前视回测']))
        try:
            review=fundamental_review(store.runtime/'fundamental_lab',code='S5801814',
                start=snapshot['start'],end=snapshot['end'],limit=5000)
        except (OSError,RuntimeError,ValueError,duckdb.Error) as exc:
            raise HTTPException(503,'铜参考价审阅数据不可用；不会自动取数或替换指标') from exc
        item=next(i for i in review['indicators'] if i['code']=='S5801814')
        if (item.get('unit'),item.get('frequency'),item.get('role'))!=('元/吨','W','reference_price_not_delivery_basis') or review['next_offset'] is not None:
            raise HTTPException(503,'铜参考价口径或覆盖发生变化，需要重新验收')
        result=analytics.reference_basis(snapshot['rows'],review['points'],symbol,price_type)
        return clean(dict(result,status='reference_only',product=product,symbol=symbol,
            contracts=sorted({r['symbol'] for r in contracts}|{symbol}),price_type=price_type,meta=snapshot['meta'],
            reference=item,reference_snapshot=review['snapshot_sha256'],
            warnings=['仅周频参考价差，非上海即期现货或交割基差，不参与雷达排名',
                '参考价减同观测日期期货价；缺失不前填、不插值，未配对点不计算',
                '观测日期不是历史首次可得日期；不是时点回测或实时可成交信号',
                '连线连接实际参考价观测，不代表每天有现货报价；未调整税价、运费、仓储和资金成本']))

    @app.get('/api/contract-history')
    def contract_history(product: str = 'RB', mode: str = 'same_month',
                         years: int = Query(5, ge=1, le=10),
                         near_month: int = Query(10, ge=1, le=12),
                         far_month: int = Query(1, ge=1, le=12),
                         far_year_offset: int = Query(1, ge=0, le=99),
                         metric: str = 'position', price_type: str = 'close',
                         anchor_year: Optional[int] = Query(None, ge=2000, le=2099),
                         end: Optional[date] = None):
        if mode not in {'same_month', 'spread', 'overview'} or metric not in {'position', 'position_share', 'price', 'volume'}:
            raise ValueError('不支持的历史模式或指标')
        if price_type not in {'close', 'settle'}:
            raise ValueError('价格口径只允许close/settle')
        if mode == 'spread' and far_year_offset*12+far_month <= near_month:
            raise ValueError('远月必须晚于近月')
        if end is None:
            latest = read(product)
            if latest['meta']['as_of'] is None:
                raise ValueError('无可用日期')
            end = date.fromisoformat(latest['meta']['as_of'])
        anchor_year = anchor_year or end.year
        if mode == 'overview':
            anchor_year = end.year
        if anchor_year < end.year-1 or mode == 'spread' and anchor_year+far_year_offset > 2099:
            raise ValueError('对比末年不能早于截止年份前一年，远腿交割年份不能超过2099')
        start = date(min(anchor_year,end.year)-years-1, 1, 1)
        try:
            snapshot = store.read(product, start.isoformat(), end.isoformat(), max_years=12)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, '历史日线源不可用，且无对应快照') from exc
        if mode != 'overview':
            symbols = {r['symbol'] for r in snapshot['rows'] if r['symbol'][-4:] not in {'8888','9998','9999'}}
            latest_year = max((analytics._contract(s)[1].year for s in symbols), default=end.year)
            if anchor_year > latest_year:
                raise ValueError('对比末年不能晚于已观测合约的最远交割年份')
        return {'meta': {**snapshot['meta'], 'read_start': start.isoformat(), 'anchor_year': anchor_year,
                         'comparison_years': years},
                **analytics.contract_history(snapshot['rows'], mode, anchor_year, years,
                    near_month, far_month, far_year_offset, metric, price_type, end=end.isoformat())}

    @app.get('/api/data/futures_daily/rows')
    def raw_rows(request: Request, product: str = 'RB', start: Optional[date] = None,
                 end: Optional[date] = None, fields: str = 'exchange,symbol,trade_date,close,position',
                 page: int = Query(1, ge=1, le=100000), page_size: int = Query(200, ge=1, le=1000),
                 snapshot_id: Optional[str] = None):
        allowed = {'product', 'start', 'end', 'fields', 'page', 'page_size', 'snapshot_id'}
        if set(request.query_params) - allowed:
            raise ValueError('包含不支持的参数；禁止 SQL、表达式及任意排序')
        columns = fields.split(',')
        if len(columns) != len(set(columns)) or not set(columns).issubset(FIELDS):
            raise ValueError('字段不在白名单或重复')
        snapshot = read(product, start.isoformat() if start else None, end.isoformat() if end else None)
        if (date.fromisoformat(snapshot['end']) - date.fromisoformat(snapshot['start'])).days > 366:
            raise ValueError('通用明细接口最多一年')
        if page > 1 and snapshot_id is None:
            raise ValueError('后续分页必须携带第一页的 snapshot_id')
        if snapshot_id and snapshot_id != snapshot['meta']['snapshot_id']:
            raise HTTPException(409, '数据版本已改变，请从第一页重新读取')
        offset = (page - 1) * page_size
        selected = snapshot['rows'][offset:offset + page_size]
        return {'rows': [{k: r[k] for k in columns} for r in selected], 'meta': snapshot['meta'],
                'page': page, 'next_page': page + 1 if offset + page_size < len(snapshot['rows']) else None}

    @contextmanager
    def state_db():
        store.runtime.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(store.runtime / 'workbench.sqlite', timeout=5)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE IF NOT EXISTS watchlists (id INTEGER PRIMARY KEY, product TEXT NOT NULL, near TEXT NOT NULL, far TEXT NOT NULL, price_type TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(product,near,far,price_type))')
        columns = {r[1] for r in con.execute('PRAGMA table_info(watchlists)')}
        for name in ('plan_json','plan_updated_at'):
            if name not in columns:
                con.execute('ALTER TABLE watchlists ADD COLUMN '+name+' TEXT')
        try:
            with con:
                yield con
        finally:
            con.close()

    @app.get('/api/watchlists')
    def watches():
        with state_db() as con:
            items = []
            for row in con.execute('SELECT * FROM watchlists ORDER BY id'):
                item = dict(row)
                item['plan'] = json.loads(item.pop('plan_json') or 'null')
                items.append(item)
            return {'items':items}

    @app.post('/api/watchlists')
    def add_watch(watch: Watch):
        for leg in (watch.near, watch.far):
            if leg[:-4] != watch.product or leg[-4:] in ('8888', '9998', '9999'):
                raise ValueError('自选必须为同品种实际月合约')
        if watch.near >= watch.far:
            raise ValueError('近月必须早于远月')
        with state_db() as con:
            con.execute('INSERT OR IGNORE INTO watchlists(product,near,far,price_type) VALUES(?,?,?,?)',
                        (watch.product, watch.near, watch.far, watch.price_type))
        return watches()

    @app.delete('/api/watchlists/{watch_id}')
    def delete_watch(watch_id: int):
        with state_db() as con:
            con.execute('DELETE FROM watchlists WHERE id=?', (watch_id,))
        return watches()

    @app.patch('/api/watchlists/{watch_id}')
    def save_plan(watch_id: int, plan: ResearchPlan):
        with state_db() as con:
            changed = con.execute('UPDATE watchlists SET plan_json=?,plan_updated_at=? WHERE id=?',
                (plan.model_dump_json(),datetime.now().astimezone().isoformat(),watch_id))
            if not changed.rowcount:
                raise HTTPException(404, '自选组合不存在，请重新加载')
        return watches()

    @app.post('/api/scenario')
    def scenario(payload: Scenario):
        return analytics.spread_cost_scenario(**payload.model_dump())

    dist = Path(__file__).resolve().parents[1] / 'frontend' / 'dist' / 'client'
    if dist.is_dir():
        app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
    return app


app = create_app()
