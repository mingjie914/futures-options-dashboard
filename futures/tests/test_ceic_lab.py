import json
import ast
import os
import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path

import duckdb
import pytest

from tools.ceic_lab import audit_dataset
from tools import ceic_lab


def candidate_fixture(tmp_path, monkeypatch, conflict=False):
    monkeypatch.setattr(ceic_lab, 'OUTPUT', tmp_path / 'out')
    source = tmp_path / 'saved'
    source.mkdir()
    rows = [{'series_id': '123', 'period_date': '2026-01-01', 'value': 0, 'frequency': 'M'},
            {'series_id': '123', 'period_date': '2026-02-01', 'value': 2.5, 'frequency': 'M'},
            {'series_id': '456', 'period_date': '2026-01-02', 'value': 3, 'frequency': 'D'}]
    if conflict:
        rows.append({**rows[0], 'value': 99})
    (source / 'candidates.jsonl').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    audit = {'errors': [], 'conflicting_keys': 0, 'observations_with_file_provenance': len(rows),
             'inventory': [{'series_id': sid, 'name': sid, '单位': '吨', '数据来源': 'test', '频率': frequency}
                           for sid, frequency in [('123', '月'), ('456', '日'), ('789', '年')]]}
    (source / 'audit.json').write_text(json.dumps(audit), encoding='utf-8')
    return source


def test_append_preview_replay_does_not_write(tmp_path, monkeypatch):
    source = candidate_fixture(tmp_path, monkeypatch)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    report = ceic_lab.preview_append(source, source)
    assert report['counts']['unchanged'] == 3
    assert report['status'] == 'ready_for_isolated_append'
    assert not report['production_write'] and not report['accepted']
    assert not ceic_lab.OUTPUT.exists()
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}


def test_recovery_rehearsal_uses_copies_and_preserves_live_files(tmp_path, monkeypatch):
    import shutil
    source = candidate_fixture(tmp_path, monkeypatch)
    for name in ('web_review', 'merge_1234567890abcdef'):
        shutil.copytree(source, ceic_lab.OUTPUT/name)
    ceic_lab.select_review(ceic_lab.OUTPUT/'merge_1234567890abcdef')
    before = {str(p):p.read_bytes() for p in ceic_lab.OUTPUT.rglob('*') if p.is_file()}
    output = ceic_lab.OUTPUT
    report = ceic_lab.rehearse_selection()
    assert all(report['checks'].values())
    assert report['observations'] == 3
    assert not report['live_selection_changed'] and not report['production_write']
    assert output == ceic_lab.OUTPUT
    assert before == {str(p):p.read_bytes() for p in output.rglob('*') if p.is_file()}
    (output/'web_review'/'audit.json').write_bytes(b'{}')
    with pytest.raises(ValueError, match='hash mismatch'):
        ceic_lab.rehearse_selection()
    assert output == ceic_lab.OUTPUT


@pytest.mark.parametrize('date,value,sid,expected', [
    ('2026-03-01', 7, '123', 'append'),
    ('2026-01-01', 7, '123', 'historical_revision'),
    ('2025-12-01', 7, '123', 'historical_missing_key'),
    ('2026-03-01', 7, '789', 'new_series'),
])
def test_append_preview_classification(tmp_path, monkeypatch, date, value, sid, expected):
    source = candidate_fixture(tmp_path, monkeypatch)
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    audit = json.loads((source / 'audit.json').read_text())
    audit['observations_with_file_provenance'] = 1
    if sid == '789':
        audit['inventory'][-1]['频率'] = '月'
    (incoming / 'audit.json').write_text(json.dumps(audit), encoding='utf-8')
    (incoming / 'candidates.jsonl').write_text(json.dumps({
        'series_id': sid, 'period_date': date, 'value': value, 'frequency': 'M'}))
    report = ceic_lab.preview_append(source, incoming)
    assert report['counts'][expected] == 1
    assert report['status'] == ('ready_for_isolated_append' if expected == 'append' else 'blocked')
    assert report['baseline_points'] == 3  # Partial export does not remove omitted data.


