from datetime import date
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.data import DailyStore


def test_isolated_fundamental_review_bounds_and_evidence(setup, monkeypatch):
    import hashlib
    import json
    from backend import data
    client, store, _ = setup
    folder = store.runtime / 'fundamental_lab'
    folder.mkdir(parents=True)
    metadata = {'production_enabled':False, 'indicators':[
        {'code':'S0049493', 'role':'warrant_reference', 'unit':'吨', 'frequency':'D',
         'limits':['历史发布时间未知']}, {'code':'S5012468', 'role':'quarantine'}]}
    raw = json.dumps(metadata).encode()
    (folder / data.REVIEW_METADATA).write_bytes(raw)
    path = folder / data.REVIEW_FILE
    with duckdb.connect() as con:
        con.execute('CREATE TABLE review AS SELECT ? AS metadata_sha256,false AS production_ready, '
                    "'S0049493' AS code,day AS observation_date,value AS raw_value FROM "
                    "(VALUES ('2026-09-01',123.5),('2026-09-02',125.0)) t(day,value)",
                    [hashlib.sha256(raw).hexdigest()])
        con.execute('COPY review TO ? (FORMAT PARQUET)', [str(path)])
    monkeypatch.setattr(data, 'REVIEW_SHA256', hashlib.sha256(path.read_bytes()).hexdigest())
    before = path.read_bytes()
    analysis = client.get('/api/fundamentals/analysis?product=CU&indicator=wind:S0049493&end=2026-09-01')
    assert analysis.status_code == 200
    assert analysis.json()['latest']['raw_value'] == 123.5
    assert analysis.json()['previous'] is None
    assert analysis.json()['indicators'][0]['basis_eligible'] is False
    assert client.get('/api/fundamentals/analysis?product=IM').json()['indicators'] == []
    assert client.get('/api/fundamentals/analysis?product=RB&indicator=wind:S0049493').status_code == 422
    catalog = client.get('/api/fundamentals/review').json()
    assert catalog['production_ready'] is False
    assert catalog['indicators'][0]['observations'] == 2
    assert catalog['excluded'][0]['code'] == 'S5012468'
    # A G-code must pass syntax validation and reach the snapshot membership guard.
    missing_g = client.get('/api/fundamentals/review?code=G8403835')
    assert missing_g.status_code == 422
    assert missing_g.json()['detail'] == '指标未进入隔离审阅快照'
    page = client.get('/api/fundamentals/review?code=S0049493&limit=1').json()
    assert page['total'] == 2 and page['next_offset'] == 1
    assert page['points'] == [{'observation_date':'2026-09-01', 'raw_value':123.5}]
    assert client.get('/api/fundamentals/review?code=S0049493&start=2026-09-02').json()['total'] == 1
    for query in ('code=S5012468', 'code=bad', 'limit=5001', 'offset=-1', 'start=2026-09-02&end=2026-09-01'):
        assert client.get('/api/fundamentals/review?'+query).status_code == 422
    assert path.read_bytes() == before
    (folder / data.REVIEW_METADATA).write_bytes(raw+b' ')
    assert client.get('/api/fundamentals/review').status_code == 503


def test_missing_fundamental_review_does_not_fetch_or_create_files(setup):
    client, store, _ = setup
    assert client.get('/api/fundamentals/review').status_code == 503
    assert not (store.runtime / 'fundamental_lab').exists()


def test_ceic_analysis_hash_gate_and_no_production_acceptance(setup):
    import hashlib
    import json
    client, store, _ = setup
    folder = store.runtime / 'ceic_lab'
    web = folder / 'web_review'
    web.mkdir(parents=True)
    raw, audit = b'{}', b'{"errors":[]}'
    (web / 'candidates.jsonl').write_bytes(raw)
    (web / 'audit.json').write_bytes(audit)
    sh, ah = hashlib.sha256(raw).hexdigest(), hashlib.sha256(audit).hexdigest()
    batch = folder / ('wide_'+hashlib.sha256((sh+ah).encode()).hexdigest()[:16])
    batch.mkdir()
    path = batch / 'candidates_M.parquet'
    with duckdb.connect() as con:
        con.execute("COPY (SELECT DATE '2026-07-01' period_date, 0.0::DOUBLE s_529645757) TO ? (FORMAT PARQUET)", [str(path)])
    report = {'accepted':False,'production_write':False,'source_jsonl_sha256':sh,'source_audit_sha256':ah,
        'tables':[{'frequency':'M','difference_rows':0,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'indicators':[{'series_id':'529645757','name':'原油港口库存','unit':'万吨','source':'test','observations':1,'first':'2026-07-01','last':'2026-07-01'}]}]}
    (batch / 'audit.json').write_text(json.dumps(report),encoding='utf-8')
    before = path.read_bytes()
    response = client.get('/api/fundamentals/analysis?product=SC&indicator=ceic:529645757&end=2026-09-09')
    assert response.status_code == 200
    result = response.json()
    assert result['latest']['raw_value'] == 0
    assert result['age_days'] == 70 and result['freshness'] == 'release_calendar_unverified'
    assert result['indicators'][0]['usage'] == 'technical_candidate_only'
    assert not result['indicators'][0]['replay_eligible']
    assert result['failures'][0]['source'] == 'Wind'
    assert path.read_bytes() == before
    pointer = folder / 'selected_review.json'
    pointer.write_text(json.dumps({'review_root':'web_review', 'source_sha256':sh, 'audit_sha256':ah,
                                   'accepted':False, 'production_write':False}), encoding='utf-8')
    assert client.get('/api/fundamentals/analysis?product=SC&indicator=ceic:529645757').json()['latest']['raw_value'] == 0
    invalid = json.loads(pointer.read_text())
    invalid['source_sha256'] = 'invalid'
    pointer.write_text(json.dumps(invalid), encoding='utf-8')
    failed = client.get('/api/fundamentals/analysis?product=SC').json()
    assert not failed['indicators'] and any(f['source']=='CEIC' for f in failed['failures'])
    pointer.unlink()
    path.write_bytes(b'corrupt')
    catalog = client.get('/api/fundamentals/analysis?product=SC').json()
    assert not catalog['indicators'] and any(f['source']=='CEIC' for f in catalog['failures'])


def test_contract_position_endpoint_targets_symbol_and_preserves_source(setup):
    client,store,path=setup
    before=path.read_bytes()
    d=client.get('/api/contract-position?product=RB&symbol=RB2701&end=2026-09-02').json()
    assert d['symbol']=='RB2701' and d['latest']['position']==200 and d['latest']['change']==0
    assert client.get('/api/contract-position?product=RB&symbol=CU2610').status_code==422
    assert client.get('/api/contract-position?product=RB&symbol=RB9998').status_code==422
    assert path.read_bytes()==before


def test_index_basis_real_local_snapshot_and_settle_rejection(setup,monkeypatch):
    from tools import fundamental_lab as lab
    from backend.data import INDEX_SERIES
    client,store,path=setup
    monkeypatch.setattr(lab,'ROOT',store.runtime.parent)
    names=dict(INDEX_SERIES.values())
    metadata=lab.save_run({'production_write':False,'state':'completed',
        'request':{'api':'wss','fields':'sec_name'},'results':[
            {'code':c,'name':n,'error_code':0,'issues':[]} for c,n in names.items()]})
    raw=lab.save_run({'production_write':False,'state':'completed',
        'request':{'api':'wsd','fields':'close','options':'','codes':list(names),'start':'2026-09-01','end':'2026-09-02'},
        'results':[{'code':c,'error_code':0,'issues':[],'observations':[
            {'date':'2026-09-01','value':8000,'missing':False},
            {'date':'2026-09-02','value':8001,'missing':False}]} for c in names]})
    report=lab.index_review([raw],metadata,'2026-09-01','2026-09-02',publish=True)
    with duckdb.connect() as con:
        con.execute('CREATE TABLE daily AS SELECT * FROM read_parquet(?)',[str(path)])
        for p in INDEX_SERIES:
            con.execute("INSERT INTO daily SELECT * REPLACE ('CFFEX' AS exchange,? AS symbol,8000 AS close,9000 AS settle_price) FROM daily WHERE symbol='RB2610'",[p+'2612'])
        con.execute('COPY daily TO ? (FORMAT PARQUET)',[str(path)])
    before=path.read_bytes(),(raw.parent/report['file']).read_bytes()
    for product in INDEX_SERIES:
        r=client.get('/api/basis-reference',params={'product':product,'start':'2026-09-01','end':'2026-09-02'})
        assert r.status_code==200,r.text
        d=r.json()
        assert d['status']=='index_close_basis' and d['reference']['unit']=='指数点'
        assert d['matched']==2 and d['latest']['reference_spread']==1
        assert d['latest']['basis_ratio']==1/8001 and not d['execution_eligible']
        terms=d['contract_terms']
        assert terms['contracts'][0]['expected_last_trade_date']=='2026-12-18'
        dashboard=client.get('/api/dashboard',params={'product':product,'start':'2026-09-01','end':'2026-09-02'}).json()
        assert dashboard['contract_terms']==terms
        assert client.get('/api/basis-reference',params={'product':product,'price_type':'settle'}).json()['status']=='unavailable'
    assert client.get('/api/basis-reference?product=IM&symbol=IF2612').status_code==422
    assert (path.read_bytes(),(raw.parent/report['file']).read_bytes())==before
    (raw.parent/report['file']).write_bytes(b'corrupt')
    assert client.get('/api/basis-reference?product=IM').status_code==503


