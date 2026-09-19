"""Read-only source inventory. No source entrypoints, Wind calls or DB writes."""
import sys
import json
from pathlib import Path
from collections import Counter
import math
import hashlib

sys.stdout.reconfigure(encoding='utf-8')
source = Path(r'E:\阿里云数据库\wind更新')
fund = source / 'fundamental'
registry = json.loads((fund / 'indicators.json').read_text(encoding='utf-8'))['indicators']

if '--workbook' in sys.argv:
    import openpyxl
    wb = openpyxl.load_workbook(fund / '期货基本面盲测对照表.xlsx', read_only=True, data_only=True)
    s2 = list(wb['2-盲测命中明细'].values)[1:]
    s3 = list(wb['3-非价格候选待核对'].values)[1:]
    gaps = [(i, r) for i, r in enumerate(wb['4-缺失清单'].values, 1)
            if i > 1 and r[2] in {'SHFE','DCE','CZCE','INE','GFEX','CFFEX'}]
    result = {'matrix_rows': wb['1-全品种覆盖矩阵'].max_row - 1,
              'spot_candidates':len(s2), 'nonprice_candidates':len(s3),
              'adopted_s2':sum(str(r[6] or '').lower() in {'是','y','1','true','√','✓'} for r in s2),
              'real_names_s2':sum(bool(r[7]) for r in s2),
              'gap_rows':len(gaps), 'gap_products':len({r[0] for _,r in gaps}),
              'gap_categories':dict(Counter(r[3] for _,r in gaps)),
              'filled_gap_codes':sum(bool(r[5]) for _,r in gaps),
              'gaps': [{'row':i,'product':r[0],'name':r[1],'category':r[3],'search':r[4]} for i,r in gaps],
              's3_not_imported':s3,
              'register_only_codes':sorted({r['code'] for r in registry}-{r[2] for r in s2})}
    if '--coverage' in sys.argv:
        root = Path(__file__).resolve().parents[1]
        candidate_path = root / 'fundamental_candidates.json'
        candidates = json.loads(candidate_path.read_text(encoding='utf-8'))
        review_path = root / 'runtime/fundamental_lab/20260907_probe_review.json'
        review = json.loads(review_path.read_text(encoding='utf-8'))
        raw = root / review['source']
        if hashlib.sha256(raw.read_bytes()).hexdigest().lower() != review['source_sha256'].lower():
            raise ValueError('Saved review raw hash mismatch')
        evidence = {r['code']: {k: r[k] for k in ('observations', 'first', 'last', 'outside_requested_range')}
                    for r in review['series']}
        codes = {}
        for i, item in enumerate(registry, 1):
            codes.setdefault(item['code'], {'code': item['code'], 'references': [], 'claims': []})
            codes[item['code']]['references'].append(f'OLD-{i:03d}')
            codes[item['code']]['claims'].append({'origin': 'legacy_registry', **item})
        for item in candidates['indicators']:
            codes.setdefault(item['code'], {'code': item['code'], 'references': [], 'claims': []})
            codes[item['code']]['references'].append(item['id'])
            codes[item['code']]['claims'].append({'origin': 'alice_candidate', **item})
        for item in codes.values():
            item.update(identity_status='unverified', production_ready=False,
                        saved_probe=evidence.get(item['code']))
        matrix = []
        for row_number, row in enumerate(wb['1-全品种覆盖矩阵'].values, 1):
            if row_number == 1 or not row[1]:
                continue
            product = str(row[1]).strip().upper()
            matrix.append({'source_row': row_number, 'exchange_claim': row[0],
                'product_raw': row[1], 'product': product, 'name_claim': row[2],
                'scope_status': 'needs_active_directory_verification',
                'branch': 'financial' if row[0] == 'CFFEX' else 'commodity_candidate',
                'core_needs': ['underlying_and_carry_inputs'] if row[0] == 'CFFEX'
                              else ['comparable_spot', 'relevant_inventory', 'warrant_if_applicable']})
        needs = [{'id': f'GAP-{i:03d}', **gap, 'status': 'unverified_requirement',
                  'priority': 1 if gap['product'] in {'RB', 'CU', 'M'} else 2}
                 for i, gap in enumerate(result['gaps'], 1)]
        financial = [
            {'id': 'FIN-01', 'products': ['IM', 'IC', 'IF', 'IH'],
             'need': '对应指数现货覆盖、资金成本、分红假设；先查已有日线，不限定EDB', 'status': 'needs_source_inventory'},
            {'id': 'FIN-02', 'products': ['T', 'TF', 'TS', 'TL'],
             'need': '可交割券、转换因子、券价、应计、资金数据；单独模型', 'status': 'needs_source_inventory'}]
        result = {'kind': 'generated_readonly_inventory_not_registry', 'production_enabled': False,
            'source_hashes': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                (fund / '期货基本面盲测对照表.xlsx', fund / 'indicators.json', candidate_path, review_path)},
            'counts': {'matrix_rows': len(matrix), 'old_codes': len(registry),
                       'new_candidates': len(candidates['indicators']), 'unique_codes': len(codes),
                       'old_gap_requirements': len(needs), 'new_unresolved': len(candidates['unresolved']),
                       'verified_codes': 0},
            'products_to_verify': matrix, 'codes': list(codes.values()), 'requirements': needs,
            'new_unresolved': candidates['unresolved'], 'financial_requirements': financial,
            'nonprice_sheet_clues': result['s3_not_imported'],
            'warnings': ['旧标签不作为身份确认，不自动修正歧义品种代码',
                         '多码/代码区间不展开；需求不与代码数相加；不同库存口径不合并',
                         '所有状态保守保留待核验；该输出不可直接作为取数启用配置']}
    print(json.dumps(result, ensure_ascii=False, default=str))
    wb.close()