def test_append_preview_blocks_unit_change(tmp_path, monkeypatch):
    import shutil
    source = candidate_fixture(tmp_path, monkeypatch)
    incoming = tmp_path / 'incoming'
    shutil.copytree(source, incoming)
    audit = json.loads((incoming / 'audit.json').read_text())
    audit['inventory'][0]['单位'] = '万吨'
    (incoming / 'audit.json').write_text(json.dumps(audit), encoding='utf-8')
    report = ceic_lab.preview_append(source, incoming)
    assert report['status'] == 'blocked'
    assert report['counts']['metadata_change'] == 2


@pytest.mark.parametrize('sid,frequency,expected', [('789','年','metadata_change'),('999','月','new_series'),('999','年','new_series')])
def test_append_blocks_metadata_only_series_changes(tmp_path, monkeypatch, sid, frequency, expected):
    import shutil
    source=candidate_fixture(tmp_path,monkeypatch)
    incoming=tmp_path/'incoming'; shutil.copytree(source,incoming)
    path=incoming/'audit.json'; audit=json.loads(path.read_text())
    audit['inventory']=[i for i in audit['inventory'] if i['series_id']!=sid]
    audit['inventory'].append({'series_id':sid,'name':'changed','频率':frequency,'单位':'吨','数据来源':'test'})
    path.write_text(json.dumps(audit))
    result=ceic_lab.preview_append(source,incoming)
    assert result['status']=='blocked'
    assert {'series_id':sid,'kind':expected} in result['metadata_issues']
    with pytest.raises(ValueError,match='Append blocked'):
        ceic_lab.merge_candidates(source,incoming)
    assert not ceic_lab.OUTPUT.exists()


def test_reviewed_new_series_initial_load(tmp_path, monkeypatch):
    source = candidate_fixture(tmp_path, monkeypatch)
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    row = {'series_id': '999', 'period_date': '2015-01-02', 'value': 7.123456789,
           'frequency': 'D', 'source_file': 'verified.xlsx'}
    audit = {'errors': [], 'conflicting_keys': 0, 'observations_with_file_provenance': 1,
             'inventory': [{'series_id': '999', 'name': 'verified', '单位': '万吨',
                            '数据来源': 'test', '频率': '日'}]}
    (incoming / 'audit.json').write_text(json.dumps(audit), encoding='utf-8')
    (incoming / 'candidates.jsonl').write_text(json.dumps(row), encoding='utf-8')
    assert ceic_lab.preview_append(source, incoming)['status'] == 'blocked'
    for ids in [('123',), ('888',)]:
        with pytest.raises(ValueError, match='Reviewed IDs'):
            ceic_lab.preview_append(source, incoming, reviewed_new_series=ids)
    result = ceic_lab.merge_candidates(source, incoming, reviewed_new_series=['999'])
    assert result['merged'] and result['difference_rows'] == 0
    assert result['preview']['counts']['append'] == 0
    assert result['preview']['reviewed_new_series'] == ['999']
    merged = Path(result['review_root'])
    rows = [json.loads(line) for line in (merged / 'candidates.jsonl').read_text().splitlines()]
    assert len(rows) == 4
    assert next(r for r in rows if r['series_id'] == '999')['value'] == row['value']
    assert len(json.loads((merged / 'audit.json').read_text(encoding='utf-8'))['inventory']) == 4
    assert ceic_lab.merge_candidates(source, incoming, reviewed_new_series=['999']) == result
    assert not ceic_lab.merge_candidates(merged, incoming)['merged']
    # Explicit initial-load approval must not permit a historical revision.
    revision = {'series_id': '123', 'period_date': '2026-01-01', 'value': 99, 'frequency': 'M'}
    audit['inventory'].append(json.loads((source / 'audit.json').read_text())['inventory'][0])
    audit['observations_with_file_provenance'] = 2
    (incoming / 'audit.json').write_text(json.dumps(audit), encoding='utf-8')
    (incoming / 'candidates.jsonl').write_text(json.dumps(row) + '\n' + json.dumps(revision))
    with pytest.raises(ValueError, match='Append blocked'):
        ceic_lab.merge_candidates(source, incoming, reviewed_new_series=['999'])


