from datetime import date
from contextlib import closing
import hashlib
import json
import pytest
import multiprocessing
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import fundamental_lab as lab


def test_candidate_snapshot_reuses_values_without_promoting_metadata(tmp_path, monkeypatch):
    import duckdb
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    folder = tmp_path/'runtime/fundamental_lab'
    folder.mkdir(parents=True)
    source = tmp_path/'legacy/year_month=2026-08'
    source.mkdir(parents=True)
    with duckdb.connect() as con:
        con.execute("COPY (SELECT 'S0031875' AS indicator, DATE '2026-08-03' AS trade_date, 0.0::DOUBLE AS value, 'old' AS name, 'spot' AS category, 'D' AS freq, '' AS unit, 'a' AS product) TO ? (FORMAT PARQUET)", [str(source/'data.parquet')])
        con.execute("COPY (SELECT 'S0031875' AS indicator, DATE '2026-08-03' AS trade_date, 0.0::DOUBLE AS value, 'second claim' AS name, 'spot' AS category, 'D' AS freq, '' AS unit, 'a' AS product) TO ? (FORMAT PARQUET)",[str(source/'variant.parquet')])
    codes = ['S0031875','S0033227']
    (folder/'coverage_status.json').write_text(json.dumps({'production_enabled':False,'codes':[
        {'code':c,'review_status':'identity_evidence_needed','claims':[{'name':'unverified claim'}],
         'references':['REQ-A']} for c in codes]}), encoding='utf-8')
    raw = {'production_write':False,'state':'completed','request':{'start':'2026-09-01','end':'2026-09-07'},
        'results':[{'code':'S0033227','error_code':0,'issues':[],
                    'observations':[{'date':'2026-09-07','value':3000,'missing':False}]}]}
    run = folder/'probe.json'
    run.write_text(json.dumps(raw), encoding='utf-8')
    (folder/'audit.json').write_text(json.dumps({'source_sha256':hashlib.sha256(run.read_bytes()).hexdigest()}),encoding='utf-8')
    (tmp_path/'fundamental_candidates.json').write_text(json.dumps({'production_enabled':False,
        'raw_probe':'runtime/fundamental_lab/probe.json','audit':'runtime/fundamental_lab/audit.json'}),encoding='utf-8')
    accepted = folder/'accepted_review.json'
    accepted.write_bytes(b'unchanged business snapshot')
    monkeypatch.setattr(lab, 'probe', Mock(side_effect=AssertionError('no fetch')))
    before = {p:p.read_bytes() for p in [run,source/'data.parquet',accepted]}
    report = lab.candidate_snapshot(source.parent)
    assert report['observations']==2 and report['request_coverage_known'] is False
    assert report['identity_status']=='unverified' and not report['network_fetch']
    assert all(r['name'] is None and r['unit'] is None and r['frequency'] is None for r in report['indicators'])
    assert report['indicators'][0]['source_metadata_claims'][0]['name']=='old'
    assert len(report['indicators'][0]['source_metadata_claims'])==2
    pointer = folder/'candidate_review.json'
    written = pointer.read_bytes(), pointer.stat().st_mtime_ns
    assert lab.candidate_snapshot(source.parent)==report
    assert (pointer.read_bytes(),pointer.stat().st_mtime_ns)==written
    coverage = json.loads((folder/'coverage_status.json').read_bytes())
    coverage['codes'][0]['review_status'] = 'not_fundamental'
    coverage['codes'][1]['review_status'] = 'terminal_metadata_only'
    (folder/'coverage_status.json').write_text(json.dumps(coverage), encoding='utf-8')
    assert lab.candidate_snapshot(source.parent) == report
    assert (pointer.read_bytes(),pointer.stat().st_mtime_ns) == written
    assert len(list(folder.glob('candidates_*.parquet'))) == 1
    with duckdb.connect() as con:
        restored=con.execute('SELECT code,observation_date,raw_value FROM read_parquet(?) ORDER BY code',[str(folder/report['file'])]).fetchall()
    assert restored==[('S0031875','2026-08-03',0.0),('S0033227','2026-09-07',3000.0)]
    assert all(p.read_bytes()==value for p,value in before.items())
    # Conflicting saved history must not replace a previous candidate pointer.
    with duckdb.connect() as con:
        con.execute("COPY (SELECT 'S0031875' AS indicator, DATE '2026-08-03' AS trade_date, 1.0::DOUBLE AS value, 'other' AS name, 'spot' AS category, 'D' AS freq, '' AS unit, 'a' AS product) TO ? (FORMAT PARQUET)",[str(source/'conflict.parquet')])
    with pytest.raises(ValueError,match='Conflicting'):
        lab.candidate_snapshot(source.parent)
    assert pointer.read_bytes()==written[0]
    run.write_bytes(b'changed raw evidence')
    (source/'conflict.parquet').unlink()
    with pytest.raises(ValueError,match='hash changed'):
        lab.candidate_snapshot(source.parent)
    assert pointer.read_bytes()==written[0]


def _daily_fixture(tmp_path, codes=('S0049493', 'S0114170')):
    import duckdb
    from datetime import datetime, timedelta, timezone

    folder = tmp_path / 'runtime/fundamental_lab'
    folder.mkdir(parents=True)
    indicators = [{'code': code, 'name': code + ' test', 'unit': '吨', 'frequency': 'D',
                   'available_start': '2021-01-01', 'role': 'reference', 'limits': []}
                  for code in codes]
    metadata = folder / 'metadata.json'
    metadata.write_text(json.dumps({'production_enabled': False,
        'user_confirmed_no_manual_conversion': True,
        'field_status': 'terminal_default_display_confirmed',
        'indicators': indicators}), encoding='utf-8')
    requested = {'codes': list(codes), 'start': '2021-01-01', 'end': '2026-09-09', 'options': ''}
    base = folder / 'base.json'
    base.write_text(json.dumps({'requested_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        'request': requested, 'state': 'completed', 'results': [
            {'code': code, 'error_code': 0, 'issues': [],
             'observations': [{'date': '2026-09-09', 'value': 100 + i, 'missing': False}]}
            for i, code in enumerate(codes)], 'production_write': False,
        'identity_status': 'unverified'}), encoding='utf-8')
    source_sha256 = hashlib.sha256(base.read_bytes()).hexdigest()
    parquet = folder / 'accepted.parquet'
    with duckdb.connect() as con:
        con.execute('CREATE TABLE review (code VARCHAR, observation_date VARCHAR, raw_value DOUBLE)')
        con.executemany('INSERT INTO review VALUES (?,?,?)',
                        [(code, '2026-09-09', 100 + i) for i, code in enumerate(codes)])
        con.execute('COPY review TO ? (FORMAT PARQUET)', [str(parquet)])
    pointer = {'metadata': metadata.name, 'file': parquet.name,
               'sha256': hashlib.sha256(parquet.read_bytes()).hexdigest(),
               'production_enabled': False}
    (folder / 'accepted_review.json').write_text(json.dumps(pointer), encoding='utf-8')
    state = {'scope': {'runs': [str(base)], 'start': '2021-01-01', 'end': '2026-09-09',
                       'metadata_sha256': hashlib.sha256(metadata.read_bytes()).hexdigest(),
                       'sources': [source_sha256]},
             'runs': [str(base)], 'state': 'completed', 'production_enabled': False}
    (folder / 'update_checkpoint.json').write_text(json.dumps(state), encoding='utf-8')
    current = {'snapshot_sha256': pointer['sha256'],
               'indicators': [{'code': code, 'last': '2026-09-09'} for code in codes]}
    return folder, metadata, base, current


def test_failed_release_check_cannot_publish_old_coverage_as_success(tmp_path, monkeypatch):
    import pytest
    folder = tmp_path / 'runtime/fundamental_lab'
    folder.mkdir(parents=True)
    monkeypatch.setattr(lab,'ROOT',tmp_path)
    pointer = folder/'accepted_review.json'
    pointer.write_text('{"old":true}')
    prepared = {'metadata_sha256':'meta','source_sha256s':[], 'pending_requests':[], 'indicators':[], 'rejected':[]}
    monkeypatch.setattr(lab,'prepare_runs',lambda *a:prepared)
    def failed_worker(command, **kwargs):
        path=Path(command[-1]); payload=json.loads(path.read_text())
        payload.update(state='failed',results=[{'code':'S0049493','error_code':-1,'issues':['Wind EDB request failed']}])
        path.write_text(json.dumps(payload))
    monkeypatch.setattr(lab.subprocess,'run',failed_worker)
    publish=Mock();monkeypatch.setattr(lab,'snapshot',publish)
    with pytest.raises(ValueError,match='unsuccessful'):
        lab.update_review([],folder/'meta.json','2026-09-01','2026-09-09',True,
                          append_requests=[{'code':'S0049493','start':'2026-09-09','end':'2026-09-09'}])
    assert pointer.read_text()=='{"old":true}'
    assert json.loads((folder/'update_checkpoint.json').read_text())['state']=='failed'
    publish.assert_not_called()


