"""Read-only bounded daily Parquet access; all writable state is project-local."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import file_digest, sha256
import json
import math
import os
from pathlib import Path
import re
from threading import RLock, BoundedSemaphore

import duckdb
from .transport import ByteCache, fingerprint

QUERY_SLOTS = BoundedSemaphore(int(os.environ.get('FUTURES_QUERY_CONCURRENCY', '1')))

@contextmanager
def query_connection():
    with QUERY_SLOTS:
        with duckdb.connect(config={'threads':int(os.environ.get('FUTURES_DUCKDB_THREADS', '1')),
                                    'memory_limit':os.environ.get('FUTURES_DUCKDB_MEMORY', '256MB'),
                                    'max_temp_directory_size':'0B'}) as connection:
            yield connection


FIELDS = ('exchange', 'symbol', 'trade_datetime', 'trade_date', 'open', 'high', 'low', 'close',
          'volume', 'amount', 'position', 'settle_price', 'pre_settle_price')
PRODUCT = re.compile(r'^[A-Z]{1,3}$')
CONTRACT = re.compile(r'^[A-Z]{1,3}\d{4}$')
SYNTHETIC = ('8888', '9998', '9999')
# Display vocabulary aligned with the seat dashboard; no external path dependency.
PRODUCT_NAMES = {
    'A': '豆一', 'AD': '铸造铝合金', 'AG': '白银', 'AL': '铝', 'AO': '氧化铝', 'AP': '苹果',
    'AU': '黄金', 'B': '豆二', 'BB': '胶合板', 'BC': '国际铜', 'BR': '丁二烯橡胶', 'BU': '沥青',
    'BZ': '纯苯', 'C': '玉米', 'CF': '棉花', 'CJ': '红枣', 'CS': '玉米淀粉', 'CU': '铜',
    'CY': '棉纱', 'EB': '苯乙烯', 'EC': '集运指数欧线', 'EG': '乙二醇', 'FB': '纤维板', 'FG': '玻璃',
    'FU': '燃料油', 'HC': '热卷', 'I': '铁矿石', 'IC': '中证500', 'IF': '沪深300', 'IH': '上证50',
    'IM': '中证1000', 'J': '焦炭', 'JD': '鸡蛋', 'JM': '焦煤', 'JR': '粳稻', 'L': '塑料',
    'LC': '碳酸锂', 'LG': '原木', 'LH': '生猪', 'LR': '晚籼稻', 'LU': '低硫燃料油', 'M': '豆粕',
    'MA': '甲醇', 'NI': '镍', 'NR': '20号胶', 'OI': '菜籽油', 'OP': '胶版印刷纸', 'P': '棕榈油',
    'PB': '铅', 'PD': '钯', 'PF': '短纤', 'PG': '液化石油气', 'PK': '花生', 'PL': '丙烯',
    'PM': '普麦', 'PP': '聚丙烯', 'PR': '瓶片', 'PS': '多晶硅', 'PT': '铂', 'PX': '对二甲苯',
    'RB': '螺纹钢', 'RI': '早籼稻', 'RM': '菜粕', 'RR': '粳米', 'RS': '油菜籽', 'RU': '橡胶',
    'SA': '纯碱', 'SC': '原油', 'SF': '硅铁', 'SH': '烧碱', 'SI': '工业硅', 'SM': '锰硅',
    'SN': '锡', 'SP': '纸浆', 'SR': '白糖', 'SS': '不锈钢', 'T': '十年期国债', 'TA': 'PTA',
    'TF': '五年期国债', 'TL': '三十年期国债', 'TS': '二年期国债', 'UR': '尿素', 'V': 'PVC', 'WH': '强麦',
    'WR': '线材', 'Y': '豆油', 'ZC': '动力煤', 'ZN': '锌',
}
INDEX_SERIES = {
    'IH': ('000016.SH', '上证50'), 'IF': ('000300.SH', '沪深300'),
    'IM': ('000852.SH', '中证1000'), 'IC': ('000905.SH', '中证500'),
}

def contract_terms(product, symbols=(), as_of=None, store=None):
    """Published RDS contract dates and separate equity rule estimates; not historical PIT."""
    rules = {'IM': (200,'zz1000/'), 'IC': (200,'cn/zz500.html'),
             'IF': (300,'hs300/'), 'IH': (300,'sz50/')}
    path = store.root.parent/'cfuturesdescription/part.parquet' if store else None
    recorded = {}
    if path and path.is_file() and symbols:
        rows = store._query([path], 'SELECT * FROM read_parquet(?) WHERE product=? AND symbol IN ('+','.join('?' for _ in symbols)+')', [product,*symbols])
        if len({r['symbol'] for r in rows}) != len(rows):
            raise ValueError('合约日期存在重复代码')
        recorded = {r['symbol']:r for r in rows}
    if product not in rules and not recorded:
        return None
    multiplier, source_path = rules.get(product, (None,''))
    # CFFEX 2026 holiday notice; weekend make-up workdays remain non-trading days.
    closures = [('2026-01-01','2026-01-03'),('2026-02-15','2026-02-23'),
        ('2026-04-04','2026-04-06'),('2026-05-01','2026-05-05'),
        ('2026-06-19','2026-06-21'),('2026-09-25','2026-09-27'),('2026-10-01','2026-10-07')]
    contracts = []
    for symbol in symbols:
        if not CONTRACT.fullmatch(symbol) or symbol[:-4] != product or not 1 <= int(symbol[-2:]) <= 12:
            raise ValueError('合约规则只接受当前品种的真实月份合约')
        first = date(2000+int(symbol[-4:-2]),int(symbol[-2:]),1)
        expected = None
        if product in rules and first.year == 2026:
            expected = first + timedelta(days=(4-first.weekday())%7+14)
            while expected.weekday() >= 5 or any(a<=expected.isoformat()<=b for a,b in closures):
                expected += timedelta(days=1)
        contracts.append({'symbol':symbol, 'expected_last_trade_date':expected.isoformat() if expected else None,
            'calendar_days_remaining':(expected-date.fromisoformat(as_of)).days if expected and as_of else None,
            'last_trade_date':None, 'last_delivery_date':None, 'status':'rule_derived' if expected else 'calendar_unverified'})
        if symbol in recorded:
            row = recorded[symbol]
            contracts[-1].update({k:row[k] for k in ('listed_date','last_trade_date','last_delivery_date','source_code')},
                status=row['date_status'], source='RDS.cfuturesdescription',
                calendar_days_remaining=(date.fromisoformat(row['last_trade_date'])-date.fromisoformat(as_of)).days if as_of else None)
    if product not in rules:
        return {'product':product,'contracts':contracts,'as_of':as_of,'execution_eligible':False,
                'warnings':['最后交易日与最后交割日分别读取；当前RDS修订版，不是历史时点档案或最终交割公告']}
    return {'product':product,'exchange':'CFFEX','multiplier':multiplier,'multiplier_unit':'元/指数点/手',
        'tick_size':0.2,'tick_value':multiplier*0.2,'price_unit':'指数点','settlement':'现金交割',
        'reviewed_on':'2026-09-12','source_url':'https://www.cffex.com.cn/'+source_path,
        'calendar_source_url':'https://www.cffex.com.cn/en_new/Notices/20251217/46426.html',
        'expiry_rule':'交割月第三个周五；法定假日或异常休市顺延至下一交易日',
        'contracts':contracts,'as_of':as_of,'account_margin_rate':None,'total_cost':None,
        'execution_eligible':False,'version':'cffex-equity-terms-20260912',
        'warnings':['last_trade_date/last_delivery_date读取当前RDS修订版；expected_last_trade_date仅为2026规则估计，二者不混用',
            'calendar_conflict为源日期与RDS交易日历不符，calendar_uncovered为日历未覆盖；已入库日期不等于最终公告或历史时点档案',
            '剩余日数相对期货源截止日，为自然日，不是从今天算起；到期日不表示当前仍可交易',
            '当前核验版本不是历史当时可得规则；不据此计算年化、放宽雷达到期拦截或产生下单建议',
            '账户保证金、手续费、滑点、资金和分红仍需另行核验；不将合约表最低保证金当作账户要求']}


# Explicitly accepted isolated snapshot, never select an arbitrary newest file.
REVIEW_FILE = 'review_2ae3a8aee4da2df5e8b790f4eb022b594518677bcac6fcc57199e613f07136b0.parquet'
REVIEW_SHA256 = 'ffa26ebd436c1e816fb42fc31bc2818bd561393fee2977fa18f471e3a667ce49'
REVIEW_METADATA = 'terminal_metadata_combined_20260909.json'
TERMINAL_METADATA_FILE = 'terminal_metadata_screenshots_20260914.json'


def index_daily(folder, product=None, start=None, end=None, published=None):
    """Read published RDS closes, or the explicit local review; never fetch on request."""
    if published is not None and published.is_dir():
        code, name = INDEX_SERIES[product]
        files = sorted(p for p in published.glob('year_month=*/*.parquet')
                       if (not start or p.parent.name[11:] >= start[:7]) and (not end or p.parent.name[11:] <= end[:7]))
        rows = DailyStore._query(files, '''SELECT trade_date::VARCHAR observation_date, close raw_value
            FROM read_parquet(?) WHERE symbol=? AND trade_date BETWEEN ?::DATE AND ?::DATE ORDER BY trade_date''',
            (code, start or '0001-01-01', end or '9999-12-31')) if files else []
        return {'source':'RDS aindexeodprices', 'sha256':fingerprint(files),
                'indicators':[{'product':product, 'code':code, 'name':name, 'frequency':'D', 'unit':'指数点',
                               'source':'RDS aindexeodprices', 'first':rows[0]['observation_date'] if rows else None,
                               'last':rows[-1]['observation_date'] if rows else None}],
                'points':[dict(row, code=code) for row in rows]}
    folder = Path(folder)
    selected = json.loads((folder/'index_review.json').read_bytes())
    if (selected.get('kind') != 'index_daily_review' or selected.get('state') != 'completed'
            or selected.get('production_enabled') is not False or selected.get('execution_eligible') is not False
            or not re.fullmatch(r'index_[a-f0-9]{64}\.parquet', selected.get('file',''))
            or Path(selected.get('metadata','')).name != selected.get('metadata')):
        raise ValueError('指数审阅指针非法')
    path = folder/selected['file']
    if sha256(path.read_bytes()).hexdigest() != selected['sha256']:
        raise ValueError('指数快照哈希不匹配')
    raw = (folder/selected['metadata']).read_bytes()
    if sha256(raw).hexdigest() != selected['metadata_sha256']:
        raise ValueError('指数身份元数据已变化')
    identity = json.loads(raw)
    if ({r['code']:r.get('name') for r in identity['results']} != dict(INDEX_SERIES.values())
            or len(selected['indicators']) != 4):
        raise ValueError('指数代码名称不匹配')
    for item in selected['indicators']:
        if (INDEX_SERIES.get(item['product']) != (item['code'],item['name'])
                or item.get('frequency') != 'D' or item.get('unit') != '指数点'):
            raise ValueError('指数口径不匹配')
    if product is not None and product not in INDEX_SERIES:
        raise ValueError('未知股指品种')
    with query_connection() as con:
        rows = con.execute('SELECT code,observation_date,raw_value FROM read_parquet(?) ORDER BY 1,2',[str(path)]).fetchall()
    if (len(rows) != selected['observations'] or len({(c,d) for c,d,v in rows}) != len(rows)
            or any(c not in dict(INDEX_SERIES.values()) or not selected['start'] <= d <= selected['end']
                   or v is None or not math.isfinite(v) or v <= 0 for c,d,v in rows)):
        raise ValueError('指数快照键值校验失败')
    return {**selected,'points':[{'code':c,'observation_date':d,'raw_value':v} for c,d,v in rows
        if (product is None or c==INDEX_SERIES[product][0]) and (not start or d>=start) and (not end or d<=end)]}


def fundamental_review(folder, code=None, start=None, end=None, offset=0, limit=1000):
    """Read a pinned review snapshot; no ingestion, conversion or trading signals."""
    if start and end and start > end:
        raise ValueError('开始日期不能晚于截止日期')
    folder = Path(folder)
    selected = {'file':REVIEW_FILE, 'sha256':REVIEW_SHA256, 'metadata':REVIEW_METADATA}
    pointer = folder / 'accepted_review.json'
    if pointer.exists():
        selected = json.loads(pointer.read_bytes())
        if selected.get('production_enabled') is not False or any(
                Path(selected[key]).name != selected[key] for key in ('file','metadata')):
            raise RuntimeError('隔离审阅指针非法')
    path = folder / selected['file']
    metadata_bytes = (folder / selected['metadata']).read_bytes()
    metadata = json.loads(metadata_bytes)
    if metadata.get('production_enabled') is not False or sha256(path.read_bytes()).hexdigest() != selected['sha256']:
        raise RuntimeError('隔离审阅快照校验失败')
    with query_connection() as con:
        con.execute('CREATE VIEW review AS SELECT * FROM read_parquet(\'' + str(path).replace("'", "''") + '\')')
        if con.execute('SELECT count(*) FROM review WHERE production_ready IS DISTINCT FROM false OR metadata_sha256 IS DISTINCT FROM ?',
                       [sha256(metadata_bytes).hexdigest()]).fetchone()[0]:
            raise RuntimeError('隔离审阅证据不匹配')
        stats = {row[0]:row[1:] for row in con.execute(
            'SELECT code,count(*),min(observation_date),max(observation_date) FROM review GROUP BY code').fetchall()}
        indicators = [{**item, 'observations':stats[item['code']][0], 'first':stats[item['code']][1],
            'last':stats[item['code']][2]} for item in metadata['indicators'] if item['code'] in stats]
        result = {'mode':'isolated_review', 'production_ready':False, 'snapshot_sha256':selected['sha256'],
            'indicators':indicators, 'excluded':[item for item in metadata['indicators'] if item['role']=='quarantine'],
            'warnings':['仅审阅，不参与交割基差或机会排名；CU另有周频参考价差', '原单位、不换算、不前填、不加总',
                        '观测日期不等于历史首次可得日期；不支持时点回测', '覆盖期不保证内部无缺数']}
        if code is not None:
            if code not in stats:
                raise ValueError('指标未进入隔离审阅快照')
            predicate = 'code = ? AND observation_date >= ? AND observation_date <= ?'
            params = [code, start or '0001-01-01', end or '9999-12-31']
            total = con.execute('SELECT count(*) FROM review WHERE '+predicate, params).fetchone()[0]
            points = con.execute('SELECT observation_date,raw_value FROM review WHERE '+predicate+
                ' ORDER BY observation_date LIMIT ? OFFSET ?', params+[limit,offset]).fetchall()
            result.update({'code':code, 'total':total, 'offset':offset, 'limit':limit,
                'next_offset':offset+len(points) if offset+len(points)<total else None,
                'points':[{'observation_date':day,'raw_value':value} for day,value in points]})
        return result


def _read_terminal_metadata(folder, candidate_codes):
    path = Path(folder) / TERMINAL_METADATA_FILE
    if not path.is_file():
        return None, {}
    raw = path.read_bytes()
    metadata = json.loads(raw)
    if (metadata.get('kind') != 'terminal_field_evidence_not_production_registry'
            or metadata.get('production_enabled') is not False
            or metadata.get('basis_eligible') is not False
            or metadata.get('replay_eligible') is not False
            or metadata.get('signal_eligible') is not False
            or metadata.get('request_coverage_known') is not False
            or metadata.get('field_status') != 'terminal_default_display_confirmed'):
        raise ValueError('Terminal metadata pointer is not isolated')
    items = metadata.get('indicators')
    if not isinstance(items, list) or len(items) != metadata.get('unique_codes'):
        raise ValueError('Terminal metadata code count is invalid')
    by_code = {item.get('code'): item for item in items}
    if (len(by_code) != len(items) or set(by_code) != set(candidate_codes)
            or metadata.get('unique_codes') != len(candidate_codes)):
        raise ValueError('Terminal metadata code set does not match candidates')
    allowed = {'reference_candidate', 'futures_excluded', 'background', 'unknown'}
    counts = {key: 0 for key in allowed}
    evidence = metadata.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        raise ValueError('Terminal metadata evidence is missing')
    evidence_by_file = {}
    displayed = []
    for item in evidence:
        file = item.get('file')
        if (not isinstance(file, str) or Path(file).name != file
                or not re.fullmatch(r'terminal_metadata_evidence_20260914_\d{2}\.png', file)
                or not isinstance(item.get('sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])):
            raise ValueError('Terminal metadata evidence entry is invalid')
        evidence_path = Path(folder) / file
        if (not evidence_path.is_file()
                or sha256(evidence_path.read_bytes()).hexdigest() != item['sha256']):
            raise ValueError('Terminal metadata evidence hash mismatch')
        codes = item.get('displayed_codes')
        if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
            raise ValueError('Terminal metadata displayed code list is invalid')
        displayed.extend(codes)
        evidence_by_file[file] = item['sha256']
    duplicate_rows = metadata.get('duplicate_rows')
    if duplicate_rows != {'S0031715': 2} or len(displayed) != len(candidate_codes) + 1:
        raise ValueError('Terminal metadata duplicate evidence is invalid')
    if set(displayed) != set(candidate_codes) or displayed.count('S0031715') != 2:
        raise ValueError('Terminal metadata displayed code set is invalid')
    for code, item in by_code.items():
        if (not re.fullmatch(r'[SG]\d{7}', code)
                or item.get('classification') not in allowed
                or not isinstance(item.get('products'), list)
                or not item.get('name') or not item.get('frequency') or not item.get('unit')
                or not item.get('display_start') or not item.get('display_end')
                or not item.get('display_updated_on')
                or not isinstance(item.get('limits'), list)
                or item.get('current_display_only') is not True):
            raise ValueError('Terminal metadata indicator is invalid')
        counts[item['classification']] += 1
        item_evidence = item.get('evidence')
        if (not isinstance(item_evidence, dict)
                or not isinstance(item_evidence.get('files'), list)
                or not isinstance(item_evidence.get('source_sha256'), list)
                or not item_evidence['files']
                or any(file not in evidence_by_file for file in item_evidence['files'])
                or item_evidence['source_sha256'] !=
                   [evidence_by_file[file] for file in item_evidence['files']]):
            raise ValueError('Terminal metadata indicator evidence is invalid')
    if metadata.get('classification_counts') != counts:
        raise ValueError('Terminal metadata classification counts are invalid')
    enriched = {}
    for code, item in by_code.items():
        enriched[code] = {**item, 'identity_status': 'same_code_current_display_confirmed',
                          'production_enabled':False, 'basis_eligible':False,
                          'replay_eligible':False, 'signal_eligible':False,
                          'request_coverage_known':False}
    summary = {
        'kind': metadata['kind'],
        'field_status': metadata['field_status'],
        'current_display_scope': metadata.get('current_display_scope'),
        'metadata_sha256': sha256(raw).hexdigest(),
        'production_enabled':False, 'basis_eligible':False,
        'replay_eligible':False, 'signal_eligible':False,
        'request_coverage_known':False,
        'unique_codes': metadata['unique_codes'],
        'duplicate_rows': duplicate_rows,
        'counts': counts,
        'evidence': [{'file':item['file'], 'sha256':item['sha256']}
                     for item in evidence],
    }
    return summary, enriched


def fundamental_candidates(folder, code=None, offset=0, limit=1000):
    """Separate raw candidate reader; never included in business review or signals."""
    folder = Path(folder)
    selected = json.loads((folder/'candidate_review.json').read_bytes())
    if (selected.get('kind') != 'unverified_candidate_snapshot'
            or selected.get('identity_status') != 'unverified'
            or any(selected.get(k) is not False for k in
                   ('production_enabled','basis_eligible','replay_eligible','signal_eligible'))
            or not isinstance(selected.get('file'), str)
            or not re.fullmatch(r'candidates_[a-f0-9]{64}\.parquet', selected['file'])):
        raise ValueError('Invalid candidate pointer')
    path = folder/selected['file']
    if sha256(path.read_bytes()).hexdigest() != selected['sha256']:
        raise ValueError('Candidate snapshot changed')
    terminal_metadata, terminal_by_code = _read_terminal_metadata(
        folder, [r['code'] for r in selected['indicators']])
    indicators = [{'code':r['code'], 'name':None, 'unit':None, 'frequency':None,
        'claimed_labels':list(dict.fromkeys(c.get('name') or c.get('claimed_name') or r['code'] for c in r['claims'])),
        'references':r['references'], 'source_metadata_claims':r.get('source_metadata_claims',[]),
        'observations':r['observations'],'first':r['first'],'last':r['last'],
        **({'terminal_metadata':terminal_by_code[r['code']]} if r['code'] in terminal_by_code else {})}
        for r in selected['indicators']]
    result = {'mode':'unverified_candidates_only', 'identity_status':'unverified',
        'production_enabled':False,'basis_eligible':False,'replay_eligible':False,'signal_eligible':False,
        'snapshot_sha256':selected['sha256'],'indicators':indicators,'observations':selected['observations'],
        'request_coverage_known':selected.get('request_coverage_known', False),
        'warnings':['未提供同码终端属性的候选仍只有旧表或Alice声明；同码终端字段仅代表当前显示',
                    '只供指标身份核查，不计入已覆盖品种、不进入基差/雷达/回测',
                    '保存观测区间不等于成功请求覆盖；不表示持续更新或历史当时可得',
                    '当前终端字段仅为同码显示证据，不自动认可保存历史值的单位或频率']}
    if terminal_metadata is not None:
        result['terminal_metadata'] = terminal_metadata
    if code is not None:
        if code not in {r['code'] for r in indicators}:
            raise ValueError('Code is not in candidate snapshot')
        with query_connection() as con:
            total = con.execute('SELECT count(*) FROM read_parquet(?) WHERE code=?',[str(path),code]).fetchone()[0]
            points = con.execute('SELECT observation_date,raw_value,source_files FROM read_parquet(?) WHERE code=? ORDER BY observation_date LIMIT ? OFFSET ?',
                                 [str(path),code,limit,offset]).fetchall()
        result.update(code=code,total=total,offset=offset,limit=limit,
            next_offset=offset+len(points) if offset+len(points)<total else None,
            points=[{'observation_date':d,'raw_value':v,'source_sha256s':[selected['sources'][p] for p in json.loads(s)]}
                    for d,v,s in points])
    return result


FUNDAMENTAL_PRODUCTS = {
    'S0049493': ['CU'], 'S0049507': ['CU'], 'S5713337': ['RB'],
    'S0181750': ['RB'], 'S0114170': ['RB'], 'S5041144': ['M'],
    'S5801814': ['CU'], 'S5715204': ['I'], 'G8403835': ['CU'],
    'S5006415': ['B', 'M', 'Y'],
    '529645757': ['SC'], '529638707': ['SC'], '6303101': ['J'],
    '304923004': ['SC'], '529632157': ['SC'],
    '531905027': ['RB'], '531952197': ['RB'], '531952557': ['RB'],
    '317697101': ['I'], '51906201': ['P'], '6130901': ['B', 'M', 'Y'],
    '304989404': ['MA'], '695751297': ['FG'], '304989304': ['SH'], '3672301': ['SA'],
    '110269001': ['TA'],
    '540064367': ['BU'],
    '532023257': ['HC'],
    '304988904': ['AL'], '304989104': ['ZN'], '304992304': ['A'], '304992504': ['PK'],
    '304990304': ['PG'], '478005147': ['J'], '695751317': ['SR'], '478005157': ['SP'],
    '304990004': ['BR'], '304992204': ['LH'], '304992604': ['UR'], '478005127': ['PP'],
    '304992404': ['M'], '447225787': ['RU'], '695751307': ['PS'], '304992004': ['C'],
    '304989504': ['BZ'], '304992104': ['CF'], '478005107': ['HC'], '478005117': ['L'],
    '447225767': ['JM'],
}

_CEIC_CZCE_WARRANT_PRODUCTS = {
    '567837947': ['MA'],
    '567837857': ['RS'],
    '567837817': ['CF'],
    '567837827': ['CY'],
    '567837937': ['PK'],
    '567837997': ['UR'],
    '567837807': ['SR'],
    '567837957': ['FG'],
    '567837847': ['OI'],
    '567837927': ['CJ'],
    '567838007': ['SA'],
    '567838027': ['PX'],
    '567837977': ['SF'],
    '567837987': ['SM'],
    '567838037': ['SH'],
    '567837837': ['RM'],
    '567838047': ['PR'],
    '567838017': ['PF'],
}
FUNDAMENTAL_PRODUCTS.update(_CEIC_CZCE_WARRANT_PRODUCTS)

_CEIC_NBS_PRICE_CODES = frozenset((
    '304989404', '695751297', '304989304', '304988904', '304989104', '304992304',
    '304992504', '304990304', '478005147', '695751317', '478005157', '304990004',
    '304992204', '304992604', '478005127', '304992404', '447225787', '695751307',
    '304992004', '304989504', '304992104', '478005107', '478005117', '447225767'))

_CEIC_SERIES_LIMITS = {
    '540064367': ['隆众社会库存，非生产企业厂库；D为存储频率，实际观测非每日，不前填。具体样本、统计期和首次发布时间未核，不与厂库或其他社会库存直接相加'],
    '532023257': ['热轧卷板33城市社会库存，非带钢或钢厂库存；D为存储频率，实际观测非每日，不前填。城市及仓库清单、样本变更、统计期和首次发布时间未核，不与其他库存直接相加'],
    '317697101': ['具体港口样本未核验，不标为45港，不与其他港口库存拼接'],
    '51906201': ['棕榈油进口数量，不是港口库存或24度现货价；具体HS范围待核'],
    '6130901': ['大豆进口数量；M/Y仅作上游原料供给背景，不是豆粕/豆油库存、压榨量或开机率'],
    '304989404': ['全国流通领域优等品旬度价格，D为存储频率；不是太仓即期可交割报价，不前填为日价'],
    '695751297': ['全国浮法平板玻璃5/6mm旬度参考价，D为存储频率；吨价仅从2026年起，不拼接旧重量箱价'],
    '304989304': ['全国32%液碱旬度参考价，D为存储频率；折百、交割区域及品质差异未核，不直接计算期货基差'],
    '3672301': ['全国纯碱月度产量，千吨；不等于重碱产量或库存，历史统计样本及1—2月口径待核'],
    '110269001': ['优等品PTA市场月均价，人民币/吨；不是每日即期现货，地区、税价、交割口径及首次发布时间待核，不前填为日价'],
    '304992204': ['生猪原单位为人民币/公斤，未换算为期货吨价；地区、均重、交割品质待核'],
    '695751307': ['多晶硅致密料原单位为人民币/公斤，历史仅自2026年起；不是工业硅SI，不直接拼接期货吨价'],
    '695751317': ['白砂糖国标一级序列仅自2026年起；不使用其他糖价补造历史'],
    '304992104': ['皮棉白棉三级参考价不自动等同于CCIndex 3128B或期货交割等级'],
    '304992304': ['黄豆全国参考价，食品级、非转基因及交割品质未核；不与进口大豆B价格混用'],
}


# Coverage is a display-only view.  Keep role/category mappings explicit; do
# not infer a category from an indicator name.
_COVERAGE_LABELS = {'exchange_inventory':'交易所库存', 'commercial_inventory':'商业库存',
    'reference_spot':'参考现货', 'supply_demand':'供需', 'financial_underlying':'金融专用标的',
    'carrying_cost':'金融专用持有成本', 'other':'其他'}
_COVERAGE_ROLES = {'exchange_inventory_reference':'exchange_inventory', 'warrant_reference':'exchange_inventory',
    'mill_inventory_reference':'commercial_inventory', 'inventory_reference':'commercial_inventory',
    'inventory_scope_unresolved':'commercial_inventory', 'reference_price_not_delivery_basis':'reference_spot',
    'trade_flow_reference':'supply_demand', 'processing_margin_reference':'supply_demand'}
_COVERAGE_CEIC_CATEGORIES = {'529645757':'commercial_inventory', '529638707':'supply_demand',
    '540064367':'commercial_inventory',
    '532023257':'commercial_inventory',
    '6303101':'supply_demand', '304923004':'supply_demand', '529632157':'supply_demand',
    '531905027':'supply_demand', '531952197':'commercial_inventory', '531952557':'commercial_inventory',
    '317697101':'commercial_inventory', '51906201':'supply_demand', '6130901':'supply_demand',
    '304989404':'reference_spot', '695751297':'reference_spot', '304989304':'reference_spot',
    '3672301':'supply_demand', '110269001':'reference_spot'}
_COVERAGE_CEIC_CATEGORIES.update(dict.fromkeys(_CEIC_NBS_PRICE_CODES, 'reference_spot'))
_COVERAGE_CEIC_CATEGORIES.update(dict.fromkeys(_CEIC_CZCE_WARRANT_PRODUCTS, 'exchange_inventory'))
_CEIC_SERIES_LIMITS.update({code: ['郑商所仓单张数，不是社会库存或厂库；每张对应数量及历史规则未核，不换算为吨，不与商业库存相加；注销与季节性需结合仓单有效期判断']
                          for code in _CEIC_CZCE_WARRANT_PRODUCTS})
_FINANCIAL_PRODUCTS = frozenset(('IF','IH','IC','IM','T','TF','TS','TL'))
_COVERAGE_STATUS = {'missing':'缺少指标', 'needs_review':'待口径核验', 'limited':'限定用途可展示',
    'technical_candidate':'技术候选未准入', 'data_check':'数据待检查'}

_UPDATE_STATE_LABELS = {'completed':'隔离检查完成（非生产发布）', 'plan_only':'仅生成计划',
    'failed':'上次执行失败', 'awaiting_release':'等待发布', 'awaiting_review':'等待复核',
    'awaiting_coverage':'等待覆盖', 'running':'运行中', 'missing':'暂无运行记录',
    'invalid':'运行记录无效'}


def fundamental_update_status(folder):
    """Read the two isolated EDB ledgers without touching data or starting work."""
    folder = Path(folder)
    issues = []
    status_path, checkpoint_path = folder/'daily_review_status.json', folder/'update_checkpoint.json'

    def read_record(path, name):
        if not path.exists():
            issues.append(f'{name}缺失')
            return None, 'missing'
        if not path.is_file():
            issues.append(f'{name}不是文件')
            return None, 'invalid'
        try:
            value = json.loads(path.read_bytes())
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            issues.append(f'{name}不可解析')
            return None, 'invalid'
        if not isinstance(value, dict):
            issues.append(f'{name}结构无效')
            return None, 'invalid'
        return value, 'ok'

    def iso_day(value, field, required=False):
        if value in (None, ''):
            if required:
                issues.append(f'{field}缺失')
            return None, required
        if not isinstance(value, str):
            issues.append(f'{field}不是ISO日期')
            return None, True
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError):
            issues.append(f'{field}不是ISO日期')
            return None, True
        if parsed.isoformat() != value:
            issues.append(f'{field}不是ISO日期')
            return None, True
        return value, False

    def checked_at(value):
        if value in (None, ''):
            issues.append('daily_review_status.observed_at缺失')
            return None, True
        if not isinstance(value, str):
            issues.append('daily_review_status.observed_at不是带时区时间')
            return None, True
        try:
            parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
            valid = parsed.tzinfo is not None and parsed.utcoffset() is not None
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            issues.append('daily_review_status.observed_at不是带时区时间')
            return None, True
        return value, False

    def boolean(record, field):
        value = record.get(field)
        if field not in record:
            issues.append(f'daily_review_status.{field}缺失')
            return None, True
        if type(value) is not bool:
            issues.append(f'daily_review_status.{field}不是布尔值')
            return None, True
        return value, value is not False

    def count(record, field):
        if field not in record or record[field] is None:
            return None
        if not isinstance(record[field], list):
            issues.append(f'daily_review_status.{field}不是列表')
            return None
        return len(record[field])

    daily, daily_read = read_record(status_path, 'daily_review_status.json')
    checked_at_value = requested_end = executed = network_fetch_enabled = None
    counts = {'pending_requests':None, 'release_check_requests':None, 'awaiting_release':None}
    waterlines = []
    if daily_read == 'missing':
        state = 'missing'
    elif daily_read != 'ok':
        state = 'invalid'
    else:
        raw_state = daily.get('state')
        state = (raw_state if isinstance(raw_state, str) and raw_state in _UPDATE_STATE_LABELS
                 else 'invalid')
        if state == 'invalid':
            issues.append('daily_review_status.state未知')
        checked_at_value, checked_bad = checked_at(daily.get('observed_at'))
        requested_end, end_bad = iso_day(daily.get('requested_end'),
                                          'daily_review_status.requested_end', required=True)
        if checked_bad or end_bad:
            state = 'invalid'
        executed, _ = boolean(daily, 'executed')
        network_fetch_enabled, _ = boolean(daily, 'network_fetch_enabled')
        for field in ('production_enabled', 'rds_write', 'scheduler_enabled'):
            value, bad = boolean(daily, field)
            if bad or value is not False:
                issues.append(f'daily_review_status.{field}不符合隔离要求')
                state = 'invalid'
        for field in counts:
            counts[field] = count(daily, field)
        raw_waterlines = daily.get('waterlines')
        if raw_waterlines is None:
            issues.append('daily_review_status.waterlines缺失')
        elif not isinstance(raw_waterlines, dict):
            issues.append('daily_review_status.waterlines结构无效')
            state = 'invalid'
        else:
            for code, value in sorted(raw_waterlines.items(), key=lambda pair: str(pair[0])):
                if not isinstance(code, str) or not re.fullmatch(r'[SG]\d{7}', code):
                    issues.append('daily_review_status.waterlines含无效编码')
                    state = 'invalid'
                    continue
                value, bad = iso_day(value, 'daily_review_status.waterline.date', required=True)
                if bad:
                    state = 'invalid'
                else:
                    waterlines.append({'code':code, 'date':value})

    checkpoint, checkpoint_read = read_record(checkpoint_path, 'update_checkpoint.json')
    checkpoint_result = {'state':'missing', 'label':_UPDATE_STATE_LABELS['missing'],
                         'requested_start':None, 'requested_end':None,
                         'lock_present':(folder/'update.lock').is_file()}
    if checkpoint_read == 'invalid':
        checkpoint_result.update(state='invalid', label=_UPDATE_STATE_LABELS['invalid'])
    elif checkpoint_read == 'ok':
        checkpoint_state = checkpoint.get('state')
        checkpoint_result['state'] = (checkpoint_state if isinstance(checkpoint_state, str)
                                      and checkpoint_state in _UPDATE_STATE_LABELS
                                      else 'invalid')
        if checkpoint_result['state'] == 'invalid':
            checkpoint_result['label'] = _UPDATE_STATE_LABELS['invalid']
            issues.append('update_checkpoint.state未知')
        else:
            checkpoint_result['label'] = _UPDATE_STATE_LABELS[checkpoint_result['state']]
        scope = checkpoint.get('scope')
        if not isinstance(scope, dict):
            issues.append('update_checkpoint.scope缺失或无效')
            checkpoint_result['state'] = 'invalid'
            checkpoint_result['label'] = _UPDATE_STATE_LABELS['invalid']
        else:
            start, start_bad = iso_day(scope.get('start'), 'update_checkpoint.scope.start', required=True)
            end, end_bad = iso_day(scope.get('end'), 'update_checkpoint.scope.end', required=True)
            checkpoint_result.update(requested_start=start, requested_end=end)
            if start_bad or end_bad:
                checkpoint_result['state'] = 'invalid'
                checkpoint_result['label'] = _UPDATE_STATE_LABELS['invalid']
        if checkpoint.get('production_enabled') is not False:
            issues.append('update_checkpoint.production_enabled不符合隔离要求')
            checkpoint_result['state'] = 'invalid'
            checkpoint_result['label'] = _UPDATE_STATE_LABELS['invalid']
        if checkpoint_result['state'] == 'failed' or checkpoint.get('error'):
            issues.append('运行记录含错误，详见本地状态文件')

    if state == 'failed' or daily_read == 'invalid' or checkpoint_read == 'invalid':
        if '运行记录含错误，详见本地状态文件' not in issues:
            issues.append('运行记录含错误，详见本地状态文件')
    result = {'mode':'isolated_readonly', 'scope':'wind_edb', 'state':state,
        'label':_UPDATE_STATE_LABELS[state], 'checked_at':checked_at_value,
        'requested_end':requested_end, 'executed':executed,
        'network_fetch_enabled':network_fetch_enabled, 'counts':counts,
        'waterlines':waterlines, 'checkpoint':checkpoint_result, 'issues':issues,
        'production_enabled':False, 'rds_write':False, 'scheduler_enabled':False}
    if issues:
        result['has_error'] = True
    return result


def _fundamental_coverage(runtime, product, catalog, failures):
    """Build a non-authoritative, snapshot-only coverage summary."""
    runtime, folder = Path(runtime), Path(runtime) / 'fundamental_lab'
    local_check = '未知'
    status_path = folder / 'daily_review_status.json'
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_bytes())
            observed = status.get('observed_at') if isinstance(status, dict) else None
            if isinstance(observed, str) and observed:
                local_check = observed
            elif observed not in (None, ''):
                failures.append({'source':'daily_review_status', 'reason':'本地检查时间不可核验'})
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            failures.append({'source':'daily_review_status', 'reason':'本地检查状态不可解析'})

    def status_of(item):
        if item.get('usage') == 'technical_candidate_only' or item.get('provider') == 'CEIC':
            return _COVERAGE_STATUS['technical_candidate']
        if item.get('role') in ('inventory_scope_unresolved', 'quarantine') or not item.get('role'):
            return _COVERAGE_STATUS['needs_review']
        return _COVERAGE_STATUS['limited']

    def record(item, status=None):
        last = item.get('last')
        if isinstance(last, (date, datetime)):
            last = last.isoformat()
        elif not isinstance(last, str) or not last:
            last = None
        result = {'id':item.get('id'), 'name':item.get('name'), 'last_observation':last,
            'frequency':item.get('frequency'), 'unit':item.get('unit'), 'usage':item.get('usage'),
            'last_fetch_at':None, 'release_rule':item.get('release_rule'),
            'status':status or status_of(item), 'limits':item.get('limits', [])}
        for key in ('provider','source','role','snapshot_sha256','observations','basis_eligible','replay_eligible'):
            if key in item:
                result[key] = item[key]
        if not isinstance(result['limits'], list):
            result['limits'] = []
        return result

    def category(category, items, failed=False, requirements=None):
        statuses = {item['status'] for item in items}
        state = (_COVERAGE_STATUS['data_check'] if failed and not items else
                 _COVERAGE_STATUS['missing'] if not items else
                 _COVERAGE_STATUS['needs_review'] if _COVERAGE_STATUS['needs_review'] in statuses else
                 _COVERAGE_STATUS['limited'] if _COVERAGE_STATUS['limited'] in statuses else
                 _COVERAGE_STATUS['technical_candidate'])
        result = {'category':category, 'label':_COVERAGE_LABELS[category], 'status':state,
                  'indicators':items}
        if requirements:
            result['requirements'] = requirements
        return result

    source_failures = {str(item.get('source')) for item in failures if isinstance(item, dict)}
    if product in _FINANCIAL_PRODUCTS:
        underlying, index_state = [], 'missing'
        if product in INDEX_SERIES:
            try:
                snapshot = index_daily(folder, product=product)
                points = snapshot.get('points', [])
                for item in snapshot.get('indicators', []):
                    if item.get('product') != product:
                        continue
                    dates = [point.get('observation_date') for point in points
                             if isinstance(point, dict) and point.get('code') == item.get('code')
                             and point.get('observation_date') is not None]
                    entry = {**item, 'id':'index_close:'+str(item.get('code')), 'provider':snapshot.get('source','Wind'),
                        'usage':'daily_close_basis_only', 'role':'observed_index_close_basis',
                        'basis_eligible':False, 'replay_eligible':False,
                        'snapshot_sha256':snapshot.get('sha256')}
                    if dates:
                        entry['last'] = max(dates)
                    underlying.append(record(entry, _COVERAGE_STATUS['limited']))
                if not underlying:
                    raise ValueError('指数快照缺少目标品种')
                index_state = 'ok'
            except FileNotFoundError:
                if (folder / 'index_review.json').exists():
                    failures.append({'source':'Index', 'reason':'指数审阅指针所需数据或身份文件缺失'})
                    index_state = 'failed'
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, duckdb.Error):
                failures.append({'source':'Index', 'reason':'指数参考快照不可用或证据校验失败'})
                index_state = 'failed'
        result = [category('financial_underlying', underlying, index_state == 'failed',
                    ('股指使用已验收指数日收盘参考；国债需可交割券、转换因子、券价、应计等专用标的',)),
                  category('carrying_cost', [], False,
                    ('金融专用持有成本：资金成本、分红/融资及合约条款；当前无已核验指标',))]
        extras = [record(item) for item in catalog if product in item.get('products', [])]
        if extras:
            result.append(category('other', extras))
    else:
        groups = {key:[] for key in ('exchange_inventory','commercial_inventory','reference_spot',
                                     'supply_demand','other')}
        for item in catalog:
            if product is not None and product not in item.get('products', []):
                continue
            role = item.get('role')
            key = _COVERAGE_ROLES.get(role)
            if key is None and item.get('provider') == 'CEIC':
                key = _COVERAGE_CEIC_CATEGORIES.get(str(item.get('code') or item.get('series_id')))
            groups[key or 'other'].append(record(item))
        wind_failed, ceic_failed = 'Wind' in source_failures, 'CEIC' in source_failures
        result = [category(key, groups[key], wind_failed or ceic_failed)
                  for key in ('exchange_inventory','commercial_inventory','reference_spot','supply_demand')]
        if groups['other']:
            result.append(category('other', groups['other']))
    note = ('覆盖状态反映当前保存快照，不随截止日期回放历史可用性。'
            f'最近Wind EDB本地检查：{local_check}（不代表CEIC检查或实际取数时间）。'
            '逐指标取数完成时间、正式发布规则尚待核验，不自动判定过期。'
            '参考现货不等于交割现货，候选展示不改变基差或回放准入；金融品种单列标的与持有成本。')
    if failures:
        note += '已保留来源校验失败，失败不改写为“缺少指标”：' + '；'.join(
            str(item.get('source')) for item in failures if isinstance(item, dict)) + '。'
    return result, note


def ceic_review_catalog(folder):
    """Locate a verified derivative batch, not an arbitrary newest file or production table."""
    folder = Path(folder)
    review_root = folder / 'web_review'
    pointer_path = folder / 'selected_review.json'
    pointer = None
    if pointer_path.exists():
        pointer = json.loads(pointer_path.read_bytes())
        name = pointer.get('review_root', '')
        if (pointer.get('production_write') is not False or pointer.get('accepted') is not False
                or not re.fullmatch(r'web_review|(?:export|merge)_[a-f0-9]{16}', name)):
            raise RuntimeError('CEIC隔离审阅指针非法')
        review_root = folder / name
        if review_root.is_symlink() or review_root.resolve().parent != folder.resolve():
            raise RuntimeError('CEIC隔离审阅目录越界')
    raw, audit = review_root / 'candidates.jsonl', review_root / 'audit.json'
    if not raw.exists() or not audit.exists():
        if pointer is not None:
            raise RuntimeError('CEIC隔离审阅指针数据缺失')
        return []
    source_hash, audit_hash = sha256(raw.read_bytes()).hexdigest(), sha256(audit.read_bytes()).hexdigest()
    if pointer is not None and (pointer.get('source_sha256') != source_hash or pointer.get('audit_sha256') != audit_hash):
        raise RuntimeError('CEIC隔离审阅指针哈希不匹配')
    selected = folder / ('wide_' + sha256((source_hash + audit_hash).encode()).hexdigest()[:16])
    report = json.loads((selected / 'audit.json').read_bytes())
    if (report.get('accepted') is not False or report.get('production_write') is not False
            or report.get('source_jsonl_sha256') != source_hash or report.get('source_audit_sha256') != audit_hash):
        raise RuntimeError('CEIC候选证据不匹配')
    result = []
    for table in report['tables']:
        frequency = table['frequency']
        if frequency not in ('M', 'D') or table.get('difference_rows') != 0:
            raise RuntimeError('CEIC候选频率或审计异常')
        path = selected / ('candidates_' + frequency + '.parquet')
        if sha256(path.read_bytes()).hexdigest() != table['sha256']:
            raise RuntimeError('CEIC候选快照校验失败')
        for item in table['indicators']:
            if not re.fullmatch(r'[0-9]+', item['series_id']):
                raise RuntimeError('CEIC指标身份异常')
            result.append({**item, 'code':item['series_id'], 'frequency':frequency,
                'snapshot_sha256':table['sha256'], '_path':str(path)})
    return result


def fundamental_analysis(runtime, product=None, indicator=None, end=None):
    """Current-vintage observation analysis. Never a point-in-time backtest or signal."""
    runtime = Path(runtime)
    cutoff = date.fromisoformat(end) if end else date.today()
    catalog, failures = [], []
    try:
        wind = fundamental_review(runtime / 'fundamental_lab')
        for item in wind['indicators']:
            catalog.append({**item, 'id':'wind:'+item['code'], 'provider':'Wind',
                'products':FUNDAMENTAL_PRODUCTS.get(item['code'], []),
                'usage':'scope_limited_review', 'basis_eligible':False, 'replay_eligible':False,
                'snapshot_sha256':wind['snapshot_sha256']})
    except (OSError, RuntimeError, ValueError, KeyError, duckdb.Error):
        failures.append({'source':'Wind', 'reason':'审阅快照不可用或证据校验失败'})
    try:
        for item in ceic_review_catalog(runtime / 'ceic_lab'):
            catalog.append({**item, 'id':'ceic:'+item['code'], 'provider':'CEIC',
                'products':FUNDAMENTAL_PRODUCTS.get(item['code'], []),
                'usage':'technical_candidate_only', 'basis_eligible':False, 'replay_eligible':False,
                'limits':['仅已保存导出的技术审阅，尚未业务准入', '样本范围与发布时间待核；禁止基差、加总或自动拼接']
                         + (['全国流通领域旬度参考价，D为存储频率；不是地区即期交割报价，不前填、不直接计算基差']
                            if item['code'] in _CEIC_NBS_PRICE_CODES else [])
                         + _CEIC_SERIES_LIMITS.get(item['code'], [])})
    except (OSError, RuntimeError, ValueError, KeyError, duckdb.Error):
        failures.append({'source':'CEIC', 'reason':'候选快照不可用或证据校验失败'})
    if product:
        product = validate_product(product)
        catalog = [i for i in catalog if product in i['products']]
    coverage, coverage_note = _fundamental_coverage(runtime, product, catalog, failures)
    result = {'mode':'isolated_analysis', 'as_of':cutoff.isoformat(), 'production_enabled':False,
        'indicators':[{k:v for k,v in i.items() if k != '_path'} for i in catalog], 'failures':failures,
        'coverage':coverage, 'coverage_note':coverage_note,
        'warnings':['按观测期筛选当前保存版本，不代表历史当时已知；禁止用于时点回测',
                    '不换算、不前填、不合并库存，不产生买卖或基差信号'],
        'missing_reason': (None if catalog else
            '此页暂无其他已关联序列；股指日线请见基差分析，标的与持有成本状态以上方覆盖表为准' if product in INDEX_SERIES else
            '国债尚需可交割券、转换因子、券价与持有成本专用数据' if product in ('T','TF','TS','TL') else
            '该品种尚无可关联审阅指标；缺少数据不代表没有基本面需求')}
    if indicator is None:
        return result
    item = next((i for i in catalog if i['id'] == indicator), None)
    if item is None:
        raise ValueError('指标不在当前品种的可审阅目录中')
    if item['provider'] == 'Wind':
        response = fundamental_review(runtime / 'fundamental_lab', item['code'], end=cutoff.isoformat(), limit=5000)
        points = response['points']
        while response['next_offset'] is not None:
            response = fundamental_review(runtime / 'fundamental_lab', item['code'], end=cutoff.isoformat(), offset=response['next_offset'], limit=5000)
            if response['snapshot_sha256'] != item['snapshot_sha256']:
                raise RuntimeError('读取期间基本面版本改变，请刷新')
            points.extend(response['points'])
        if response['snapshot_sha256'] != item['snapshot_sha256']:
            raise RuntimeError('读取期间基本面版本改变，请刷新')
    else:
        with query_connection() as con:
            rows = con.execute('SELECT period_date, s_' + item['code'] + ' FROM read_parquet(?) '
                'WHERE period_date <= ? AND s_' + item['code'] + ' IS NOT NULL ORDER BY period_date',
                [item['_path'], cutoff]).fetchall()
        if sha256(Path(item['_path']).read_bytes()).hexdigest() != item['snapshot_sha256']:
            raise RuntimeError('读取期间CEIC版本改变，请刷新')
        points = [{'observation_date':str(day), 'raw_value':value} for day,value in rows]
    from .analytics import fundamental_statistics
    result.update(indicator=indicator, snapshot_sha256=item['snapshot_sha256'],
        points=points, **fundamental_statistics(points, cutoff.isoformat(), item['frequency']))
    return result


def clean(value):
    if isinstance(value, Decimal):
        return clean(float(value))
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def validate_product(product):
    product = product.upper()
    if not PRODUCT.fullmatch(product):
        raise ValueError('品种必须为 1—3 位英文字母')
    return product


class DailyStore:
    def __init__(self, root=None, runtime=None):
        self._source_root = Path(root or os.environ.get('FUTURES_DATA_ROOT') or Path(__file__).resolve().parents[1] / 'data').absolute()
        if self._source_root.name != 'futureshistoryprices1d':
            self._source_root /= 'futureshistoryprices1d'
        self._pinned_root = ContextVar('futures_root', default=None)
        self.runtime = Path(runtime or os.environ.get('FUTURES_RUNTIME', str(Path(__file__).resolve().parents[1] / 'runtime')))
        if self.runtime.resolve() == self.root.resolve() or self.root.resolve() in self.runtime.resolve().parents:
            raise ValueError('运行目录不能位于生产日线源内')
        self.cache = ByteCache(int(os.environ.get('FUTURES_CACHE_MB', '96')) * 1024**2)
        self.lock = RLock()
        self.persist_queries = os.environ.get('DASHBOARD_PUBLIC_READONLY') != '1'

    @property
    def root(self):
        return self._pinned_root.get() or self._source_root.resolve()

    @contextmanager
    def read_session(self):
        token = self._pinned_root.set(self.root)
        try:
            yield
        finally:
            self._pinned_root.reset(token)

    def market_snapshot(self, end=None):
        """One bounded, read-only scan for the radar; never write source or snapshots."""
        end = end or date.today().isoformat()
        start = (date.fromisoformat(end)-timedelta(days=400)).isoformat()
        with self.lock:
            files = self._files(start, end)
            before = self._fingerprints(files)
            if not files:
                raise FileNotFoundError('全市场日线源不可用')
            key = (start, end, before)
            cached = self.cache.get('market')
            if cached and cached[0] == key:
                return cached[1]
            rows = self._query(files, """SELECT exchange,upper(symbol) symbol,trade_date,
                close,settle_price,volume,position FROM read_parquet(?, union_by_name=true)
                WHERE regexp_full_match(symbol, '[A-Za-z]{1,3}[0-9]{4}')
                AND right(symbol,4) NOT IN ('8888','9998','9999')
                AND trade_date BETWEEN ?::DATE AND ?::DATE
                ORDER BY trade_date,symbol LIMIT 600001""", (start, end))
            if len(rows) > 600000:
                raise ValueError('全市场窗口超过60万行，停止扫描')
            if before != self._fingerprints(files):
                raise OSError('扫描期间源文件变化，请重试')
            result = {'rows':rows, 'requested_end':end, 'start':start,
                'snapshot_id':sha256(json.dumps([key, rows], sort_keys=True).encode()).hexdigest()[:24]}
            self.cache['market'] = (key, result)
            return result

    def position_peaks(self, end):
        """Aggregate contract peaks once per source version, without loading daily history into Python."""
        with self.lock:
            files = [p for p in self._files() if p.parent.name.split('=', 1)[1] <= end[:7]]
            if not files:
                raise FileNotFoundError('历史持仓源不可用')
            before = self._fingerprints(files)
            key = (end, before)
            cached = self.cache.get('peaks')
            if cached and cached[0] == key:
                return cached[1]
            rows = self._query(files, """SELECT upper(symbol) symbol, max(position) peak
                FROM read_parquet(?, union_by_name=true)
                WHERE regexp_full_match(symbol, '[A-Za-z]{1,3}[0-9]{4}')
                  AND right(symbol,4) NOT IN ('8888','9998','9999')
                  AND trade_date <= ?::DATE AND position >= 0 AND isfinite(position)
                GROUP BY upper(symbol)""", (end,))
            if before != self._fingerprints(files):
                raise OSError('历史持仓源已变化，请重试')
            self.cache['peaks'] = (key, rows)
            return rows

    def _files(self, start=None, end=None):
        files = sorted(self.root.glob('year_month=*/*.parquet'))
        if start and end:
            files = [p for p in files if start[:7] <= p.parent.name.split('=', 1)[1] <= end[:7]]
        return files

    @staticmethod
    def _fingerprints(files):
        return fingerprint(files)

    @staticmethod
    def _query(files, sql, params=()):
        with query_connection() as con:
            cursor = con.execute(sql, [[str(p) for p in files], *params])
            names = [d[0] for d in cursor.description]
            return clean([dict(zip(names, row)) for row in cursor.fetchall()])

    def products(self):
        files = self._files()
        if not files:
            return []
        # Only the latest month is scanned for current coverage.
        month = files[-1].parent
        files = [p for p in files if p.parent == month]
        return self._query(files, """SELECT upper(regexp_extract(symbol, '^([A-Za-z]+)', 1)) product,
            max(trade_date) as_of, count(distinct symbol) contract_count
            FROM read_parquet(?, union_by_name=true)
            WHERE regexp_full_match(symbol, '[A-Za-z]{1,3}[0-9]{4}')
            AND right(symbol,4) NOT IN ('8888','9998','9999')
            GROUP BY 1 ORDER BY 1""")

    def read_all(self, product, end):
        """Read the available partition range; retain snapshot validation/row limits."""
        end_date = date.fromisoformat(end)
        files = [p for p in self._files() if p.parent.name.split('=', 1)[1] <= end[:7]]
        if not files:
            raise FileNotFoundError('无法确定全部历史范围：本地分区不可用')
        start = files[0].parent.name.split('=', 1)[1] + '-01'
        return self.read(product, start, end, max_years=end_date.year-date.fromisoformat(start).year+1)

    def read(self, product, start=None, end=None, *, max_years=5):
        product = validate_product(product)
        default_end = not end
        end_pointer = self.runtime / 'snapshots' / f'latest-{product}.json'
        source_error = None
        if not end:
            try:
                coverage = next((p for p in self.products() if p['product'] == product), None)
                if not coverage:
                    raise FileNotFoundError('品种最新日线源不可用')
                end = coverage['as_of']
            except (OSError, duckdb.Error) as exc:
                source_error = exc
                end = (json.loads(end_pointer.read_text(encoding='utf-8'))['end']
                       if end_pointer.exists() else date.today().isoformat())
        end_date = date.fromisoformat(end)
        start = start or (end_date - timedelta(days=365)).isoformat()
        start_date = date.fromisoformat(start)
        if not 0 <= (end_date - start_date).days <= 366 * max_years:
            raise ValueError(f'日期范围须按先后顺序，且不超过{max_years}年')
        key = sha256(f'{product}|{start}|{end}'.encode()).hexdigest()[:24]
        def remember_end():
            if default_end and self.persist_queries:
                end_pointer.parent.mkdir(parents=True, exist_ok=True)
                tmp = end_pointer.with_suffix('.tmp')
                tmp.write_text(json.dumps({'end': end}), encoding='utf-8')
                os.replace(tmp, end_pointer)
        with self.lock:
            pointer = self.runtime / 'snapshots' / f'{key}.json'
            try:
                if source_error:
                    raise source_error
                files = self._files(start, end)
                before = self._fingerprints(files)
                if not files:
                    raise FileNotFoundError('请求范围内没有日线文件')
                cached = self.cache.get(key)
                if cached and cached[0] == before:
                    remember_end()
                    return cached[1]
                rows = self._query(files, f"""SELECT {','.join(FIELDS)} FROM read_parquet(?, union_by_name=true)
                    WHERE upper(regexp_extract(symbol, '^([A-Za-z]+)', 1)) = ?
                    AND regexp_full_match(symbol, '[A-Za-z]{{1,3}}[0-9]{{4}}')
                    AND right(symbol,4) NOT IN ('8888','9998','9999')
                    AND trade_date BETWEEN ?::DATE AND ?::DATE
                    ORDER BY trade_date,exchange,symbol,trade_datetime LIMIT 100001""", (product, start, end))
                if len(rows) > 100000:
                    raise ValueError('单次快照超过十万行，请缩短日期范围')
                keys = [(r['exchange'], r['symbol'], r['trade_date']) for r in rows]
                if len(keys) != len(set(keys)):
                    raise ValueError('日线逻辑键重复，拒绝发布')
                hashes = []
                for p in files:
                    hash_key, stamp = ('sha256', str(p)), fingerprint([p])
                    cached_hash = self.cache.get(hash_key)
                    if not cached_hash or cached_hash[0] != stamp:
                        with p.open('rb') as source:
                            cached_hash = (stamp, file_digest(source, 'sha256').hexdigest())
                        self.cache[hash_key] = cached_hash
                    hashes.append({'file': str(p.relative_to(self.root)), 'sha256': cached_hash[1]})
                if before != self._fingerprints(files):
                    raise OSError('读取期间源文件变化，拒绝发布')
                body = {'product': product, 'start': start, 'end': end, 'rows': rows, 'files': hashes}
                encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
                snapshot_id = sha256(encoded).hexdigest()[:24]
                body['meta'] = {'snapshot_id': snapshot_id, 'source': 'local_parquet',
                    'as_of': max((r['trade_date'] for r in rows), default=None),
                    'requested_end': end, 'row_count': len(rows), 'quality_flags': [],
                    'formula_version': 'daily-v1', 'realtime': False, 'stale': False,
                    'contract_catalog': 'observed_daily_only', 'available_at': datetime.now().astimezone().isoformat()}
                if self.persist_queries:
                    pointer.parent.mkdir(parents=True, exist_ok=True)
                    immutable = pointer.parent / f'{snapshot_id}.json'
                    payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
                    if not immutable.exists():
                        tmp = immutable.with_suffix('.tmp')
                        tmp.write_bytes(payload)
                        os.replace(tmp, immutable)
                    tmp = pointer.with_suffix('.tmp')
                    tmp.write_bytes(payload)
                    os.replace(tmp, pointer)
                remember_end()
            except (OSError, duckdb.Error):
                if not pointer.exists():
                    raise
                body = json.loads(pointer.read_text(encoding='utf-8'))
                body['meta']['quality_flags'] = ['source_unavailable_using_previous_snapshot']
                body['meta']['stale'] = True
                return body
            self.cache[key] = (before, body)
            return body