def test_merge_preserves_all_original_file_provenance(tmp_path, monkeypatch):
    source=candidate_fixture(tmp_path,monkeypatch)
    old=[json.loads(l) for l in (source/'candidates.jsonl').read_text().splitlines()]
    for row in old: row['source_file']='old.xlsx'
    (source/'candidates.jsonl').write_text('\n'.join(json.dumps(r) for r in old))
    incoming=tmp_path/'incoming'; incoming.mkdir()
    rows=[{**old[0],'source_file':'new.xlsx'},
          {**old[1],'period_date':'2026-03-01','source_file':'new.xlsx','source_files':['third.xlsx','new.xlsx']}]
    (incoming/'candidates.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    audit=json.loads((source/'audit.json').read_text());audit['observations_with_file_provenance']=2
    (incoming/'audit.json').write_text(json.dumps(audit))
    result=ceic_lab.merge_candidates(source,incoming)
    merged=Path(result['review_root'])
    points=[json.loads(l) for l in (merged/'candidates.jsonl').read_text().splitlines()]
    by_key={(r['series_id'],r['period_date']):r for r in points}
    assert by_key['123','2026-01-01']['source_files']==['new.xlsx','old.xlsx']
    assert by_key['123','2026-03-01']['source_files']==['new.xlsx','third.xlsx']
    assert by_key['456','2026-01-02']['source_files']==['old.xlsx']
    assert json.loads((merged/'audit.json').read_text(encoding='utf-8'))['observations_missing_file_provenance']==0
    assert result==ceic_lab.merge_candidates(source,incoming)


def test_merge_append_preserves_old_values_and_reuses_output(tmp_path, monkeypatch):
    source = candidate_fixture(tmp_path, monkeypatch)
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    audit = json.loads((source / 'audit.json').read_text())
    audit['observations_with_file_provenance'] = 1
    (incoming / 'audit.json').write_text(json.dumps(audit), encoding='utf-8')
    (incoming / 'candidates.jsonl').write_text(json.dumps({
        'series_id': '123', 'period_date': '2026-03-01', 'value': 7.123456789012345, 'frequency': 'M'}))
    source_before = (source / 'candidates.jsonl').read_bytes()
    report = ceic_lab.merge_candidates(source, incoming)
    assert report['merged'] and report['difference_rows'] == 0
    merged_audit = json.loads((Path(report['review_root']) / 'audit.json').read_text(encoding='utf-8'))
    assert merged_audit['observations_missing_file_provenance'] == 4  # Legacy input: do not invent filenames.
    assert report['preview']['counts']['append'] == 1
    table = report['wide']['tables'][0]['path']
    with duckdb.connect() as con:
        assert con.execute('SELECT s_123 FROM read_parquet(?) ORDER BY period_date', [table]).fetchall() == [
            (0.0,), (2.5,), (7.123456789012345,)]
    assert (source / 'candidates.jsonl').read_bytes() == source_before
    before = {str(p): p.read_bytes() for p in ceic_lab.OUTPUT.rglob('*') if p.is_file()}
    assert ceic_lab.merge_candidates(source, incoming) == report
    assert before == {str(p): p.read_bytes() for p in ceic_lab.OUTPUT.rglob('*') if p.is_file()}
    # The accepted technical union can be the next explicit baseline; no pointer is promoted.
    replay = ceic_lab.merge_candidates(report['review_root'], incoming)
    assert not replay['merged'] and replay['preview']['counts']['append'] == 0
    assert not list(ceic_lab.OUTPUT.glob('.merge_*'))


@pytest.mark.parametrize('files', ['not-a-list', [''], [None]])
def test_merge_rejects_invalid_file_provenance(tmp_path, monkeypatch, files):
    source = candidate_fixture(tmp_path, monkeypatch)
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    audit = json.loads((source / 'audit.json').read_text())
    audit['observations_with_file_provenance'] = 1
    (incoming / 'audit.json').write_text(json.dumps(audit))
    (incoming / 'candidates.jsonl').write_text(json.dumps({
        'series_id': '123', 'period_date': '2026-03-01', 'value': 7,
        'frequency': 'M', 'source_files': files}))
    with pytest.raises(ValueError, match='Invalid candidate file provenance'):
        ceic_lab.merge_candidates(source, incoming)
    assert not list(ceic_lab.OUTPUT.iterdir())


def test_saved_real_candidates_split_merge_roundtrip(tmp_path, monkeypatch):
    saved = Path(__file__).resolve().parents[1] / 'runtime/ceic_lab/web_review'
    if not (saved / 'candidates.jsonl').exists():
        pytest.skip('Optional saved CEIC evidence absent')
    originals = {name: (saved / name).read_bytes() for name in ('candidates.jsonl', 'audit.json')}
    rows = [json.loads(line) for line in originals['candidates.jsonl'].decode('utf-8').splitlines() if line.strip()]
    tails = {}
    for row in rows:
        sid, day = row['series_id'], row['period_date']
        tails[sid] = max(tails.get(sid, day), day)
    batches = [[], []]
    for row in rows:
        batches[int(row['period_date'] == tails[row['series_id']])].append(row)
    # Rehearse a real saved tail append, never importing a production runner.
    roots = [tmp_path / 'baseline', tmp_path / 'incoming']
    for root, batch in zip(roots, batches):
        root.mkdir()
        audit = json.loads(originals['audit.json'])
        audit['observations_with_file_provenance'] = len(batch)
        (root / 'audit.json').write_text(json.dumps(audit), encoding='utf-8')
        (root / 'candidates.jsonl').write_text('\n'.join(json.dumps(row) for row in batch), encoding='utf-8')
    monkeypatch.setattr(ceic_lab, 'OUTPUT', tmp_path / 'out')
    result = ceic_lab.merge_candidates(*roots)
    merged = [json.loads(line) for line in
              (Path(result['review_root']) / 'candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    def values(points):
        # Saved Excel timestamps and merged ISO dates share the existing DATE business key.
        return {(r['series_id'], r['period_date'][:10], r['value'], r['frequency']) for r in points}
    def provenance(points):
        files = {}
        for row in points:
            key = (row['series_id'], row['period_date'][:10])
            files.setdefault(key, set()).update(row.get('source_files', []))
            if row.get('source_file'):
                files[key].add(row['source_file'])
        return files
    assert result['merged'] and result['difference_rows'] == 0
    assert result['preview']['counts']['append'] == len(tails)
    assert values(merged) == values(rows)
    assert provenance(merged) == provenance(rows)
    assert originals == {name: (saved / name).read_bytes() for name in originals}


def test_merge_blocks_revision_before_output(tmp_path, monkeypatch):
    import shutil
    source = candidate_fixture(tmp_path, monkeypatch)
    incoming = tmp_path / 'incoming'
    shutil.copytree(source, incoming)
    path = incoming / 'candidates.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]['value'] = 9
    path.write_text('\n'.join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match='Append blocked'):
        ceic_lab.merge_candidates(source, incoming)
    assert not ceic_lab.OUTPUT.exists()


def test_export_batches_do_not_overwrite_baseline(tmp_path, monkeypatch):
    pd = pytest.importorskip('pandas', reason='Excel export ingestion runs in the Wind Python environment')
    monkeypatch.setattr(ceic_lab, 'OUTPUT', tmp_path / 'out')
    baseline = ceic_lab.OUTPUT / 'web_review'
    baseline.mkdir(parents=True)
    (baseline / 'audit.json').write_text('baseline must remain unchanged')
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'ceic_growth_web_export.py').write_text('# test parser identity')
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    export = inbox / 'test.xlsx'
    export.write_bytes(b'test fixture; Excel reader mocked')
    header = pd.DataFrame([['指标', 'test'], ['数列ID', '123'], ['频率', '月'],
                           ['单位', '吨'], ['数据来源', 'test'], ['区域', '中国'],
                           ['状态', '继续'], ['最后更新时间', pd.Timestamp('2026-02-03')],
                           ['数列备注', '样本说明'], ['函数说明', '']])
    monkeypatch.setattr(pd, 'read_excel', lambda *a, **k: header)
    def parse(*args, **kwargs):
        if kwargs['expected_frequency'] == 'D':
            raise ValueError('未包含任何日频指标')
        return None, pd.DataFrame([{'series_id': '123', 'period_date': pd.Timestamp('2026-01-01'), 'value': 0.0}])
    fake_parser = SimpleNamespace(_series_id=str, parse_web_xlsx=parse)
    monkeypatch.setattr(ceic_lab.importlib.util, 'spec_from_file_location',
                        lambda *a: SimpleNamespace(loader=SimpleNamespace(exec_module=lambda m: None)))
    monkeypatch.setattr(ceic_lab.importlib.util, 'module_from_spec', lambda s: fake_parser)
    report = ceic_lab.audit_exports(project, inbox)
    assert report['export_audit_version'] == 2
    item = report['inventory'][0]
    assert item['区域'] == '中国' and item['状态'] == '继续'
    assert item['最后更新时间'] == '2026-02-03 00:00:00'
    assert item['数列备注'] == '样本说明' and item['函数说明'] == ''
    assert 'release_time_rule' not in item and not item['basis_eligible']
    first = Path(report['review_root'])
    before = {p.name: p.read_bytes() for p in first.iterdir()}
    assert ceic_lab.audit_exports(project, inbox) == report
    assert before == {p.name: p.read_bytes() for p in first.iterdir()}
    assert ceic_lab.build_candidates(first)['tables'][0]['difference_rows'] == 0
    export.write_bytes(b'new export version')
    assert ceic_lab.audit_exports(project, inbox)['review_root'] != str(first)
    assert (baseline / 'audit.json').read_text() == 'baseline must remain unchanged'
    assert before == {p.name: p.read_bytes() for p in first.iterdir()}
    assert not list(ceic_lab.OUTPUT.glob('.export_*'))
    # A damaged saved output must fail, not be silently replaced.
    latest = Path(ceic_lab.audit_exports(project, inbox)['review_root'])
    (latest / 'candidates.jsonl').write_text('damaged')
    with pytest.raises(ValueError, match='Corrupt existing export audit'):
        ceic_lab.audit_exports(project, inbox)
    assert (latest / 'candidates.jsonl').read_text() == 'damaged'


def test_selection_and_rollback_are_verified_local_and_idempotent(tmp_path, monkeypatch):
    from backend.data import ceic_review_catalog
    source = candidate_fixture(tmp_path, monkeypatch)
    ceic_lab.OUTPUT.mkdir()
    baseline = ceic_lab.OUTPUT / 'web_review'
    source.rename(baseline)
    original = ceic_lab.select_review(baseline)
    pointer_path = ceic_lab.OUTPUT / 'selected_review.json'
    before = pointer_path.read_bytes()
    assert ceic_lab.select_review(baseline) == original
    assert pointer_path.read_bytes() == before
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    audit = json.loads((baseline / 'audit.json').read_text())
    audit['observations_with_file_provenance'] = 1
    (incoming / 'audit.json').write_text(json.dumps(audit), encoding='utf-8')
    (incoming / 'candidates.jsonl').write_text(json.dumps({
        'series_id': '123', 'period_date': '2026-03-01', 'value': 7, 'frequency': 'M'}))
    merged = ceic_lab.merge_candidates(baseline, incoming)
    selected = ceic_lab.select_review(merged['review_root'])
    assert selected['previous']['review_root'] == 'web_review'
    assert not selected['accepted'] and not selected['production_write']
    assert ceic_review_catalog(ceic_lab.OUTPUT)[0]['observations'] == 3
    restored = ceic_lab.select_review(restore_previous=True)
    assert restored['review_root'] == 'web_review'
    assert ceic_review_catalog(ceic_lab.OUTPUT)[0]['observations'] == 2
    # Corrupt previous versions cannot be silently restored.
    path = Path(merged['review_root']) / 'candidates.jsonl'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='Previous selection changed'):
        ceic_lab.select_review(restore_previous=True)
    assert json.loads(pointer_path.read_text()) == restored
    assert not (ceic_lab.OUTPUT / 'selection.lock').exists()


def test_selection_rejects_external_paths_and_locked_updates(tmp_path, monkeypatch):
    source = candidate_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='isolated CEIC'):
        ceic_lab.select_review(source)
    lock = ceic_lab.OUTPUT / 'selection.lock'
    lock.write_text('existing owner')
    with pytest.raises(ValueError, match='locked'):
        ceic_lab.select_review(source)
    assert lock.read_text() == 'existing owner'