def test_basis_reference_only_uses_pinned_cu_and_reports_unavailable(setup,monkeypatch):
    import hashlib,json
    from backend import data
    client,store,path=setup
    for product in ('IM','RB','T'):
        d=client.get('/api/basis-reference?product='+product).json()
        assert d['status']=='unavailable' and d['execution_eligible'] is False
    assert client.get('/api/basis-reference?product=CU&end=2026-09-02').status_code==503
    assert not (store.runtime/'fundamental_lab').exists()
    folder=store.runtime/'fundamental_lab'; folder.mkdir()
    item={'code':'S5801814','role':'reference_price_not_delivery_basis','frequency':'W','unit':'元/吨','limits':['非交割基差']}
    raw=json.dumps({'production_enabled':False,'indicators':[item]}).encode()
    (folder/data.REVIEW_METADATA).write_bytes(raw)
    review_path=folder/data.REVIEW_FILE
    with duckdb.connect() as con:
        con.execute("CREATE TABLE review AS SELECT ? metadata_sha256,false production_ready,'S5801814' code,'2026-09-02' observation_date,81000.0 raw_value",[hashlib.sha256(raw).hexdigest()])
        con.execute('COPY review TO ? (FORMAT PARQUET)',[str(review_path)])
    monkeypatch.setattr(data,'REVIEW_SHA256',hashlib.sha256(review_path.read_bytes()).hexdigest())
    before=path.read_bytes(),review_path.read_bytes()
    d=client.get('/api/basis-reference?product=CU&end=2026-09-02').json()
    assert d['status']=='reference_only' and d['latest']['reference_spread']==1000
    assert d['matched']==1 and d['execution_eligible'] is False
    assert client.get('/api/basis-reference?product=CU&price_type=settle&end=2026-09-02').json()['latest']['reference_spread']==998
    assert client.get('/api/basis-reference?product=CU&symbol=RB2701').status_code==422
    assert (path.read_bytes(),review_path.read_bytes())==before
    (folder/data.REVIEW_METADATA).write_bytes(raw+b' ')
    assert client.get('/api/basis-reference?product=CU&end=2026-09-02').status_code==503


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'source' / 'futureshistoryprices1d'
    part = root / 'year_month=2026-09'
    part.mkdir(parents=True)
    path = part / 'data.parquet'
    with duckdb.connect() as con:
        con.execute("""CREATE TABLE daily AS SELECT 'SHFE' exchange, symbol,
            CAST(trade_date AS TIMESTAMP) trade_datetime, CAST(trade_date AS DATE) trade_date,
            price AS open, price+1 AS high, price-1 AS low, price AS close,
            100.0 volume, 10000.0 amount, 200.0 AS "position",
            price+2 AS settle_price, price+1 AS pre_settle_price
            FROM (VALUES ('RB2610','2026-09-01',3500.0),('RB2701','2026-09-01',3450.0),
                         ('RB2610','2026-09-02',3510.0),('RB2701','2026-09-02',3455.0),
                         ('RB9999','2026-09-02',9999.0),('CU2610','2026-09-02',80000.0))
                 AS t(symbol,trade_date,price)""")
        con.execute('COPY daily TO ? (FORMAT PARQUET)', [str(path)])
    store = DailyStore(root, tmp_path / 'runtime')
    return TestClient(create_app(store)), store, path


def test_dashboard_real_fixture_and_no_source_write(setup):
    client, store, path = setup
    before = path.read_bytes()
    response = client.get('/api/dashboard?product=RB&start=2026-09-01&end=2026-09-02')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['near'] == 'RB2610' and data['far'] == 'RB2701'
    assert data['spread']['summary']['latest']['spread'] == 55
    assert data['risk']['as_of'] == '2026-09-02'
    assert data['risk']['volatility'][0]['value'] is None
    assert data['risk']['horizons'][0]['n'] == 0
    previous, five = data['curve']['comparisons']
    assert previous['as_of'] == '2026-09-01'
    assert [node['change'] for node in previous['nodes']] == [10, 5]
    assert five['status'] == 'insufficient_observed_dates'
    assert five['as_of'] is None
    assert len(data['contracts']) == 2
    assert data['meta']['row_count'] == 4
    assert path.read_bytes() == before
    assert list((store.runtime / 'snapshots').glob('*.json'))