def test_daily_review_defaults_to_saved_plan_and_rejects_history_changes(tmp_path, monkeypatch):
    import hashlib
    import duckdb
    import pytest
    from backend import data
    folder = tmp_path / 'runtime/fundamental_lab'
    folder.mkdir(parents=True)
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    (folder / 'meta.json').write_bytes(b'{}')
    (folder / 'accepted_review.json').write_text(json.dumps({'metadata':'meta.json','file':'p.parquet'}))
    (folder / 'update_checkpoint.json').write_text(json.dumps({'production_enabled':False,'state':'completed',
        'scope':{'start':'2026-09-01','end':'2026-09-09','metadata_sha256':hashlib.sha256(b'{}').hexdigest()},'runs':[]}))
    with duckdb.connect() as con:
        con.execute("COPY (SELECT 'S0049493' code, '2026-09-08' observation_date, 0.0 raw_value) TO ? (FORMAT PARQUET)",[str(folder/'p.parquet')])
    monkeypatch.setattr(data,'fundamental_review',lambda *a,**k:{'snapshot_sha256':'same','indicators':[{'code':'S0049493','last':'2026-09-08'}]})
    prepared = {'pending_requests':[], 'indicators':[{'code':'S0049493','points':[{'observation_date':'2026-09-08','raw_value':0}]}]}
    monkeypatch.setattr(lab,'prepare_runs',lambda *a:prepared)
    updater = Mock(return_value={'state':'completed','sha256':'same'})
    monkeypatch.setattr(lab,'update_review',updater)
    result = lab.daily_review('2026-09-09')
    assert result['state']=='plan_only' and not result['executed'] and not result['production_ready']
    assert result['release_check_requests'][0]['start']=='2026-09-09'
    updater.assert_not_called()
    assert lab.daily_review('2026-09-09',execute=True)['after_sha256']=='same'
    assert updater.call_args.kwargs['fetch'] is False
    from datetime import datetime, timezone, timedelta
    raw = folder / 'raw.json'
    state = json.loads((folder/'update_checkpoint.json').read_text())
    state['runs']=[str(raw)]
    (folder/'update_checkpoint.json').write_text(json.dumps(state))
    monkeypatch.setattr(lab,'prepare_saved',lambda *a:{'indicators':[{'code':'S0049493',
        'reused_request_interval':['2026-09-09','2026-09-09']}]})
    raw.write_text(json.dumps({'requested_at':datetime.now(timezone.utc).isoformat()}))
    assert lab.daily_review('2026-09-09')['release_check_requests']==[]
    raw.write_text(json.dumps({'requested_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}))
    assert len(lab.daily_review('2026-09-09')['release_check_requests'])==1
    prepared['indicators'][0]['points'][0]['raw_value']=1
    with pytest.raises(ValueError,match='explicit repair'):
        lab.daily_review('2026-09-09',execute=True)
    with pytest.raises(ValueError,match='backwards'):
        lab.daily_review('2026-09-08')


def test_daily_review_resume_keeps_recorded_runs_and_skips_successful_code(tmp_path, monkeypatch):
    from backend import data

    folder, metadata, base, current = _daily_fixture(tmp_path)
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    calls = []
    attempts = {}

    def worker(command, **kwargs):
        path = Path(command[-1])
        payload = json.loads(path.read_text(encoding='utf-8'))
        code = payload['request']['codes'][0]
        calls.append(code)
        attempts[code] = attempts.get(code, 0) + 1
        if code == 'S0049493' or attempts[code] > 1:
            payload.update(state='completed', results=[{'code': code, 'error_code': 0,
                'issues': [], 'observations': [{'date': '2026-09-10',
                'value': 200 + attempts[code], 'missing': False}]}])
        else:
            payload.update(state='failed', results=[{'code': code, 'error_code': -1,
                'issues': ['Wind EDB request failed'], 'observations': []}])
        path.write_text(json.dumps(payload), encoding='utf-8')

    monkeypatch.setattr(data, 'fundamental_review', lambda *args, **kwargs: current)
    monkeypatch.setattr(lab.subprocess, 'run', worker)
    monkeypatch.setattr(lab, 'stage', Mock())
    monkeypatch.setattr(lab, 'snapshot', Mock(return_value={
        'path': str(folder / 'review.parquet'), 'sha256': 'after',
        'roundtrip_verified': True, 'production_enabled': False}))

    with pytest.raises(ValueError, match='unsuccessful'):
        lab.daily_review('2026-09-11', fetch=True)
    state = json.loads((folder / 'update_checkpoint.json').read_text(encoding='utf-8'))
    assert len(state['scope']['runs']) == 1 and len(state['runs']) == 3

    result = lab.daily_review('2026-09-11', fetch=True)
    assert result['state'] == 'completed'
    assert calls == ['S0049493', 'S0114170', 'S0114170']
    state = json.loads((folder / 'update_checkpoint.json').read_text(encoding='utf-8'))
    assert state['scope']['runs'] == [str(base)] and len(state['runs']) == 4


def test_daily_review_empty_append_awaits_release_reuses_same_day_and_recovers_next_day(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from backend import data

    folder, metadata, base, current = _daily_fixture(tmp_path, ('S0033227',))
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    calls = []

    def worker(command, **kwargs):
        path = Path(command[-1])
        payload = json.loads(path.read_text(encoding='utf-8'))
        code = payload['request']['codes'][0]
        calls.append((code, payload['request']['start'], payload['request']['end']))
        if len(calls) == 1:
            payload.update(state='failed', results=[{'code': code, 'error_code': 0,
                'issues': [lab._EMPTY_OBSERVATION_ISSUE], 'observations': []}])
        else:
            payload.update(state='completed', results=[{'code': code, 'error_code': 0,
                'issues': [], 'observations': [{'date': '2026-09-11', 'value': 123,
                'missing': False}]}])
        path.write_text(json.dumps(payload), encoding='utf-8')

    monkeypatch.setattr(data, 'fundamental_review', lambda *args, **kwargs: current)
    monkeypatch.setattr(lab.subprocess, 'run', worker)
    monkeypatch.setattr(lab, 'stage', Mock())
    monkeypatch.setattr(lab, 'snapshot', Mock(return_value={
        'path': str(folder / 'review.parquet'), 'sha256': 'after',
        'roundtrip_verified': True, 'production_enabled': False}))
    pointer = folder / 'accepted_review.json'
    before = pointer.read_bytes()

    first = lab.daily_review('2026-09-11', fetch=True)
    assert first['state'] == 'awaiting_release'
    assert first['no_new_observations'][0]['state'] == 'no_new_observation'
    assert pointer.read_bytes() == before

    second = lab.daily_review('2026-09-11', fetch=True)
    assert second['state'] == 'awaiting_release'
    assert len(calls) == 1
    assert pointer.read_bytes() == before

    state = json.loads((folder / 'update_checkpoint.json').read_text(encoding='utf-8'))
    empty_run = next(Path(path) for path in state['runs'] if Path(path).name.startswith('update_raw_'))
    payload = json.loads(empty_run.read_text(encoding='utf-8'))
    payload['requested_at'] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    empty_run.write_text(json.dumps(payload), encoding='utf-8')

    recovered = lab.daily_review('2026-09-12', fetch=True)
    assert recovered['state'] == 'completed'
    assert calls == [('S0033227', '2026-09-10', '2026-09-11'),
                     ('S0033227', '2026-09-10', '2026-09-12')]
    assert recovered['no_new_observations'] == []
    assert pointer.read_bytes() != before


def test_empty_append_marker_clears_after_later_observation(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    folder, metadata, base, _ = _daily_fixture(tmp_path, ('S0033227',))
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    empty = folder / 'update_raw_empty.json'
    empty.write_text(json.dumps({'requested_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        'request': {'codes': ['S0033227'], 'start': '2026-09-10', 'end': '2026-09-12',
                    'options': '', 'mode': 'append'}, 'state': 'failed',
        'results': [{'code': 'S0033227', 'error_code': 0,
                     'issues': [lab._EMPTY_OBSERVATION_ISSUE], 'observations': []}],
        'production_write': False, 'identity_status': 'unverified'}), encoding='utf-8')
    later = folder / 'update_raw_later.json'
    later.write_text(json.dumps({'requested_at': datetime.now(timezone.utc).isoformat(),
        'request': {'codes': ['S0033227'], 'start': '2026-09-13', 'end': '2026-09-14',
                    'options': '', 'mode': 'append'}, 'state': 'completed',
        'results': [{'code': 'S0033227', 'error_code': 0, 'issues': [],
                     'observations': [{'date': '2026-09-14', 'value': 123,
                                       'missing': False}]}],
        'production_write': False, 'identity_status': 'unverified'}), encoding='utf-8')

    review = lab.prepare_runs([base, empty, later], metadata, '2021-01-01', '2026-09-14')
    assert review['no_new_observations'] == []
    assert any(point['observation_date'] == '2026-09-14'
               for item in review['indicators'] for point in item['points'])


def test_stage_accepts_append_empty_as_nonpublished_evidence(tmp_path, monkeypatch):
    folder, metadata, _, _ = _daily_fixture(tmp_path, ('S0033227',))
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    empty = folder / 'update_raw_empty.json'
    empty.write_text(json.dumps({'request': {'codes': ['S0033227'], 'start': '2026-09-10',
        'end': '2026-09-12', 'options': '', 'mode': 'append'}, 'state': 'completed',
        'results': [{'code': 'S0033227', 'error_code': 0,
                     'issues': [lab._EMPTY_OBSERVATION_ISSUE], 'observations': []}],
        'production_write': False, 'identity_status': 'unverified'}), encoding='utf-8')

    summary = lab.stage(empty, metadata)
    assert summary['raw_observations'] == 0
    assert summary['series_errors'][0]['reason'] == 'no_observations'
    assert summary['production_ready'] is False


def test_daily_review_recovers_registered_running_raw_before_prepare(tmp_path, monkeypatch):
    from backend import data

    folder, metadata, base, current = _daily_fixture(tmp_path, ('S0033227',))
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    running = folder / 'update_raw_running.json'
    running.write_text(json.dumps({'request': {'codes': ['S0033227'], 'start': '2026-09-10',
        'end': '2026-09-11', 'options': '', 'mode': 'append'}, 'state': 'running',
        'results': [], 'production_write': False, 'identity_status': 'unverified'}), encoding='utf-8')
    state_path = folder / 'update_checkpoint.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['scope']['end'] = '2026-09-11'
    state['runs'] = [str(base), str(running)]
    state['state'] = 'failed'
    state_path.write_text(json.dumps(state), encoding='utf-8')
    base_before = base.read_bytes()
    monkeypatch.setattr(data, 'fundamental_review', lambda *args, **kwargs: current)

    lock = folder / 'update.lock'
    lock.write_text('owner', encoding='utf-8')
    running_before = running.read_bytes()
    with pytest.raises(ValueError, match='already locked'):
        lab.daily_review('2026-09-11', execute=True)
    assert running.read_bytes() == running_before
    lock.unlink()

    result = lab.daily_review('2026-09-11', execute=True)
    assert result['state'] == 'awaiting_coverage'
    recovered = json.loads(running.read_text(encoding='utf-8'))
    assert recovered['state'] == 'failed'
    assert recovered['results'] == [{'code': 'S0033227', 'error': 'interrupted before response'}]
    assert base.read_bytes() == base_before


def test_update_review_reuses_complete_running_success_after_finalization(tmp_path, monkeypatch):
    folder, metadata, base, _ = _daily_fixture(tmp_path, ('S0033227',))
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    running = folder / 'update_raw_complete.json'
    running.write_text(json.dumps({'request': {'codes': ['S0033227'], 'start': '2026-09-10',
        'end': '2026-09-11', 'options': '', 'mode': 'append'}, 'state': 'running',
        'results': [{'code': 'S0033227', 'error_code': 0, 'issues': [],
                     'observations': [{'date': '2026-09-10', 'value': 123,
                                       'missing': False}]}],
        'production_write': False, 'identity_status': 'unverified'}), encoding='utf-8')
    state_path = folder / 'update_checkpoint.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['scope']['end'] = '2026-09-11'
    state['runs'] = [str(base), str(running)]
    state['state'] = 'failed'
    state_path.write_text(json.dumps(state), encoding='utf-8')
    monkeypatch.setattr(lab, 'stage', Mock())
    monkeypatch.setattr(lab, 'snapshot', Mock(return_value={
        'path': str(folder / 'review.parquet'), 'sha256': 'after',
        'roundtrip_verified': True, 'production_enabled': False}))

    result = lab.update_review([base, running], metadata, '2021-01-01', '2026-09-11')
    assert result['state'] == 'completed'
    assert json.loads(running.read_text(encoding='utf-8'))['state'] == 'completed'


def test_daily_review_does_not_refetch_complete_running_success(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from backend import data

    folder, metadata, base, current = _daily_fixture(tmp_path, ('S0033227',))
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    running = folder / 'update_raw_complete.json'
    running.write_text(json.dumps({'requested_at': datetime.now(timezone.utc).isoformat(),
        'request': {'codes': ['S0033227'], 'start': '2026-09-10', 'end': '2026-09-11',
                    'options': '', 'mode': 'append'}, 'state': 'running',
        'results': [{'code': 'S0033227', 'error_code': 0, 'issues': [],
                     'observations': [{'date': '2026-09-10', 'value': 123,
                                       'missing': False}]}],
        'production_write': False, 'identity_status': 'unverified'}), encoding='utf-8')
    state_path = folder / 'update_checkpoint.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['scope']['end'] = '2026-09-11'
    state['runs'] = [str(base), str(running)]
    state['state'] = 'failed'
    state_path.write_text(json.dumps(state), encoding='utf-8')
    calls = []

    def worker(command, **kwargs):
        path = Path(command[-1])
        calls.append(path)
        payload = json.loads(path.read_text(encoding='utf-8'))
        code = payload['request']['codes'][0]
        payload.update(state='completed', results=[{'code': code, 'error_code': 0,
            'issues': [], 'observations': [{'date': payload['request']['end'], 'value': 123,
            'missing': False}]}])
        path.write_text(json.dumps(payload), encoding='utf-8')

    monkeypatch.setattr(data, 'fundamental_review', lambda *args, **kwargs: current)
    monkeypatch.setattr(lab.subprocess, 'run', worker)
    monkeypatch.setattr(lab, 'stage', Mock())
    snapshot = Mock(return_value={'path': str(folder / 'accepted.parquet'),
                                  'sha256': hashlib.sha256((folder / 'accepted.parquet').read_bytes()).hexdigest(),
                                  'roundtrip_verified': True, 'production_enabled': False})
    monkeypatch.setattr(lab, 'snapshot', snapshot)

    result = lab.daily_review('2026-09-11', fetch=True)
    assert result['state'] == 'completed'
    assert calls == []
    snapshot.assert_called_once()
    assert json.loads(running.read_text(encoding='utf-8'))['state'] == 'completed'

    next_day = lab.daily_review('2026-09-12', fetch=True)
    assert next_day['state'] == 'completed'
    assert len(calls) == 1
    assert json.loads(calls[0].read_text(encoding='utf-8'))['request']['start'] == '2026-09-12'
    assert snapshot.call_count == 2


def test_unfinished_checkpoint_rejects_missing_recorded_run_or_changed_source(tmp_path, monkeypatch):
    folder, metadata, base, _ = _daily_fixture(tmp_path, ('S0033227',))
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    extra = folder / 'update_raw_recorded.json'
    extra.write_bytes(base.read_bytes())
    state = json.loads((folder / 'update_checkpoint.json').read_text(encoding='utf-8'))
    state['scope']['end'] = '2026-09-11'
    state['runs'] = [str(base), str(extra)]
    state['state'] = 'failed'
    (folder / 'update_checkpoint.json').write_text(json.dumps(state), encoding='utf-8')
    review = {'metadata_sha256': state['scope']['metadata_sha256'], 'source_sha256s': [state['scope']['sources'][0]],
              'pending_requests': [], 'indicators': [], 'rejected': []}
    monkeypatch.setattr(lab, 'prepare_runs', lambda *args, **kwargs: review)
    unrecorded = folder / 'update_raw_unrecorded.json'
    unrecorded.write_bytes(base.read_bytes())
    with pytest.raises(ValueError, match='different scope'):
        lab.update_review([base, extra, unrecorded], metadata, '2021-01-01', '2026-09-11')
    assert not (folder / 'update.lock').exists()

    base.write_bytes(base.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='different scope'):
        lab.update_review([base, extra], metadata, '2021-01-01', '2026-09-11')


def test_saved_disjoint_coverage_keeps_middle_gap(monkeypatch):
    import copy
    def prepared(start, end):
        return {'source_sha256':start, 'indicators':[{'code':'S0049493','points':[],
                'reused_request_interval':[start,end]}], 'rejected':[],
                'pending_requests': ([{'code':'S0049493','start':'2026-01-04','end':'2026-01-10'}]
                    if start == '2026-01-01' else [{'code':'S0049493','start':'2026-01-01','end':'2026-01-07'}])}
    batches = {'a':prepared('2026-01-01','2026-01-03'), 'b':prepared('2026-01-08','2026-01-10')}
    monkeypatch.setattr(lab,'prepare_saved',lambda run,*a:copy.deepcopy(batches[run]))
    result = lab.prepare_runs(['a','b'],None,'2026-01-01','2026-01-10')
    assert [(r['start'],r['end']) for r in result['pending_requests']] == [('2026-01-04','2026-01-07')]


@pytest.mark.parametrize('raw_value,expected', [(2,'historical_value_conflict'), (3,'needs_explicit_approval')])
def test_update_unresolved_observations_keep_pointer(tmp_path, monkeypatch, raw_value, expected):
    monkeypatch.setattr(lab,'ROOT',tmp_path)
    folder = tmp_path/'runtime/fundamental_lab'
    folder.mkdir(parents=True)
    pointer = folder/'accepted_review.json'
    pointer.write_text('existing pointer untouched')
    metadata = folder/'metadata.json'
    metadata.write_text('{}')
    review = {'metadata_sha256':'test','source_sha256s':[], 'pending_requests':[],
              'indicators':[{'code':'S0049493','points':[{'observation_date':'2026-01-01','raw_value':1}]}],
              'rejected':[{'code':'S0049493','date':'2026-01-01' if raw_value==2 else '2025-12-31',
                           'raw_value':raw_value,'reason':'outside_requested_range'}]}
    monkeypatch.setattr(lab,'prepare_runs',lambda *a:review)
    snapshot = Mock()
    monkeypatch.setattr(lab,'snapshot',snapshot)
    result = lab.update_review([],metadata,'2026-01-01','2026-01-10')
    assert result['state'] == 'awaiting_review' and not result['published']
    assert result['unresolved_observations'][0]['review_state'] == expected
    assert pointer.read_text() == 'existing pointer untouched'
    assert not (folder/'update.lock').exists()
    snapshot.assert_not_called()


def test_product_actions_prioritize_im_and_do_not_grant_basis():
    report = {'products_to_verify':[{'product':p} for p in ['RB','IM','SC','RB']],
              'codes':[{'code':'S0114170','claims':[], 'review_status':'available_scope_limited',
                        'terminal_evidence':{'role':'warrant_reference','limits':['release unknown']}}],
              'financial_requirements':[{'id':'FIN-01','products':['IM'],'need':'underlying and carry'}],
              'requirements':[{'id':'GAP-001','product':'RB'}],
              'ceic_candidates':[{'series_id':'529645757','products':['SC'],'metadata_missing':['sample_scope']} ]}
    result = lab.product_actions(report)
    assert [r['product'] for r in result] == ['IM','RB','SC']
    assert result[0]['existing_requirement_ids'] == ['FIN-01']
    assert result[1]['review_series'][0]['id'] == 'wind:S0114170'
    assert result[2]['review_series'][0]['id'] == 'ceic:529645757'
    assert all(not r['basis_eligible'] and not r['replay_eligible'] for r in result)


def test_product_actions_use_current_fields_without_reusing_false_old_product_claims():
    report = {'products_to_verify':[{'product':p} for p in ['CU','SN','M','AU']],
        'codes':[
            {'code':'S0031717','claims':[{'product':'SN'}], 'candidate_terminal_evidence':
                {'classification':'reference_candidate','products':['CU'],'name':'电解铜月均价','limits':['current only']}},
            {'code':'S0031865','claims':[{'product':'M'}], 'candidate_terminal_evidence':
                {'classification':'futures_excluded','products':['M'],'name':'豆粕连续期货'}},
            {'code':'S0031570','claims':[{'product':'AU'}], 'candidate_terminal_evidence':
                {'classification':'background','products':[],'name':'移动电话用户数'}}]}
    rows = {r['product']:r for r in lab.product_actions(report)}
    assert [r['code'] for r in rows['CU']['terminal_metadata_candidates']] == ['S0031717']
    assert all(not r['unverified_candidates'] and not r['review_series'] and not r['basis_eligible'] for r in rows.values())
    assert not any(rows[p]['terminal_metadata_candidates'] for p in ('SN','M','AU'))


def test_ceic_browser_discovery_is_pending_not_imported_coverage():
    report = {'products_to_verify':[{'product':'RB'},{'product':'SC'}], 'codes':[],
        'ceic_browser_review':{'indicators':[
            {'series_id':'531905027','name':'螺纹钢周产量','local_end':None,'requirement_ids':['REQ-RB-SUPPLY']},
            {'series_id':'529645757','name':'原油库存','local_end':'2026-07','requirement_ids':['REQ-SC-INV']}]}}
    rows = {r['product']:r for r in lab.product_actions(report)}
    assert rows['RB']['ceic_pending_import'] == [{'series_id':'531905027','name':'螺纹钢周产量'}]
    assert '待正常导出与隔离验收' in rows['RB']['next_action']
    assert rows['SC']['ceic_pending_import'] == []
    assert all(not r['review_series'] and not r['basis_eligible'] and not r['replay_eligible'] for r in rows.values())


def test_product_actions_imported_warrant_does_not_close_commercial_inventory():
    item = {'series_id':'567838007','products':['SA'],'metadata_missing':['sample_scope']}
    report = {'products_to_verify':[{'product':'SA'}], 'codes':[], 'ceic_candidates':[item],
              'ceic_browser_review':{'indicators':[{**item,'name':'仓单','local_end':None,
                                                  'requirement_ids':['REQ-SA-WARRANT']}]}}
    row = lab.product_actions(report)[0]
    uses = {u['category']:u for u in row['use_coverage']}
    assert row['ceic_pending_import'] == []
    assert uses['exchange_inventory']['series_ids'] == ['ceic:567838007']
    assert uses['commercial_inventory']['series_ids'] == []
    assert all(not u['business_complete'] for u in uses.values())


def test_product_actions_financial_underlying_is_not_carry():
    report = {'products_to_verify':[{'product':'IM'}], 'codes':[],
              'index_close_review':{'indicators':[{'product':'IM','code':'000852.SH','limits':[]}]}}
    uses = lab.product_actions(report)[0]['use_coverage']
    assert [u['category'] for u in uses] == ['financial_underlying','carrying_cost']
    assert uses[0]['series_ids'] == ['wind_wsd:000852.SH']
    assert not uses[1]['series_ids']


@pytest.mark.parametrize('catalog', [[], [{'product':'SA','as_of':'2026-09-14'}]])
def test_delivery_check_separates_current_catalog_and_full_delivery(tmp_path, catalog):
    report = {'products_to_verify':[{'product':'RB'}], 'codes':[],
              'counts':{'review_available_codes':10},
              'ceic_candidate_audit':{'status':'verified_saved_candidate_audit'},
              'index_close_review':{'indicators':[{'product':'IM','code':'000852.SH','limits':[]}]}}
    with patch.object(lab, 'ROOT', tmp_path), patch.object(lab, 'coverage_status', return_value=report), \
         patch('backend.data.DailyStore.products', return_value=catalog), \
         patch('backend.data.DailyStore.market_snapshot', return_value={
             'rows':[], 'start':'2025-08-10','requested_end':'2026-09-14','snapshot_id':'fixture'}):
        result = lab.delivery_check()
    assert result['local_snapshot_check_passed'] == bool(catalog)
    assert result['counts']['historical_only_labels'] == 1
    assert len(result['discovery_tasks']) == (4 if catalog else 0)
    assert not result['full_delivery_ready'] and not result['network_fetch'] and not result['rds_write']
    assert not list(tmp_path.rglob('*'))  # The checker itself only reads.


def test_delivery_check_detects_concurrent_pointer_change(tmp_path):
    pointer = tmp_path/'runtime/ceic_lab/selected_review.json'
    pointer.parent.mkdir(parents=True)
    pointer.write_text('before')
    def changed():
        pointer.write_text('after')
        return {'codes':[], 'counts':{}, 'products_to_verify':[]}
    with patch.object(lab, 'ROOT', tmp_path), patch.object(lab, 'coverage_status', side_effect=changed), \
         patch('backend.data.DailyStore.products', return_value=[]), \
         patch('backend.data.DailyStore.market_snapshot', return_value={
             'rows':[], 'start':'2025-08-10','requested_end':'2026-09-14','snapshot_id':'fixture'}):
        with pytest.raises(ValueError, match='Protected pointers changed'):
            lab.delivery_check()


@pytest.mark.parametrize('stale', [None, 'pointer', 'implementation', 'failed_check'])
def test_handoff_preflight_requires_matching_recovery(tmp_path, stale):
    pointer = tmp_path/'runtime/ceic_lab/selected_review.json'
    pointer.parent.mkdir(parents=True)
    pointer.write_bytes(b'fixture')
    implementation = tmp_path/'tools/ceic_lab.py'
    implementation.parent.mkdir()
    implementation.write_bytes(b'fixture code')
    checks = ('wide_roundtrip','same_version_noop','restore_previous','return_to_current',
        'lock_rejected','corrupt_previous_rejected','failure_keeps_pointer','lock_released',
        'original_sources_unchanged','temporary_copy_removed')
    evidence = {'kind':'ceic_recovery_rehearsal', 'checks':dict.fromkeys(checks, True),
        'selected_pointer_sha256':hashlib.sha256(pointer.read_bytes()).hexdigest(),
        'implementation_sha256':hashlib.sha256(implementation.read_bytes()).hexdigest(),
        'production_write':False,'network_fetch':False,'live_selection_changed':False}
    if stale == 'pointer':
        evidence['selected_pointer_sha256'] = 'old'
    elif stale == 'implementation':
        evidence['implementation_sha256'] = 'old'
    elif stale == 'failed_check':
        evidence['checks']['failure_keeps_pointer'] = False
    (pointer.parent/'recovery_rehearsal.json').write_text(json.dumps(evidence))
    report = {'products_to_verify':[], 'codes':[], 'counts':{'review_available_codes':10},
        'ceic_candidate_audit':{'status':'verified_saved_candidate_audit'},
        'index_close_review':{'indicators':[{'product':'IM','code':'000852.SH','limits':[]}]}}
    with patch.object(lab,'ROOT',tmp_path), patch.object(lab,'coverage_status',return_value=report), \
         patch('backend.data.DailyStore.products',return_value=[{'product':'IM'}]), \
         patch('backend.data.DailyStore.market_snapshot',return_value={
             'rows':[], 'start':'2025-08-10','requested_end':'2026-09-14','snapshot_id':'fixture'}):
        result = lab.delivery_check()
    assert result['local_handoff_preflight_passed'] == (stale is None)
    assert not result['full_delivery_ready']


@pytest.mark.parametrize('error', [ValueError('bad source'), lab.duckdb.IOException('bad source')])
def test_delivery_cli_failure_replaces_old_success_report(tmp_path, error):
    with patch.object(lab, 'ROOT', tmp_path), patch.object(lab, 'delivery_check', side_effect=error):
        with pytest.raises(SystemExit) as exc:
            lab.main(['delivery-check'])
    assert exc.value.code == 2
    result = json.loads((tmp_path/'runtime/fundamental_lab/delivery_status.json').read_bytes())
    assert not result['local_snapshot_check_passed'] and result['error'] == 'bad source'
    assert not result['full_delivery_ready'] and not result['network_fetch']
    assert not result['local_handoff_preflight_passed']


@pytest.mark.parametrize('strict,expected', [(False,0),(True,2)])
def test_delivery_cli_requires_recovery_only_when_requested(tmp_path, strict, expected):
    report = {'checks':{'source':True}, 'counts':{}, 'local_snapshot_check_passed':True,
              'local_handoff_preflight_passed':False}
    with patch.object(lab,'ROOT',tmp_path), patch.object(lab,'delivery_check',return_value=report):
        assert lab.main(['delivery-check']+(['--require-recovery'] if strict else [])) == expected


def test_coverage_current_fields_do_not_promote_values_or_erase_claims(tmp_path):
    folder = tmp_path/'runtime/fundamental_lab'
    folder.mkdir(parents=True)
    baseline = {'production_enabled':False,'counts':{},'warnings':[],
        'codes':[{'code':code,'claims':[{'name':'old'}]} for code in ('S0031717','S0031865','S0031716')]}
    (folder/'coverage_inventory.json').write_text(json.dumps(baseline),encoding='utf-8')
    (folder/'candidate_review.json').write_text('{}',encoding='utf-8')
    current = {'indicators':[
        {'code':'S0031717','terminal_metadata':{'identity_status':'same_code_current_display_confirmed','classification':'reference_candidate','name':'铜','products':['CU']}},
        {'code':'S0031865','terminal_metadata':{'identity_status':'same_code_current_display_confirmed','classification':'futures_excluded','name':'期货','products':['M']}}]}
    browser_path = tmp_path/'runtime/ceic_lab/browser_review_20260914.json'
    browser_path.parent.mkdir()
    browser = {'kind':'ceic_browser_metadata_review','indicators':[],
        'production_write':False,'basis_eligible':False,'replay_eligible':False,'data_imported':False}
    browser_path.write_text(json.dumps(browser),encoding='utf-8')
    with patch.object(lab,'ROOT',tmp_path), patch('backend.data.fundamental_review',return_value={'indicators':[],'excluded':[],'snapshot_sha256':'unchanged'}), patch('backend.data.fundamental_candidates',return_value=current):
        report = lab.coverage_status()
        assert report['ceic_browser_review']['source_sha256'] == hashlib.sha256(browser_path.read_bytes()).hexdigest()
        browser['basis_eligible'] = True
        browser_path.write_text(json.dumps(browser),encoding='utf-8')
        with pytest.raises(ValueError,match='metadata-only'):
            lab.coverage_status()
    assert [r['review_status'] for r in report['codes']] == ['terminal_metadata_only','not_fundamental','identity_evidence_needed']
    assert report['counts']['identity_evidence_needed_codes'] == 1
    assert report['counts']['candidate_terminal_confirmed_codes'] == 2
    assert report['counts']['review_observations'] == 0
    assert all(r['claims']==[{'name':'old'}] and not r['production_ready'] for r in report['codes'])


def test_financial_inventory_is_bounded_and_does_not_confuse_synthetic_futures(tmp_path):
    import duckdb
    source = tmp_path/'futureshistoryprices1d'
    old = source/'year_month=2026-08'
    latest = source/'year_month=2026-09'
    old.mkdir(parents=True)
    latest.mkdir()
    (old/'not_read.parquet').write_bytes(b'older partition intentionally excluded')
    path = latest/'data.parquet'
    with duckdb.connect() as con:
        con.execute("COPY (SELECT 'CFFEX' exchange,'IM8888' symbol,DATE '2026-09-09' trade_date,100.0 AS \"close\" "
                    "UNION ALL SELECT 'SSE','000300.SH',DATE '2026-09-09',200.0) TO ? (FORMAT PARQUET)", [str(path)])
    before=path.read_bytes()
    result=lab.financial_inventory(tmp_path)
    items={r['product']:r for r in result['indicators']}
    assert items['IM']['status']=='not_found_in_sampled_partition'
    assert items['IF']['status']=='identity_and_coverage_review_needed'
    assert not items['IF']['basis_eligible']
    assert result['sample_end']=='2026-09-09'
    assert len(result['source_sha256'])==1 and path.read_bytes()==before
    json.dumps(result)  # Saved report must be JSON serializable.


def index_evidence(tmp_path, monkeypatch, start='2026-09-07', end='2026-09-10'):
    from backend.data import INDEX_SERIES
    monkeypatch.setattr(lab,'ROOT',tmp_path)
    names=dict(INDEX_SERIES.values())
    identity=lab.save_run({'production_write':False,'state':'completed',
        'request':{'api':'wss','fields':'sec_name'},
        'results':[{'code':c,'name':n,'error_code':0,'issues':[]} for c,n in names.items()]})
    run=lab.save_run({'production_write':False,'state':'completed',
        'request':{'api':'wsd','fields':'close','options':'','codes':list(names),'start':start,'end':end},
        'results':[{'code':c,'error_code':0,'issues':[],'observations':[
            {'date':start,'value':100+i,'missing':False},{'date':end,'value':101+i,'missing':False}]} for i,c in enumerate(names)]})
    return run,identity


def test_index_saved_request_gaps_and_no_automatic_fetch(tmp_path,monkeypatch):
    run,identity=index_evidence(tmp_path,monkeypatch)
    monkeypatch.setattr(lab,'probe',Mock(side_effect=AssertionError('network forbidden')))
    report=lab.index_update([run],identity,'2021-09-01','2026-09-11')
    assert len(report['pending_requests'])==8
    assert {(r['start'],r['end']) for r in report['pending_requests']}=={('2021-09-01','2026-09-06'),('2026-09-11','2026-09-11')}
    assert not (run.parent/'index_review.json').exists()


def test_index_publish_replay_and_tamper_fail_closed(tmp_path,monkeypatch):
    from backend.data import index_daily
    run,identity=index_evidence(tmp_path,monkeypatch)
    report=lab.index_update([run],identity,'2026-09-07','2026-09-10')
    pointer=run.parent/'index_review.json'
    before=pointer.read_bytes()
    assert report['state']=='completed'
    assert len(index_daily(run.parent,'IM')['points'])==2
    monkeypatch.setattr(lab,'probe',Mock(side_effect=AssertionError('no repeat fetch')))
    lab.index_update(None,None,None,'2026-09-10',fetch=True)
    assert pointer.read_bytes()==before
    assert not (run.parent/'index_update.lock').exists()
    (run.parent/report['file']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='哈希'):
        index_daily(run.parent)


def test_index_history_changes_and_wrong_metadata_rejected(tmp_path,monkeypatch):
    run,identity=index_evidence(tmp_path,monkeypatch)
    lab.index_review([run],identity,'2026-09-07','2026-09-10',publish=True)
    pointer=run.parent/'index_review.json'; before=pointer.read_bytes()
    modified=json.loads(run.read_bytes()); modified['results'][0]['observations'][0]['value']=999
    changed=lab.save_run(modified)
    with pytest.raises(ValueError,match='conflict'):
        lab.index_review([run,changed],identity,'2026-09-07','2026-09-10',publish=True)
    with pytest.raises(ValueError,match='removed or revised'):
        lab.index_review([changed],identity,'2026-09-07','2026-09-10',publish=True)
    modified['results'][0]['observations'][0]['value']=100
    modified['results'][0]['observations'].append({'date':'2026-09-08','value':102,'missing':False})
    with pytest.raises(ValueError,match='Historical index gap'):
        lab.index_review([lab.save_run(modified)],identity,'2026-09-07','2026-09-10',publish=True)
    wrong=json.loads(identity.read_bytes()); wrong['results'][0]['name']='ETF'
    with pytest.raises(ValueError,match='code/name'):
        lab.index_review([run],lab.save_run(wrong),'2026-09-07','2026-09-10')
    assert pointer.read_bytes()==before


def test_index_append_preserves_old_keys(tmp_path,monkeypatch):
    from backend.data import index_daily
    run,identity=index_evidence(tmp_path,monkeypatch,'2026-09-01','2026-09-02')
    lab.index_update([run],identity,'2026-09-01','2026-09-02')
    old=index_daily(run.parent)['points']
    incoming=json.loads(run.read_bytes())
    incoming['request'].update(start='2026-09-03',end='2026-09-04')
    for r in incoming['results']:
        r['observations']=[{**p,'date':p['date'].replace('-01','-03').replace('-02','-04')} for p in r['observations']]
    new=lab.save_run(incoming)
    report=lab.index_update([new],None,None,'2026-09-04')
    assert report['state']=='completed' and report['observations']==16
    assert all(p in index_daily(run.parent)['points'] for p in old)


def test_index_partial_failure_retries_only_unsaved_code(tmp_path,monkeypatch):
    run,identity=index_evidence(tmp_path,monkeypatch,'2026-09-01','2026-09-02')
    lab.index_update([run],identity,'2026-09-01','2026-09-02')
    original=json.loads(run.read_bytes())
    calls=[]
    def fake_probe(codes,start,end,**kwargs):
        calls.append((codes,start,end))
        result=[]
        for c in codes:
            result.append({'code':c,'error_code':-1,'issues':['failed']} if len(calls)==1 and c==codes[0] else
                {'code':c,'error_code':0,'issues':[],'observations':[{'date':end,'value':105,'missing':False}]})
        payload={**original,'request':{**original['request'],'codes':codes,'start':start,'end':end},'results':result}
        return lab.save_run(payload),int(len(calls)==1)
    monkeypatch.setattr(lab,'probe',fake_probe)
    pointer=run.parent/'index_review.json'; before=pointer.read_bytes()
    first=lab.index_update(None,None,None,'2026-09-03',fetch=True)
    assert first['state']=='pending' and pointer.read_bytes()==before
    assert len(first['pending_requests'])==1
    second=lab.index_update(None,None,None,'2026-09-03',fetch=True)
    assert second['state']=='completed' and calls[1][0]==[calls[0][0][0]]
    assert not (run.parent/'index_update.lock').exists()


def test_index_tail_retries_from_observation_waterline_and_reuses_same_day(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from backend.data import INDEX_SERIES

    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    names = dict(INDEX_SERIES.values())
    identity = lab.save_run({'production_write': False, 'state': 'completed',
        'request': {'api': 'wss', 'fields': 'sec_name'},
        'results': [{'code': code, 'name': name, 'error_code': 0, 'issues': []}
                    for code, name in names.items()]})
    payload = {'requested_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        'production_write': False, 'state': 'completed',
        'request': {'api': 'wsd', 'fields': 'close', 'options': '', 'codes': list(names),
                    'start': '2026-09-07', 'end': '2026-09-08'},
        'results': [{'code': code, 'error_code': 0, 'issues': [],
                     'observations': [{'date': '2026-09-07', 'value': 100 + i,
                                       'missing': False}]}
                    for i, code in enumerate(names)]}
    run = lab.save_run(payload)
    calls = []

    def fake_probe(codes, start, end, **kwargs):
        calls.append((codes, start, end))
        result = {**payload, 'requested_at': datetime.now(timezone.utc).isoformat(),
            'request': {**payload['request'], 'codes': codes, 'start': start, 'end': end},
            'results': [{'code': code, 'error_code': 0, 'issues': [],
                         'observations': [{'date': end, 'value': 110 + i,
                                           'missing': False}]}
                        for i, code in enumerate(codes)]}
        return lab.save_run(result), 1

    monkeypatch.setattr(lab, 'probe', fake_probe)
    report = lab.index_update([run], identity, '2026-09-07', '2026-09-08')
    assert report['state'] == 'awaiting_release'
    report = lab.index_update(None, None, None, '2026-09-09', fetch=True)
    assert calls == [([*names], '2026-09-08', '2026-09-09')]
    assert report['observation_waterlines'][next(iter(names))] == '2026-09-09'

    same_root = tmp_path / 'same_day'
    same_root.mkdir()
    monkeypatch.setattr(lab, 'ROOT', same_root)
    identity = lab.save_run({'production_write': False, 'state': 'completed',
        'request': {'api': 'wss', 'fields': 'sec_name'},
        'results': [{'code': code, 'name': name, 'error_code': 0, 'issues': []}
                    for code, name in names.items()]})
    same_day = {**payload, 'requested_at': datetime.now(timezone.utc).isoformat()}
    same_day_run = lab.save_run(same_day)
    monkeypatch.setattr(lab, 'probe', Mock(side_effect=AssertionError('same-day retry forbidden')))
    report = lab.index_update([same_day_run], identity, '2026-09-07', '2026-09-08', fetch=True)
    assert report['state'] == 'awaiting_release'
    assert report['awaiting_release']


def test_index_same_range_empty_response_is_reused_on_same_day(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from backend.data import INDEX_SERIES

    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    names = dict(INDEX_SERIES.values())
    identity = lab.save_run({'production_write': False, 'state': 'completed',
        'request': {'api': 'wss', 'fields': 'sec_name'},
        'results': [{'code': code, 'name': name, 'error_code': 0, 'issues': []}
                    for code, name in names.items()]})
    yesterday = {'requested_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        'production_write': False, 'state': 'completed',
        'request': {'api': 'wsd', 'fields': 'close', 'options': '', 'codes': list(names),
                    'start': '2026-09-07', 'end': '2026-09-08'},
        'results': [{'code': code, 'error_code': 0, 'issues': [], 'observations': []}
                    for code in names]}
    run = lab.save_run(yesterday)
    calls = []

    def fake_probe(codes, start, end, **kwargs):
        calls.append((codes, start, end))
        today = {**yesterday, 'requested_at': datetime.now(timezone.utc).isoformat(),
            'request': {**yesterday['request'], 'codes': codes, 'start': start, 'end': end},
            'results': [{'code': code, 'error_code': 0, 'issues': [], 'observations': []}
                        for code in codes]}
        return lab.save_run(today), 0

    monkeypatch.setattr(lab, 'probe', fake_probe)
    first = lab.index_update([run], identity, '2026-09-07', '2026-09-08', fetch=True)
    second = lab.index_update(None, None, None, '2026-09-08', fetch=True)
    assert first['state'] == second['state'] == 'awaiting_release'
    assert len(calls) == 1 and calls[0][1:] == ('2026-09-07', '2026-09-08')


def test_index_no_new_observation_keeps_awaiting_and_pointer(tmp_path, monkeypatch):
    import copy
    from datetime import datetime, timezone

    run, identity = index_evidence(tmp_path, monkeypatch, '2026-09-03', '2026-09-04')
    lab.index_update([run], identity, '2026-09-03', '2026-09-04')
    pointer = run.parent / 'index_review.json'
    before = pointer.read_bytes()
    no_new = copy.deepcopy(json.loads(run.read_bytes()))
    no_new['requested_at'] = datetime.now(timezone.utc).isoformat()
    no_new['request'].update(start='2026-09-05', end='2026-09-05')
    for result in no_new['results']:
        result['observations'] = []
    no_new_run = lab.save_run(no_new)
    probe = Mock(side_effect=AssertionError('same-day no-new retry forbidden'))
    monkeypatch.setattr(lab, 'probe', probe)

    report = lab.index_update([no_new_run], None, None, '2026-09-05', fetch=True)
    assert report['state'] == 'awaiting_release'
    assert report['pending_requests'] == []
    assert report['awaiting_release']
    assert pointer.read_bytes() == before
    probe.assert_not_called()


def synchronous_context():
    """Offline process double: exercise the worker with fake Wind, not a real terminal."""
    context = Mock()
    def process(target, args):
        child = Mock(pid=123, exitcode=0)
        child.start.side_effect = lambda: target(*args)
        child.is_alive.return_value = False
        return child
    context.Process.side_effect = process
    return context


def checkpoint_then_hang(path):
    # Real spawned-process supervision test; deliberately never imports Wind.
    lab.ROOT = Path(path).parents[2]
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["results"].append({"code": "S0033227", "observations": [{"date": "2026-09-01", "value": 100}]})
    lab.save_run(payload, path)
    time.sleep(30)


class FundamentalLabTests(unittest.TestCase):
    def test_prepare_merges_saved_ranges_and_quarantines_boundary_without_refetch(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab,'ROOT',Path(folder)):
            run,_=self.stage_fixture(Path(folder),[
                {'date':'2026-09-01','value':100,'missing':False},
                {'date':'2026-09-08','value':101,'missing':False}])
            p=json.loads(run.read_text());p['results'][0]['issues']=['Duplicate or out-of-range date: 2026-09-08']
            run.write_text(json.dumps(p),encoding='utf-8')
            metadata=run.parent/'meta.json'
            metadata.write_text(json.dumps({'production_enabled':False,
                'user_confirmed_no_manual_conversion':True,'field_status':'terminal_default_display_confirmed',
                'indicators':[{'code':'S0033227','name':'n','unit':'吨','frequency':'W',
                'available_start':'2020-01-01','role':'reference','limits':[]}]}),encoding='utf-8')
            second=run.parent/'second.json'
            q=json.loads(run.read_text());q['request'].update(start='2026-08-01',end='2026-08-31')
            q['results'][0].update(issues=[],observations=[{'date':'2026-08-28','value':99,'missing':False}])
            second.write_text(json.dumps(q),encoding='utf-8')
            result=lab.prepare_runs([run,second],metadata,'2026-08-01','2026-09-07')
            self.assertEqual(result['pending_requests'],[])
            self.assertEqual(result['observations'],2)
            self.assertEqual(len(result['rejected']),1)
            q['request'].update(start='2026-09-01',end='2026-09-07')
            q['results'][0]['observations']=[{'date':'2026-09-01','value':999,'missing':False}]
            second.write_text(json.dumps(q),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'conflict'):
                lab.prepare_runs([run,second],metadata,'2026-08-01','2026-09-07')

    def test_prepare_reuses_weekly_request_not_only_observation_dates(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab,'ROOT',Path(folder)):
            run,_=self.stage_fixture(Path(folder))
            metadata=run.parent/'metadata.json'
            item={'code':'S0033227','name':'terminal name','frequency':'W','unit':'吨',
                  'available_start':'2020-01-01','role':'reference','limits':[]}
            metadata.write_text(json.dumps({'production_enabled':False,
                'user_confirmed_no_manual_conversion':True,'field_status':'terminal_default_display_confirmed',
                'indicators':[item]}),encoding='utf-8')
            before=(run.read_bytes(),metadata.read_bytes())
            result=lab.prepare_saved(run,metadata,'2026-08-01','2026-09-10')
            self.assertEqual([(r['start'],r['end']) for r in result['pending_requests']],
                             [('2026-08-01','2026-08-31'),('2026-09-08','2026-09-10')])
            self.assertEqual(result['observations'],1)
            self.assertFalse(result['production_enabled'])
            self.assertEqual(before,(run.read_bytes(),metadata.read_bytes()))
            self.assertFalse((run.parent/'staged.sqlite').exists())
            self.assertEqual(lab.prepare_saved(run,metadata,'2026-09-02','2026-09-04')['pending_requests'],[])
            item['role']='quarantine'
            metadata.write_text(json.dumps({'production_enabled':False,
                'user_confirmed_no_manual_conversion':True,'field_status':'terminal_default_display_confirmed',
                'indicators':[item]}),encoding='utf-8')
            blocked=lab.prepare_saved(run,metadata,'2026-01-01','2026-09-10')
            self.assertEqual((blocked['observations'],len(blocked['excluded']),blocked['pending_requests']),(0,1,[]))

    def test_prepare_failed_series_does_not_claim_reusable_coverage(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab,'ROOT',Path(folder)):
            run,_=self.stage_fixture(Path(folder))
            metadata=run.parent/'metadata.json'
            catalog={'production_enabled':False,'user_confirmed_no_manual_conversion':True,
                'field_status':'terminal_default_display_confirmed','indicators':[
                {'code':'S0033227','name':'n','frequency':'D','unit':'吨',
                 'available_start':'2026-09-03','role':'reference','limits':[]}]}
            metadata.write_text(json.dumps(catalog),encoding='utf-8')
            payload=json.loads(run.read_text());payload['results'][0]['error_code']=-1
            run.write_text(json.dumps(payload),encoding='utf-8')
            result=lab.prepare_saved(run,metadata,'2026-09-01','2026-09-10')
            self.assertEqual(result['observations'],0)
            self.assertEqual(result['pending_requests'][0]['start'],'2026-09-03')
            catalog['production_enabled']=True
            metadata.write_text(json.dumps(catalog),encoding='utf-8')
            with self.assertRaises(ValueError):lab.prepare_saved(run,metadata,'2026-09-01','2026-09-10')

    def stage_fixture(self, root, observations=None, name="run.json"):
        folder = root / "runtime" / "fundamental_lab"
        folder.mkdir(parents=True, exist_ok=True)
        run = folder / name
        observations = observations if observations is not None else [{"date": "2026-09-01", "value": 100, "missing": False}]
        payload = {"state": "failed", "production_write": False,
                   "request": {"codes": ["S0033227"], "start": "2026-09-01", "end": "2026-09-07"},
                   "results": [{"code": "S0033227", "error_code": 0, "issues": [], "observations": observations}]}
        run.write_text(json.dumps(payload), encoding="utf-8")
        catalog = root / "fundamental_candidates.json"
        catalog.write_text(json.dumps({"production_enabled": False,
                                      "indicators": [{"code": "S0033227", "identity_status": "verified",
                                                      "unit": "untrusted claim"}]}), encoding="utf-8")
        return run, catalog

    def test_stage_clean_and_outside_rows_unverified_without_waterline(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            observations = [{"date": "2026-09-01", "value": 100, "missing": False},
                            {"date": "2026-12-31", "value": 111000, "missing": False}]
            run, catalog = self.stage_fixture(Path(folder), observations)
            before = run.read_bytes()
            with patch.object(lab.multiprocessing, "get_context") as context:
                summary = lab.stage(run, catalog)
                context.assert_not_called()
            self.assertEqual((summary["technical_qualified"], summary["quarantined"]), (1, 1))
            self.assertEqual(run.read_bytes(), before)
            with closing(sqlite3.connect(summary["database"])) as db:
                self.assertEqual(db.execute("SELECT identity_status,production_ready FROM staged_observations").fetchone(),
                                 ("unverified", 0))
                self.assertEqual(db.execute("SELECT reason FROM quarantine").fetchone()[0], "outside_requested_range")
                self.assertEqual(db.execute("SELECT identity_status,production_ready FROM quarantine").fetchone(),
                                 ("unverified", 0))
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(tables, {"stage_runs", "staged_observations", "quarantine"})
            self.assertFalse(summary["production_write"])
            self.assertFalse(summary["production_ready"])

    def scoped_fixture(self, root):
        run, catalog = self.stage_fixture(root)
        payload = json.loads(run.read_text())
        payload["request"]["codes"] += ["S5006030", "S5006031"]
        payload["results"] += [
            {"code": "S5006030", "error_code": 0, "observations": [
                {"date": "2026-09-02", "value": 200, "missing": False},
                {"date": "2026-12-31", "value": 300, "missing": False}]},
            {"code": "S5006031", "error_code": -1, "error": "saved error", "observations": []}]
        run.write_text(json.dumps(payload), encoding="utf-8")
        catalog.write_text(json.dumps({"production_enabled": False,
                                      "indicators": [{"code": c} for c in payload["request"]["codes"]]}))
        return run, catalog

    def test_stage_subset_isolation_remaining_scope_and_order_independent_replay(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.scoped_fixture(Path(folder))
            before = run.read_bytes()
            first = lab.stage(run, catalog, ["S0033227"])
            self.assertEqual((first["raw_observations"], first["inserted"], first["quarantined"]), (1, 1, 0))
            self.assertEqual(first["series_errors"], [])
            with closing(sqlite3.connect(first["database"])) as db:
                self.assertEqual(db.execute("SELECT code FROM staged_observations").fetchall(), [("S0033227",)])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0], 0)
            remaining = lab.stage(run, catalog, ["S5006031", "S5006030"])
            replay = lab.stage(run, catalog, ["S5006030", "S5006031"])
            self.assertTrue(replay["already_staged"])
            self.assertEqual(remaining["stage_sha256"], replay["stage_sha256"])
            self.assertNotEqual(first["stage_sha256"], remaining["stage_sha256"])
            self.assertEqual(first["run_sha256"], remaining["run_sha256"])
            self.assertEqual((remaining["inserted"], remaining["quarantined"]), (1, 1))
            self.assertEqual([r["code"] for r in remaining["series_errors"]], ["S5006031"])
            with closing(sqlite3.connect(first["database"])) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT code FROM quarantine").fetchall(), [("S5006030",)])
            complete = lab.stage(run, catalog)
            self.assertEqual(complete["stage_sha256"], complete["run_sha256"])
            self.assertEqual((complete["inserted"], complete["existing_equal"]), (0, 2))
            self.assertTrue(lab.stage(run, catalog, ["S5006031", "S0033227", "S5006030"])["already_staged"])
            self.assertEqual(run.read_bytes(), before)

    def test_stage_subset_validates_scope_but_does_not_process_unselected_values(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.scoped_fixture(Path(folder))
            payload = json.loads(run.read_text())
            payload["results"][1]["observations"] += [
                {"date": "2026-09-02", "value": 999, "missing": False}]
            run.write_text(json.dumps(payload))
            for invalid in ([], ["*"], ["S0049493"]):
                with self.assertRaises(ValueError):
                    lab.stage(run, catalog, invalid)
            self.assertFalse((run.parent / "staged.sqlite").exists())
            # Only selected codes need catalog approval; another code's conflict is isolated.
            catalog.write_text(json.dumps({"production_enabled": False, "indicators": [{"code": "S0033227"}]}))
            with self.assertRaisesRegex(ValueError, "candidate allowlist"):
                lab.stage(run, catalog, ["S5006030"])
            self.assertEqual(lab.stage(run, catalog, ["S0033227"])["inserted"], 1)

    def test_stage_catalog_change_blocks_replay_and_remaining_scope(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.scoped_fixture(Path(folder))
            first = lab.stage(run, catalog, ["S0033227"])
            catalog.write_bytes(catalog.read_bytes() + b"\n")
            for scope in (["S0033227"], ["S5006030"], None):
                with self.assertRaisesRegex(ValueError, "Catalog SHA256 changed"):
                    lab.stage(run, catalog, scope)
            with closing(sqlite3.connect(first["database"])) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0], 1)

    def test_stage_cli_passes_only_scope_without_probe(self):
        with patch.object(lab, "stage", return_value={}) as stage, patch.object(lab, "probe") as probe:
            self.assertEqual(lab.main(["stage", "--run", "saved.json", "--catalog", "catalog.json",
                                       "--only", "S5006030,S0033227"]), 0)
            stage.assert_called_once_with(Path("saved.json"), Path("catalog.json"), ["S5006030", "S0033227"])
            probe.assert_not_called()

    def test_stage_subset_conflict_rolls_back_selected_rows_and_audit(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.scoped_fixture(Path(folder))
            first = lab.stage(run, catalog, ["S0033227"])
            payload = json.loads(run.read_text())
            payload["results"][0]["observations"] = [
                {"date": "2026-09-02", "value": 101, "missing": False},
                {"date": "2026-09-01", "value": 999, "missing": False}]
            new_run = run.with_name("conflicting_scope.json")
            new_run.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "entire new run rolled back"):
                lab.stage(new_run, catalog, ["S0033227", "S5006030"])
            with closing(sqlite3.connect(first["database"])) as db:
                self.assertEqual(db.execute("SELECT code,observation_date,value FROM staged_observations").fetchall(),
                                 [("S0033227", "2026-09-01", 100)])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0], 0)

    def test_stage_legacy_full_batch_replay_and_scoped_migration(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.scoped_fixture(Path(folder))
            first = lab.stage(run, catalog)
            legacy_summary = {k: v for k, v in first.items()
                              if k not in {"stage_sha256", "scope_sha256", "selected_codes"}}
            with closing(sqlite3.connect(first["database"])) as db:
                db.execute("ALTER TABLE stage_runs RENAME TO newer_runs")
                db.execute("""CREATE TABLE stage_runs (
                    run_sha256 TEXT PRIMARY KEY, source_path TEXT UNIQUE NOT NULL, catalog_sha256 TEXT NOT NULL,
                    requested_start TEXT NOT NULL, requested_end TEXT NOT NULL, created_at TEXT NOT NULL,
                    summary_json TEXT NOT NULL)""")
                db.execute("INSERT INTO stage_runs SELECT * FROM newer_runs")
                db.execute("UPDATE stage_runs SET summary_json=?", (json.dumps(legacy_summary),))
                db.execute("DROP TABLE newer_runs")
                db.commit()
            self.assertTrue(lab.stage(run, catalog)["already_staged"])
            scoped = lab.stage(run, catalog, ["S0033227"])
            self.assertEqual((scoped["inserted"], scoped["existing_equal"]), (0, 1))
            run.write_bytes(run.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "changed SHA256"):
                lab.stage(run, catalog, ["S5006030"])

    def test_stage_same_hash_is_idempotent_and_equal_new_run_keeps_original(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.stage_fixture(Path(folder))
            first = lab.stage(run, catalog)
            second = lab.stage(run, catalog)
            self.assertTrue(second["already_staged"])
            self.assertEqual(first["run_sha256"], second["run_sha256"])
            new_run, _ = self.stage_fixture(Path(folder), name="next.json")
            payload = json.loads(new_run.read_text())
            payload["requested_at"] = "different batch, same point"
            new_run.write_text(json.dumps(payload), encoding="utf-8")
            third = lab.stage(new_run, catalog)
            self.assertEqual((third["inserted"], third["existing_equal"]), (0, 1))
            with closing(sqlite3.connect(first["database"])) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM staged_observations").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT first_run_sha256 FROM staged_observations").fetchone()[0], first["run_sha256"])

    def test_stage_conflicting_new_run_rolls_back_all_rows_and_audit(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.stage_fixture(Path(folder))
            first = lab.stage(run, catalog)
            new_run, _ = self.stage_fixture(Path(folder), [
                {"date": "2026-09-02", "value": 101, "missing": False},
                {"date": "2026-09-01", "value": 999, "missing": False},
                {"date": "2026-12-31", "value": 10, "missing": False}], name="conflict.json")
            with self.assertRaisesRegex(ValueError, "Conflicting staged value"):
                lab.stage(new_run, catalog)
            with closing(sqlite3.connect(first["database"])) as db:
                self.assertEqual(db.execute("SELECT observation_date,value FROM staged_observations").fetchall(), [("2026-09-01", 100)])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0], 0)

    def test_stage_duplicate_missing_nonfinite_and_bad_dates_quarantined(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            rows = [{"date": "2026-09-01", "value": 100, "missing": False}] * 2 + [
                {"date": "2026-09-02", "value": float("inf"), "missing": False},
                {"date": "not-a-date", "value": 1, "missing": False},
                {"date": "2026-09-03", "value": None, "missing": True}]
            run, catalog = self.stage_fixture(Path(folder), rows)
            summary = lab.stage(run, catalog)
            self.assertEqual((summary["technical_qualified"], summary["quarantined"]), (0, 5))

    def test_stage_conflict_within_run_rejected_before_database(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.stage_fixture(Path(folder), [
                {"date": "2026-09-01", "value": 100, "missing": False},
                {"date": "2026-09-01", "value": 999, "missing": False}])
            with self.assertRaisesRegex(ValueError, "Conflicting values within run"):
                lab.stage(run, catalog)
            self.assertFalse((run.parent / "staged.sqlite").exists())

    def test_stage_modified_source_hash_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.stage_fixture(Path(folder))
            summary = lab.stage(run, catalog)
            run.write_bytes(run.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "changed SHA256"):
                lab.stage(run, catalog)
            with closing(sqlite3.connect(summary["database"])) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0], 1)

    def test_stage_scope_codes_and_structure_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            run, catalog = self.stage_fixture(Path(folder))
            with self.assertRaisesRegex(ValueError, "must stay"):
                lab.stage(Path(folder) / "outside_lab.json", catalog)
            for change in ("unlisted", "mismatched", "running"):
                run, catalog = self.stage_fixture(Path(folder))
                payload = json.loads(run.read_text())
                if change == "unlisted":
                    catalog.write_text(json.dumps({"production_enabled": False, "indicators": [{"code": "S5006030"}]}))
                elif change == "mismatched":
                    payload["results"][0]["code"] = "S5006030"
                else:
                    payload["state"] = "running"
                run.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    lab.stage(run, catalog)
            self.assertFalse((run.parent / "staged.sqlite").exists())

    def test_append_uses_each_published_waterline(self):
        rows = lab.plan_requests(["S0033227", "S0031723"], "append", "2026-09-07",
                                 {"S0033227": "2026-09-04", "S0031723": "2026-09-07"})
        self.assertEqual(rows[0]["start"], "2026-09-05")
        self.assertFalse(rows[1]["due"])
        self.assertTrue(all(r["existing_key_policy"] == "keep" for r in rows))

    def test_missing_waterline_requires_explicit_initial(self):
        with self.assertRaises(ValueError):
            lab.plan_requests(["S0033227"], "append", "2026-09-07")

    def test_append_cannot_override_start(self):
        with self.assertRaises(ValueError):
            lab.plan_requests(["S0033227"], "append", "2026-09-07", start="2021-01-01")

    def test_initial_keeps_existing_and_does_not_publish(self):
        row = lab.plan_requests(["S0033227"], "initial", "2026-09-07", start="2021-01-01")[0]
        self.assertEqual(row["existing_key_policy"], "keep")
        self.assertFalse(row["publishes"])

    def test_repair_requires_exact_scope(self):
        for codes, start in (([], "2026-01-01"), (["S0033227"], None), (["*"], "2026-01-01")):
            with self.assertRaises(ValueError):
                lab.plan_requests(codes, "repair", "2026-09-07", start=start)
        row = lab.plan_requests(["S0033227"], "repair", "2026-09-07", start="2026-09-01")[0]
        self.assertEqual(row["existing_key_policy"], "reviewed_update")

    def test_bad_date_interval(self):
        with self.assertRaises(ValueError):
            lab.plan_requests(["S0033227"], "initial", "2026-01-01", start="2026-02-01")

    def test_response_preserves_missing_without_verifying_identity(self):
        data = SimpleNamespace(ErrorCode=0, Codes=["S0033227"],
                               Times=[date(2026, 9, 1), date(2026, 9, 2)], Data=[[100, float("nan")]])
        row = lab.normalize_response(data, "S0033227", "2026-09-01", "2026-09-07")
        self.assertEqual(row["identity_status"], "unverified")
        self.assertEqual(row["observations"][1], {"date": "2026-09-02", "value": None, "missing": True})
        self.assertEqual(row["issues"], [])

    def test_shape_code_dates_and_api_errors(self):
        data = SimpleNamespace(ErrorCode=0, Codes=["S0033227"], Times=[date(2026, 9, 1)], Data=[[1, 2]])
        self.assertTrue(lab.normalize_response(data, "S0033227", "2026-09-01", "2026-09-07")["issues"])
        data.Data = [[1]]
        self.assertTrue(lab.normalize_response(data, "S0031723", "2026-09-01", "2026-09-07")["issues"])
        self.assertTrue(lab.normalize_response(data, "S0033227", "2026-09-02", "2026-09-07")["issues"])
        self.assertEqual(lab.normalize_response(SimpleNamespace(ErrorCode=-1), "S0033227", "2026-09-01", "2026-09-07")["error_code"], -1)

    def test_inventory_and_output_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = root / "registry.json"
            registry.write_text(json.dumps({"indicators": [{"code": "S0033227"}]}), encoding="utf-8")
            before = registry.read_bytes()
            self.assertFalse(lab.inventory(registry)["trusted"])
            with patch.object(lab, "ROOT", root):
                saved = lab.save_run({"production_write": False})
                self.assertEqual(saved.parent, root / "runtime" / "fundamental_lab")
                self.assertEqual(json.loads(saved.read_text())["production_write"], False)
            self.assertEqual(registry.read_bytes(), before)

    def test_probe_fake_wind_keeps_no_fill_and_only_lab_output(self):
        calls = []
        def edb(code, start, end, options):
            calls.append((code, start, end, options))
            return SimpleNamespace(ErrorCode=0, Codes=[code], Times=[date(2026, 9, 1)], Data=[[100]])
        wind = SimpleNamespace(start=lambda **kwargs: SimpleNamespace(ErrorCode=0), edb=edb)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(lab, "ROOT", Path(folder)), patch.object(lab, "WIND_PYTHON", Path(sys.executable)), \
                 patch.object(lab.multiprocessing, "get_context", return_value=synchronous_context()), \
                 patch.dict(sys.modules, {"WindPy": SimpleNamespace(w=wind)}):
                path, status = lab.probe(["S0033227"], "2026-09-01", "2026-09-07")
                payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(status, 0)
            self.assertEqual(calls, [("S0033227", "2026-09-01", "2026-09-07", "")])
            self.assertEqual(payload["identity_status"], "unverified")
            self.assertFalse(payload["production_write"])

    def test_index_probe_uses_close_without_fill_and_rejects_wrong_field(self):
        for field, expected in [('CLOSE', 0), ('OPEN', 1)]:
            wind = SimpleNamespace(start=lambda **kw: SimpleNamespace(ErrorCode=0), wsd=Mock(return_value=SimpleNamespace(
                ErrorCode=0, Codes=['000852.SH'], Fields=[field], Times=[date(2026,9,1)], Data=[[100]])))
            with tempfile.TemporaryDirectory() as folder:
                with patch.object(lab, 'ROOT', Path(folder)), patch.object(lab, 'WIND_PYTHON', Path(sys.executable)), \
                     patch.object(lab.multiprocessing, 'get_context', return_value=synchronous_context()), \
                     patch.dict(sys.modules, {'WindPy':SimpleNamespace(w=wind)}):
                    path, status = lab.probe(['000852.SH'], '2026-09-01', '2026-09-07', api='wsd')
                    payload = json.loads(path.read_text(encoding='utf-8'))
                assert status == expected
                wind.wsd.assert_called_once_with('000852.SH', 'close', '2026-09-01', '2026-09-07', '')
                assert payload['identity_status'] == 'unverified' and not payload['production_write']
        with self.assertRaises(ValueError):
            lab.probe(['IM8888'], '2026-09-01', '2026-09-07', api='wsd')

    def test_probe_connection_failure_still_records_error(self):
        wind = SimpleNamespace(start=lambda **kwargs: SimpleNamespace(ErrorCode=-1))
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(lab, "ROOT", Path(folder)), patch.object(lab, "WIND_PYTHON", Path(sys.executable)), \
                 patch.object(lab.multiprocessing, "get_context", return_value=synchronous_context()), \
                 patch.dict(sys.modules, {"WindPy": SimpleNamespace(w=wind)}):
                path, status = lab.probe(["S0033227"], "2026-09-01", "2026-09-07")
                payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(status, 1)
            self.assertIn("Wind start error", payload["error"])

    def test_empty_and_all_missing_responses_are_not_success(self):
        for values, times in (([], []), ([None], [date(2026, 9, 1)])):
            response = SimpleNamespace(ErrorCode=0, Codes=["S0033227"], Times=times, Data=[values])
            result = lab.normalize_response(response, "S0033227", "2026-09-01", "2026-09-07")
            self.assertIn("No non-missing observations returned", result["issues"])

    def test_partial_failure_checkpoints_same_batch_and_returns_nonzero(self):
        snapshots = []
        original = lab.save_run
        def save(payload, path=None):
            saved = original(payload, path)
            snapshots.append((saved, len(json.loads(saved.read_text())["results"])))
            return saved
        def edb(code, *args):
            if code == "S0031723":
                raise RuntimeError("offline failure")
            return SimpleNamespace(ErrorCode=0, Codes=[code], Times=[date(2026, 9, 1)], Data=[[100]])
        wind = SimpleNamespace(start=lambda **kwargs: SimpleNamespace(ErrorCode=0), edb=edb)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(lab, "ROOT", Path(folder)), patch.object(lab, "WIND_PYTHON", Path(sys.executable)), \
                 patch.object(lab, "save_run", side_effect=save), \
                 patch.object(lab.multiprocessing, "get_context", return_value=synchronous_context()), \
                 patch.dict(sys.modules, {"WindPy": SimpleNamespace(w=wind)}):
                path, status = lab.probe(["S0033227", "S0031723"], "2026-09-01", "2026-09-07")
            payload = json.loads(path.read_text())
            self.assertEqual(status, 1)
            self.assertEqual(payload["results"][0]["observations"][0]["value"], 100)
            self.assertEqual(payload["results"][1]["error"], "offline failure")
            self.assertEqual([count for _, count in snapshots], [0, 1, 2, 2])
            self.assertEqual({saved for saved, _ in snapshots}, {path})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_timeout_terminates_only_child_and_preserves_checkpoint(self):
        real_context = multiprocessing.get_context("spawn")
        context = Mock()
        children = []
        def process(target, args):
            child = real_context.Process(target=checkpoint_then_hang, args=args)
            children.append(child)
            return child
        context.Process.side_effect = process
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(lab, "ROOT", Path(folder)), patch.object(lab, "WIND_PYTHON", Path(sys.executable)), \
                 patch.object(lab.multiprocessing, "get_context", return_value=context):
                path, status = lab.probe(["S0033227", "S0031723"], "2026-09-01", "2026-09-07", timeout=2)
            payload = json.loads(path.read_text())
            self.assertEqual(status, 1)
            self.assertIn("timed out", payload["error"])
            self.assertEqual(len(payload["results"]), 1)
            self.assertFalse(children[0].is_alive())
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_invalid_timeout_rejected_before_process_or_wind(self):
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError), patch.object(lab.multiprocessing, "get_context") as context:
                lab.probe(["S0033227"], "2026-09-01", "2026-09-07", timeout=timeout)
            context.assert_not_called()

    def test_process_start_failure_keeps_initial_evidence(self):
        context = Mock()
        child = context.Process.return_value
        child.pid = None
        child.start.side_effect = OSError("offline spawn failure")
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(lab, "ROOT", Path(folder)), patch.object(lab, "WIND_PYTHON", Path(sys.executable)), \
                 patch.object(lab.multiprocessing, "get_context", return_value=context):
                path, status = lab.probe(["S0033227"], "2026-09-01", "2026-09-07")
            self.assertEqual(status, 1)
            self.assertIn("offline spawn failure", json.loads(path.read_text())["error"])
            child.terminate.assert_not_called()

    def test_unexpected_child_exit_is_not_success(self):
        context = Mock()
        child = context.Process.return_value
        child.pid, child.exitcode = 123, 5
        child.is_alive.return_value = False
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(lab, "ROOT", Path(folder)), patch.object(lab, "WIND_PYTHON", Path(sys.executable)), \
                 patch.object(lab.multiprocessing, "get_context", return_value=context):
                path, status = lab.probe(["S0033227"], "2026-09-01", "2026-09-07")
            self.assertEqual(status, 1)
            self.assertIn("exited unexpectedly: 5", json.loads(path.read_text())["error"])

    def test_atomic_checkpoint_rejects_outside_target(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, "ROOT", Path(folder)):
            with self.assertRaises(ValueError):
                lab.save_run({}, Path(folder) / "production.json")

    def test_empty_response_batch_returns_failure(self):
        wind = SimpleNamespace(start=lambda **kwargs: SimpleNamespace(ErrorCode=0),
                               edb=lambda code, *args: SimpleNamespace(ErrorCode=0, Codes=[code], Times=[], Data=[[]]))
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(lab, "ROOT", Path(folder)), patch.object(lab, "WIND_PYTHON", Path(sys.executable)), \
                 patch.object(lab.multiprocessing, "get_context", return_value=synchronous_context()), \
                 patch.dict(sys.modules, {"WindPy": SimpleNamespace(w=wind)}):
                path, status = lab.probe(["S0033227"], "2026-09-01", "2026-09-07")
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(path.read_text())["state"], "failed")


class SnapshotTests(unittest.TestCase):
    def test_update_fetch_timeout_keeps_checkpoint_and_old_pointer(self):
        review={'metadata_sha256':'meta','source_sha256s':['raw'],
                'pending_requests':[{'code':'S0049493','start':'2026-09-09','end':'2026-09-09'}],
                'rejected':[]}
        with tempfile.TemporaryDirectory() as folder, patch.object(lab,'ROOT',Path(folder)), \
             patch.object(lab,'prepare_runs',return_value=review), \
             patch.object(lab.subprocess,'run',side_effect=lab.subprocess.TimeoutExpired('test',50)):
            root=Path(folder)/'runtime/fundamental_lab'
            root.mkdir(parents=True)
            pointer=root/'accepted_review.json'
            pointer.write_text('{"old":true}')
            with self.assertRaises(ValueError):
                lab.update_review([root/'raw.json'],root/'meta.json','2021-01-01','2026-09-09',True)
            state=json.loads((root/'update_checkpoint.json').read_text())
            self.assertEqual(state['state'],'failed')
            self.assertEqual(len(state['runs']),2)
            self.assertTrue(Path(state['runs'][-1]).exists())
            self.assertEqual(pointer.read_text(),'{"old":true}')
            self.assertFalse((root/'update.lock').exists())
            # A no-fetch recovery preserves completed evidence and marks interrupted response.
            result=lab.update_review([root/'raw.json'],root/'meta.json','2021-01-01','2026-09-09')
            self.assertEqual(result['state'],'awaiting_coverage')
            failed=json.loads(Path(state['runs'][-1]).read_text())
            self.assertEqual(failed['state'],'failed')
            self.assertEqual(failed['results'][0]['code'],'S0049493')
            self.assertEqual(pointer.read_text(),'{"old":true}')

    def test_update_workflow_publishes_only_verified_snapshot_and_replays(self):
        review={'metadata_sha256':'meta','source_sha256s':['raw'],
                'indicators':[{'code':'S0049493','points':[{'observation_date':'2026-01-01','raw_value':1}]}],
                'pending_requests':[],'rejected':[{'code':'S0049493','date':'2026-01-01','raw_value':1,
                                                  'reason':'outside_requested_range'}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(lab,'ROOT',Path(folder)), \
             patch.object(lab,'prepare_runs',return_value=review), patch.object(lab,'stage') as stage, \
             patch.object(lab.subprocess,'run') as fetch:
            root=Path(folder)/'runtime/fundamental_lab'
            root.mkdir(parents=True)
            raw=root/'raw.json'
            raw.write_text('{}')
            accepted={'path':str(root/'review_test.parquet'),'sha256':'digest',
                      'production_enabled':False,'roundtrip_verified':True}
            with patch.object(lab,'snapshot',return_value=accepted):
                self.assertEqual(lab.update_review([raw],root/'meta.json','2021-01-01','2026-09-08')['state'],'completed')
            pointer=(root/'accepted_review.json').read_bytes()
            with patch.object(lab,'snapshot',side_effect=ValueError('corrupt snapshot')):
                with self.assertRaises(ValueError):
                    lab.update_review([raw],root/'meta.json','2021-01-01','2026-09-08')
            self.assertEqual((root/'accepted_review.json').read_bytes(),pointer)
            self.assertFalse((root/'update.lock').exists())
            fetch.assert_not_called()

    def test_update_missing_coverage_does_not_publish_or_fetch_by_default(self):
        review={'metadata_sha256':'meta','source_sha256s':['raw'],
                'pending_requests':[{'code':'S0049493','start':'2026-09-08','end':'2026-09-08'}], 'rejected':[]}
        with tempfile.TemporaryDirectory() as folder, patch.object(lab,'ROOT',Path(folder)), \
             patch.object(lab,'prepare_runs',return_value=review), patch.object(lab,'snapshot') as publish, \
             patch.object(lab.subprocess,'run') as fetch:
            root=Path(folder)/'runtime/fundamental_lab'
            result=lab.update_review([root/'raw.json'],root/'meta.json','2021-01-01','2026-09-08')
            self.assertEqual(result['state'],'awaiting_coverage')
            with self.assertRaises(ValueError):
                lab.update_review([root/'raw.json'],root/'meta.json','2021-01-01','2026-09-09')
            publish.assert_not_called()
            fetch.assert_not_called()

    def test_recovery_plan_keeps_request_coverage_separate_from_observation_waterline(self):
        pending = {'code':'S0049493','start':'2026-09-08','end':'2026-09-09','executes':False}
        item = {'code':'S0049493','name':'test','unit':'吨','frequency':'W',
            'points':[{'observation_date':'2026-09-04','raw_value':1}],
            'reused_request_intervals':[['2021-01-01','2026-09-07']]}
        review = {'source_sha256s':['raw'],'metadata_sha256':'meta',
            'indicators':[item], 'pending_requests':[pending], 'excluded':[{'code':'S5012468'}], 'rejected':[]}
        with tempfile.TemporaryDirectory() as folder, patch.object(lab,'ROOT',Path(folder)), \
             patch.object(lab,'prepare_runs',return_value=review), patch.object(lab,'probe') as fetch:
            first = lab.review_plan([],None,'2021-01-01','2026-09-09')
            report = json.loads(Path(first['path']).read_text(encoding='utf-8'))
            self.assertEqual(report['pending_requests'],[pending])
            self.assertEqual(report['indicators'][0]['last_observation'],'2026-09-04')
            self.assertEqual(report['indicators'][0]['state'],'missing_request_coverage')
            self.assertFalse(report['production_enabled'])
            self.assertEqual(first,lab.review_plan([],None,'2021-01-01','2026-09-09'))
            review['pending_requests']=[]
            lab.review_plan([],None,'2021-01-01','2026-09-07')
            report=json.loads(Path(first['path']).read_text(encoding='utf-8'))
            self.assertEqual(report['indicators'][0]['state'],'saved_request_covered')
            item['points']=[]
            lab.review_plan([],None,'2021-01-01','2026-09-07')
            report=json.loads(Path(first['path']).read_text(encoding='utf-8'))
            self.assertEqual(report['indicators'][0]['state'],'covered_without_observations')
            fetch.assert_not_called()

    def test_coverage_overlay_preserves_claims_and_does_not_approve(self):
        baseline = {'production_enabled':False,'counts':{'verified_codes':0}, 'warnings':[],
            'codes':[{'code':code,'claims':[{'name':'old claim'}]} for code in ('S0049493','S5012468','S0033227')]}
        review = {'indicators':[{'code':'S0049493','frequency':'D','observations':2}],
            'excluded':[{'code':'S5012468','role':'quarantine'}], 'snapshot_sha256':'hash'}
        with tempfile.TemporaryDirectory() as folder, patch.object(lab,'ROOT',Path(folder)), \
             patch('backend.data.fundamental_review',return_value=review):
            path=Path(folder)/'runtime/fundamental_lab/coverage_inventory.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(baseline),encoding='utf-8')
            before=path.read_bytes()
            result=lab.coverage_status()
            self.assertEqual([i['review_status'] for i in result['codes']],
                ['available_scope_limited','quarantined','identity_evidence_needed'])
            self.assertTrue(all(i['production_ready'] is False for i in result['codes']))
            self.assertEqual(result['codes'][0]['claims'],baseline['codes'][0]['claims'])
            self.assertEqual(result['counts']['review_observations'],2)
            self.assertEqual(result['counts']['verified_codes'],0)
            self.assertEqual(path.read_bytes(),before)
            review['indicators'].append({'code':'S9999999','observations':1})
            with self.assertRaises(ValueError):
                lab.coverage_status()

    def test_coverage_ceic_uses_current_hash_pair_and_verified_parquet_only(self):
        baseline = {'production_enabled': False,
            'counts': {'verified_codes': 0, 'unique_codes': 1}, 'warnings': [],
            'requirements': [{'id': 'GAP-001', 'status': 'unverified_requirement'}],
            'codes': [{'code': 'S0049493', 'references': ['OLD-001'], 'claims': []}]}
        review = {'indicators': [], 'excluded': [], 'snapshot_sha256': 'wind-snapshot'}
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, 'ROOT', Path(folder)), \
             patch('backend.data.fundamental_review', return_value=review):
            root = Path(folder) / 'runtime'
            fundamental = root / 'fundamental_lab'
            fundamental.mkdir(parents=True)
            inventory = fundamental / 'coverage_inventory.json'
            inventory.write_text(json.dumps(baseline), encoding='utf-8')

            ceic = root / 'ceic_lab'
            web = ceic / 'web_review'
            web.mkdir(parents=True)
            source = web / 'candidates.jsonl'
            source.write_text('{"series_id":"529645757"}\n', encoding='utf-8')
            source_audit = web / 'audit.json'
            source_audit.write_text(json.dumps({
                'verification_scope': 'saved_XLSX_to_JSONL_inventory',
                'inventory': [
                    {'series_id': '529645757', 'name': '原油:港口库存', '频率': '月',
                     '单位': '万吨', '数据来源': 'test', 'source_file': 'saved.xlsx'},
                    {'series_id': 'unknown-duplicate', 'name': 'not a candidate'},
                ]}), encoding='utf-8')
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            source_audit_hash = hashlib.sha256(source_audit.read_bytes()).hexdigest()

            matched = ceic / ('wide_' + hashlib.sha256(
                (source_hash + source_audit_hash).encode()).hexdigest()[:16])
            matched.mkdir()
            parquet = matched / 'candidates_M.parquet'
            parquet.write_bytes(b'isolated parquet fixture')
            parquet_hash = hashlib.sha256(parquet.read_bytes()).hexdigest()
            (matched / 'audit.json').write_text(json.dumps({
                'source_jsonl_sha256': source_hash,
                'source_audit_sha256': source_audit_hash,
                'accepted': False, 'production_write': False, 'network_fetch': False,
                'verification_scope': 'saved_JSONL_to_Parquet_only',
                'tables': [{'frequency': 'M', 'path': str(parquet), 'sha256': parquet_hash,
                            'difference_rows': 0, 'series_count': 1,
                            'indicators': [{'series_id': '529645757', 'name': '原油:港口库存',
                                            'unit': '万吨', 'source': 'test', 'observations': 1,
                                            'first': '2026-07-01', 'last': '2026-07-01'}]}]}),
                encoding='utf-8')

            # A newer directory with a different source pair must not be selected by mtime.
            newest = ceic / 'wide_newest'
            newest.mkdir()
            (newest / 'audit.json').write_text(json.dumps({
                'source_jsonl_sha256': 'not-current', 'source_audit_sha256': source_audit_hash,
                'accepted': False, 'production_write': False, 'network_fetch': False,
                'verification_scope': 'arbitrary-newest', 'tables': []}), encoding='utf-8')
            before = (inventory.read_bytes(), source.read_bytes(), source_audit.read_bytes())

            result = lab.coverage_status()
            candidates = result['ceic_candidates']
            self.assertEqual([item['series_id'] for item in candidates], ['529645757'])
            self.assertEqual(candidates[0]['parquet_sha256'], parquet_hash)
            self.assertTrue(candidates[0]['parquet_sha256_verified'])
            self.assertEqual(candidates[0]['requirement_ids'], ['REQ-SC-INV'])
            self.assertEqual(candidates[0]['products'], ['SC'])
            self.assertEqual(candidates[0]['usage_status'],
                             {'review': True, 'reference': False, 'basis': False, 'replay': False})
            self.assertIn('sample_scope', candidates[0]['metadata_missing'])
            self.assertEqual(result['ceic_candidate_audit']['verification_scope'],
                             'saved_JSONL_to_Parquet_only')
            self.assertEqual(result['ceic_candidate_audit']['source_audit_verification_scope'],
                             'saved_XLSX_to_JSONL_inventory')
            self.assertEqual(result['ceic_usage_status']['review'], True)
            self.assertFalse(result['ceic_usage_status']['basis'])
            self.assertEqual(result['counts']['unique_codes'], baseline['counts']['unique_codes'])
            self.assertEqual(result['requirements'], baseline['requirements'])
            self.assertEqual((inventory.read_bytes(), source.read_bytes(), source_audit.read_bytes()), before)
            self.assertFalse((ceic / 'accepted_review.json').exists())

    def test_coverage_ceic_rejects_stale_or_tampered_candidate_audit(self):
        baseline = {'production_enabled': False, 'counts': {'verified_codes': 0}, 'warnings': [],
            'codes': [{'code': 'S0049493', 'claims': []}]}
        review = {'indicators': [], 'excluded': [], 'snapshot_sha256': 'wind-snapshot'}
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, 'ROOT', Path(folder)), \
             patch('backend.data.fundamental_review', return_value=review):
            fundamental = Path(folder) / 'runtime/fundamental_lab'
            fundamental.mkdir(parents=True)
            (fundamental / 'coverage_inventory.json').write_text(json.dumps(baseline), encoding='utf-8')
            ceic = Path(folder) / 'runtime/ceic_lab'
            web = ceic / 'web_review'
            web.mkdir(parents=True)
            source = web / 'candidates.jsonl'
            source.write_text('{"series_id":"1"}\n', encoding='utf-8')
            source_audit = web / 'audit.json'
            source_audit.write_text(json.dumps({'inventory': []}), encoding='utf-8')
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            source_audit_hash = hashlib.sha256(source_audit.read_bytes()).hexdigest()
            wide = ceic / ('wide_' + hashlib.sha256(
                (source_hash + source_audit_hash).encode()).hexdigest()[:16])
            wide.mkdir()
            parquet = wide / 'candidates_M.parquet'
            parquet.write_bytes(b'changed')
            audit = {'source_jsonl_sha256': source_hash, 'source_audit_sha256': source_audit_hash,
                     'accepted': False, 'production_write': False, 'network_fetch': False,
                     'verification_scope': 'saved_JSONL_to_Parquet_only',
                     'tables': [{'frequency': 'M', 'path': str(parquet), 'sha256': 'wrong',
                                 'difference_rows': 0, 'indicators': [{'series_id': '1'}]}]}
            (wide / 'audit.json').write_text(json.dumps(audit), encoding='utf-8')
            result = lab.coverage_status()
            self.assertEqual(result['ceic_candidates'], [])
            self.assertEqual(result['ceic_candidate_audit']['status'], 'unavailable')
            self.assertFalse(result['ceic_usage_status']['review'])
            self.assertFalse(result['ceic_candidate_audit']['production_write'])

    def test_snapshot_roundtrip_reuse_and_corruption_rejection(self):
        review = {'metadata_sha256':'meta', 'source_sha256s':['raw'], 'pending_requests':[],
            'indicators':[{'code':'S0049493', 'name':'库存期货:阴极铜', 'unit':'吨',
                'frequency':'D', 'identity_status':'terminal_fields_confirmed_scope_limited',
                'points':[{'observation_date':'2026-09-07', 'raw_value':123.5}]}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(lab, 'ROOT', Path(folder)), \
             patch.object(lab, 'prepare_runs', return_value=review):
            first = lab.snapshot([], None, '2026-09-01', '2026-09-07')
            path = Path(first['path'])
            before = path.stat().st_mtime_ns
            review['no_new_observations'] = []
            self.assertEqual(first, lab.snapshot([], None, '2026-09-01', '2026-09-07'))
            self.assertEqual(before, path.stat().st_mtime_ns)
            self.assertTrue(first['roundtrip_verified'])
            self.assertFalse(first['production_enabled'])
            self.assertEqual(list(path.parent.glob('*.tmp')), [])
            review['no_new_observations'] = [{'code': 'S0049493', 'state': 'no_new_observation'}]
            with self.assertRaisesRegex(ValueError, 'await release'):
                lab.snapshot([], None, '2026-09-01', '2026-09-07')
            review['no_new_observations'] = []
            import duckdb
            with duckdb.connect() as conn:
                conn.execute("COPY (SELECT 'bad' AS code) TO '" + str(path).replace("'", "''") + "' (FORMAT PARQUET)")
            with self.assertRaises(Exception):
                lab.snapshot([], None, '2026-09-01', '2026-09-07')


if __name__ == "__main__":
    unittest.main()