def test_candidate_wide_roundtrip_and_idempotence(tmp_path, monkeypatch):
    source = candidate_fixture(tmp_path, monkeypatch)
    before = (source / 'candidates.jsonl').read_bytes()
    report = ceic_lab.build_candidates(source)
    assert not report['accepted'] and not report['production_write']
    assert [t['frequency'] for t in report['tables']] == ['M', 'D']
    assert all(t['difference_rows'] == 0 and t['series_count'] == 1 for t in report['tables'])
    with duckdb.connect() as con:
        assert con.execute('SELECT s_123 FROM read_parquet(?) ORDER BY period_date', [report['tables'][0]['path']]).fetchall() == [(0.0,), (2.5,)]
    output = Path(report['tables'][0]['path']).parent
    output_before = {path.name: path.read_bytes() for path in output.iterdir()}
    assert ceic_lab.build_candidates(source) == report
    assert {path.name: path.read_bytes() for path in output.iterdir()} == output_before
    assert (source / 'candidates.jsonl').read_bytes() == before


def test_candidate_rejects_corrupt_existing_output_without_overwrite(tmp_path, monkeypatch):
    source = candidate_fixture(tmp_path, monkeypatch)
    report = ceic_lab.build_candidates(source)
    table_path = Path(report['tables'][0]['path'])
    original = table_path.read_bytes()
    table_path.write_bytes(original + b'corrupt')
    changed = table_path.read_bytes()
    with pytest.raises(ValueError, match='Corrupt existing candidate output'):
        ceic_lab.build_candidates(source)
    assert table_path.read_bytes() == changed


