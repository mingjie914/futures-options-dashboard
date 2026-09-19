"""Isolated EDB discovery/probe tool. Never imports production modules or publishes data."""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from uuid import uuid4
import duckdb

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path(r"E:\阿里云数据库\wind更新\fundamental\indicators.json")
WIND_PYTHON = Path(r"C:\pythonvenv\Scripts\python.exe")
_EMPTY_OBSERVATION_ISSUE = "No non-missing observations returned"


def codes_list(value):
    codes = [item.strip().upper() for item in value.split(",")]
    if not codes or any(not re.fullmatch(r"[A-Z][0-9]{6,12}", code) for code in codes):
        raise ValueError("Provide explicit comma-separated EDB codes, not ranges or wildcards")
    return list(dict.fromkeys(codes))


def plan_requests(codes, mode, end, waterlines=None, start=None):
    """Pure planning; waterlines are accepted/published dates, not raw-fetch maxima."""
    codes = codes_list(",".join(codes))
    end_day = date.fromisoformat(end)
    waterlines = waterlines or {}
    if mode not in {"initial", "append", "repair"}:
        raise ValueError("Unknown mode")
    if mode == "append" and start is not None:
        raise ValueError("append cannot override its published waterline; use explicit repair")
    if mode != "append" and start is None:
        raise ValueError("initial/repair require explicit start and end")
    if start is not None and date.fromisoformat(start) > end_day:
        raise ValueError("start must not exceed end")
    result = []
    for code in codes:
        if mode == "append":
            if not waterlines.get(code):
                raise ValueError(f"{code}: no published waterline; run initial explicitly")
            begin = date.fromisoformat(waterlines[code]) + timedelta(days=1)
        else:
            begin = date.fromisoformat(start)
        result.append({"code": code, "mode": mode, "start": begin.isoformat(),
                       "end": end, "due": begin <= end_day,
                       "existing_key_policy": "reviewed_update" if mode == "repair" else "keep",
                       "publishes": False})
    return result


def inventory(path=REGISTRY):
    records = json.loads(Path(path).read_text(encoding="utf-8-sig"))["indicators"]
    return {"source": str(path), "count": len(records), "trusted": False,
            "indicators": records}


def candidate_snapshot(source_root):
    """Reuse saved local/probe values for identity review, never accept or fetch them."""
    import duckdb
    folder = ROOT / 'runtime/fundamental_lab'
    catalog_path = folder / 'coverage_status.json'
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    if catalog.get('production_enabled') is not False:
        raise ValueError('Isolated candidate inventory required')
    candidates = {r['code']:r for r in catalog['codes']
                  if r.get('review_status') in ('identity_evidence_needed', 'terminal_metadata_only',
                                               'not_fundamental', 'background_only')}
    if not candidates or any(not re.fullmatch(r'[SG]\d{7}', c) for c in candidates):
        raise ValueError('Explicit unverified candidate codes required')
    sources, values = {}, {}
    metadata_claims = {c:set() for c in candidates}

    def add(code, day, value, source):
        if code not in candidates:
            return
        if (not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                or day > date.today().isoformat() or type(value) not in (int, float)
                or not math.isfinite(value)):
            raise ValueError('Invalid saved candidate observation; old pointer retained')
        key = code, day
        if key in values and values[key][0] != value:
            raise ValueError(f'Conflicting candidate values: {code}/{day}; explicit review needed')
        number, provenance = values.setdefault(key, (value, set()))
        provenance.add(source)

    # These are saved observation dates, NOT proof of Wind request coverage.
    files = sorted(Path(source_root).resolve().glob('year_month=*/*.parquet'))
    if not files:
        raise ValueError('No saved legacy Parquet files')
    with duckdb.connect() as con:
        con.execute("SET threads=2")
        con.execute("SET memory_limit='512MB'")
        sources.update({str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in files})
        for code, day, value, name, category, freq, unit, product, filename in con.execute(
                'SELECT indicator, CAST(trade_date AS DATE), value, name, category, freq, unit, product, filename FROM read_parquet(?, filename=true)',
                [[str(path) for path in files]]).fetchall():
            add(code, day.isoformat() if day else None, value, str(Path(filename)))
            if code in candidates:
                metadata_claims[code].add((name,category,freq,unit,product))
        for path in files:
            if hashlib.sha256(path.read_bytes()).hexdigest() != sources[str(path)]:
                raise ValueError('Legacy source changed during read')
    candidate_path = ROOT / 'fundamental_candidates.json'
    claims = json.loads(candidate_path.read_bytes())
    if claims.get('production_enabled') is not False:
        raise ValueError('Isolated probe candidate catalogue required')
    run = (ROOT / claims['raw_probe']).resolve()
    audit_path = (ROOT / claims['audit']).resolve()
    if run.parent != folder.resolve() or audit_path.parent != folder.resolve():
        raise ValueError('Saved probe/audit must remain in lab')
    raw = run.read_bytes()
    audit = json.loads(audit_path.read_bytes())
    digest = hashlib.sha256(raw).hexdigest()
    if digest != audit['source_sha256'].lower():
        raise ValueError('Saved probe hash changed')
    payload = json.loads(raw)
    if payload.get('production_write') is not False or payload.get('state') not in ('completed','failed'):
        raise ValueError('Finished isolated probe required')
    sources[str(run)] = digest
    request = payload['request']
    for result in payload['results']:
        if result['code'] not in candidates:
            continue
        if result.get('error') or result.get('error_code') != 0 or result.get('issues'):
            raise ValueError('Unreviewed probe error; old candidate pointer retained')
        for row in result['observations']:
            if row.get('missing') is not False or not request['start'] <= row['date'] <= request['end']:
                raise ValueError('Unreviewed probe row; old candidate pointer retained')
            add(result['code'], row['date'], row['value'], str(run))
    if set(candidates) != {code for code, day in values}:
        raise ValueError('Some candidate codes have no saved values; inspect before snapshot')
    sources[str(catalog_path)] = hashlib.sha256(catalog_bytes).hexdigest()
    sources[str(candidate_path)] = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    sources[str(audit_path)] = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    rows = [(code, day, value, json.dumps(sorted(provenance), ensure_ascii=False))
            for (code, day), (value, provenance) in sorted(values.items())]
    signature = hashlib.sha256(json.dumps({'version':1,'sources':sources}, sort_keys=True).encode()).hexdigest()
    path = folder / f'candidates_{signature}.parquet'
    pointer = folder / 'candidate_review.json'
    previous = json.loads(pointer.read_bytes()) if pointer.exists() else None
    # Coverage classifications are an overlay, not new raw data. Reuse the pinned
    # file when only that report changed; still verify every stored row below.
    if (previous and re.fullmatch(r'candidates_[a-f0-9]{64}\.parquet', previous.get('file',''))
            and {k:v for k,v in previous['sources'].items() if k != str(catalog_path)} ==
                {k:v for k,v in sources.items() if k != str(catalog_path)}):
        path = folder / previous['file']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != previous['sha256']:
            raise ValueError('Saved candidate hash changed; pointer retained')
    with duckdb.connect() as con:
        con.execute('CREATE TABLE candidates (code VARCHAR, observation_date VARCHAR, raw_value DOUBLE, source_files VARCHAR)')
        for begin in range(0, len(rows), 10000):
            columns = [list(column) for column in zip(*rows[begin:begin+10000])]
            con.execute('INSERT INTO candidates SELECT unnest(?::VARCHAR[]), unnest(?::VARCHAR[]), unnest(?::DOUBLE[]), unnest(?::VARCHAR[])', columns)
        if not path.exists():
            temporary = path.with_suffix('.tmp')
            try:
                con.execute('COPY (SELECT * FROM candidates ORDER BY code,observation_date) TO ? (FORMAT PARQUET)', [str(temporary)])
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        restored = con.execute('SELECT * FROM read_parquet(?) ORDER BY code,observation_date', [str(path)]).fetchall()
        if restored != rows:
            raise ValueError('Candidate Parquet differs from saved values; pointer unchanged')
        stats = {c:(n,a,b) for c,n,a,b in con.execute('SELECT code,count(*),min(observation_date),max(observation_date) FROM candidates GROUP BY code').fetchall()}
    report = {'kind':'unverified_candidate_snapshot','version':1,'identity_status':'unverified',
        'production_enabled':False,'basis_eligible':False,'replay_eligible':False,'signal_eligible':False,
        'network_fetch':False,'request_coverage_known':False,'observations':len(rows),
        'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'sources':sources,
        'indicators':[{'code':c,'claims':candidates[c]['claims'],'references':candidates[c]['references'],
            'source_metadata_claims':[dict(zip(('name','category','frequency','unit','product'), variant))
                                      for variant in sorted(metadata_claims[c], key=str)],
            'name':None,'unit':None,'frequency':None,'observations':stats[c][0],
            'first':stats[c][1],'last':stats[c][2]} for c in sorted(candidates)]}
    if previous and {**previous, 'sources':sources} == report:
        return previous
    if not pointer.exists() or json.loads(pointer.read_bytes()) != report:
        save_run(report, pointer)
    return report


def normalize_response(response, code, start, end):
    """Preserve missing observations; API success cannot verify series identity."""
    result = {"code": code, "error_code": response.ErrorCode,
              "identity_status": "unverified", "observations": [], "issues": []}
    if response.ErrorCode != 0:
        result["issues"].append("Wind EDB request failed")
        return result
    returned = [str(c).upper() for c in getattr(response, "Codes", [])]
    if returned != [code] or len(response.Data) != 1:
        result["issues"].append("Returned code/data shape does not match request")
        return result
    if len(response.Times) != len(response.Data[0]):
        result["issues"].append("Times/Data length mismatch")
        return result
    seen = set()
    for moment, value in zip(response.Times, response.Data[0]):
        day = moment.date().isoformat() if isinstance(moment, datetime) else moment.isoformat()
        if day in seen or not start <= day <= end:
            result["issues"].append(f"Duplicate or out-of-range date: {day}")
        seen.add(day)
        missing = value is None
        try:
            number = None if missing else float(value)
            if number is not None and not math.isfinite(number):
                number, missing = None, True
        except (TypeError, ValueError):
            number, missing = None, True
            result["issues"].append(f"Non-numeric observation: {day}")
        result["observations"].append({"date": day, "value": number, "missing": missing})
    if not any(not row["missing"] for row in result["observations"]):
        result["issues"].append(_EMPTY_OBSERVATION_ISSUE)
    return result


def _is_append_empty_result(result, code, mode):
    """Recognize a successful low-frequency append with no published point."""
    if mode != "append" or not isinstance(result, dict):
        return False
    if result.get("code") != code or result.get("error") or result.get("error_code") != 0:
        return False
    if result.get("issues") != [_EMPTY_OBSERVATION_ISSUE]:
        return False
    observations = result.get("observations")
    return isinstance(observations, list) and all(
        isinstance(row, dict) and row.get("missing") is True and row.get("value") is None
        for row in observations)


def save_run(payload, path=None):
    folder = (ROOT / "runtime" / "fundamental_lab").resolve()
    if not folder.is_relative_to(ROOT.resolve()):
        raise ValueError("Lab output must remain within this project")
    folder.mkdir(parents=True, exist_ok=True)
    path = Path(path).resolve() if path else folder / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex + ".json")
    if path.parent != folder or path.suffix != ".json":
        raise ValueError("Checkpoint must be a JSON file in the isolated lab folder")
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return path