def test_source_hash_reused_across_products_and_invalidated_on_replace(setup, monkeypatch):
    import hashlib
    from backend import data
    _, store, path = setup
    calls = []
    original = data.file_digest
    def digest(source, algorithm):
        calls.append(source.name)
        return original(source, algorithm)
    monkeypatch.setattr(data, 'file_digest', digest)
    first = store.read('RB', '2026-09-01', '2026-09-02')
    other = store.read('CU', '2026-09-01', '2026-09-02')
    assert len(calls) == 1 and first['files'] == other['files']
    updated = path.with_suffix('.next')
    with duckdb.connect() as con:
        con.execute('CREATE TABLE corrected AS SELECT * REPLACE(close+10 AS close) FROM read_parquet(?)', [str(path)])
        con.execute('COPY corrected TO ? (FORMAT PARQUET)', [str(updated)])
    updated.replace(path)
    corrected = store.read('RB', '2026-09-01', '2026-09-02')
    assert len(calls) == 2
    assert corrected['files'][0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert corrected['files'] != first['files']
    assert corrected['rows'][0]['close'] == first['rows'][0]['close'] + 10


def test_radar_bounded_read_cache_and_source_safety(setup):
    client, store, path = setup
    before=path.read_bytes()
    result=client.get('/api/radar?end=2026-09-02').json()
    assert result['expected']==result['scanned']==2
    assert result['as_of']=='2026-09-02' and result['failures']==[]
    again=client.get('/api/radar?end=2026-09-02')
    assert again.json()==result
    assert client.get('/api/radar?end=2026-09-02',headers={'If-None-Match':again.headers['etag']}).status_code==304
    assert client.get('/api/radar?end=2026-09-02&price_type=settle').headers['etag']!=again.headers['etag']
    assert path.read_bytes()==before
    assert not store.runtime.exists()
    assert client.get('/api/radar?end=2026-09-01').json()['as_of']=='2026-09-01'
    for query in ('price_type=bad','min_volume=-1','min_position=1.5','end=bad'):
        assert client.get('/api/radar?'+query).status_code==422


def test_http_same_day_revision_deletion_and_public_no_snapshot_writes(setup, monkeypatch):
    client, store, path = setup
    store.persist_queries = False
    url='/api/dashboard?product=RB&start=2026-09-01&end=2026-09-02'
    original=client.get(url)
    assert original.status_code==200
    query=store._query
    monkeypatch.setattr(store,'_query',lambda *a,**kw:pytest.fail('304 must not query DuckDB'))
    store.cache.clear()
    assert client.get(url,headers={'If-None-Match':original.headers['etag']}).status_code==304
    monkeypatch.setattr(store,'_query',query)
    replacement=path.with_name('replacement.tmp')
    with duckdb.connect() as con:
        con.execute('CREATE TABLE corrected AS SELECT * FROM read_parquet(?)',[str(path)])
        con.execute("UPDATE corrected SET close=close+10 WHERE symbol='RB2610'")
        con.execute("DELETE FROM corrected WHERE symbol='RB2701' AND trade_date=DATE '2026-09-01'")
        con.execute('COPY corrected TO ? (FORMAT PARQUET)',[str(replacement)])
    replacement.replace(path)
    corrected=client.get(url,headers={'If-None-Match':original.headers['etag']})
    assert corrected.status_code==200 and corrected.headers['etag']!=original.headers['etag']
    assert corrected.json()['meta']['row_count']==3
    assert corrected.json()['meta']['as_of']==original.json()['meta']['as_of']
    assert corrected.json()['spread']!=original.json()['spread']
    assert not store.runtime.exists()
    # Security remains outside the response cache.
    assert client.get(url,headers={'Origin':'https://evil.example','If-None-Match':corrected.headers['etag']}).status_code==403


def test_duckdb_resource_limits_and_cache_default(setup):
    from backend.data import query_connection
    _, store, _=setup
    assert store.cache.limit==96*1024**2
    with query_connection() as con:
        assert con.execute("SELECT current_setting('threads')").fetchone()[0]==1
        assert con.execute("SELECT current_setting('memory_limit')").fetchone()[0]=='244.1 MiB'
        assert con.execute("SELECT current_setting('max_temp_directory_size')").fetchone()[0]=='0 bytes'


def test_radar_partial_duplicate_failure_and_source_loss(setup,monkeypatch):
    client,store,path=setup
    original=store.market_snapshot
    snapshot=original('2026-09-02')
    snapshot={**snapshot,'snapshot_id':'duplicate','rows':snapshot['rows']+[snapshot['rows'][0]]}
    monkeypatch.setattr(store,'market_snapshot',lambda end: snapshot)
    d=client.get('/api/radar?end=2026-09-02').json()
    assert d['expected']==2 and d['scanned']==1
    assert d['failures'][0]['product']=='RB'
    monkeypatch.setattr(store,'market_snapshot',original)
    path.unlink()
    assert client.get('/api/radar?end=2026-09-02').status_code==503


def test_health_does_not_require_source(tmp_path):
    client = TestClient(create_app(DailyStore(tmp_path / 'missing', tmp_path / 'runtime')))
    assert client.get('/api/health').json()['wind'] == 'not_started'
    assert client.get('/api/products').json()['products'] == []
    assert not (tmp_path / 'runtime').exists()


def test_spread_history_real_pair_ranges_and_validation(setup):
    client,store,path=setup
    before=path.read_bytes()
    url='/api/spread-history?product=RB&near=RB2610&far=RB2701&end=2026-09-02'
    result=client.get(url+'&span=all').json()
    assert [p['spread'] for p in result['series']]==[50,55]
    assert result['paired_count']==2 and result['first']=='2026-09-01'
    assert all(p['ma20'] is None for p in result['series'])
    assert client.get(url+'&span=custom&start=2026-09-02').json()['paired_count']==1
    assert client.get(url+'&price_type=settle').json()['series'][-1]['spread']==55
    for suffix in ('&span=bad','&span=custom','&price_type=bad','&start=2027-01-01'):
        assert client.get(url+suffix).status_code==422
    assert client.get(url.replace('near=RB2610','near=CU2610')).status_code==422
    assert path.read_bytes()==before


def test_all_seasonal_history_includes_old_years_and_respects_cutoff(setup):
    client, store, path = setup
    before = path.read_bytes()
    old = store.root / 'year_month=2010-01' / 'data.parquet'
    old.parent.mkdir()
    with duckdb.connect() as con:
        con.execute("CREATE TABLE old AS SELECT * FROM read_parquet(?) WHERE symbol='RB2610' LIMIT 1", [str(path)])
        con.execute("UPDATE old SET symbol='RB1005', trade_date='2010-01-04', trade_datetime='2010-01-04'")
        con.execute('COPY old TO ? (FORMAT PARQUET)', [str(old)])
    old_before = old.read_bytes()
    url = '/api/seasonal-position?product=RB&end=2026-09-02'
    assert client.get(url).json()['years'] == [2026]
    response = client.get(url+'&all_years=true')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['years'] == [2010, 2026]
    assert [l['year'] for l in data['lines']] == data['years']
    assert data['meta']['history_scope'] == 'all_local'
    assert data['observations'][0]['position'] == 200
    assert client.get(url.replace('2026-09-02','2020-01-01')+'&all_years=true').json()['years'] == [2010]
    assert path.read_bytes() == before and old.read_bytes() == old_before
    with pytest.raises(ValueError):
        store.read('RB','2010-01-01','2026-09-02')


def test_all_seasonal_source_unavailable_does_not_claim_full_coverage(tmp_path):
    client = TestClient(create_app(DailyStore(tmp_path/'missing', tmp_path/'runtime')))
    assert client.get('/api/seasonal-position?product=IM&end=2026-09-07&all_years=true').status_code == 503


def test_contract_history_modes_and_unchanged_source(setup):
    client, store, path = setup
    before = path.read_bytes()
    base = '/api/contract-history?product=RB&end=2026-09-02&anchor_year=2026&years=5&near_month=10'
    same = client.get(base).json()
    assert same['lines'][0]['label'] == 'RB2610'
    assert same['lines'][0]['points'][0]['value'] == 200
    share = client.get(base + '&metric=position_share').json()
    assert share['lines'][0]['points'][0]['value'] == .5
    spread = client.get(base + '&mode=spread&far_month=1&far_year_offset=1').json()
    assert [p['value'] for p in spread['lines'][0]['points']] == [50, 55]
    assert spread['seasonal_summary']['cutoff'] == '2026-09-02'
    assert spread['seasonal_summary']['current']['value'] == 55
    assert spread['seasonal_summary']['status'] == 'no_historical_sample'
    assert same['seasonal_summary'] is None
    overview = client.get(base + '&mode=overview&metric=price').json()
    assert len(overview['lines']) == 2
    assert overview['axis'] == ['2026-09-01', '2026-09-02']
    assert client.get(base.replace('years=5', 'years=10')).status_code == 200
    assert path.read_bytes() == before
    cut = client.get(base.replace('end=2026-09-02', 'end=2026-09-01')).json()
    assert all(p['trade_date'] <= '2026-09-01' for line in cut['lines'] for p in line['points'])


@pytest.mark.parametrize('query', ['years=11', 'near_month=13', 'metric=sql',
    'mode=spread&near_month=10&far_month=1&far_year_offset=0',
    'mode=unknown', 'price_type=last', 'anchor_year=2040'])
def test_contract_history_guards(setup, query):
    client, _, _ = setup
    assert client.get('/api/contract-history?product=RB&end=2026-09-02&'+query).status_code == 422


@pytest.mark.parametrize('query', [
    'fields=product', 'fields=close;DROP TABLE daily', 'sql=select+1', 'page_size=1001',
    'start=2020-01-01&end=2026-09-02', 'product=RB%27', 'page=2',
])
def test_query_guards(setup, query):
    client, _, _ = setup
    assert client.get('/api/data/futures_daily/rows?' + query).status_code == 422


def test_stable_pages_and_revision_conflict(setup):
    client, _, _ = setup
    url = '/api/data/futures_daily/rows?product=RB&start=2026-09-01&end=2026-09-02&page_size=2'
    first = client.get(url).json()
    second = client.get(url + '&page=2&snapshot_id=' + first['meta']['snapshot_id']).json()
    assert first['next_page'] == 2 and second['next_page'] is None
    assert first['rows'][0]['trade_date'] != second['rows'][0]['trade_date']
    assert client.get(url + '&snapshot_id=incorrect').status_code == 409


def test_watchlist_persistence_and_origin_guard(setup):
    client, store, _ = setup
    payload = {'product': 'RB', 'near': 'RB2610', 'far': 'RB2701', 'price_type': 'close'}
    assert client.post('/api/watchlists', json=payload, headers={'Origin': 'https://evil.test'}).status_code == 403
    assert client.get('/api/health', headers={'host': 'evil.test'}).status_code == 400
    assert len(client.post('/api/watchlists', json=payload).json()['items']) == 1
    client.post('/api/watchlists', json=payload)
    second = TestClient(create_app(DailyStore(store.root, store.runtime)))
    items = second.get('/api/watchlists').json()['items']
    assert len(items) == 1
    assert second.delete('/api/watchlists/' + str(items[0]['id'])).json()['items'] == []


def test_watch_research_plan_persistence_validation_and_no_source_write(setup):
    client, store, path = setup
    before = path.read_bytes()
    payload = {'product':'RB','near':'RB2610','far':'RB2701','price_type':'close'}
    saved = client.post('/api/watchlists', json=payload).json()['items'][0]
    plan = {'direction':'buy','rationale':'季节对比待验证','reference_spread':55,
            'reference_date':'2026-09-02','snapshot_id':'fixture-snapshot',
            'entry_spread':52,'exit_spread':70,'invalidation':'跌破45后复核',
            'lots':1,'multiplier':None,'multiplier_source':None,'total_cost':None,'review_date':'2026-09-03'}
    url = '/api/watchlists/'+str(saved['id'])
    updated = client.patch(url, json=plan)
    assert updated.status_code == 200
    second = TestClient(create_app(DailyStore(store.root, store.runtime)))
    item = second.get('/api/watchlists').json()['items'][0]
    assert item['plan'] == plan and item['plan_updated_at']
    # Ordinary re-adding a favourite must not erase its saved plan.
    assert client.post('/api/watchlists', json=payload).json()['items'][0]['plan'] == plan
    assert client.patch(url, json={**plan,'multiplier':0}).status_code == 422
    provenance={**plan,'multiplier':10,'multiplier_source':'explicit user reference'}
    assert client.patch(url,json=provenance).json()['items'][0]['plan']==provenance
    assert client.patch(url,json={**plan,'multiplier_source':'x'*301}).status_code==422
    assert client.patch(url, json={**plan,'direction':'automatic_order'}).status_code == 422
    assert client.patch(url, json={**plan,'near':'CU2610'}).status_code == 422
    assert client.patch('/api/watchlists/99999', json=plan).status_code == 404
    assert client.patch(url,json=plan,headers={'Origin':'https://evil.test'}).status_code == 403
    result = client.post('/api/scenario', json={k:plan[k] for k in (
        'direction','entry_spread','exit_spread','lots','multiplier','total_cost')})
    assert result.status_code == 200 and result.json()['net_pnl'] is None
    assert path.read_bytes() == before


def test_radar_object_rules_are_authoritative_and_family_specific():
    from backend.api import radar_objects
    event = dict(kind='spread_level', family='spread', near='IM2610',far='IM2612',
                 n=80,short=False,percentile=99,weak_volume=2000,weak_position=2000)
    item = dict(product='IM',as_of='2026-09-11',level='普通观察',quality=[],events=[event])
    obj = radar_objects(item)[0]
    assert obj['bucket'] == '优先核查' and obj['direction_code'] == 'sell'
    assert obj['product_summary_level'] == '普通观察'
    assert radar_objects(item,'position') == []
    for changed in [dict(short=True,n=25),dict(weak_volume=None),dict(percentile=None)]:
        assert radar_objects({**item,'events':[{**event,**changed}]})[0]['direction_code'] is None
    assert radar_objects({**item,'as_of':'2026-10-01'})[0]['direction_code'] is None
    assert radar_objects({**item,'quality':['字段缺失']})[0]['bucket'] == '观察名单'
    mixed = {**item,'events':[{**event,'percentile':96},
        {**event,'near':'IM2611','family':'position','kind':'migration'}]}
    assert all(o['bucket']=='观察名单' for o in radar_objects(mixed))


def test_radar_family_endpoint_reuses_scan_and_exposes_object_counts(setup):
    client, _, _ = setup
    all_data=client.get('/api/radar?end=2026-09-02&min_volume=0&min_position=0').json()
    filtered=client.get('/api/radar?end=2026-09-02&min_volume=0&min_position=0&family=position').json()
    assert filtered['family']=='position'
    assert filtered['object_counts']==all_data['object_counts']
    assert all(e['family']=='position' for o in filtered['objects'] for e in o['events'])
    assert client.get('/api/radar?family=bad').status_code==422


def test_fundamental_coverage_states_and_unknown_fetch_times(setup, monkeypatch):
    from backend import data
    client, _, _ = setup
    monkeypatch.setattr(data,'fundamental_review',lambda *a,**kw:{'snapshot_sha256':'fixture','indicators':[
        {'code':'S0049493','name':'库存期货:阴极铜','role':'warrant_reference',
         'last':'2026-09-09','frequency':'D','unit':'吨','limits':['发布时间未知']},
        {'code':'S5801814','name':'全国铜参考价','role':'national_weekly_price_reference',
         'last':'2026-09-04','frequency':'W','unit':'元/吨','limits':['非上海单一地区']}]})
    monkeypatch.setattr(data,'ceic_review_catalog',lambda *a:[])
    result=client.get('/api/fundamentals/analysis?product=CU&end=2026-08-01').json()
    coverage={c['category']:c for c in result['coverage']}
    assert len(coverage)>=4 and coverage['commercial_inventory']['indicators']==[]
    item=coverage['exchange_inventory']['indicators'][0]
    assert item['last_observation']=='2026-09-09'  # Current stored version, not fabricated PIT availability.
    assert item['last_fetch_at'] is None and item['release_rule'] is None
    assert all(not i['basis_eligible'] and not i['replay_eligible'] for i in result['indicators'])
    financial=client.get('/api/fundamentals/analysis?product=IM').json()
    assert {c['category'] for c in financial['coverage']}=={'financial_underlying','carrying_cost'}
    assert financial['coverage'][1]['indicators']==[]


def test_rb_ceic_associations_are_review_only(setup, monkeypatch):
    from backend import data
    from tools.fundamental_lab import _CEIC_REQUIREMENT_ASSOCIATIONS
    client, _, _ = setup
    ids = ['531905027', '531952197', '531952557']
    monkeypatch.setattr(data, 'ceic_review_catalog', lambda *a: [
        {'code': sid, 'name': sid, 'frequency': 'D', 'unit': '万吨', 'last': '2026-09-11'}
        for sid in ids])
    result = client.get('/api/fundamentals/analysis?product=RB').json()
    ceic = [i for i in result['indicators'] if i['provider'] == 'CEIC']
    assert {i['code'] for i in ceic} == set(ids)
    assert all(not i['basis_eligible'] and not i['replay_eligible'] for i in ceic)
    groups = {c['category']: c for c in result['coverage']}
    assert groups['supply_demand']['status'] == '技术候选未准入'
    assert [i['id'] for i in groups['supply_demand']['indicators']] == ['ceic:531905027']
    assert sum(i['provider'] == 'CEIC' for i in groups['commercial_inventory']['indicators']) == 2
    assert all(_CEIC_REQUIREMENT_ASSOCIATIONS[sid]['products'] == ['RB'] for sid in ids)


def test_ceic_failure_never_becomes_missing_inventory(setup, monkeypatch):
    from backend import data
    client, _, _ = setup
    monkeypatch.setattr(data, 'fundamental_review', lambda *a, **kw: {'indicators':[]})
    def failed(*args):
        raise ValueError('snapshot hash mismatch')
    monkeypatch.setattr(data, 'ceic_review_catalog', failed)
    result = client.get('/api/fundamentals/analysis?product=SA').json()
    assert len(result['coverage']) == 4
    assert all(c['status'] == '数据待检查' for c in result['coverage'])
    assert result['failures'][0]['source'] == 'CEIC'
    assert '不代表CEIC检查或实际取数时间' in result['coverage_note']


@pytest.mark.parametrize('product,code,name', [
    ('BU','540064367','沥青:社会库存'),
    ('HC','532023257','热轧卷板:社会库存:33个城市'),
])
def test_social_inventory_is_not_warrant_or_basis(setup, monkeypatch, product, code, name):
    from backend import data
    from tools.fundamental_lab import _CEIC_REQUIREMENT_ASSOCIATIONS
    client, _, _ = setup
    monkeypatch.setattr(data, 'ceic_review_catalog', lambda *a: [
        {'code':code,'name':name,'frequency':'D','unit':'万吨','last':'2026-09-11'}])
    result = client.get('/api/fundamentals/analysis?product='+product).json()
    groups = {c['category']:c for c in result['coverage']}
    assert groups['commercial_inventory']['indicators'][0]['id'] == 'ceic:'+code
    assert not groups['exchange_inventory']['indicators']
    assert not result['indicators'][0]['basis_eligible']
    assert '不前填' in result['indicators'][0]['limits'][-1]
    assert _CEIC_REQUIREMENT_ASSOCIATIONS[code]['requirement_ids'] == ['REQ-'+product+'-INV']
    assert not client.get('/api/fundamentals/analysis?product=SC').json()['indicators']


def test_ceic_supply_expansion_preserves_product_and_business_boundaries(setup, monkeypatch):
    from backend import data
    from tools.fundamental_lab import _CEIC_REQUIREMENT_ASSOCIATIONS
    client, _, _ = setup
    mapping = {'317697101': ['I'], '51906201': ['P'], '6130901': ['B', 'M', 'Y']}
    monkeypatch.setattr(data, 'ceic_review_catalog', lambda *a: [
        {'code': sid, 'name': sid, 'frequency': 'D' if sid == '317697101' else 'M',
         'unit': '千吨', 'last': '2026-07-01'} for sid in mapping])
    for sid, products in mapping.items():
        assert _CEIC_REQUIREMENT_ASSOCIATIONS[sid]['products'] == products
        for product in products:
            result = client.get('/api/fundamentals/analysis', params={'product': product}).json()
            entries = [i for i in result['indicators'] if i['provider'] == 'CEIC']
            assert len(entries) == 1 and entries[0]['code'] == sid
            assert not entries[0]['basis_eligible'] and not entries[0]['replay_eligible']
            assert entries[0]['limits'][-1] == data._CEIC_SERIES_LIMITS[sid][0]
            category = 'commercial_inventory' if product == 'I' else 'supply_demand'
            group = next(c for c in result['coverage'] if c['category'] == category)
            assert any(i['id'] == 'ceic:' + sid for i in group['indicators'])
    # Imported soybeans are not silently treated as domestic food-grade A or CU data.
    for product in ['A', 'CU']:
        result = client.get('/api/fundamentals/analysis', params={'product': product}).json()
        assert not any(i['provider'] == 'CEIC' for i in result['indicators'])


def test_ceic_warrants_keep_original_unit_and_separate_inventory(setup, monkeypatch):
    from backend import data
    from tools.fundamental_lab import _CEIC_REQUIREMENT_ASSOCIATIONS
    client, _, _ = setup
    pairs = [["567837947","MA"],["567837857","RS"],["567837817","CF"],["567837827","CY"],["567837937","PK"],["567837997","UR"],["567837807","SR"],["567837957","FG"],["567837847","OI"],["567837927","CJ"],["567838007","SA"],["567838027","PX"],["567837977","SF"],["567837987","SM"],["567838037","SH"],["567837837","RM"],["567838047","PR"],["567838017","PF"]]
    monkeypatch.setattr(data, 'ceic_review_catalog', lambda *a: [
        {'code': sid, 'name': sid, 'frequency': 'D', 'unit': '张', 'last': '2026-09-14'}
        for sid, _ in pairs])
    for sid, product in pairs:
        assert _CEIC_REQUIREMENT_ASSOCIATIONS[sid]['products'] == [product]
        assert _CEIC_REQUIREMENT_ASSOCIATIONS[sid]['requirement_ids'] == ['REQ-' + product + '-WARRANT']
        result = client.get('/api/fundamentals/analysis', params={'product': product}).json()
        entries = [i for i in result['indicators'] if i['provider'] == 'CEIC']
        assert len(entries) == 1 and entries[0]['code'] == sid
        assert entries[0]['unit'] == '张' and entries[0]['frequency'] == 'D'
        assert not entries[0]['basis_eligible'] and not entries[0]['replay_eligible']
        assert '不换算为吨' in entries[0]['limits'][-1]
        groups = {c['category']: c for c in result['coverage']}
        assert any(i['id'] == 'ceic:' + sid for i in groups['exchange_inventory']['indicators'])
        assert not any(i['id'] == 'ceic:' + sid for i in groups['commercial_inventory']['indicators'])
    assert not (set(data._CEIC_CZCE_WARRANT_PRODUCTS) & data._CEIC_NBS_PRICE_CODES)


def test_ceic_chemical_references_do_not_become_delivery_basis(setup, monkeypatch):
    from backend import data
    from tools.fundamental_lab import _CEIC_REQUIREMENT_ASSOCIATIONS
    client, _, _ = setup
    mapping = {'304989404': 'MA', '695751297': 'FG', '304989304': 'SH', '3672301': 'SA', '110269001': 'TA'}
    monkeypatch.setattr(data, 'ceic_review_catalog', lambda *a: [
        {'code': sid, 'name': sid, 'frequency': 'M' if sid in ('3672301', '110269001') else 'D',
         'unit': '千吨' if sid == '3672301' else '人民币/吨', 'last': '2026-09-10'} for sid in mapping])
    for sid, product in mapping.items():
        assert _CEIC_REQUIREMENT_ASSOCIATIONS[sid]['products'] == [product]
        result = client.get('/api/fundamentals/analysis', params={'product': product}).json()
        entries = [i for i in result['indicators'] if i['provider'] == 'CEIC']
        assert len(entries) == 1 and entries[0]['code'] == sid
        assert not entries[0]['basis_eligible'] and not entries[0]['replay_eligible']
        assert entries[0]['limits'][-1] == data._CEIC_SERIES_LIMITS[sid][0]
        category = 'supply_demand' if product == 'SA' else 'reference_spot'
        group = next(c for c in result['coverage'] if c['category'] == category)
        assert any(i['id'] == 'ceic:' + sid for i in group['indicators'])
    result = client.get('/api/fundamentals/analysis?product=SI').json()
    assert not any(i['provider'] == 'CEIC' for i in result['indicators'])


def test_ceic_nbs_prices_preserve_reference_only_mapping(setup, monkeypatch):
    from backend import data
    from tools.fundamental_lab import _CEIC_REQUIREMENT_ASSOCIATIONS
    client, _, _ = setup
    pairs = [('304988904','AL'), ('304989104','ZN'), ('304992304','A'), ('304992504','PK'),
             ('304990304','PG'), ('478005147','J'), ('695751317','SR'), ('478005157','SP'),
             ('304990004','BR'), ('304992204','LH'), ('304992604','UR'), ('478005127','PP'),
             ('304992404','M'), ('447225787','RU'), ('695751307','PS'), ('304992004','C'),
             ('304989504','BZ'), ('304992104','CF'), ('478005107','HC'), ('478005117','L'),
             ('447225767','JM')]
    monkeypatch.setattr(data, 'ceic_review_catalog', lambda *a: [
        {'code': sid, 'name': sid, 'frequency': 'D', 'last': '2026-09-10',
         'unit': '人民币/公斤' if product in ('LH','PS') else '人民币/吨'} for sid, product in pairs])
    for sid, product in pairs:
        assert _CEIC_REQUIREMENT_ASSOCIATIONS[sid]['products'] == [product]
        result = client.get('/api/fundamentals/analysis', params={'product': product}).json()
        item, = [i for i in result['indicators'] if i['provider'] == 'CEIC']
        assert item['code'] == sid and item['frequency'] == 'D'
        assert not item['basis_eligible'] and not item['replay_eligible']
        assert any('旬度参考价' in text for text in item['limits'])
        assert item['unit'] == ('人民币/公斤' if product in ('LH','PS') else '人民币/吨')
        group = next(c for c in result['coverage'] if c['category'] == 'reference_spot')
        assert any(i['id'] == 'ceic:' + sid for i in group['indicators'])
    for product in ('B', 'SI', 'NR', 'TA'):
        result = client.get('/api/fundamentals/analysis', params={'product': product}).json()
        assert not any(i['provider'] == 'CEIC' for i in result['indicators'])


def test_index_coverage_distinguishes_missing_pointer_from_broken_artifact(setup):
    client,store,_=setup
    before=client.get('/api/fundamentals/analysis?product=IM').json()
    assert before['coverage'][0]['status']=='缺少指标'
    folder=store.runtime/'fundamental_lab'
    folder.mkdir(parents=True,exist_ok=True)
    # Existing pointer with absent referenced artifact is a data failure, not an empty catalogue.
    import json
    (folder/'index_review.json').write_text(json.dumps({'kind':'index_daily_review','state':'completed',
        'production_enabled':False,'execution_eligible':False,'file':'index_'+('a'*64)+'.parquet',
        'metadata':'identity.json'}),encoding='utf-8')
    failed=client.get('/api/fundamentals/analysis?product=IM').json()
    assert failed['coverage'][0]['status']=='数据待检查'
    assert any(f['source']=='Index' for f in failed['failures'])


def test_raw_candidates_are_separate_paged_and_never_business_inputs(setup):
    import hashlib, json
    client,store,_=setup
    folder=store.runtime/'fundamental_lab'
    folder.mkdir(parents=True)
    path=folder/('candidates_'+('a'*64)+'.parquet')
    with duckdb.connect() as con:
        con.execute('CREATE TABLE c(code VARCHAR, observation_date VARCHAR, raw_value DOUBLE, source_files VARCHAR)')
        con.executemany('INSERT INTO c VALUES (?,?,?,?)',[
            ('S0031875','2026-08-03',0,json.dumps(['E:/private/data.parquet'])),
            ('S0031875','2026-08-04',10,json.dumps(['E:/private/data.parquet']))])
        con.execute('COPY c TO ? (FORMAT PARQUET)',[str(path)])
    report={'kind':'unverified_candidate_snapshot','identity_status':'unverified',
        'production_enabled':False,'basis_eligible':False,'replay_eligible':False,'signal_eligible':False,
        'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'observations':2,'sources':{'E:/private/data.parquet':'b'*64},
        'indicators':[{'code':'S0031875','claims':[{'name':'suspected spot'}],'references':['OLD-001'],
                       'observations':2,'first':'2026-08-03','last':'2026-08-04'}]}
    pointer=folder/'candidate_review.json'
    pointer.write_text(json.dumps(report),encoding='utf-8')
    before=pointer.read_bytes(),path.read_bytes()
    page=client.get('/api/fundamentals/candidates?code=S0031875&limit=1').json()
    assert page['mode']=='unverified_candidates_only' and page['next_offset']==1
    assert page['points'][0]['raw_value']==0
    assert page['points'][0]['source_sha256s']==['b'*64]
    assert 'E:/private' not in json.dumps(page)
    assert page['indicators'][0]['unit'] is None and not page['basis_eligible']
    assert client.get('/api/fundamentals/candidates?code=S0031875&limit=1&offset=1').json()['next_offset'] is None
    assert client.get('/api/fundamentals/candidates?limit=5001').status_code==422
    assert client.get('/api/fundamentals/candidates?code=../bad').status_code==422
    assert client.get('/api/fundamentals/review').status_code==503  # No accepted snapshot created by candidate access.
    assert (pointer.read_bytes(),path.read_bytes())==before
    report['basis_eligible']=True
    pointer.write_text(json.dumps(report),encoding='utf-8')
    assert client.get('/api/fundamentals/candidates').status_code==503
    pointer.write_bytes(before[0]);path.write_bytes(b'corrupt')
    assert client.get('/api/fundamentals/candidates').status_code==503


def test_raw_candidates_overlay_same_code_terminal_metadata_and_fail_closed_on_missing_evidence(setup):
    import hashlib, json
    from backend import data
    client,store,_=setup
    folder=store.runtime/'fundamental_lab'
    folder.mkdir(parents=True)
    path=folder/('candidates_'+('b'*64)+'.parquet')
    source_name='fixture.parquet'
    with duckdb.connect() as con:
        con.execute('CREATE TABLE c(code VARCHAR, observation_date VARCHAR, raw_value DOUBLE, source_files VARCHAR)')
        con.executemany('INSERT INTO c VALUES (?,?,?,?)',[
            ('S0031715','2026-08-03',0,json.dumps([source_name])),
            ('S0031715','2026-08-04',10,json.dumps([source_name])),
            ('S0031716','2026-08-03',20,json.dumps([source_name]))])
        con.execute('COPY c TO ? (FORMAT PARQUET)',[str(path)])
    report={'kind':'unverified_candidate_snapshot','identity_status':'unverified',
        'production_enabled':False,'basis_eligible':False,'replay_eligible':False,'signal_eligible':False,
        'request_coverage_known':False,'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'observations':3,'sources':{source_name:'c'*64},
        'indicators':[{'code':code,'claims':[{'name':'old '+code}],'references':['OLD'],
                       'observations':2 if code=='S0031715' else 1,
                       'first':'2026-08-03','last':'2026-08-04' if code=='S0031715' else '2026-08-03'}
                      for code in ('S0031715','S0031716')]}
    pointer=folder/'candidate_review.json'
    pointer.write_text(json.dumps(report),encoding='utf-8')
    evidence=folder/'terminal_metadata_evidence_20260914_01.png'
    evidence.write_bytes(b'fixture evidence')
    evidence_hash=hashlib.sha256(evidence.read_bytes()).hexdigest()
    metadata={'kind':'terminal_field_evidence_not_production_registry',
        'production_enabled':False,'basis_eligible':False,'replay_eligible':False,
        'signal_eligible':False,'request_coverage_known':False,
        'field_status':'terminal_default_display_confirmed',
        'current_display_scope':'current display only','unique_codes':2,
        'duplicate_rows':{'S0031715':2},
        'classification_counts':{'reference_candidate':2,'futures_excluded':0,
                                  'background':0,'unknown':0},
        'evidence':[{'file':evidence.name,'sha256':evidence_hash,
                     'displayed_codes':['S0031715','S0031715','S0031716']}],
        'indicators':[
            {'code':'S0031715','name':'当前棉花','frequency':'D','unit':'元/吨',
             'display_start':'2002-06-17','display_end':'2026-09-14',
             'display_updated_on':'2026-09-14','source':'全国棉花交易市场',
             'classification':'reference_candidate','products':['CF'],
             'limits':['current only'],'current_display_only':True,
             'evidence':{'files':[evidence.name],'source_sha256':[evidence_hash]}},
            {'code':'S0031716','name':'当前棉花2','frequency':'D','unit':'元/吨',
             'display_start':'2002-06-17','display_end':'2026-09-14',
             'display_updated_on':'2026-09-14','source':'全国棉花交易市场',
             'classification':'reference_candidate','products':['CF'],
             'limits':['current only'],'current_display_only':True,
             'evidence':{'files':[evidence.name],'source_sha256':[evidence_hash]}}]}
    metadata_path=folder/data.TERMINAL_METADATA_FILE
    metadata_path.write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
    before=path.read_bytes()
    page=client.get('/api/fundamentals/candidates?code=S0031715&limit=1')
    assert page.status_code==200,page.text
    payload=page.json()
    assert payload['terminal_metadata']['unique_codes']==2
    assert payload['terminal_metadata']['counts']['reference_candidate']==2
    assert payload['indicators'][0]['terminal_metadata']['identity_status']=='same_code_current_display_confirmed'
    assert payload['indicators'][0]['name'] is None
    assert payload['indicators'][0]['unit'] is None and payload['indicators'][0]['frequency'] is None
    assert payload['indicators'][0]['terminal_metadata']['unit']=='元/吨'
    assert payload['indicators'][0]['terminal_metadata']['products']==['CF']
    assert payload['indicators'][0]['source_metadata_claims']==[]
    assert payload['points'][0]['raw_value']==0 and payload['next_offset']==1
    second=client.get('/api/fundamentals/candidates?code=S0031715&limit=1&offset=1').json()
    assert second['points'][0]['raw_value']==10 and second['next_offset'] is None
    assert path.read_bytes()==before
    evidence.unlink()
    assert client.get('/api/fundamentals/candidates').status_code==503


def test_fundamental_status_isolated_states_keep_counts_and_checkpoint(setup):
    import json
    client, store, _ = setup
    folder = store.runtime / 'fundamental_lab'
    folder.mkdir(parents=True)
    daily = {'kind':'independent_daily_review', 'state':'completed',
             'observed_at':'2026-09-12T05:22:40+00:00', 'requested_end':'2026-09-11',
             'production_enabled':False, 'rds_write':False, 'scheduler_enabled':False,
             'executed':True, 'network_fetch_enabled':True, 'pending_requests':[],
             'release_check_requests':[{'code':'S0049493'}], 'waterlines':{'S0049493':'2026-09-09'}}
    checkpoint = {'state':'failed', 'production_enabled':False,
                  'scope':{'start':'2021-01-01','end':'2026-09-09'}}
    (folder/'daily_review_status.json').write_text(json.dumps(daily), encoding='utf-8')
    (folder/'update_checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
    completed = client.get('/api/fundamentals/status').json()
    assert completed['state']=='completed' and completed['label']=='隔离检查完成（非生产发布）'
    assert completed['counts']=={'pending_requests':0,'release_check_requests':1,'awaiting_release':None}
    assert completed['network_fetch_enabled'] is True
    assert completed['checkpoint']['state']=='failed'
    assert completed['checkpoint']['label']=='上次执行失败'

    daily.update(state='awaiting_release', executed=True, network_fetch_enabled=False,
                 awaiting_release=[{'code':'S0049493'}])
    (folder/'daily_review_status.json').write_text(json.dumps(daily), encoding='utf-8')
    waiting = client.get('/api/fundamentals/status').json()
    assert waiting['state']=='awaiting_release' and waiting['label']=='等待发布'
    assert waiting['counts']['awaiting_release']==1

    daily.update(state='plan_only', executed=False, network_fetch_enabled=False)
    (folder/'daily_review_status.json').write_text(json.dumps(daily), encoding='utf-8')
    (folder/'update.lock').write_text('owner', encoding='utf-8')
    planned = client.get('/api/fundamentals/status').json()
    assert planned['state']=='plan_only' and planned['label']=='仅生成计划'
    assert planned['checkpoint']['state']=='failed' and planned['checkpoint']['lock_present'] is True


def test_fundamental_status_preserves_real_failed_record_with_optional_details_missing(setup):
    import json
    client, store, _ = setup
    folder = store.runtime / 'fundamental_lab'
    folder.mkdir(parents=True)
    (folder/'daily_review_status.json').write_text(json.dumps({
        'kind':'independent_daily_review', 'state':'failed',
        'observed_at':'2026-09-12T05:22:40+00:00', 'requested_end':'2026-09-11',
        'error':'details intentionally omitted', 'production_enabled':False,
        'rds_write':False, 'scheduler_enabled':False}), encoding='utf-8')
    result = client.get('/api/fundamentals/status')
    assert result.status_code == 200
    data = result.json()
    assert data['state']=='failed' and data['label']=='上次执行失败'
    assert data['checked_at'].endswith('+00:00') and data['requested_end']=='2026-09-11'
    assert data['executed'] is None and data['network_fetch_enabled'] is None
    assert data['waterlines']==[] and all(value is None for value in data['counts'].values())
    assert data['has_error'] is True and 'details intentionally omitted' not in json.dumps(data, ensure_ascii=False)


def test_fundamental_status_rejects_bad_records_but_is_200_without_market_data(tmp_path):
    import json
    from backend import data
    runtime = tmp_path / 'runtime'
    folder = runtime / 'fundamental_lab'
    folder.mkdir(parents=True)
    daily = {'state':'completed', 'observed_at':'2026-09-12T05:22:40+00:00',
             'requested_end':'2026-02-30', 'production_enabled':False, 'rds_write':False,
             'scheduler_enabled':False, 'executed':True, 'network_fetch_enabled':False,
             'pending_requests':[], 'release_check_requests':[], 'waterlines':{}}
    checkpoint = {'state':'completed', 'production_enabled':False,
                  'scope':{'start':'2021-01-01','end':'2026-09-09'}}
    status_path, checkpoint_path = folder/'daily_review_status.json', folder/'update_checkpoint.json'
    status_path.write_text(json.dumps(daily), encoding='utf-8')
    checkpoint_path.write_text(json.dumps(checkpoint), encoding='utf-8')
    before = status_path.read_bytes(), checkpoint_path.read_bytes()
    client = TestClient(create_app(DailyStore(tmp_path/'no-market-data', runtime)))
    result = client.get('/api/fundamentals/status')
    assert result.status_code == 200
    assert result.json()['state']=='invalid' and result.json()['requested_end'] is None
    assert any('ISO日期' in issue for issue in result.json()['issues'])
    assert (status_path.read_bytes(), checkpoint_path.read_bytes()) == before

    status_path.write_bytes(b'{bad json')
    broken = client.get('/api/fundamentals/status').json()
    assert broken['state']=='invalid' and broken['checkpoint']['state']=='completed'
    status_path.unlink()
    missing = client.get('/api/fundamentals/status').json()
    assert missing['state']=='missing' and missing['checkpoint']['state']=='completed'
    assert missing['counts']=={'pending_requests':None,'release_check_requests':None,'awaiting_release':None}
    assert data.fundamental_update_status(folder)['production_enabled'] is False

    status_path.write_text(json.dumps({'state':[], 'observed_at':'2026-09-12T05:22:40+00:00',
        'requested_end':'2026-09-11', 'production_enabled':False, 'rds_write':False,
        'scheduler_enabled':False, 'waterlines':{}}), encoding='utf-8')
    invalid_state = client.get('/api/fundamentals/status').json()
    assert invalid_state['state']=='invalid' and invalid_state['checkpoint']['state']=='completed'

    status_path.write_text(json.dumps({'state':'completed', 'observed_at':'2026-09-12T05:22:40+00:00',
        'requested_end':'2026-09-11', 'production_enabled':False, 'rds_write':False,
        'scheduler_enabled':False, 'waterlines':{'C:/secret':'2026-09-11'}}), encoding='utf-8')
    invalid_code = client.get('/api/fundamentals/status').json()
    assert invalid_code['state']=='invalid' and invalid_code['waterlines']==[]
    assert all('C:/secret' not in issue for issue in invalid_code['issues'])

    daily.update(requested_end='2026-09-11')
    for field, value in [('observed_at','2026-09-12T05:22:40'),
                         ('observed_at','not-a-time'), ('state',{}),
                         ('production_enabled',True), ('rds_write','false')]:
        status_path.write_text(json.dumps({**daily, field:value}), encoding='utf-8')
        invalid = client.get('/api/fundamentals/status')
        assert invalid.status_code==200 and invalid.json()['state']=='invalid'
    status_path.write_text(json.dumps(daily), encoding='utf-8')
    for value in ([], {}):
        checkpoint_path.write_text(json.dumps({**checkpoint, 'state':value}), encoding='utf-8')
        invalid = client.get('/api/fundamentals/status')
        assert invalid.status_code==200 and invalid.json()['state']=='completed'
        assert invalid.json()['checkpoint']['state']=='invalid'


def test_legacy_watchlist_schema_upgrade_preserves_existing_rows(setup):
    import sqlite3
    client,store,_=setup
    store.runtime.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(store.runtime/'workbench.sqlite') as con:
        con.execute('CREATE TABLE watchlists (id INTEGER PRIMARY KEY, product TEXT NOT NULL, '
            'near TEXT NOT NULL,far TEXT NOT NULL,price_type TEXT NOT NULL,created_at TEXT, '
            'UNIQUE(product,near,far,price_type))')
        con.execute("INSERT INTO watchlists VALUES(7,'RB','RB2610','RB2701','close','2026-09-01')")
    saved=client.get('/api/watchlists').json()['items']
    assert len(saved)==1 and saved[0]['id']==7 and saved[0]['created_at']=='2026-09-01'
    assert saved[0]['plan'] is None and saved[0]['plan_updated_at'] is None


def test_source_unavailable_falls_back_whole_snapshot(setup):
    _, store, path = setup
    before = store.read('RB', '2026-09-01', '2026-09-02')
    path.rename(path.with_suffix('.offline'))
    after = store.read('RB', '2026-09-01', '2026-09-02')
    assert before['rows'] == after['rows']
    assert after['meta']['quality_flags'] == ['source_unavailable_using_previous_snapshot']
    assert after['meta']['stale'] is True


def test_default_window_survives_source_outage_and_restart(setup):
    _, store, path = setup
    before = store.read('RB')
    assert before['meta']['stale'] is False
    path.rename(path.with_suffix('.offline'))
    restarted = DailyStore(store.root, store.runtime)
    after = restarted.read('RB')
    assert after['rows'] == before['rows']
    assert after['end'] == '2026-09-02'
    assert after['meta']['stale'] is True


def test_stat_permission_failure_returns_snapshot(setup, monkeypatch):
    _, store, _ = setup
    before = store.read('RB', '2026-09-01', '2026-09-02')
    def denied(_files):
        raise PermissionError('simulated inaccessible source')
    monkeypatch.setattr(store, '_fingerprints', denied)
    after = store.read('RB', '2026-09-01', '2026-09-02')
    assert after['rows'] == before['rows'] and after['meta']['stale']


def test_read_change_returns_previous_snapshot(setup, monkeypatch):
    _, store, _ = setup
    before = store.read('RB', '2026-09-01', '2026-09-02')
    store.cache.clear()
    original = store._fingerprints
    calls = []
    def changing(files):
        calls.append(1)
        return original(files) + ('' if len(calls) == 1 else 'changed')
    monkeypatch.setattr(store, '_fingerprints', changing)
    after = store.read('RB', '2026-09-01', '2026-09-02')
    assert after['meta']['snapshot_id'] == before['meta']['snapshot_id']
    assert after['meta']['stale']


def test_index_contract_terms_scope_calendar_and_cost_boundaries():
    from backend.data import contract_terms
    for product, multiplier in [('IM',200),('IC',200),('IF',300),('IH',300)]:
        terms=contract_terms(product,[product+'2609',product+'2612'],'2026-09-11')
        assert terms['multiplier']==multiplier and terms['tick_size']==0.2
        assert terms['tick_value']==multiplier*0.2
        assert terms['contracts'][0]['expected_last_trade_date']=='2026-09-18'
        assert terms['contracts'][0]['calendar_days_remaining']==7
        assert terms['contracts'][0]['last_trade_date'] is None
        assert terms['account_margin_rate'] is None and terms['total_cost'] is None
        assert terms['execution_eligible'] is False
    dates=contract_terms('IM',['IM2602','IM2606','IM2703'],'2026-09-11')['contracts']
    assert [r['expected_last_trade_date'] for r in dates]==['2026-02-24','2026-06-22',None]
    assert dates[0]['calendar_days_remaining']<0
    assert contract_terms('IM',['IM2609'],None)['contracts'][0]['calendar_days_remaining'] is None
    assert contract_terms('RB',['RB2610'],'2026-09-11') is None
    for bad in ['IM9999','IM2613','IF2609']:
        with pytest.raises(ValueError):
            contract_terms('IM',[bad],'2026-09-11')


def test_bad_pairs_and_removed_export(setup):
    client, _, _ = setup
    assert client.get('/api/dashboard?near=RB9999&far=RB2701').status_code == 422
    assert client.get('/api/dashboard?near=RB2701&far=RB2610').status_code == 422
    result = client.get('/api/export?product=RB&start=2026-09-01&end=2026-09-02')
    assert result.status_code == 404
    scenario = client.post('/api/scenario', json={'direction': 'buy', 'entry_spread': 51,
        'exit_spread': 61, 'lots': 2, 'multiplier': 10, 'total_cost': 40})
    assert scenario.status_code == 200
    assert scenario.json()['net_pnl'] == 160


def test_snapshot_rejects_source_change(setup, monkeypatch):
    _, store, _ = setup
    original = store._fingerprints
    calls = []
    def changed(files):
        result = original(files)
        calls.append(1)
        return result if len(calls) == 1 else result + 'changed'
    monkeypatch.setattr(store, '_fingerprints', changed)
    with pytest.raises(OSError):
        store.read('RB', '2026-09-01', '2026-09-02')
    assert not store.runtime.exists()


def test_daily_source_configuration_survives_directory_move(setup,monkeypatch,tmp_path):
    _,store,_=setup
    expected=store.read('RB','2026-09-01','2026-09-02')
    moved=tmp_path/'mounted-market';store.root.parent.rename(moved)
    monkeypatch.setenv('FUTURES_DATA_ROOT',str(moved))
    configured=DailyStore(runtime=tmp_path/'other-runtime')
    actual=configured.read('RB','2026-09-01','2026-09-02')
    assert actual['rows']==expected['rows']
    assert actual['meta']['snapshot_id']==expected['meta']['snapshot_id']
    assert actual['meta']['stale'] is False


def test_no_daily_source_does_not_block_health_and_user_state(monkeypatch,tmp_path):
    monkeypatch.delenv('FUTURES_DATA_ROOT',raising=False)
    store=DailyStore(runtime=tmp_path/'runtime')
    assert store.root==Path(__file__).resolve().parents[1]/'data/futureshistoryprices1d'
    client=TestClient(create_app(DailyStore(tmp_path/'missing-data',tmp_path/'runtime')))
    assert client.get('/api/health').json()['status']=='ok'
    assert client.get('/api/products').json()['products']==[]
    assert client.get('/api/watchlists').json()['items']==[]
    assert client.get('/api/dashboard?product=IM').status_code==503


def test_position_peaks_endpoint_cutoff_and_source_invalidation(setup):
    client, store, source = setup
    old = store.root / 'year_month=2025-09' / 'data.parquet'
    old.parent.mkdir()
    def write_peak(value):
        with duckdb.connect() as con:
            con.execute("CREATE TABLE old AS SELECT * REPLACE ('RB2510' AS symbol, DATE '2025-09-02' AS trade_date, ?::DOUBLE AS position) FROM read_parquet(?) WHERE symbol='RB2610' AND trade_date=DATE '2026-09-02'", [value,str(source)])
            con.execute("COPY old TO ? (FORMAT PARQUET)", [str(old)])
    before = source.read_bytes()
    write_peak(100)
    r=client.get('/api/position-peaks?end=2026-09-02&min_volume=0&min_position=0')
    assert r.status_code==200
    item=r.json()['items'][0]
    assert (item['product'],item['symbol'],item['name'])==('RB','RB2610','螺纹钢')
    assert item['baseline_peak']==100 and item['deviation']==100
    assert item['history_years']==[2025]
    assert client.get('/api/position-peaks?end=2026-09-02&min_volume=0&min_position=0').json()==r.json()
    write_peak(160)
    assert client.get('/api/position-peaks?end=2026-09-02&min_volume=0&min_position=0').json()['items'][0]['deviation']==25
    assert client.get('/api/position-peaks?end=2025-09-01').json()['items']==[]
    assert client.get('/api/position-peaks?end=bad').status_code==422
    assert source.read_bytes()==before
    assert next(p for p in client.get('/api/products').json()['products'] if p['product']=='RB')['name']=='螺纹钢'


@pytest.mark.parametrize('view,sections', [('catalog',[]),('spread',['spread','risk']),('curve',['curve']),('rollover',['spread','rollover']),('scan',['opportunities'])])
def test_dashboard_view_preserves_values_and_omits_unrelated_sections(setup, view, sections):
    client, _, _ = setup
    full = client.get('/api/dashboard?product=RB').json()
    response = client.get('/api/dashboard?product=RB&view='+view)
    assert response.status_code == 200
    result = response.json()
    expected = {'meta','product','near','far','price_type','contracts',*sections}
    assert set(result) == expected
    for key in expected:
        assert result[key] == full[key]


def test_catalog_does_not_compute_charts_and_rejects_unknown_view(setup, monkeypatch):
    from backend import analytics
    client, _, _ = setup
    def unexpected(*args, **kwargs):
        raise AssertionError('Catalog must not compute unused analytics')
    for name in ('spread_series','spread_risk','term_structure_comparison','rollover_series'):
        monkeypatch.setattr(analytics, name, unexpected)
    assert client.get('/api/dashboard?product=RB&view=catalog').status_code == 200
    assert client.get('/api/dashboard?product=RB&view=unknown').status_code == 422