def test_candidate_rejects_corrupt_existing_audit_without_overwrite(tmp_path, monkeypatch):
    source = candidate_fixture(tmp_path, monkeypatch)
    report = ceic_lab.build_candidates(source)
    audit_path = Path(report['tables'][0]['path']).parent / 'audit.json'
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    audit['accepted'] = True
    audit_path.write_text(json.dumps(audit), encoding='utf-8')
    changed = audit_path.read_bytes()
    with pytest.raises(ValueError, match='Corrupt existing candidate audit'):
        ceic_lab.build_candidates(source)
    assert audit_path.read_bytes() == changed


def test_candidate_rejects_conflict_even_if_old_audit_says_zero(tmp_path, monkeypatch):
    source = candidate_fixture(tmp_path, monkeypatch, conflict=True)
    with pytest.raises(ValueError, match='Conflicting candidate'):
        ceic_lab.build_candidates(source)
    assert not (tmp_path / 'out').exists()


def test_candidate_rejects_metadata_frequency_mismatch(tmp_path, monkeypatch):
    source = candidate_fixture(tmp_path, monkeypatch)
    path = source / 'audit.json'
    audit = json.loads(path.read_text())
    audit['inventory'][0]['频率'] = '日'
    path.write_text(json.dumps(audit))
    with pytest.raises(ValueError, match='identity/frequency'):
        ceic_lab.build_candidates(source)