def _probe_worker(path):
    """Only this spawned process imports Wind; checkpoint after every indicator."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    request = payload["request"]
    start, end = request["start"], request["end"]
    try:
        from WindPy import w
        connected = w.start(waitTime=30)
        if connected.ErrorCode != 0:
            raise RuntimeError(f"Wind start error: {connected.ErrorCode}")
        for code in request["codes"]:
            try:
                if request.get('api') == 'wss':
                    response = w.wss(code, 'sec_name')
                    valid = (response.ErrorCode == 0 and list(response.Codes) == [code]
                             and [str(f).upper() for f in response.Fields] == ['SEC_NAME']
                             and len(response.Data) == 1 and len(response.Data[0]) == 1
                             and isinstance(response.Data[0][0], str) and bool(response.Data[0][0].strip()))
                    payload['results'].append({'code':code, 'error_code':response.ErrorCode,
                        'name':response.Data[0][0] if valid else None,
                        'issues':[] if valid else ['Invalid index identity response']})
                    save_run(payload, path)
                    continue
                if request.get('api') == 'wsd':
                    response = w.wsd(code, 'close', start, end, '')
                else:
                    response = w.edb(code, start, end, "")
                result = normalize_response(response, code, start, end)
                if request.get('api') == 'wsd' and [str(f).upper() for f in getattr(response, 'Fields', [])] != ['CLOSE']:
                    result['issues'].append('Expected CLOSE field')
                payload["results"].append(result)
            except Exception as exc:
                payload["results"].append({"code": code, "error": str(exc), "identity_status": "unverified"})
            save_run(payload, path)
    except Exception as exc:
        payload["error"] = str(exc)
    failed = "error" in payload or any(r.get("error") or r.get("error_code") or r.get("issues") for r in payload["results"])
    payload["state"] = "failed" if failed else "completed"
    save_run(payload, path)


def probe(codes, start, end, timeout=120, api='edb'):
    # No production imports, w.stop, or database access. Timeout covers the whole batch.
    if api in ('wsd', 'wss'):
        if not codes or len(set(codes)) != len(codes) or not set(codes).issubset({'000016.SH','000300.SH','000852.SH','000905.SH'}):
            raise ValueError('Index probe accepts only the four explicit index candidates')
        if date.fromisoformat(start) > date.fromisoformat(end):
            raise ValueError('start must not exceed end')
    elif api == 'edb':
        codes = codes_list(",".join(codes))
        plan_requests(codes, "initial", end, start=start)
    else:
        raise ValueError('Unsupported probe API')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number of seconds")
    if Path(sys.executable).resolve() != WIND_PYTHON.resolve():
        raise ValueError(f"Run probe with {WIND_PYTHON}")
    payload = {"requested_at": datetime.now(timezone.utc).isoformat(),
               "request": {"codes": codes, "start": start, "end": end, "options": "", "timeout": timeout,
                           "api":api, "fields":{'wsd':'close','wss':'sec_name'}.get(api)},
               "identity_status": "unverified", "production_write": False,
               "state": "running", "results": []}
    path = save_run(payload)
    process = multiprocessing.get_context("spawn").Process(target=_probe_worker, args=(path,))
    error = None
    try:
        process.start()
        process.join(timeout)
        if process.is_alive():
            error = f"Probe timed out after {timeout:g} seconds; incomplete results retained"
    except Exception as exc:
        error = f"Probe process failed: {exc}"
    finally:
        if process.pid is not None and process.is_alive():
            process.terminate()
            process.join(1)
            if process.is_alive():
                process.kill()
                process.join(1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if error is None and (process.exitcode != 0 or payload.get("state") not in {"completed", "failed"}):
        error = f"Probe process exited unexpectedly: {process.exitcode}"
    if error:
        payload.update(error=error, state="failed")
        save_run(payload, path)
    failed = (payload.get("state") != "completed" or not payload["results"]
              or len(payload["results"]) != len(codes))
    return path, 1 if failed else 0


def index_review(runs, metadata, start, end, publish=False):
    """Merge saved index evidence, keeping request coverage and observed waterlines separate."""
    import duckdb
    from backend.data import INDEX_SERIES, index_daily
    folder = (ROOT / 'runtime/fundamental_lab').resolve()
    if not date.fromisoformat(start) <= date.fromisoformat(end) < date.today():
        raise ValueError('Index history requires ordered dates through a completed local day')
    def saved(path):
        path = Path(path).resolve()
        if path.parent != folder or path.suffix != '.json':
            raise ValueError('Index evidence must be saved in the isolated lab')
        raw = path.read_bytes()
        return path.name, hashlib.sha256(raw).hexdigest(), json.loads(raw)
    name, digest, identity = saved(metadata)
    expected = dict(INDEX_SERIES.values())
    if (identity.get('production_write') is not False or identity.get('state') != 'completed'
            or identity.get('request', {}).get('api') != 'wss'
            or identity['request'].get('fields') != 'sec_name'
            or len(identity.get('results', [])) != len(expected)
            or {r['code']:r.get('name') for r in identity['results']} != expected
            or any(r.get('error_code') != 0 or r.get('issues') for r in identity['results'])):
        raise ValueError('Index code/name evidence does not match the explicit mapping')
    sources, values = {}, {}
    request_coverage = {code:[] for code in expected}
    for run in dict.fromkeys(runs):
        source, source_hash, payload = saved(run)
        sources[source] = source_hash
        req = payload.get('request', {})
        if (payload.get('production_write') is not False or req.get('api') != 'wsd'
                or req.get('fields') != 'close' or req.get('options') != ''
                or not req.get('codes') or not set(req['codes']).issubset(expected)):
            raise ValueError('Not an isolated index close batch')
        a, b = date.fromisoformat(req['start']), date.fromisoformat(req['end'])
        if a > b:
            raise ValueError('Invalid saved request interval')
        seen_codes = set()
        for result in payload.get('results', []):
            code = result['code']
            if code not in req['codes'] or code in seen_codes:
                raise ValueError('Unexpected or duplicate returned index')
            seen_codes.add(code)
            if result.get('error') or result.get('error_code') != 0 or result.get('issues'):
                continue  # Reuse successful series after a partial failure, not failed requests.
            request_coverage[code].append({'start':a.isoformat(), 'end':b.isoformat(),
                                           'requested_at':payload.get('requested_at')})
            seen = set()
            for p in result.get('observations', []):
                day, value = p['date'], p['value']
                if day in seen or not a <= date.fromisoformat(day) <= b:
                    raise ValueError('Index date duplicate or outside saved request')
                seen.add(day)
                if value is None or p.get('missing'):
                    raise ValueError('Missing index close requires explicit review')
                if isinstance(value, bool) or not isinstance(value, (float,int)) or not math.isfinite(value) or value <= 0:
                    raise ValueError('Index close must be finite and positive')
                key = code, day
                if key in values and values[key] != value:
                    raise ValueError('Saved index history conflict: '+str(key))
                values[key] = float(value)
    pending = []
    request_coverage_report = {code:[] for code in expected}
    def request_time(item):
        raw = item.get('requested_at')
        if not raw:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            stamp = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)
    for code, requests in request_coverage.items():
        ranges = [(date.fromisoformat(item['start']), date.fromisoformat(item['end']))
                  for item in requests]
        cursor, finish = date.fromisoformat(start), date.fromisoformat(end)
        for a,b in sorted(ranges):
            if b < cursor or a > finish:
                continue
            covered_start, covered_end = max(a, cursor), min(b, finish)
            request_coverage_report[code].append([covered_start.isoformat(), covered_end.isoformat()])
            if cursor < a:
                pending.append({'code':code,'start':cursor.isoformat(),'end':(a-timedelta(days=1)).isoformat()})
            cursor = max(cursor,b+timedelta(days=1))
        if cursor <= finish:
            pending.append({'code':code,'start':cursor.isoformat(),'end':end})
    rows = sorted((code,day,value) for (code,day),value in values.items() if start <= day <= end)
    observation_waterlines = {code:max((day for c,day,value in rows if c==code), default=None)
                              for code in expected}
    awaiting_release = []
    for code, requests in request_coverage.items():
        ranges = [(date.fromisoformat(item['start']), date.fromisoformat(item['end']), item)
                  for item in requests]
        if not ranges:
            continue
        request_start, request_end, request = max(
            ranges, key=lambda item:(item[1], item[0], request_time(item[2])))
        scope_start, scope_end = date.fromisoformat(start), date.fromisoformat(end)
        if request_end < scope_start or request_start > scope_end:
            continue
        waterline = observation_waterlines[code]
        waterline_day = date.fromisoformat(waterline) if waterline else None
        if waterline_day is not None and waterline_day >= min(request_end, scope_end):
            continue
        tail_start = max(request_start, scope_start)
        if waterline_day is not None:
            tail_start = max(tail_start, waterline_day + timedelta(days=1))
        tail_end = min(request_end, scope_end)
        if tail_start <= tail_end:
            awaiting_release.append({'code':code, 'start':tail_start.isoformat(),
                'end':tail_end.isoformat(), 'waterline':waterline,
                'request_end':request_end.isoformat(), 'requested_at':request.get('requested_at'),
                'reason':'request_covered_without_observation'})
    state = 'pending' if pending else 'awaiting_release' if awaiting_release else 'ready'
    report = {'kind':'index_daily_review','start':start,'end':end,'sources':sources,
        'metadata':name,'metadata_sha256':digest,'production_enabled':False,'execution_eligible':False,
        'request_coverage':request_coverage_report,
        'observation_waterlines':observation_waterlines,
        'awaiting_release':awaiting_release,
        'pending_requests':pending,'observations':len(rows),'state':state,
        'indicators':[{'product':product,'code':code,'name':label,'unit':'指数点','frequency':'D',
            'first':min((d for c,d,v in rows if c==code),default=None),
            'last':max((d for c,d,v in rows if c==code),default=None),
            'observation_waterline':observation_waterlines[code],
            'request_coverage':request_coverage_report[code],
            'observations':sum(c==code for c,d,v in rows),
            'identity_status':'wind_sec_name_matched',
            'limits':['日收盘指数点；非ETF或全收益指数','未调整资金、分红、交易成本；非可执行套利收益',
                      '当前下载历史值，非历史首次发布版本；Wind返回缺失不做客户端填充']}
            for product,(code,label) in INDEX_SERIES.items()]}
    if not publish:
        return report
    if pending:
        raise ValueError('Index requests not fully covered; keep previous snapshot')
    if awaiting_release or any(not i['observations'] for i in report['indicators']):
        raise ValueError('Index observations await release; keep previous snapshot')
    pointer = folder/'index_review.json'
    if pointer.exists():
        old = index_daily(folder)
        old_values = {(p['code'],p['observation_date']):p['raw_value'] for p in old['points']}
        waterlines = {i['code']:i['last'] for i in old['indicators']}
        new_values = {(c,d):v for c,d,v in rows}
        if any(new_values.get(k) != v for k,v in old_values.items()):
            raise ValueError('Existing index history would be removed or revised')
        if any(k not in old_values and k[1] <= waterlines[k[0]] for k in new_values):
            raise ValueError('Historical index gap requires explicit repair, not append')
    target = folder/('index_'+hashlib.sha256(json.dumps(rows).encode()).hexdigest()+'.parquet')
    temporary = target.with_suffix('.tmp')
    with duckdb.connect() as con:
        if not target.exists():
            try:
                con.execute('CREATE TABLE prices(code VARCHAR,observation_date VARCHAR,raw_value DOUBLE)')
                con.executemany('INSERT INTO prices VALUES (?,?,?)',rows)
                con.execute('COPY prices TO ? (FORMAT PARQUET)',[str(temporary)])
                if con.execute('SELECT * FROM read_parquet(?) ORDER BY 1,2',[str(temporary)]).fetchall() != rows:
                    raise ValueError('Index Parquet roundtrip mismatch')
                os.replace(temporary,target)
            finally:
                temporary.unlink(missing_ok=True)
        if con.execute('SELECT * FROM read_parquet(?) ORDER BY 1,2',[str(target)]).fetchall() != rows:
            raise ValueError('Existing index artifact mismatch')
    if any(saved(folder/p)[1] != h for p,h in {**sources,name:digest}.items()):
        raise ValueError('Saved index evidence changed during review')
    report.update(state='completed',file=target.name,sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    if not pointer.exists() or json.loads(pointer.read_bytes()) != report:
        save_run(report,pointer)
    return report


def index_update(runs, metadata, start, end, fetch=False):
    """Explicit independent invocation; no scheduler, production imports or RDS."""
    folder = ROOT/'runtime/fundamental_lab'
    folder.mkdir(parents=True,exist_ok=True)
    lock, checkpoint = folder/'index_update.lock', folder/'index_checkpoint.json'
    with lock.open('x') as handle:
        handle.write(str(os.getpid()))
    try:
        previous = json.loads(checkpoint.read_bytes()) if checkpoint.exists() else None
        if previous and previous['state'] != 'completed':
            if previous['state'] == 'awaiting_release':
                if ((start and start != previous['start'])
                        or date.fromisoformat(end) < date.fromisoformat(previous['end'])):
                    raise ValueError('Resume index checkpoint at the original date range')
            elif end != previous['end'] or (start and start != previous['start']):
                raise ValueError('Resume index checkpoint at the original date range')
        elif (folder/'index_review.json').exists():
            previous = json.loads((folder/'index_review.json').read_bytes())
        if previous:
            for name,digest in {**previous['sources'],previous['metadata']:previous['metadata_sha256']}.items():
                if Path(name).name != name or hashlib.sha256((folder/name).read_bytes()).hexdigest() != digest:
                    raise ValueError('Saved index lineage changed')
            runs = list(dict.fromkeys([*(folder/p for p in previous['sources']),*(runs or [])]))
            metadata = metadata or folder/previous['metadata']
            start = start or previous['start']
        if not runs or not metadata or not start:
            raise ValueError('Initial index run requires --run --metadata --start')
        report = index_review(runs,metadata,start,end)
        save_run(report,checkpoint)
        if fetch:
            fetch_requests = [dict(req) for req in report['pending_requests']]
            for release in report.get('awaiting_release', []):
                requested_at = release.get('requested_at')
                same_day = False
                if requested_at:
                    try:
                        stamp = datetime.fromisoformat(str(requested_at).replace('Z', '+00:00'))
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=timezone.utc)
                        same_day = stamp.astimezone().date() == date.today()
                    except (TypeError, ValueError):
                        pass
                if not same_day:
                    fetch_requests.append({'code':release['code'], 'start':release['start'],
                                           'end':release['end'], 'reason':'observation_waterline'})
            # An old request's unreleased tail is retried together with newly uncovered
            # dates, but a successful request made today is reused until the next day.
            by_code = {}
            for request in fetch_requests:
                by_code.setdefault(request['code'], []).append(request)
            merged = []
            for code, requests in by_code.items():
                current = None
                for request in sorted(requests, key=lambda item:(item['start'], item['end'])):
                    begin, finish = date.fromisoformat(request['start']), date.fromisoformat(request['end'])
                    if current is None or begin > date.fromisoformat(current['end']) + timedelta(days=1):
                        if current is not None:
                            merged.append(current)
                        current = {'code':code, 'start':request['start'], 'end':request['end']}
                    else:
                        current['end'] = max(current['end'], request['end'])
                if current is not None:
                    merged.append(current)
            grouped = {}
            for req in merged:
                grouped.setdefault((req['start'],req['end']),[]).append(req['code'])
            for (a,b),codes in grouped.items():
                path,status = probe(codes,a,b,timeout=50,api='wsd')
                runs.append(path)
                report = index_review(runs,metadata,start,end)
                save_run(report,checkpoint)
                if status:
                    return report  # Saved successes survive; no automatic retry storm.
        if not report['pending_requests'] and not report.get('awaiting_release'):
            report = index_review(runs,metadata,start,end,publish=True)
            save_run(report,checkpoint)
        return report
    finally:
        lock.unlink(missing_ok=True)


def stage(run, catalog=None, only=None):
    """Stage saved evidence only; technical validity never certifies business identity."""
    root = ROOT.resolve()
    folder = (root / "runtime" / "fundamental_lab").resolve()
    run = Path(run).resolve()
    catalog = Path(catalog or root / "fundamental_candidates.json").resolve()
    database = (folder / "staged.sqlite").resolve()
    if (not folder.is_relative_to(root) or not run.is_relative_to(folder)
            or run.suffix != ".json" or not catalog.is_relative_to(root)
            or catalog.suffix != ".json" or database.parent != folder):
        raise ValueError("Raw input/output must stay in this project's lab; catalog must stay in this project")
    raw_bytes, catalog_bytes = run.read_bytes(), catalog.read_bytes()
    run_hash = hashlib.sha256(raw_bytes).hexdigest()
    catalog_hash = hashlib.sha256(catalog_bytes).hexdigest()
    payload, claims = json.loads(raw_bytes), json.loads(catalog_bytes)
    allowed = [row["code"] for row in claims["indicators"]]
    if claims.get("production_enabled") is not False or codes_list(",".join(allowed)) != allowed:
        raise ValueError("Catalog must be an explicit unique-code lab allowlist with production_enabled=false")
    request = payload["request"]
    requested = request["codes"]
    if codes_list(",".join(requested)) != requested:
        raise ValueError("Requested codes must be unique")
    selected = sorted(requested if only is None else codes_list(",".join(only)))
    if not set(selected).issubset(requested) or not set(selected).issubset(allowed):
        raise ValueError("Selected codes must be requested and present in the candidate allowlist")
    scope_hash = hashlib.sha256(",".join(selected).encode("ascii")).hexdigest()
    # Keep the original full-batch identity, including for an explicit complete scope.
    stage_hash = (run_hash if set(selected) == set(requested) else
                  hashlib.sha256(f"{run_hash}:{scope_hash}".encode("ascii")).hexdigest())
    plan_requests(requested, "initial", request["end"], start=request["start"])
    results = payload["results"]
    returned = [row["code"] for row in results]
    if len(returned) != len(set(returned)) or set(returned) != set(requested):
        raise ValueError("Returned codes must exactly match the requested codes")
    if payload.get("state") not in {"completed", "failed"} or payload.get("production_write") is not False:
        raise ValueError("Only finished isolated probe evidence can be staged")
    clean, rejected, series_errors = [], [], []
    observed_count = 0
    for result in results:
        code, observations = result["code"], result.get("observations", [])
        if code not in selected:
            continue
        observed_count += len(observations)
        counts = Counter(row.get("date") for row in observations)
        seen_values = {}
        for index, row in enumerate(observations):
            day, value = row.get("date"), row.get("value")
            reasons = []
            try:
                if date.fromisoformat(day).isoformat() != day:
                    raise ValueError("noncanonical date")
                if not request["start"] <= day <= request["end"]:
                    reasons.append("outside_requested_range")
            except (TypeError, ValueError):
                reasons.append("invalid_date")
            if type(value) not in (int, float) or not math.isfinite(value):
                reasons.append("invalid_or_nonfinite_value")
            elif day in seen_values and seen_values[day] != value:
                raise ValueError(f"Conflicting values within run for {code}/{day}; nothing staged")
            else:
                seen_values[day] = value
            if row.get("missing") is not False:
                reasons.append("missing_observation")
            if counts[day] > 1:
                reasons.append("duplicate_key")
            if result.get("error") or result.get("error_code") != 0:
                reasons.append("series_request_failed")
            # Known row-level issues are checked above; unknown structural issues block the series.
            if any(not str(issue).startswith(("Duplicate or out-of-range date:", "Non-numeric observation:",
                                                _EMPTY_OBSERVATION_ISSUE))
                   for issue in result.get("issues", [])):
                reasons.append("series_structure_issue")
            if reasons:
                rejected.append((code, index, json.dumps(row, ensure_ascii=False), ",".join(reasons)))
            else:
                clean.append((code, day, value))
        if not observations:
            series_errors.append({"code": code, "reason": "no_observations", "error": result.get("error"),
                                  "error_code": result.get("error_code"), "issues": result.get("issues", [])})
    connection = sqlite3.connect(database, timeout=10)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            # Older labs allowed only one stage per raw path. Preserve their rows and
            # full-batch IDs while allowing independently audited code subsets.
            old_schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='stage_runs'").fetchone()
            if old_schema and "source_path TEXT UNIQUE" in old_schema[0]:
                connection.execute("ALTER TABLE stage_runs RENAME TO stage_runs_legacy")
            connection.execute("""CREATE TABLE IF NOT EXISTS stage_runs (
                run_sha256 TEXT PRIMARY KEY, source_path TEXT NOT NULL, catalog_sha256 TEXT NOT NULL,
                requested_start TEXT NOT NULL, requested_end TEXT NOT NULL, created_at TEXT NOT NULL,
                summary_json TEXT NOT NULL)""")
            if old_schema and "source_path TEXT UNIQUE" in old_schema[0]:
                connection.execute("INSERT INTO stage_runs SELECT * FROM stage_runs_legacy")
                connection.execute("DROP TABLE stage_runs_legacy")
            connection.execute("""CREATE TABLE IF NOT EXISTS staged_observations (
                code TEXT NOT NULL, observation_date TEXT NOT NULL, value REAL NOT NULL,
                first_run_sha256 TEXT NOT NULL, identity_status TEXT NOT NULL CHECK(identity_status='unverified'),
                production_ready INTEGER NOT NULL CHECK(production_ready=0), PRIMARY KEY(code, observation_date))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS quarantine (
                run_sha256 TEXT NOT NULL, code TEXT NOT NULL, observation_index INTEGER NOT NULL,
                raw_json TEXT NOT NULL, reason TEXT NOT NULL,
                identity_status TEXT NOT NULL CHECK(identity_status='unverified'),
                production_ready INTEGER NOT NULL CHECK(production_ready=0),
                PRIMARY KEY(run_sha256, code, observation_index))""")
            for old_key, old_path, old_catalog, old_summary in connection.execute(
                    "SELECT run_sha256,source_path,catalog_sha256,summary_json FROM stage_runs"):
                old_raw = json.loads(old_summary).get("run_sha256", old_key)
                if old_path == str(run) and old_raw != run_hash:
                    raise ValueError("Previously staged raw path changed SHA256; preserve evidence and use a new run")
                if old_raw == run_hash and old_catalog != catalog_hash:
                    raise ValueError("Catalog SHA256 changed for staged raw evidence; review is required")
            previous = connection.execute("SELECT summary_json FROM stage_runs WHERE run_sha256=?", (stage_hash,)).fetchone()
            if previous:
                return dict(json.loads(previous[0]), already_staged=True)
            inserted = 0
            for code, day, value in clean:
                old = connection.execute("SELECT value FROM staged_observations WHERE code=? AND observation_date=?",
                                         (code, day)).fetchone()
                if old is not None:
                    if old[0] != value:
                        raise ValueError(f"Conflicting staged value for {code}/{day}; entire new run rolled back")
                else:
                    connection.execute("INSERT INTO staged_observations VALUES (?,?,?,?,?,?)",
                                       (code, day, value, stage_hash, "unverified", 0))
                    inserted += 1
            connection.executemany("INSERT INTO quarantine VALUES (?,?,?,?,?,?,?)",
                                   [(stage_hash, *row, "unverified", 0) for row in rejected])
            summary = {"run_sha256": run_hash, "catalog_sha256": catalog_hash, "database": str(database),
                       "stage_sha256": stage_hash, "scope_sha256": scope_hash, "selected_codes": selected,
                       "raw_observations": observed_count, "technical_qualified": len(clean),
                       "inserted": inserted, "existing_equal": len(clean) - inserted,
                       "quarantined": len(rejected), "series_errors": series_errors,
                       "identity_status": "unverified", "production_ready": False,
                       "production_write": False, "already_staged": False}
            connection.execute("INSERT INTO stage_runs VALUES (?,?,?,?,?,?,?)",
                               (stage_hash, str(run), catalog_hash, request["start"], request["end"],
                                datetime.now(timezone.utc).isoformat(), json.dumps(summary, ensure_ascii=False)))
            if hashlib.sha256(run.read_bytes()).hexdigest() != run_hash:
                raise ValueError("Raw SHA256 changed during staging; transaction rolled back")
            if hashlib.sha256(catalog.read_bytes()).hexdigest() != catalog_hash:
                raise ValueError("Catalog SHA256 changed during staging; transaction rolled back")
        return summary
    finally:
        connection.close()


def prepare_saved(run, metadata, start, end):
    """Build a review-only dataset and missing-request plan without Wind or DB writes.

    A successful saved request covers its requested interval, NOT merely the
    min/max observed dates. Low-frequency non-observation days are not gaps.
    Terminal display metadata is preserved separately from API raw values.
    """
    folder = (ROOT / 'runtime' / 'fundamental_lab').resolve()
    run, metadata = Path(run).resolve(), Path(metadata).resolve()
    if any(not p.is_relative_to(folder) or p.suffix != '.json' for p in (run, metadata)):
        raise ValueError('Preparation inputs must be JSON evidence in the lab')
    raw_bytes, meta_bytes = run.read_bytes(), metadata.read_bytes()
    payload, catalog = json.loads(raw_bytes), json.loads(meta_bytes)
    if (catalog.get('production_enabled') is not False
            or catalog.get('user_confirmed_no_manual_conversion') is not True
            or catalog.get('field_status') != 'terminal_default_display_confirmed'):
        raise ValueError('Terminal field evidence required; production must remain disabled')
    request = payload['request']
    unfinished = payload.get('state') == 'running'
    if payload.get('production_write') is not False or payload.get('state') not in {'completed', 'failed', 'running'}:
        raise ValueError('Finished isolated saved request required')
    requested = codes_list(','.join(request['codes']))
    raw_results = payload.get('results', [])
    if not isinstance(raw_results, list):
        raise ValueError('Saved response codes mismatch')
    results = {r['code']: r for r in raw_results}
    if (len(results) != len(raw_results) or not set(results).issubset(set(requested))
            or (not unfinished and set(results) != set(requested))):
        raise ValueError('Saved response codes mismatch')
    indicators = catalog['indicators']
    codes = [r['code'] for r in indicators]
    if codes_list(','.join(codes)) != codes:
        raise ValueError('Metadata codes must be unique')
    plan_requests(codes, 'initial', end, start=start)
    plan_requests(requested, 'initial', request['end'], start=request['start'])
    output, pending, excluded, rejected, no_new_observations = [], [], [], [], []
    for item in indicators:
        code = item['code']
        if not item.get('name') or not item.get('unit') or item.get('frequency') not in {'D','W','M','Q','Y'}:
            raise ValueError('Incomplete terminal fields for '+code)
        if item['role'] == 'quarantine':
            excluded.append({'code': code, 'reasons': item['limits']})
            continue
        result = results.get(code)
        empty_append = _is_append_empty_result(result, code, request.get('mode'))
        issues = result.get('issues', []) if result is not None else []
        valid = not unfinished and result is not None and not (result.get('error') or result.get('error_code') != 0) and (
            empty_append or (bool(result.get('observations')) and
                all(str(issue).startswith('Duplicate or out-of-range date:') for issue in issues)))
        points = []
        seen = set()
        if valid:
            if empty_append:
                no_new_observations.append({'code':code, 'start':request['start'],
                    'end':request['end'], 'state':'no_new_observation',
                    'reason':'awaiting_release', 'requested_at':payload.get('requested_at')})
            for row in result.get('observations', []):
                day, value = row['date'], row['value']
                if date.fromisoformat(day).isoformat() != day or day in seen:
                    raise ValueError('Invalid saved observation dates: '+code)
                seen.add(day)
                if not request['start'] <= day <= request['end']:
                    rejected.append({'code':code,'date':day,'raw_value':value,'reason':'outside_requested_range'})
                    continue
                if row.get('missing') is True and value is None:
                    continue
                if row.get('missing') is not False or type(value) not in (float,int) or not math.isfinite(value):
                    raise ValueError('Invalid saved observation value: '+code)
                if start <= day <= end:
                    points.append({'observation_date': day, 'raw_value': value})
        begin = max(start, item['available_start'])
        # Do not silently schedule dates outside the caller's range.
        gaps = [] if begin > end else [(begin, end)]
        if valid:
            a,b = request['start'],request['end']
            gaps = [(lo,hi) for lo,hi in (
                (begin, min(end,(date.fromisoformat(a)-timedelta(days=1)).isoformat())),
                (max(begin,(date.fromisoformat(b)+timedelta(days=1)).isoformat()),end)) if lo <= hi]
        pending.extend({'code':code,'start':lo,'end':hi,'mode':'initial',
                        'executes':False,'reason':'not_covered_by_reusable_saved_request'} for lo,hi in gaps)
        output.append({**item, 'points': sorted(points,key=lambda p:p['observation_date']),
            'reused_request_interval': [request['start'],request['end']] if valid else None,
            'identity_status':'terminal_fields_confirmed_scope_limited',
            'production_ready':False,'raw_unit_independently_verified':False})
    return {'kind':'isolated_review_dataset_not_published','production_enabled':False,
        'source_sha256':hashlib.sha256(raw_bytes).hexdigest(),
        'metadata_sha256':hashlib.sha256(meta_bytes).hexdigest(),
        'requested_start':start,'requested_end':end,'indicators':output,
        'observations':sum(len(r['points']) for r in output),'excluded':excluded,'rejected':rejected,
        'pending_requests':pending,'no_new_observations':no_new_observations,
        'warning':'No unit conversion, forward fill, live fetch or published waterline; terminal dates are not historical availability timestamps'}


def prepare_runs(runs, metadata, start, end):
    """Merge saved batches exactly, intersect uncovered ranges; never request twice."""
    prepared = [prepare_saved(run,metadata,start,end) for run in runs]
    if not prepared:
        raise ValueError('At least one saved batch required')
    result = prepared[0]
    sources = [result['source_sha256']]
    for other in prepared[1:]:
        sources.append(other['source_sha256'])
        for left,right in zip(result['indicators'],other['indicators']):
            assert left['code']==right['code']
            values={p['observation_date']:p['raw_value'] for p in left['points']}
            for point in right['points']:
                day,value=point['observation_date'],point['raw_value']
                if day in values and values[day]!=value:
                    raise ValueError('Saved batches conflict: '+left['code']+'/'+day)
                values[day]=value
            left['points']=[{'observation_date':day,'raw_value':value} for day,value in sorted(values.items())]
        result['pending_requests']=[{**a,'start':max(a['start'],b['start']),'end':min(a['end'],b['end'])}
            for a in result['pending_requests'] for b in other['pending_requests']
            if a['code']==b['code'] and max(a['start'],b['start'])<=min(a['end'],b['end'])]
        result['rejected'].extend(other['rejected'])
    for item in result['indicators']:
        item['reused_request_intervals']=[p['reused_request_interval'] for batch in prepared
            for p in batch['indicators'] if p['code']==item['code'] and p.get('reused_request_interval')]
    markers = [marker for batch in prepared for marker in batch.get('no_new_observations', [])]
    result['no_new_observations'] = [marker for marker in markers if not any(
        point['code'] == marker['code'] and point['observation_date'] >= marker['start']
        for point in ({'code':item['code'], **p} for item in result['indicators'] for p in item['points']))]
    result['source_sha256s']=sources
    result['observations']=sum(len(r['points']) for r in result['indicators'])
    return result


def review_plan(runs, metadata, start, end):
    """Persist a non-executable recovery plan from saved request coverage, not raw maxima."""
    review = prepare_runs(runs, metadata, start, end)
    series = []
    for item in review['indicators']:
        dates = [p['observation_date'] for p in item['points']]
        pending = [r for r in review['pending_requests'] if r['code'] == item['code']]
        series.append({'code': item['code'], 'name': item['name'],
            'frequency': item['frequency'], 'unit': item['unit'],
            'observations': len(dates), 'last_observation': max(dates) if dates else None,
            'reused_request_intervals': item['reused_request_intervals'],
            'pending_requests': pending,
            'state': 'missing_request_coverage' if pending else
                     'saved_request_covered' if dates else 'covered_without_observations',
            'production_ready': False})
    report = {'kind': 'isolated_review_recovery_plan', 'production_enabled': False,
        'executes': False, 'requested_start': start, 'requested_end': end,
        'source_sha256s': review['source_sha256s'], 'metadata_sha256': review['metadata_sha256'],
        'indicators': series, 'excluded': review['excluded'],
        'rejected': review['rejected'], 'pending_requests': review['pending_requests'],
        'warning': 'Request coverage is not freshness or a published waterline. '
                   'This plan never fetches, publishes or authorizes production append.'}
    path = save_run(report, ROOT / 'runtime/fundamental_lab/recovery_plan.json')
    return {'path': str(path), 'indicators': len(series),
        'observations': sum(i['observations'] for i in series),
        'pending_requests': len(review['pending_requests']),
        'excluded': len(review['excluded']), 'executes': False, 'production_enabled': False}


def snapshot(runs, metadata, start, end):
    """Build and verify an immutable review Parquet from saved evidence only."""
    import duckdb
    review = prepare_runs(runs, metadata, start, end)
    if review.get('no_new_observations'):
        raise ValueError('Observations await release; previous snapshot retained')
    # Empty marker lists were not part of the original review identity. Omit
    # them from the digest so replaying unchanged evidence reuses its address.
    digest_review = dict(review)
    digest_review.pop('no_new_observations', None)
    digest = hashlib.sha256(json.dumps(digest_review, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    target = ROOT / 'runtime' / 'fundamental_lab' / ('review_' + digest + '.parquet')
    rows = sorted((item['code'], p['observation_date'], float(p['raw_value']),
        item['name'], item['unit'], item['frequency'], item['identity_status'],
        review['metadata_sha256'], json.dumps(review['source_sha256s']), False)
        for item in review['indicators'] for p in item['points'])
    if not rows or len({r[:2] for r in rows}) != len(rows):
        raise ValueError('Nonempty unique review observations required')
    temporary = target.with_suffix('.' + uuid4().hex + '.tmp')
    target.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        def verify(path):
            actual = connection.execute('SELECT * FROM read_parquet(?) ORDER BY code, observation_date',
                                        [str(path)]).fetchall()
            if actual != rows:
                raise ValueError('Review Parquet key/value/metadata mismatch')
        if target.exists():
            verify(target)
        else:
            try:
                connection.execute('CREATE TABLE review (code VARCHAR, observation_date VARCHAR, raw_value DOUBLE, '
                    'name VARCHAR, unit VARCHAR, frequency VARCHAR, identity_status VARCHAR, '
                    'metadata_sha256 VARCHAR, source_sha256s VARCHAR, production_ready BOOLEAN)')
                connection.execute('BEGIN TRANSACTION')
                connection.executemany('INSERT INTO review VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
                connection.execute('COMMIT')
                escaped = str(temporary).replace("'", "''")
                connection.execute("COPY review TO '" + escaped + "' (FORMAT PARQUET, COMPRESSION ZSTD)")
                verify(temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    return {'path':str(target), 'observations':len(rows), 'indicators':len(review['indicators']),
        'sha256':hashlib.sha256(target.read_bytes()).hexdigest(), 'roundtrip_verified':True,
        'production_enabled':False, 'pending_requests':review['pending_requests']}


def _recover_interrupted_update_runs(runs, folder):
    """Finalize only registered, isolated update raws before they are prepared."""
    folder = Path(folder).resolve()
    completed = []
    for candidate in runs:
        path = Path(candidate).resolve()
        if (path.parent != folder or path.suffix != '.json'
                or not path.name.startswith('update_raw_')):
            continue
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('state') != 'running':
            continue
        results = payload.get('results')
        requested = payload.get('request', {}).get('codes', [])
        if not isinstance(results, list) or not isinstance(requested, list):
            continue
        if any(not isinstance(row, dict) or 'code' not in row for row in results):
            continue
        returned = {row['code'] for row in results}
        if len(returned) != len(results) or not returned.issubset(set(requested)):
            continue
        missing = [code for code in requested if code not in returned]
        if missing:
            results.extend({'code':code, 'error':'interrupted before response'} for code in missing)
            payload['state'] = 'failed'
            save_run(payload, path)
            continue
        successful = all(row.get('error_code') == 0 and not row.get('error') and not row.get('issues')
                        for row in results)
        if successful:
            payload['state'] = 'completed'
        else:
            payload['state'] = 'failed'
        save_run(payload, path)
        if successful:
            completed.extend({'code': code, 'start': payload['request']['start'],
                              'end': payload['request']['end']}
                             for code in requested)
    return completed


def update_review(runs, metadata, start, end, fetch=False, append_requests=None):
    """Locked isolated workflow. Resume saved checkpoints; never write production."""
    folder = ROOT / 'runtime/fundamental_lab'
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / 'update.lock'
    try:
        handle = lock.open('x')
    except FileExistsError:
        raise ValueError('Isolated update already locked; inspect owner before recovery')
    manifest_path = folder / 'update_checkpoint.json'
    try:
        with handle:
            handle.write(str(os.getpid()))
        runs = [Path(p).resolve() for p in runs]
        metadata = Path(metadata).resolve()
        # Validate the supplied scope before touching a checkpoint or fetching.
        review = prepare_runs(runs, metadata, start, end)
        scope = {'runs': [str(p) for p in runs], 'start':start, 'end':end,
                 'metadata_sha256':review['metadata_sha256'], 'sources':review['source_sha256s']}
        state = {'scope':scope, 'runs':[str(p) for p in runs], 'state':'running',
                 'production_enabled':False}
        if manifest_path.exists():
            old = json.loads(manifest_path.read_text(encoding='utf-8'))
            if old['scope'] == scope:
                state = old
                runs = [Path(p) for p in state['runs']]
            elif old['state'] != 'completed':
                old_scope = old.get('scope', {})
                try:
                    original_runs = [Path(p).resolve() for p in old_scope['runs']]
                    recorded_runs = [Path(p).resolve() for p in old['runs']]
                    supplied_runs = [Path(p).resolve() for p in runs]
                    original_sources = old_scope['sources']
                    sources_unchanged = (len(original_runs) == len(original_sources) and
                        all(p.parent == folder.resolve() and p.suffix == '.json' and p.is_file() and
                            hashlib.sha256(p.read_bytes()).hexdigest() == digest
                            for p,digest in zip(original_runs, original_sources)))
                except (KeyError, TypeError, OSError, ValueError):
                    original_runs, recorded_runs, supplied_runs = [], [], []
                    sources_unchanged = False
                same_start_metadata = (old_scope.get('start') == scope['start'] and
                    old_scope.get('metadata_sha256') == scope['metadata_sha256'])
                same_bounds = (same_start_metadata and old_scope.get('end') == scope['end'])
                release_extension = (old.get('state') == 'awaiting_release' and same_start_metadata and
                    date.fromisoformat(scope['end']) >= date.fromisoformat(old_scope.get('end', scope['end'])))
                recorded_scope = (supplied_runs == recorded_runs and
                    all(p in recorded_runs for p in original_runs))
                if not ((same_bounds or release_extension) and recorded_scope and sources_unchanged):
                    raise ValueError('Unfinished checkpoint has a different scope; resolve it explicitly')
                # Keep the original scope evidence; top-level runs include recorded recovery raw files.
                state = old
                state['runs'] = [str(p) for p in supplied_runs]
                runs = supplied_runs
        state['state'] = 'running'
        save_run(state, manifest_path)
        recovered = _recover_interrupted_update_runs(runs, folder)
        review = prepare_runs(runs, metadata, start, end)
        fetch_requests = (review['pending_requests'] if append_requests is None
                          else [dict(request) for request in append_requests])
        for recovered_request in recovered:
            remaining = []
            lower = date.fromisoformat(recovered_request['start'])
            upper = date.fromisoformat(recovered_request['end'])
            for request in fetch_requests:
                if (request['code'] != recovered_request['code']
                        or request['end'] < recovered_request['start']
                        or request['start'] > recovered_request['end']):
                    remaining.append(request)
                    continue
                for lo, hi in ((request['start'], (lower - timedelta(days=1)).isoformat()),
                               ((upper + timedelta(days=1)).isoformat(), request['end'])):
                    if lo <= hi:
                        remaining.append({**request, 'start': lo, 'end': hi})
            fetch_requests = remaining
        if fetch:
            # Each code/interval gets a known checkpoint path before the child starts.
            for request in fetch_requests:
                path = folder / ('update_raw_' + uuid4().hex + '.json')
                payload = {'requested_at':datetime.now(timezone.utc).isoformat(),
                    'request':{'codes':[request['code']], 'start':request['start'],
                    'end':request['end'], 'options':'', 'mode':request.get('mode', 'initial')},
                    'state':'running', 'results':[],
                    'production_write':False, 'identity_status':'unverified'}
                save_run(payload, path)
                runs.append(path)
                state['runs'] = [str(p) for p in runs]
                save_run(state, manifest_path)
                try:
                    subprocess.run([str(WIND_PYTHON), str(Path(__file__).resolve()),
                        '_worker', '--run', str(path)], check=True, timeout=50,
                        capture_output=True)
                except (subprocess.SubprocessError, OSError) as exc:
                    state.update(state='failed', error=str(exc))
                    save_run(state, manifest_path)
                    raise ValueError('Isolated fetch failed; saved checkpoint retained') from exc
                completed = json.loads(path.read_bytes())
                returned = completed.get('results', [])
                empty_append = (len(returned) == 1 and _is_append_empty_result(
                    returned[0], request['code'], request.get('mode')))
                if (completed.get('error') or len(returned) != 1 or returned[0].get('code') != request['code']
                        or returned[0].get('error') or returned[0].get('error_code') != 0
                        or (any(not str(issue).startswith('Duplicate or out-of-range date:')
                                for issue in returned[0].get('issues', [])) and not empty_append)):
                    state.update(state='failed', error='Wind response unsuccessful; previous snapshot retained')
                    save_run(state, manifest_path)
                    raise ValueError(state['error'])
        review = prepare_runs(runs, metadata, start, end)
        state['pending_requests'] = review['pending_requests']
        accepted_values = {(i['code'],p['observation_date']):p['raw_value']
            for i in review.get('indicators',[]) for p in i['points']}
        for row in review['rejected']:
            key = (row.get('code'),row.get('date'))
            row['review_state'] = ('already_in_accepted_history' if key in accepted_values
                and accepted_values[key] == row.get('raw_value') else
                'historical_value_conflict' if key in accepted_values else 'needs_explicit_approval')
        state['late_observations'] = review['rejected']
        state['no_new_observations'] = review.get('no_new_observations', [])
        unresolved = [row for row in review['rejected'] if row['review_state'] != 'already_in_accepted_history']
        if unresolved:
            state['state'] = 'awaiting_review'
            save_run(state, manifest_path)
            return {'state':state['state'], 'pending_requests':review['pending_requests'],
                    'unresolved_observations':unresolved,
                    'no_new_observations':state['no_new_observations'],
                    'production_enabled':False, 'published':False}
        if review['pending_requests']:
            state['state'] = 'awaiting_coverage'
            save_run(state, manifest_path)
            return {'state':state['state'], 'pending_requests':review['pending_requests'],
                    'no_new_observations':state['no_new_observations'],
                    'production_enabled':False, 'published':False}
        if review.get('no_new_observations'):
            state['state'] = 'awaiting_release'
            save_run(state, manifest_path)
            return {'state':state['state'], 'pending_requests':[],
                    'no_new_observations':state['no_new_observations'],
                    'awaiting_release':state['no_new_observations'],
                    'production_enabled':False, 'published':False}
        staged_hashes = set()
        database = folder / 'staged.sqlite'
        if database.exists():
            with sqlite3.connect(database) as connection:
                staged_hashes = {json.loads(row[0])['run_sha256'] for row in
                    connection.execute('SELECT summary_json FROM stage_runs')}
        for run in runs:
            if hashlib.sha256(run.read_bytes()).hexdigest() not in staged_hashes:
                stage(run)
        accepted = snapshot(runs, metadata, start, end)
        # Checkpoints and candidate files are not a website publication pointer.
        pointer = {'file':Path(accepted['path']).name, 'sha256':accepted['sha256'],
            'metadata':metadata.name, 'production_enabled':False}
        save_run(pointer, folder / 'accepted_review.json')
        state.update(state='completed', snapshot=accepted, error=None)
        save_run(state, manifest_path)
        return {'state':'completed', **accepted, 'late_observations':review['rejected']}
    finally:
        lock.unlink(missing_ok=True)


_CEIC_USAGE_STATUS = {'review': True, 'reference': False, 'basis': False, 'replay': False}
_CEIC_REQUIREMENT_ASSOCIATIONS = {
    '540064367': {'products': ['BU'], 'requirement_ids': ['REQ-BU-INV']},
    '532023257': {'products': ['HC'], 'requirement_ids': ['REQ-HC-INV']},
    '567837947': {'products': ['MA'], 'requirement_ids': ['REQ-MA-WARRANT']},
    '567837857': {'products': ['RS'], 'requirement_ids': ['REQ-RS-WARRANT']},
    '567837817': {'products': ['CF'], 'requirement_ids': ['REQ-CF-WARRANT']},
    '567837827': {'products': ['CY'], 'requirement_ids': ['REQ-CY-WARRANT']},
    '567837937': {'products': ['PK'], 'requirement_ids': ['REQ-PK-WARRANT']},
    '567837997': {'products': ['UR'], 'requirement_ids': ['REQ-UR-WARRANT']},
    '567837807': {'products': ['SR'], 'requirement_ids': ['REQ-SR-WARRANT']},
    '567837957': {'products': ['FG'], 'requirement_ids': ['REQ-FG-WARRANT']},
    '567837847': {'products': ['OI'], 'requirement_ids': ['REQ-OI-WARRANT']},
    '567837927': {'products': ['CJ'], 'requirement_ids': ['REQ-CJ-WARRANT']},
    '567838007': {'products': ['SA'], 'requirement_ids': ['REQ-SA-WARRANT']},
    '567838027': {'products': ['PX'], 'requirement_ids': ['REQ-PX-WARRANT']},
    '567837977': {'products': ['SF'], 'requirement_ids': ['REQ-SF-WARRANT']},
    '567837987': {'products': ['SM'], 'requirement_ids': ['REQ-SM-WARRANT']},
    '567838037': {'products': ['SH'], 'requirement_ids': ['REQ-SH-WARRANT']},
    '567837837': {'products': ['RM'], 'requirement_ids': ['REQ-RM-WARRANT']},
    '567838047': {'products': ['PR'], 'requirement_ids': ['REQ-PR-WARRANT']},
    '567838017': {'products': ['PF'], 'requirement_ids': ['REQ-PF-WARRANT']},
    '304988904': {'products': ['AL'], 'requirement_ids': ['REQ-AL-SPOT']},
    '304989104': {'products': ['ZN'], 'requirement_ids': ['REQ-ZN-SPOT']},
    '304992304': {'products': ['A'], 'requirement_ids': ['REQ-A-SPOT']},
    '304992504': {'products': ['PK'], 'requirement_ids': ['REQ-PK-SPOT']},
    '304990304': {'products': ['PG'], 'requirement_ids': ['REQ-PG-SPOT']},
    '478005147': {'products': ['J'], 'requirement_ids': ['REQ-J-SPOT']},
    '695751317': {'products': ['SR'], 'requirement_ids': ['REQ-SR-SPOT']},
    '478005157': {'products': ['SP'], 'requirement_ids': ['REQ-SP-SPOT']},
    '304990004': {'products': ['BR'], 'requirement_ids': ['REQ-BR-SPOT']},
    '304992204': {'products': ['LH'], 'requirement_ids': ['REQ-LH-SPOT']},
    '304992604': {'products': ['UR'], 'requirement_ids': ['REQ-UR-SPOT']},
    '478005127': {'products': ['PP'], 'requirement_ids': ['REQ-PP-SPOT']},
    '304992404': {'products': ['M'], 'requirement_ids': ['REQ-M-SPOT']},
    '447225787': {'products': ['RU'], 'requirement_ids': ['REQ-RU-SPOT']},
    '695751307': {'products': ['PS'], 'requirement_ids': ['REQ-PS-SPOT']},
    '304992004': {'products': ['C'], 'requirement_ids': ['REQ-C-SPOT']},
    '304989504': {'products': ['BZ'], 'requirement_ids': ['REQ-BZ-SPOT']},
    '304992104': {'products': ['CF'], 'requirement_ids': ['REQ-CF-SPOT']},
    '478005107': {'products': ['HC'], 'requirement_ids': ['REQ-HC-SPOT']},
    '478005117': {'products': ['L'], 'requirement_ids': ['REQ-L-SPOT']},
    '447225767': {'products': ['JM'], 'requirement_ids': ['REQ-JM-SPOT']},
    '304989404': {'products': ['MA'], 'requirement_ids': ['REQ-MA-SPOT']},
    '695751297': {'products': ['FG'], 'requirement_ids': ['REQ-FG-SPOT']},
    '304989304': {'products': ['SH'], 'requirement_ids': ['REQ-SH-SPOT']},
    '3672301': {'products': ['SA'], 'requirement_ids': ['REQ-SA-SUPPLY']},
    '110269001': {'products': ['TA'], 'requirement_ids': ['REQ-TA-SPOT']},
    '317697101': {'products': ['I'], 'requirement_ids': ['REQ-I-INV']},
    '51906201': {'products': ['P'], 'requirement_ids': ['REQ-P-TRADE']},
    '6130901': {'products': ['B', 'M', 'Y'], 'requirement_ids': ['REQ-B-TRADE', 'REQ-M-UPSTREAM', 'REQ-Y-UPSTREAM']},
    '531905027': {'products': ['RB'], 'requirement_ids': ['REQ-RB-SUPPLY']},
    '531952197': {'products': ['RB'], 'requirement_ids': ['REQ-RB-INV']},
    '531952557': {'products': ['RB'], 'requirement_ids': ['REQ-RB-INV']},
    '529645757': {'products': ['SC'], 'requirement_ids': ['REQ-SC-INV']},
    '529638707': {'products': ['SC'], 'requirement_ids': ['REQ-SC-DEMAND']},
    '6303101': {'products': ['J'], 'requirement_ids': ['REQ-J-TRADE']},
    '304923004': {'products': ['SC'], 'requirement_ids': ['REQ-SC-SUPPLY'],
                  'alternative_group': 'REQ-SC-SUPPLY', 'splice_allowed': False},
    '529632157': {'products': ['SC'], 'requirement_ids': ['REQ-SC-SUPPLY'],
                  'alternative_group': 'REQ-SC-SUPPLY', 'splice_allowed': False},
}
_CEIC_METADATA_FIELDS = {
    'sample_scope': ('sample_scope', 'inventory_scope', 'scope', '样本范围', '统计范围'),
    'period_basis': ('period_basis', 'period_type', 'period_semantics', '月末', '月均', '累计'),
    'release_time_rule': ('published_at', 'release_time', 'release_time_rule', '发布时间', '实际可得时间'),
    'update_permission': ('update_permission', 'update_rule', 'permission', '更新权限'),
}


def _ceic_metadata_missing(item, source_items):
    values = [value for value in (item, *source_items) if isinstance(value, dict)]
    missing = {field for field, keys in _CEIC_METADATA_FIELDS.items()
               if not any(any(value.get(key) not in (None, '') for key in keys) for value in values)}
    for value in values:
        extra = value.get('metadata_missing', [])
        if isinstance(extra, (list, tuple, set)):
            missing.update(str(entry) for entry in extra if entry not in (None, ''))
    return sorted(missing)


def _ceic_candidate_status(root, catalog_reader):
    """Read the content-addressed CEIC review candidate, never the newest file."""
    ceic_root = Path(root) / 'runtime' / 'ceic_lab'
    web_root = ceic_root / 'web_review'
    pointer = ceic_root / 'selected_review.json'
    if pointer.exists():
        selected = json.loads(pointer.read_bytes())
        name = selected.get('review_root', '')
        if not re.fullmatch(r'web_review|(?:export|merge)_[a-f0-9]{16}', name):
            raise ValueError('Invalid isolated CEIC selection')
        web_root = ceic_root / name  # catalog_reader below independently verifies the pinned hashes.
    source, source_audit_path = web_root / 'candidates.jsonl', web_root / 'audit.json'
    result = {'status': 'unavailable', 'source_jsonl': str(source),
              'source_audit': str(source_audit_path), 'source_jsonl_sha256': None,
              'source_audit_sha256': None, 'source_audit_verification_scope': None,
              'source_audit_metadata_missing': [], 'verification_scope': None,
              'metadata_missing': [], 'indicators': [],
              'usage_status': {key: False for key in _CEIC_USAGE_STATUS},
              'production_write': False, 'network_fetch': False}
    if not source.is_file() or not source_audit_path.is_file():
        return result
    try:
        source_bytes, audit_bytes = source.read_bytes(), source_audit_path.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        audit_hash = hashlib.sha256(audit_bytes).hexdigest()
        source_audit = json.loads(audit_bytes.decode('utf-8'))
        catalog = catalog_reader(ceic_root)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, ValueError, KeyError):
        return result
    if not isinstance(source_audit, dict) or not catalog:
        result.update(source_jsonl_sha256=source_hash, source_audit_sha256=audit_hash)
        return result
    expected = ceic_root / ('wide_' + hashlib.sha256((source_hash + audit_hash).encode()).hexdigest()[:16])
    candidate_audit_path = expected / 'audit.json'
    try:
        candidate_audit_bytes = candidate_audit_path.read_bytes()
        candidate_audit = json.loads(candidate_audit_bytes.decode('utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return result
    if not isinstance(candidate_audit, dict):
        return result
    inventory_by_id = {}
    for entry in source_audit.get('inventory', []):
        if isinstance(entry, dict) and entry.get('series_id') is not None:
            inventory_by_id.setdefault(str(entry['series_id']), []).append(entry)
    indicators = {}
    for item in catalog:
        series_id = str(item['series_id'])
        if series_id in indicators:
            continue
        record = {key: value for key, value in item.items() if key != '_path'}
        record.update(series_id=series_id, parquet_path=item['_path'],
                      parquet_sha256=item['snapshot_sha256'], parquet_sha256_verified=True,
                      audit_path=str(candidate_audit_path), source_jsonl_sha256=source_hash,
                      source_audit_sha256=audit_hash,
                      verification_scope=candidate_audit.get('verification_scope'),
                      usage_status=dict(_CEIC_USAGE_STATUS), accepted=False,
                      production_write=False, basis_eligible=False, replay_eligible=False)
        association = _CEIC_REQUIREMENT_ASSOCIATIONS.get(series_id)
        if association:
            record.update(association, association_status='explicit_id_mapping')
        elif series_id == '3658101':
            record.update(products=[], requirement_ids=[], association_status='background_only',
                          association_note='煤炭背景指标，不映射为JM供给')
        else:
            record.update(products=[], requirement_ids=[], association_status='unmapped')
        source_items = inventory_by_id.get(series_id, [])
        record['metadata_missing'] = _ceic_metadata_missing(item, source_items)
        indicators[series_id] = record
    result.update(status='verified_saved_candidate_audit', source_jsonl_sha256=source_hash,
                  source_audit_sha256=audit_hash,
                  source_audit_verification_scope=source_audit.get('verification_scope'),
                  source_audit_metadata_missing=source_audit.get('metadata_missing', []),
                  verification_scope=candidate_audit.get('verification_scope'),
                  indicators=[indicators[key] for key in sorted(indicators)],
                  usage_status=dict(_CEIC_USAGE_STATUS),
                  candidate_audit_sha256=hashlib.sha256(candidate_audit_bytes).hexdigest(),
                  candidate_audit_path=str(candidate_audit_path))
    result['metadata_missing'] = sorted({entry for item in result['indicators']
                                         for entry in item['metadata_missing']})
    return result


def financial_inventory(source_root):
    """Bounded read-only check of latest local daily partition, not an entitlement probe."""
    import duckdb
    source_root = Path(source_root)
    partitions = sorted((source_root/'futureshistoryprices1d').glob('year_month=*'))
    if not partitions:
        raise ValueError('No daily partitions to inspect')
    partition = partitions[-1]
    files = sorted(partition.glob('*.parquet'))
    if not files:
        raise ValueError('Latest daily partition is empty')
    hashes = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    candidates = {'IM':'000852','IF':'000300','IC':'000905','IH':'000016'}
    with duckdb.connect() as con:
        con.execute('CREATE TABLE sampled AS SELECT exchange,symbol,trade_date,close FROM read_parquet(?)', [list(hashes)])
        exchanges = dict(con.execute('SELECT exchange,count(*) FROM sampled GROUP BY exchange ORDER BY exchange').fetchall())
        bounds = con.execute('SELECT CAST(min(trade_date) AS VARCHAR),CAST(max(trade_date) AS VARCHAR) FROM sampled').fetchone()
        rows = []
        for product,code in candidates.items():
            matches = con.execute("SELECT exchange,symbol,count(*),count(close),CAST(min(trade_date) AS VARCHAR),CAST(max(trade_date) AS VARCHAR) "
                "FROM sampled WHERE split_part(upper(symbol),'.',1)=? GROUP BY exchange,symbol ORDER BY exchange,symbol", [code]).fetchall()
            rows.append({'id':'FIN-01-'+product,'product':product,'candidate_identifier':code,
                         'status':'identity_and_coverage_review_needed' if matches else 'not_found_in_sampled_partition',
                         'matches':[dict(zip(('exchange','symbol','rows','priced_rows','first','last'),row)) for row in matches],
                         'basis_eligible':False, 'carry_inputs_status':'funding_dividend_and_expiry_not_verified'})
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest()!=digest for p,digest in hashes.items()):
        raise ValueError('Daily source changed during inventory; rerun bounded audit')
    return {'kind':'financial_local_inventory','partition':str(partition),'source_sha256':hashes,
            'sample_start':bounds[0],'sample_end':bounds[1],'exchanges':exchanges,'indicators':rows,
            'production_write':False,'network_fetch':False,
            'limits':['仅最新月分区，不推断全历史或RDS不存在','候选代码命中不等于指数身份/收盘时点已核实',
                      '8888/9999期货合成序列不作为指数现货','未查询Wind或CEIC权限，不安装依赖']}


def product_actions(report):
    """Prioritize existing evidence and queries without granting trading eligibility."""
    from backend.data import (FUNDAMENTAL_PRODUCTS, _COVERAGE_ROLES, _COVERAGE_CEIC_CATEGORIES,
                              _COVERAGE_LABELS, _FINANCIAL_PRODUCTS)
    priority = ['IM','IF','IC','IH','RB','CU','M','Y','P','I','J','JM','SC','T','TF','TS','TL']
    products = sorted({p['product'] for p in report.get('products_to_verify',[])},
                      key=lambda p:(priority.index(p) if p in priority else len(priority),p))
    rows = []
    imported_ceic = {str(i['series_id']) for i in report.get('ceic_candidates', [])}
    for product in products:
        available, candidates, terminal_candidates = [], [], []
        for code in report['codes']:
            mapped = product in FUNDAMENTAL_PRODUCTS.get(code['code'], [])
            claimed = any(str(c.get('product','')).upper() == product for c in code.get('claims',[]))
            terminal = code.get('candidate_terminal_evidence')
            if terminal:
                # Current same-code evidence supersedes old product guesses only;
                # it does not accept values or approve business use.
                if terminal.get('classification') == 'reference_candidate' and product in terminal.get('products', []):
                    terminal_candidates.append({'code':code['code'], 'name':terminal['name'],
                                                'limits':terminal.get('limits', [])})
            elif mapped and code.get('review_status') == 'available_scope_limited':
                item = code['terminal_evidence']
                available.append({'id':'wind:'+code['code'], 'role':item['role'],
                                  'category':_COVERAGE_ROLES.get(item['role'], 'other'),
                                  'limits':item.get('limits',[]), 'use':'original_series_review_only'})
            elif mapped or claimed:
                candidates.append(code['code'])
        for item in report.get('ceic_candidates',[]):
            if product in item.get('products',[]):
                available.append({'id':'ceic:'+item['series_id'], 'role':'technical_candidate',
                                  'category':_COVERAGE_CEIC_CATEGORIES.get(item['series_id'], 'other'),
                                  'limits':item.get('metadata_missing',[]), 'use':'original_series_review_only'})
        financial = [r for r in report.get('financial_requirements',[]) if product in r['products']]
        index = next((i for i in report.get('index_close_review',{}).get('indicators',[]) if i['product']==product),None)
        if index:
            available.append({'id':'wind_wsd:'+index['code'],'role':'observed_index_close_basis',
                              'category':'financial_underlying',
                              'limits':index['limits'],'use':'daily_close_basis_only'})
        gaps = [r for r in report.get('requirements',[]) if r['product']==product]
        pending_import = [i for i in report.get('ceic_browser_review',{}).get('indicators',[])
                          if str(i['series_id']) not in imported_ceic and i.get('local_end') is None and any(
                              r.startswith('REQ-'+product+'-') for r in i.get('requirement_ids',[]))]
        categories = (['financial_underlying','carrying_cost'] if product in _FINANCIAL_PRODUCTS else
                      ['reference_spot','exchange_inventory','commercial_inventory','supply_demand'])
        use_coverage = []
        for category in categories:
            ids = [i['id'] for i in available if i['category'] == category]
            use_coverage.append({'id':f'REQ-{product}-{category.upper()}', 'category':category,
                'label':_COVERAGE_LABELS[category], 'series_ids':ids,
                'status':'saved_limited_review' if ids else 'missing_or_applicability_unverified',
                'business_complete':False,
                'next_action':'核验现有序列适用范围及历史可得性，不重复取数' if ids else
                              '发现可用序列；如不适用须记录依据，不以空数据算完成'})
        rows.append({'id':'REQ-'+product,'product':product,'review_series':available,
                     'use_coverage':use_coverage,
                     'unverified_candidates':sorted(set(candidates)),
                     'terminal_metadata_candidates':terminal_candidates,
                     'ceic_pending_import':[{'series_id':i['series_id'],'name':i['name']} for i in pending_import],
                     'existing_requirement_ids':[r['id'] for r in financial+gaps],
                     'next_action': ('指数日收盘基差已开放；待核验分红、资金、到期及交易成本，不输出套利收益' if index else
                                     '核查已有标的与持有成本数据：'+financial[0]['need'] if financial else
                                     'CEIC已定位新增序列，待正常导出与隔离验收；不重复询问Alice或提前计入覆盖' if pending_import else
                                     '先核对已有同码参考序列的历史单位及可比口径；不重复索取已给属性截图' if terminal_candidates else
                                     '先补现货可比口径；已有库存/供需序列补统计范围、统计期和发布规则' if available else
                                     '核验保存候选身份与适用性；无候选则发现现货及相关库存指标'),
                     'basis_eligible':False,'replay_eligible':False,
                     'active_product_verified':False})
    return rows


def coverage_status():
    """Overlay verified isolated read evidence on the saved inventory, not the registry."""
    from backend.data import fundamental_review, fundamental_candidates, ceic_review_catalog, index_daily
    folder = ROOT / 'runtime' / 'fundamental_lab'
    inventory_bytes = (folder / 'coverage_inventory.json').read_bytes()
    base = json.loads(inventory_bytes)
    if base.get('production_enabled') is not False:
        raise ValueError('Read-only inventory required')
    review = fundamental_review(folder)
    available = {item['code']: item for item in review['indicators']}
    excluded = {item['code']: item for item in review['excluded']}
    candidate_review = fundamental_candidates(folder) if (folder/'candidate_review.json').exists() else {}
    current_fields = {item['code']:item['terminal_metadata'] for item in candidate_review.get('indicators', [])
                      if item.get('terminal_metadata')}
    candidate_path = ROOT / 'fundamental_candidates.json'
    if candidate_path.exists():
        candidates = json.loads(candidate_path.read_text(encoding='utf-8'))
        if candidates.get('production_enabled') is not False:
            raise ValueError('Candidate production approval not permitted')
        for item in candidates['indicators']:
            if item['code'] not in {c['code'] for c in base['codes']}:
                base['codes'].append({'code':item['code'], 'references':[item['id']],
                    'claims':[item], 'production_ready':False})
    known = {item['code'] for item in base['codes']}
    if not (available.keys() | excluded.keys()) <= known:
        raise ValueError('Review codes missing from saved inventory')
    for item in base['codes']:
        code = item['code']
        item['production_ready'] = False
        item['review_status'] = ('available_scope_limited' if code in available else
                                 'quarantined' if code in excluded else 'identity_evidence_needed')
        if code in available or code in excluded:
            item['terminal_evidence'] = available.get(code, excluded.get(code))
            item['identity_status'] = 'terminal_fields_confirmed_scope_limited'
        elif code in current_fields:
            evidence = current_fields[code]
            item['candidate_terminal_evidence'] = evidence
            item['identity_status'] = evidence['identity_status']
            item['review_status'] = {'futures_excluded':'not_fundamental', 'background':'background_only'}.get(
                evidence.get('classification'), 'terminal_metadata_only')
        # Keep old claims and saved probe separate; new terminal evidence does not erase history.
    base.update(kind='isolated_coverage_status_not_registry',
        inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
        review_snapshot_sha256=review['snapshot_sha256'])
    base['counts'].update(review_available_codes=len(available), quarantined_codes=len(excluded),
        unique_codes=len(known),
        identity_evidence_needed_codes=len(known-available.keys()-excluded.keys()-current_fields.keys()),
        candidate_terminal_confirmed_codes=len(current_fields),
        review_observations=sum(i['observations'] for i in available.values()), verified_codes=0)
    ceic = _ceic_candidate_status(ROOT, ceic_review_catalog)
    # Keep CEIC candidate evidence separate from the Wind code ledger and its
    # requirement matrix; a technical review does not close any requirement.
    base['ceic_candidates'] = ceic['indicators']
    base['ceic_candidate_audit'] = {key: value for key, value in ceic.items() if key != 'indicators'}
    base['ceic_usage_status'] = dict(ceic['usage_status'])
    browser_path = ROOT / 'runtime/ceic_lab/browser_review_20260914.json'
    if browser_path.exists():
        browser_bytes = browser_path.read_bytes()
        browser_review = json.loads(browser_bytes)
        if (browser_review.get('kind') != 'ceic_browser_metadata_review' or any(
                browser_review.get(k) is not False for k in ('production_write','basis_eligible','replay_eligible','data_imported'))):
            raise ValueError('CEIC browser evidence must remain metadata-only')
        base['ceic_browser_review'] = {**browser_review, 'source_sha256':hashlib.sha256(browser_bytes).hexdigest()}
    if (folder/'index_review.json').exists():
        base['index_close_review'] = {k:v for k,v in index_daily(folder).items() if k!='points'}
    base['product_actions'] = product_actions(base)
    base['warnings'].append('terminal_evidence为当前隔离证据；claims和saved_probe保留旧记录。请求覆盖不代表需求已满足，所有生产启用状态仍为false。')
    base['warnings'].append('CEIC候选仅在当前web_review源JSONL/审计哈希匹配且候选Parquet SHA256校验通过时列入；review仅表示技术审阅，reference/basis/replay均为false，未关闭任何需求。')
    return base


def delivery_check():
    """Check pinned local evidence and expose delivery gaps; never fetch or select data."""
    from backend.data import DailyStore
    protected = [ROOT/'runtime/fundamental_lab'/name for name in
                 ('accepted_review.json','index_review.json','candidate_review.json')]
    protected.append(ROOT/'runtime/ceic_lab/selected_review.json')
    def hashes():
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                if p.is_file() else None for p in protected}
    before = hashes()
    report = coverage_status()  # Existing readers verify snapshot/source hashes.
    store = DailyStore()
    catalog = store.products()
    market = store.market_snapshot()
    radar_products = {r['symbol'][:-4] for r in market['rows']}
    current = {r['product'] for r in catalog}
    delivery_products = current | radar_products
    historical = {r['product'] for r in report.get('products_to_verify', [])}
    report['products_to_verify'] = [{'product':p} for p in sorted(delivery_products | historical)]
    actions = product_actions(report)
    for row in actions:
        row['in_dashboard_catalog'] = row['product'] in current
        row['in_radar_window'] = row['product'] in radar_products
        row['in_delivery_scope'] = row['product'] in delivery_products
    checks = {'dashboard_catalog_available':bool(current),
              'wind_review_available':report['counts'].get('review_available_codes', 0) > 0,
              'ceic_snapshot_verified':report.get('ceic_candidate_audit', {}).get('status') == 'verified_saved_candidate_audit',
              'index_review_available':bool(report.get('index_close_review', {}).get('indicators')),
              'protected_pointers_unchanged':before == hashes()}
    if not checks['protected_pointers_unchanged']:
        raise ValueError('Protected pointers changed during read-only delivery check; retry after writer finishes')
    # Deduplicate by provider-qualified identity, retaining every affected product.
    evidence_tasks = {}
    for row in actions:
        if not row['in_delivery_scope']:
            continue
        for item in row['review_series']:
            task = evidence_tasks.setdefault(item['id'], {'id':item['id'], 'products':[],
                'route':'source_metadata_review', 'missing_or_limits':item['limits']})
            task['products'].append(row['product'])
        for item in row['terminal_metadata_candidates']:
            task = evidence_tasks.setdefault('wind:'+item['code'], {'id':'wind:'+item['code'],
                'products':[], 'route':'reuse_current_attributes_review_historical_scope',
                'missing_or_limits':item['limits']})
            task['products'].append(row['product'])
    missing = [{**use, 'product':row['product']} for row in actions if row['in_delivery_scope']
               for use in row['use_coverage'] if not use['series_ids']]
    import_tasks = {}
    for row in actions:
        if row['in_delivery_scope']:
            for item in row['ceic_pending_import']:
                task = import_tasks.setdefault(item['series_id'], {**item, 'products':[],
                    'route':'authorized_browser_export_then_isolated_audit_not_user_question'})
                task['products'].append(row['product'])
    waterlines = []
    entries = [('wind:'+r['code'], r['terminal_evidence']) for r in report['codes']
               if r.get('review_status') == 'available_scope_limited']
    entries += [('ceic:'+r['series_id'], r) for r in report.get('ceic_candidates', [])]
    entries += [('wind_wsd:'+r['code'], r) for r in report.get('index_close_review', {}).get('indicators', [])]
    for identity, item in entries:
        last = item.get('last')
        next_period = None
        if last:
            day = date.fromisoformat(last[:10])
            next_period = ((day.replace(day=28)+timedelta(days=4)).replace(day=1) if
                           identity.startswith('ceic:') and item.get('frequency') == 'M' else
                           day+timedelta(days=1)).isoformat()
        waterlines.append({'id':identity, 'last_saved_observation':last,
            'frequency':item.get('frequency'), 'next_interval_start':next_period,
            'action':'authorized_export_then_preview' if identity.startswith('ceic:') else 'explicit_isolated_append',
            'fetch_due_verified':False, 'note':'日期是增量起点，不保证此日发布；缺失不前填，历史修订另行审批'})
    recovery_path = ROOT/'runtime/ceic_lab/recovery_rehearsal.json'
    recovery = json.loads(recovery_path.read_bytes()) if recovery_path.exists() else {}
    recovery_checks = ('wide_roundtrip','same_version_noop','restore_previous','return_to_current',
        'lock_rejected','corrupt_previous_rejected','failure_keeps_pointer','lock_released',
        'original_sources_unchanged','temporary_copy_removed')
    recovery_current = (recovery.get('kind') == 'ceic_recovery_rehearsal'
        and all(recovery.get('checks', {}).get(k) is True for k in recovery_checks)
        and recovery.get('selected_pointer_sha256') == before.get(str(Path('runtime/ceic_lab/selected_review.json')))
        and (ROOT/'tools/ceic_lab.py').is_file()
        and recovery.get('implementation_sha256') == hashlib.sha256((ROOT/'tools/ceic_lab.py').read_bytes()).hexdigest()
        and all(recovery.get(k) is False for k in ('production_write','network_fetch','live_selection_changed')))
    return {'kind':'isolated_delivery_check', 'checked_at':datetime.now(timezone.utc).isoformat(),
        'checks':checks, 'local_snapshot_check_passed':all(checks.values()),
        'recovery_rehearsal_current':bool(recovery_current),
        'local_handoff_preflight_passed':all(checks.values()) and bool(recovery_current),
        'handoff_preflight_scope':'saved_sources_and_recovery_only_not_business_or_visual_or_production_acceptance',
        'scope':'latest_local_partition_union_radar_window_not_official_listing', 'catalog':catalog,
        'radar_scope':{'start':market['start'], 'end':market['requested_end'],
                       'snapshot_id':market['snapshot_id'], 'radar_only_products':sorted(radar_products-current)},
        'counts':{**report['counts'], 'dashboard_products':len(current),
                  'historical_labels':len(historical), 'historical_only_labels':len(historical-current),
                  'radar_products':len(radar_products), 'delivery_products':len(delivery_products),
                  'ceic_series':len(report.get('ceic_candidates', [])),
                  'products_with_saved_series':sum(bool(r['review_series']) for r in actions if r['in_delivery_scope']),
                  'missing_product_uses':len(missing), 'deduplicated_evidence_tasks':len(evidence_tasks)},
        'protected_pointer_sha256':before, 'ceic_audit':report.get('ceic_candidate_audit'),
        'product_actions':actions, 'discovery_tasks':missing, 'import_tasks':list(import_tasks.values()),
        'update_plan':waterlines,
        'evidence_tasks':list(evidence_tasks.values()),
        'full_delivery_ready':False, 'production_enabled':False, 'rds_write':False, 'network_fetch':False,
        'remaining_gates':['逐品种用途/适用性及候选业务口径仍需验收',
                           '自动检查不代替页面视觉验收，详见HANDOFF实际测试记录',
                           '无API的CEIC采用授权导出文件接入，不声称稳定无人值守采集',
                           '生产范围、RDS写入、调度未授权，真实连续发布观察未完成']}


def daily_review(end, execute=False, fetch=False):
    """Independent, fail-closed continuation of the pinned isolated history.

    Default is a plan. --execute reuses saved responses; --fetch additionally
    permits missing intervals. No production publisher or scheduler is imported.
    """
    from backend.data import fundamental_review
    folder = ROOT / 'runtime/fundamental_lab'
    cutoff = date.fromisoformat(end)
    if cutoff > date.today():
        raise ValueError('Future cutoff is not permitted')
    current = fundamental_review(folder)
    state = json.loads((folder / 'update_checkpoint.json').read_bytes())
    if state.get('production_enabled') is not False:
        raise ValueError('Isolated checkpoint required')
    scope = state['scope']
    if end < scope['end']:
        raise ValueError('Cannot move accepted history backwards; use explicit repair/review')
    pointer = json.loads((folder / 'accepted_review.json').read_bytes())
    metadata = folder / pointer['metadata']
    if hashlib.sha256(metadata.read_bytes()).hexdigest() != scope['metadata_sha256']:
        raise ValueError('Metadata changed; explicit initial review is required')
    runs = [Path(p) for p in state['runs']]
    prepared = prepare_runs(runs, metadata, scope['start'], end)
    waterlines = {i['code']:i['last'] for i in current['indicators']}
    import duckdb
    with duckdb.connect() as con:
        accepted_rows = con.execute('SELECT code, observation_date, raw_value FROM read_parquet(?)',
                                   [str(folder / pointer['file'])]).fetchall()
    previous_values = {(code, day):value for code,day,value in accepted_rows}
    reused_values = {(i['code'],p['observation_date']):p['raw_value']
        for i in prepared['indicators'] for p in i['points']
        if p['observation_date'] <= waterlines.get(i['code'], '')}
    if reused_values != previous_values:
        raise ValueError('Saved history changed or gained late observations; explicit repair required')
    append = plan_requests(list(waterlines), 'append', end, waterlines)
    # Historical saved coverage is not a permanent guarantee of future releases.
    for request in prepared['pending_requests']:
        allowed = next((p for p in append if p['code']==request['code']), None)
        if not allowed or request['start'] < allowed['start']:
            raise ValueError('History hole detected; explicit repair required, not daily append')
    fresh_requests = [dict(p) for p in append if p['due']]
    for run in runs:
        payload = json.loads(run.read_bytes())
        observed = payload.get('requested_at')
        if not observed or datetime.fromisoformat(observed).astimezone().date() != date.today():
            continue
        saved = prepare_saved(run, metadata, scope['start'], end)
        for item in saved['indicators']:
            interval = item.get('reused_request_interval')
            if not interval:
                continue
            lower, upper = map(date.fromisoformat, interval)
            remaining = []
            for request in fresh_requests:
                if request['code'] != item['code'] or request['end'] < interval[0] or request['start'] > interval[1]:
                    remaining.append(request)
                    continue
                for lo, hi in ((request['start'], (lower-timedelta(days=1)).isoformat()),
                               ((upper+timedelta(days=1)).isoformat(), request['end'])):
                    if lo <= hi:
                        remaining.append({**request, 'start':lo, 'end':hi})
            fresh_requests = remaining
    report = {'kind':'independent_daily_review', 'observed_at':datetime.now(timezone.utc).isoformat(),
        'requested_end':end, 'production_enabled':False, 'rds_write':False, 'scheduler_enabled':False,
        'before_sha256':current['snapshot_sha256'], 'waterlines':waterlines,
        'append_plan':append, 'pending_requests':prepared['pending_requests'],
        'release_check_requests':fresh_requests,
        'no_new_observations':prepared.get('no_new_observations', []),
        'network_fetch_enabled':bool(fetch), 'executed':bool(execute or fetch),
        'ceic_update':'saved_candidates_only_no_online_update',
        'state':'plan_only', 'production_ready':False,
        'remaining_gates':['business_metadata_and_spot_comparability', 'ceic_authorized_update_channel',
            'real_daily_weekly_monthly_observation_cycles', 'production_publish_and_schedule_authorization']}
    if execute or fetch:
        if (state['state'] != 'completed' and scope['end'] != end and
                not (state['state'] == 'awaiting_release' and end >= scope['end'])):
            raise ValueError('Resume unfinished checkpoint at its original cutoff first')
        outcome = update_review(runs, metadata, scope['start'], end, fetch=fetch,
                                append_requests=fresh_requests if fetch else None)
        report.update(state=outcome['state'], after_sha256=outcome.get('sha256'),
                      pending_requests=outcome.get('pending_requests', []),
                      no_new_observations=outcome.get('no_new_observations', []),
                      awaiting_release=outcome.get('awaiting_release', []))
        report['evidence_file'] = str(save_run(report))
    save_run(report, folder / 'daily_review_status.json')
    return report


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    daily = sub.add_parser('daily-review', help='Standalone isolated continuation; default plan, no fetch/RDS')
    daily.add_argument('--end', required=True)
    daily.add_argument('--execute', action='store_true', help='Apply reusable saved evidence only')
    daily.add_argument('--fetch', action='store_true', help='Explicitly fetch missing new intervals into isolation')
    inv = sub.add_parser("inventory", help="Read candidate registry; no Wind calls")
    inv.add_argument("--registry", type=Path, default=REGISTRY)
    sub.add_parser("catalog", help="Print existing plain-text Alice question batches")
    sub.add_parser('coverage-status', help='Save current isolated coverage report; never fetch/publish')
    delivery = sub.add_parser('delivery-check', help='Verify pinned local sources and save delivery gaps; no fetch/publish')
    delivery.add_argument('--require-recovery', action='store_true', help='Also require matching successful offline recovery evidence')
    candidate = sub.add_parser('candidate-review', help='Reuse saved unverified values, never fetch or accept business metadata')
    candidate.add_argument('--source-root', type=Path, default=Path(r'E:\交易数据\本地表\futuresfundamentals'))
    financial = sub.add_parser('financial-inventory', help='Inspect latest local daily partition for candidate index identifiers; no network')
    financial.add_argument('--source-root', type=Path, default=Path(r'E:\交易数据\本地表'))
    index_probe = sub.add_parser('index-probe', help='Explicit isolated Wind close probe for four index candidates; no publication')
    index_probe.add_argument('--start', required=True)
    index_probe.add_argument('--end', required=True)
    index_probe.add_argument('--timeout', type=float, default=50)
    index_probe.add_argument('--metadata-only', action='store_true', help='Query code/name only, no historical prices')
    index_update_cmd = sub.add_parser('index-update', help='Saved index replay / isolated append; --fetch explicitly queries Wind')
    index_update_cmd.add_argument('--run', type=Path, nargs='+')
    index_update_cmd.add_argument('--metadata', type=Path)
    index_update_cmd.add_argument('--start')
    index_update_cmd.add_argument('--end', required=True)
    index_update_cmd.add_argument('--fetch', action='store_true')
    update = sub.add_parser('update-review', help='Isolated saved-data workflow; fetching requires --fetch')
    update.add_argument('--run', type=Path, nargs='+', required=True)
    update.add_argument('--metadata', type=Path, required=True)
    update.add_argument('--start', required=True)
    update.add_argument('--end', required=True)
    update.add_argument('--fetch', action='store_true')
    worker = sub.add_parser('_worker', help=argparse.SUPPRESS)
    worker.add_argument('--run', type=Path, required=True)
    prepare = sub.add_parser('prepare', help='Review saved evidence and plan uncovered intervals; no fetch/publish')
    prepare.add_argument('--run', type=Path, nargs='+', required=True)
    prepare.add_argument('--metadata', type=Path, required=True)
    prepare.add_argument('--start', required=True)
    prepare.add_argument('--end', required=True)
    recovery = sub.add_parser('review-plan', help='Save isolated recovery plan from existing evidence; no fetch/publish')
    recovery.add_argument('--run', type=Path, nargs='+', required=True)
    recovery.add_argument('--metadata', type=Path, required=True)
    recovery.add_argument('--start', required=True)
    recovery.add_argument('--end', required=True)
    snap = sub.add_parser('snapshot', help='Verify isolated review Parquet from saved evidence; no fetch/publish')
    snap.add_argument('--run', type=Path, nargs='+', required=True)
    snap.add_argument('--metadata', type=Path, required=True)
    snap.add_argument('--start', required=True)
    snap.add_argument('--end', required=True)
    staging = sub.add_parser("stage", help="Stage saved raw evidence in isolated SQLite; never publishes or fetches")
    staging.add_argument("--run", type=Path, required=True)
    staging.add_argument("--catalog", type=Path, default=ROOT / "fundamental_candidates.json")
    staging.add_argument("--only", help="Explicit comma-separated code subset of this saved run; omitted means whole batch")
    for command in ("plan", "probe"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--codes", required=True)
        cmd.add_argument("--start", required=command == "probe")
        cmd.add_argument("--end", required=True)
        if command == "plan":
            cmd.add_argument("--mode", choices=("initial", "append", "repair"), required=True)
            cmd.add_argument("--waterlines", type=Path, help="JSON code: published YYYY-MM-DD mapping")
        else:
            cmd.add_argument("--timeout", type=float, default=120,
                             help="Whole-batch timeout in seconds (default: 120); retains checkpoints")
    args = parser.parse_args(argv)
    try:
        if args.command == 'index-update':
            sys.path.insert(0,str(ROOT))
            output = index_update(args.run,args.metadata,args.start,args.end,args.fetch)
            print(json.dumps(output,ensure_ascii=False,indent=2))
            return 0 if output['state']=='completed' else 2
        if args.command == 'index-probe':
            path, status = probe(['000016.SH','000300.SH','000852.SH','000905.SH'], args.start, args.end, args.timeout,
                                 api='wss' if args.metadata_only else 'wsd')
            print(json.dumps({'path':str(path),'status':status}, ensure_ascii=False))
            return status
        if args.command == '_worker':
            if args.run.resolve().parent != (ROOT / 'runtime/fundamental_lab').resolve():
                raise ValueError('Worker requires isolated checkpoint')
            _probe_worker(args.run)
            return 0
        if args.command == 'daily-review':
            sys.path.insert(0, str(ROOT))
            output = daily_review(args.end, args.execute, args.fetch)
        elif args.command == 'update-review':
            output = update_review(args.run, args.metadata, args.start, args.end, args.fetch)
        elif args.command == "inventory":
            output = inventory(args.registry)
        elif args.command == 'candidate-review':
            output = candidate_snapshot(args.source_root)
        elif args.command == "catalog":
            print((ROOT / "ALICE_PROMPTS.md").read_text(encoding="utf-8"))
            return 0
        elif args.command == 'financial-inventory':
            report = financial_inventory(args.source_root)
            path = save_run(report, ROOT / 'runtime/fundamental_lab/financial_inventory.json')
            output = {'path':str(path), **report}
        elif args.command == 'delivery-check':
            sys.path.insert(0, str(ROOT))
            report = delivery_check()
            path = save_run(report, ROOT/'runtime/fundamental_lab/delivery_status.json')
            print(json.dumps({'path':str(path), 'checks':report['checks'], 'counts':report['counts'],
                              'local_handoff_preflight_passed':report['local_handoff_preflight_passed'],
                              'full_delivery_ready':False}, ensure_ascii=False, indent=2))
            passed = report['local_handoff_preflight_passed'] if args.require_recovery else report['local_snapshot_check_passed']
            return 0 if passed else 2
        elif args.command == 'coverage-status':
            # Script execution must resolve the project backend without production imports.
            sys.path.insert(0, str(ROOT))
            report = coverage_status()
            path = save_run(report, ROOT / 'runtime/fundamental_lab/coverage_status.json')
            action_path = path.parent / 'product_actions.md'
            lines = ['# 全品种基本面：现有证据与最小补数清单', '',
                     '由保存证据生成；排序为实施优先级，不是交易机会排名。品种标签尚未核实为在市目录。',
                     'EDB/CEIC保留原值审阅限制；四个股指另开放日收盘基差，不代表套利、回测或信号准入。缺发布时间不允许历史前视回测。', '',
                     '|需求ID|已有审阅序列及用途|当前终端属性已核验的候选（未准入）|CEIC已定位待导入|未核验候选|原需求编号|下一动作|',
                     '|---|---|---|---|---|---|---|']
            for row in report['product_actions']:
                fields = [row['id'], '; '.join(i['id']+' ('+i['role']+')' for i in row['review_series']) or '无',
                          '; '.join(i['code']+' '+i['name'] for i in row['terminal_metadata_candidates']) or '无',
                          '; '.join(i['series_id']+' '+i['name'] for i in row['ceic_pending_import']) or '无',
                          ', '.join(row['unverified_candidates']) or '无',
                          ', '.join(row['existing_requirement_ids']) or '沿用固定REQ编号', row['next_action']]
                lines.append('|'+ '|'.join(f.replace('|','/').replace('\n',' ') for f in fields)+'|')
            lines += ['', '## 已有序列需要补齐的证据（去重）', '']
            evidence = {i['id']:i for row in report['product_actions'] for i in row['review_series']}
            lines += ['- '+key+'：'+'；'.join(str(v) for v in item['limits']) for key,item in sorted(evidence.items())]
            lines += ['', '## 按用途缺口（有保存序列也不代表业务完成）', '',
                      '|品种|用途|已有序列|下一动作|', '|---|---|---|---|']
            for row in report['product_actions']:
                for use in row['use_coverage']:
                    lines.append('|'+ '|'.join([row['product'], use['label'],
                        ', '.join(use['series_ids']) or '缺少/适用性待核', use['next_action']])+'|')
            lines += ['', '不再让Alice重复查询已穷尽问题：精确身份/口径转终端或数据商人工支持；无API的CEIC走授权网页导出。',
                      '未按品种映射的CEIC序列仍保留在coverage_status.json，不按名称猜配，也不视为已满足某品种需求。']
            action_path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
            output = {'path':str(path), 'actions':str(action_path), 'counts':report['counts'], 'production_enabled':False}
        elif args.command == "plan":
            lines = json.loads(args.waterlines.read_text(encoding="utf-8")) if args.waterlines else {}
            output = plan_requests(codes_list(args.codes), args.mode, args.end, lines, args.start)
        elif args.command == 'prepare':
            output = prepare_runs(args.run, args.metadata, args.start, args.end)
        elif args.command == 'review-plan':
            output = review_plan(args.run, args.metadata, args.start, args.end)
        elif args.command == 'snapshot':
            output = snapshot(args.run, args.metadata, args.start, args.end)
        elif args.command == "stage":
            output = stage(args.run, args.catalog, codes_list(args.only) if args.only is not None else None)
        else:
            path, status = probe(codes_list(args.codes), args.start, args.end, timeout=args.timeout)
            print(path)
            return status
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.command in ('daily-review', 'update-review') and output.get('state') not in ('completed', 'plan_only'):
            return 2
        return 0
    except (ValueError, OSError, KeyError, sqlite3.Error, duckdb.Error) as exc:
        if args.command == 'delivery-check':
            save_run({'kind':'isolated_delivery_check', 'checked_at':datetime.now(timezone.utc).isoformat(),
                'local_snapshot_check_passed':False, 'local_handoff_preflight_passed':False,
                'recovery_rehearsal_current':False, 'full_delivery_ready':False, 'error':str(exc),
                'production_enabled':False, 'rds_write':False, 'network_fetch':False},
                ROOT/'runtime/fundamental_lab/delivery_status.json')
        if args.command == 'daily-review':
            save_run({'kind':'independent_daily_review', 'state':'failed', 'requested_end':args.end,
                'observed_at':datetime.now(timezone.utc).isoformat(), 'error':str(exc),
                'production_enabled':False, 'production_ready':False, 'rds_write':False,
                'scheduler_enabled':False}, ROOT / 'runtime/fundamental_lab/daily_review_status.json')
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