else:
    import duckdb
    import pymysql
    sys.path.insert(0, str(source))
    import update_config as cfg
    con = duckdb.connect()
    con.execute("SET memory_limit='512MB'")
    con.execute('SET threads=2')
    glob = (Path(cfg.LOCAL_TABLE_ROOT)/cfg.TBL_FUNDAMENTAL/'year_month=*'/'*.parquet').as_posix()
    con.from_parquet(glob,hive_partitioning=True,union_by_name=True).create_view('f')
    rows = con.execute('SELECT indicator, CAST(trade_date AS DATE), value, name, category, freq, unit, product FROM f').fetchall()
    groups = con.execute('SELECT indicator, any_value(product), any_value(category), count(*), min(trade_date), max(trade_date), count(*) FILTER (WHERE value IS NULL OR NOT isfinite(value)) FROM f GROUP BY 1 ORDER BY 1').fetchall()
    local = {(r[0],r[1]):r[2:] for r in rows}
    summary = {'local_rows':len(rows),'local_duplicate_keys':len(rows)-len(local),'local_groups':groups,
               'registry_count':len(registry),'registry_categories':dict(Counter(r['category'] for r in registry)),
               'registry_products':len({r['product'] for r in registry}),
               'registry_empty_unit':sum(not r['unit'] for r in registry),
               'raw_files':len(list((fund/'_fund_raw').glob('*.parquet'))),
               'state_file_exists':Path(cfg.FUND_STATE_FILE).exists()}
    db = None
    try:
        db = pymysql.connect(host=cfg.RDS_HOST,port=cfg.RDS_PORT,user=cfg.RDS_USER,password=cfg.RDS_PASS,
                             database=cfg.RDS_DB,charset='utf8mb4',connect_timeout=8,read_timeout=20,autocommit=False)
        with db.cursor() as cur:
            cur.execute('START TRANSACTION READ ONLY')
            cur.execute('SELECT indicator,trade_date,value,name,category,freq,unit,product FROM futuresfundamentals')
            rr = cur.fetchall()
        remote = {(r[0],r[1]):r[2:] for r in rr}
        both = local.keys() & remote.keys()
        value_diff = [k for k in both if (local[k][0] is None)!=(remote[k][0] is None) or
                      (local[k][0] is not None and remote[k][0] is not None and
                       not math.isclose(float(local[k][0]),float(remote[k][0]),rel_tol=1e-10,abs_tol=1e-8))]
        meta_diff = [k for k in both if local[k][1:] != remote[k][1:]]
        summary['rds']={'rows':len(rr),'duplicate_keys':len(rr)-len(remote),
                        'local_only':len(local.keys()-remote.keys()),'rds_only':len(remote.keys()-local.keys()),
                        'value_diff':len(value_diff),'metadata_diff':len(meta_diff),
                        'max_date':max((k[1] for k in remote),default=None),
                        'metadata_diff_by_indicator':dict(Counter(k[0] for k in meta_diff)),
                        'metadata_examples':[{'key':k,'local':local[k][1:],'rds':remote[k][1:]} for k in sorted(meta_diff)[:3]]}
    except Exception as exc:
        summary['rds']={'error_type':type(exc).__name__,'detail':'Connection/query failed; credentials and host intentionally omitted'}
    finally:
        if db:
            db.rollback()
            db.close()
        con.close()
    print(json.dumps(summary,ensure_ascii=False,default=str))