def fixture(tmp_path, duplicate=False):
    source = tmp_path / 'source'
    partition = source / 'year=2026'
    partition.mkdir(parents=True)
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'dataset_name': 'ceicgrowthdaily', 'series': [
        {'series_id': '123', 'column_name': 's_123', 'ordinal': 1}]}))
    with duckdb.connect() as con:
        con.execute('CREATE TABLE t(period_date DATE, s_123 DOUBLE)')
        con.execute("INSERT INTO t VALUES ('2026-01-01', 0), ('2026-01-02', NULL)")
        if duplicate:
            con.execute("INSERT INTO t VALUES ('2026-01-01', 1)")
        con.execute('COPY t TO ? (FORMAT PARQUET)', [str(partition / 'data_0.parquet')])
    return source, registry


def test_replay_is_exact_and_source_unchanged(tmp_path):
    source, registry = fixture(tmp_path)
    result = audit_dataset(source, registry, tmp_path / 'lab')
    assert result['rows'] == 2
    assert result['exact_difference_rows'] == 0
    assert result['indicators'][0]['observations'] == 1
    assert not result['production_write'] and not result['network_fetch']
    assert not result['indicators'][0]['basis_eligible']
    # Replaying saved input is idempotent and never advances production state.
    assert audit_dataset(source, registry, tmp_path / 'lab')['snapshot_sha256'] == result['snapshot_sha256']


def test_reject_duplicate_dates_before_output(tmp_path):
    source, registry = fixture(tmp_path, duplicate=True)
    with pytest.raises(ValueError, match='duplicate'):
        audit_dataset(source, registry, tmp_path / 'lab')
    assert not (tmp_path / 'lab').exists()


def test_reject_source_output_overlap(tmp_path):
    source, registry = fixture(tmp_path)
    with pytest.raises(ValueError, match='isolated'):
        audit_dataset(source, registry, source / 'lab')


UPSTREAM = Path(os.environ.get('WIND_UPDATE_PROJECT', r'E:\阿里云数据库\wind更新'))


@pytest.mark.skipif(not UPSTREAM.exists(), reason='Optional upstream integration checkout absent')
def test_shared_ceic_config_keeps_daily_monthly_separate(monkeypatch, tmp_path):
    # Substitute all external configuration; never read credentials or import runners.
    monkeypatch.setitem(sys.modules, 'update_config', SimpleNamespace(LOCAL_TABLE_ROOT=str(tmp_path)))
    monkeypatch.setenv('CEIC_API_TOKEN', 'test-only')
    monkeypatch.setenv('CEIC_DAILY_WORK_ROOT', str(tmp_path / 'daily'))
    monkeypatch.setenv('CEIC_DAILY_FULL_WINDOW_YEARS', '2')
    path = UPSTREAM / 'ceic_growth_config.py'
    module = ModuleType('test_ceic_config')
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), module.__dict__)
    daily = module.daily_config()
    assert module.DATASET == 'ceicgrowthdata' and module.FREQUENCY == 'M'
    assert daily.DATASET == 'ceicgrowthdaily' and daily.FREQUENCY == 'D'
    assert daily.WORK_ROOT == tmp_path / 'daily'
    assert daily.FULL_WINDOW_YEARS == 2
    assert daily.RDS_TABLE == 'ceicgrowthdaily'
    assert daily.REGISTRY_FILE.name == 'ceic_daily_registry.json'
    assert daily.TRUTH_FILE.name == 'ceicgrowthdaily_long.parquet'
    daily.DATASET = 'changed'
    assert module.daily_config().DATASET == 'ceicgrowthdaily'


@pytest.mark.skipif(not UPSTREAM.exists(), reason='Optional upstream integration checkout absent')
def test_main_has_no_fundamental_or_ceic_execution_path():
    tree = ast.parse((UPSTREAM / 'main.py').read_text(encoding='utf-8'))
    imports = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any('fundamental' in n or 'ceic' in n for n in imports)
    assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'modules' for t in n.targets))
    assert ast.literal_eval(assignment.value.body) == ['minute']


@pytest.mark.skipif(not UPSTREAM.exists(), reason='Optional upstream integration checkout absent')
def test_merged_probe_is_import_safe_and_preserves_variants():
    path = UPSTREAM / 'fundamental' / 'probe_wind_names.py'
    module = ModuleType('test_probe')
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), module.__dict__)
    calls = []
    fake = SimpleNamespace(**{name: (lambda *args, name=name: calls.append((name, args))) for name in ['edb', 'wss', 'wset']})
    for mode, expected in [('basic', 5), ('options', 10)]:
        variants = module.probes(fake, 'S0031711', mode)
        assert len(variants) == expected
        for _, call in variants:
            call()
    assert calls[5] == ('edb', ('S0031711', '20240101', '', 'showIndicatorName=Y'))
    assert module.main(['--mode', 'options']) == 0  # no WindPy import
