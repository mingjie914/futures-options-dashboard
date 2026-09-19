"""Offline CEIC wide-table replay. Never imports production runners or connects to RDS."""
import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import importlib.util
import shutil
import tempfile



OUTPUT = Path(__file__).resolve().parents[1] / 'runtime' / 'ceic_lab'


_CANDIDATE_LABELS = {
    '月': 'M', 'M': 'M', 'Monthly': 'M',
    '日': 'D', '日，每日': 'D', 'D': 'D', 'Daily': 'D',
}


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _atomic_write_json(path, value):
    """Publish one JSON file atomically; callers must not use this on a reused artifact."""
    path = Path(path)
    fd, temporary_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_candidate_inputs(review_root):
    review_root = Path(review_root)
    source = review_root / 'candidates.jsonl'
    audit_path = review_root / 'audit.json'
    try:
        audit = json.loads(audit_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid saved candidate audit') from exc
    if not isinstance(audit, dict) or audit.get('errors') or audit.get('conflicting_keys') != 0:
        raise ValueError('Export audit has errors or conflicts')
    expected_count = audit.get('observations_with_file_provenance')
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise ValueError('Invalid saved observation count')
    identity = {}
    inventory = audit.get('inventory')
    if not isinstance(inventory, list):
        raise ValueError('Invalid candidate inventory')
    for item in inventory:
        if not isinstance(item, dict):
            raise ValueError('Invalid candidate inventory')
        raw_frequency = item.get('频率')
        if not isinstance(raw_frequency, str) or not raw_frequency.strip():
            raise ValueError('Invalid candidate frequency')
        # Compare the whole inventory, including metadata without M/D observations.
        frequency = _CANDIDATE_LABELS.get(raw_frequency, raw_frequency)
        sid = item.get('series_id')
        if not isinstance(sid, str) or not re.fullmatch(r'[0-9]+', sid):
            raise ValueError('Invalid CEIC series ID')
        try:
            metadata = (item['name'], item['单位'], item['数据来源'], frequency)
        except KeyError as exc:
            raise ValueError('Invalid candidate inventory') from exc
        if sid in identity and identity[sid] != metadata:
            raise ValueError('Conflicting series metadata')
        identity[sid] = metadata
    return (source, audit_path, audit, expected_count, identity,
            _sha256_file(source), _sha256_file(audit_path))


def _load_candidate_points(con, source, audit, expected_count, identity):
    con.execute('CREATE TABLE raw AS SELECT CAST(series_id AS VARCHAR) AS series_id, CAST(period_date AS DATE) AS period_date, CAST(value AS DOUBLE) AS "value", frequency FROM read_json_auto(?)', [str(source)])
    if con.execute('SELECT count(*) FROM raw').fetchone()[0] != expected_count:
        raise ValueError('Saved observation count differs from audit')
    if con.execute("SELECT count(*) FROM raw WHERE series_id IS NULL OR period_date IS NULL OR value IS NULL OR NOT isfinite(value) OR frequency IS NULL OR frequency NOT IN ('M','D')").fetchone()[0]:
        raise ValueError('Invalid candidate observations')
    if con.execute('SELECT count(*) FROM (SELECT series_id, period_date FROM raw GROUP BY ALL HAVING count(DISTINCT value)>1 OR count(DISTINCT frequency)>1)').fetchone()[0]:
        raise ValueError('Conflicting candidate keys')
    observed = sorted(con.execute('SELECT DISTINCT series_id, frequency FROM raw').fetchall())
    if any(sid not in identity or identity[sid][3] != frequency for sid, frequency in observed):
        raise ValueError('Candidate identity/frequency mismatch')
    con.execute('CREATE VIEW points AS SELECT DISTINCT * FROM raw')
    return observed


def _roundtrip_candidate_table(con, path, frequency, ids):
    """Load and validate one wide table against the already validated ``points`` view."""
    con.execute('CREATE OR REPLACE TEMP TABLE candidate_output AS SELECT * FROM read_parquet(?)', [str(path)])
    expected_schema = [('period_date', 'DATE')] + [(f's_{sid}', 'DOUBLE') for sid in ids]
    schema = [(row[0], row[1]) for row in con.execute('DESCRIBE candidate_output').fetchall()]
    if schema != expected_schema:
        raise ValueError('Candidate output schema/order differs from source')
    value_columns = ', '.join(f'"s_{sid}"' for sid in ids)
    output_count, output_unique, output_dated = con.execute(
        'SELECT count(*), count(DISTINCT period_date), count(period_date) FROM candidate_output').fetchone()
    expected_count = con.execute(
        'SELECT count(DISTINCT period_date) FROM points WHERE frequency=?', [frequency]).fetchone()[0]
    if output_count != expected_count or output_count != output_unique or output_dated != output_count:
        raise ValueError('Candidate output has empty, duplicate or null dates')
    for column in value_columns.split(', '):
        if con.execute(f'SELECT count(*) FROM candidate_output WHERE {column} IS NOT NULL AND NOT isfinite({column})').fetchone()[0]:
            raise ValueError('Candidate output has nonfinite observations')
    rebuilt = ' UNION ALL '.join(
        f"SELECT '{sid}' AS series_id, period_date, s_{sid} AS \"value\", '{frequency}' AS frequency FROM candidate_output WHERE s_{sid} IS NOT NULL"
        for sid in ids)
    con.execute('CREATE OR REPLACE TEMP TABLE rebuilt AS ' + rebuilt)
    difference = con.execute(
        'SELECT count(*) FROM ((SELECT * FROM points WHERE frequency=? EXCEPT ALL SELECT * FROM rebuilt) '
        'UNION ALL (SELECT * FROM rebuilt EXCEPT ALL SELECT * FROM points WHERE frequency=?))',
        [frequency, frequency]).fetchone()[0]
    if difference:
        raise ValueError('Wide-table roundtrip differs')
    coverage = con.execute(
        'SELECT series_id, count(*), min(period_date), max(period_date) FROM rebuilt GROUP BY series_id ORDER BY series_id').fetchall()
    return coverage


def _table_report(destination, frequency, ids, identity, coverage, content_path=None):
    path = Path(destination) / f'candidates_{frequency}.parquet'
    return {
        'frequency': frequency,
        'path': str(path),
        'series_count': len(ids),
        'sha256': _sha256_file(content_path or path),
        'difference_rows': 0,
        'indicators': [
            {'series_id': sid, 'name': identity[sid][0], 'unit': identity[sid][1], 'source': identity[sid][2],
             'observations': count, 'first': str(first), 'last': str(last), 'basis_eligible': False}
            for sid, count, first, last in coverage]
    }


def _base_candidate_report(source_hash, audit_hash, tables):
    return {
        'source_jsonl_sha256': source_hash,
        'source_audit_sha256': audit_hash,
        'accepted': False,
        'production_write': False,
        'network_fetch': False,
        'verification_scope': 'saved_JSONL_to_Parquet_only',
        'tables': tables,
    }


def _reuse_candidate_output(con, destination, source_hash, audit_hash, identity, observed):
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError('Corrupt existing candidate output directory')
    expected_ids = {frequency: sorted(sid for sid, observed_frequency in observed if observed_frequency == frequency)
                    for frequency in ('M', 'D')}
    expected_names = {'audit.json'} | {f'candidates_{frequency}.parquet' for frequency, ids in expected_ids.items() if ids}
    entries = list(destination.iterdir())
    if (any(path.is_symlink() or not path.is_file() for path in entries)
            or {path.name for path in entries} != expected_names):
        raise ValueError('Corrupt existing candidate output layout')
    audit_path = destination / 'audit.json'
    try:
        existing_report = json.loads(audit_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Corrupt existing candidate audit') from exc
    if not isinstance(existing_report, dict) or existing_report.get('source_jsonl_sha256') != source_hash or existing_report.get('source_audit_sha256') != audit_hash:
        raise ValueError('Existing candidate audit source hash mismatch')
    stored_tables = existing_report.get('tables')
    if not isinstance(stored_tables, list):
        raise ValueError('Corrupt existing candidate audit')
    tables = []
    for frequency in ('M', 'D'):
        ids = expected_ids[frequency]
        if not ids:
            continue
        path = destination / f'candidates_{frequency}.parquet'
        stored = [table for table in stored_tables
                   if isinstance(table, dict) and table.get('frequency') == frequency]
        if len(stored) != 1 or stored[0].get('sha256') != _sha256_file(path):
            raise ValueError(f'Corrupt existing candidate output: {path.name}')
        try:
            coverage = _roundtrip_candidate_table(con, path, frequency, ids)
        except Exception as exc:
            raise ValueError(f'Corrupt existing candidate output: {path.name}') from exc
        tables.append(_table_report(destination, frequency, ids, identity, coverage))
    expected_report = _base_candidate_report(source_hash, audit_hash, tables)
    if existing_report != expected_report:
        raise ValueError('Corrupt existing candidate audit')
    return existing_report


def _assert_candidate_inputs_unchanged(source, audit_path, source_hash, audit_hash):
    if _sha256_file(source) != source_hash or _sha256_file(audit_path) != audit_hash:
        raise ValueError('Saved inputs changed during conversion')


def build_candidates(review_root):
    """Convert saved derivative JSONL to immutable, frequency-separated review tables.

    Proves JSONL/Parquet equality, not XLSX equivalence or business acceptance.
    Existing content-addressed outputs are validated and reused, never overwritten.
    """
    import duckdb
    source, audit_path, audit, expected_count, identity, source_hash, audit_hash = _load_candidate_inputs(review_root)
    destination = OUTPUT / ('wide_' + hashlib.sha256((source_hash + audit_hash).encode()).hexdigest()[:16])
    with duckdb.connect() as con:
        observed = _load_candidate_points(con, source, audit, expected_count, identity)
        if destination.exists() or destination.is_symlink():
            report = _reuse_candidate_output(con, destination, source_hash, audit_hash, identity, observed)
            _assert_candidate_inputs_unchanged(source, audit_path, source_hash, audit_hash)
            return report

        OUTPUT.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f'.{destination.name}.', dir=str(OUTPUT)))
        try:
            tables = []
            for frequency in ('M', 'D'):
                ids = sorted(sid for sid, observed_frequency in observed if observed_frequency == frequency)
                if not ids:
                    continue
                columns = ', '.join(f"max(value) FILTER (WHERE series_id='{sid}') AS s_{sid}" for sid in ids)
                path = temporary / f'candidates_{frequency}.parquet'
                con.execute(
                    f"COPY (SELECT period_date, {columns} FROM points WHERE frequency='{frequency}' GROUP BY period_date ORDER BY period_date) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                    [str(path)])
                _roundtrip_candidate_table(con, path, frequency, ids)
                tables.append(_table_report(destination, frequency, ids, identity,
                                             con.execute('SELECT series_id, count(*), min(period_date), max(period_date) FROM rebuilt GROUP BY series_id ORDER BY series_id').fetchall(),
                                             content_path=path))
            report = _base_candidate_report(source_hash, audit_hash, tables)
            _atomic_write_json(temporary / 'audit.json', report)
            _assert_candidate_inputs_unchanged(source, audit_path, source_hash, audit_hash)
            if destination.exists() or destination.is_symlink():
                raise ValueError('Content-addressed candidate output appeared during build')
            try:
                os.replace(str(temporary), str(destination))
            except FileExistsError as exc:
                raise ValueError('Content-addressed candidate output appeared during build') from exc
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                if temporary.resolve().parent != OUTPUT.resolve():
                    raise ValueError('Temporary cleanup target escaped isolated output')
                shutil.rmtree(temporary)
    _assert_candidate_inputs_unchanged(source, audit_path, source_hash, audit_hash)
    return report


def preview_append(baseline_root, incoming_root, reviewed_new_series=()):
    """Read-only publication rehearsal; exports may overlap but cannot revise history.

    Inputs are saved audited JSONL batches, not production tables. Missing keys in
    a partial incoming export never mean deletion. New series require initial-load review.
    """
    import duckdb
    batches = []
    identities = []
    hashes = []
    for root in (baseline_root, incoming_root):
        source, audit_path, audit, count, identity, source_hash, audit_hash = _load_candidate_inputs(root)
        with duckdb.connect() as con:
            _load_candidate_points(con, source, audit, count, identity)
            batches.append(con.execute('SELECT series_id, period_date, value FROM points').fetchall())
        identities.append(identity)
        hashes.append((source, audit_path, source_hash, audit_hash))
    old = {(sid, date): value for sid, date, value in batches[0]}
    waterlines = {}
    for sid, date in old:
        waterlines[sid] = max(waterlines.get(sid, date), date)
    reviewed = set(reviewed_new_series)
    new_ids = {sid for sid, _, _ in batches[1]} - identities[0].keys()
    if not reviewed <= new_ids:
        raise ValueError('Reviewed IDs must be new series with audited observations in this batch')
    counts = dict(unchanged=0, append=0, historical_revision=0,
                  historical_missing_key=0, new_series=0, metadata_change=0)
    metadata_issues = [
        {'series_id': sid, 'kind': 'new_series' if sid not in identities[0] else 'metadata_change'}
        for sid in sorted(identities[1]) if identities[0].get(sid) != identities[1][sid]]
    series = {}
    for sid, date, value in batches[1]:
        key = (sid, date)
        if sid not in waterlines:
            kind = 'new_series'
        elif identities[0].get(sid) != identities[1].get(sid):
            kind = 'metadata_change'
        elif key in old:
            kind = 'unchanged' if value == old[key] else 'historical_revision'
        else:
            kind = 'append' if date > waterlines[sid] else 'historical_missing_key'
        counts[kind] += 1
        detail = series.setdefault(sid, {'waterline': str(waterlines[sid]) if sid in waterlines else None,
                                         'counts': dict.fromkeys(counts, 0)})
        detail['counts'][kind] += 1
    for values in hashes:
        _assert_candidate_inputs_unchanged(*values)
    blocked = (any(issue['series_id'] not in reviewed for issue in metadata_issues)
               or any(detail['counts']['new_series'] for sid, detail in series.items() if sid not in reviewed)
               or any(counts[k] for k in ('historical_revision', 'historical_missing_key', 'metadata_change')))
    return {'kind': 'ceic_append_preview', 'status': 'blocked' if blocked else 'ready_for_isolated_append',
            'accepted': False, 'production_write': False, 'network_fetch': False,
            'baseline_sha256': hashes[0][2], 'incoming_sha256': hashes[1][2],
            'baseline_audit_sha256': hashes[0][3], 'incoming_audit_sha256': hashes[1][3],
            'baseline_points': len(old), 'incoming_points': len(batches[1]),
            'counts': counts, 'series': series, 'metadata_issues': metadata_issues,
            **({'reviewed_new_series': sorted(reviewed)} if reviewed else {}),
            'policy': 'keep_existing; no_deletes; history_and_new_series_require_explicit_review'}


def merge_candidates(baseline_root, incoming_root, reviewed_new_series=()):
    """Build an isolated immutable union after preflight; never promote a pointer."""
    import duckdb
    preview = preview_append(baseline_root, incoming_root, reviewed_new_series)
    if preview['status'] == 'blocked':
        raise ValueError('Append blocked: history, identity or initial-load review required')
    roots = [Path(baseline_root), Path(incoming_root)]
    added = preview['counts']['append'] + preview['counts']['new_series']
    if not added:
        return {'preview': preview, 'review_root': str(roots[0]),
                'wide': build_candidates(roots[0]), 'merged': False}
    lineage = {key: preview[key] for key in ('baseline_sha256', 'incoming_sha256',
                                            'baseline_audit_sha256', 'incoming_audit_sha256')}
    lineage['format_version'] = 2  # Preserve old content-addressed four-column merges.
    if preview.get('reviewed_new_series'):
        lineage['reviewed_new_series'] = preview['reviewed_new_series']
    destination = OUTPUT / ('merge_' + hashlib.sha256(json.dumps(lineage, sort_keys=True).encode()).hexdigest()[:16])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.merge_', dir=str(OUTPUT)))
    try:
        combined = {}
        for root in roots:
            with (root / 'candidates.jsonl').open(encoding='utf-8') as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    key = (str(row['series_id']), date.fromisoformat(str(row['period_date'])[:10]).isoformat())
                    files = row.get('source_files', [])
                    if not isinstance(files, list):
                        raise ValueError('Invalid candidate file provenance')
                    files = files + ([row['source_file']] if row.get('source_file') is not None else [])
                    if any(not isinstance(name, str) or not name.strip() for name in files):
                        raise ValueError('Invalid candidate file provenance')
                    point = combined.setdefault(key, {
                        'series_id': key[0], 'period_date': key[1], 'value': float(row['value']),
                        'frequency': row['frequency'], 'source_files': set()})
                    point['source_files'].update(files)
        count = len(combined)
        if count != preview['baseline_points'] + added:
            raise ValueError('Merged key count differs from approved append plan')
        missing_provenance = sum(not point['source_files'] for point in combined.values())
        with (temporary / 'candidates.jsonl').open('w', encoding='utf-8', newline='\n') as handle:
            for key in sorted(combined):
                point = combined[key]
                point['source_files'] = sorted(point['source_files'])
                handle.write(json.dumps(point, ensure_ascii=False, allow_nan=False) + '\n')
        audit = json.loads((roots[0] / 'audit.json').read_text(encoding='utf-8'))
        if preview.get('reviewed_new_series'):
            incoming_audit = json.loads((roots[1] / 'audit.json').read_text(encoding='utf-8'))
            audit['inventory'].extend(item for item in incoming_audit['inventory']
                                      if str(item['series_id']) in preview['reviewed_new_series'])
        audit = {'inventory': audit['inventory'], 'errors': [], 'conflicting_keys': 0,
                 'observations_with_file_provenance': count,
                 'observations_missing_file_provenance': missing_provenance, 'lineage': lineage,
                 'verification_scope': 'isolated_saved_batch_union', 'accepted': False,
                 'production_write': False, 'network_fetch': False}
        _atomic_write_json(temporary / 'audit.json', audit)
        # Recheck the exact source bytes used by the preflight before publishing.
        for root, prefix in zip(roots, ('baseline', 'incoming')):
            _assert_candidate_inputs_unchanged(root / 'candidates.jsonl', root / 'audit.json',
                                              preview[prefix + '_sha256'], preview[prefix + '_audit_sha256'])
        source, _, merged_audit, count, identity, _, _ = _load_candidate_inputs(temporary)
        with duckdb.connect() as con:
            _load_candidate_points(con, source, merged_audit, count, identity)
            con.execute('CREATE TABLE expected AS SELECT DISTINCT CAST(series_id AS VARCHAR) AS series_id, '
                        'CAST(period_date AS DATE) AS period_date, CAST(value AS DOUBLE) AS value, frequency '
                        'FROM read_json_auto(?)', [[str(root / 'candidates.jsonl') for root in roots]])
            difference = con.execute('SELECT count(*) FROM ((SELECT * FROM points EXCEPT SELECT * FROM expected) '
                                     'UNION ALL (SELECT * FROM expected EXCEPT SELECT * FROM points))').fetchone()[0]
            if difference:
                raise ValueError('Merged values differ from source union')
        for root, prefix in zip(roots, ('baseline', 'incoming')):
            _assert_candidate_inputs_unchanged(root / 'candidates.jsonl', root / 'audit.json',
                                              preview[prefix + '_sha256'], preview[prefix + '_audit_sha256'])
        if destination.exists() or destination.is_symlink():
            if (destination.is_symlink() or not destination.is_dir()
                    or {p.name for p in destination.iterdir()} != {'candidates.jsonl', 'audit.json'}
                    or any(p.is_symlink() or not p.is_file() or _sha256_file(p) != _sha256_file(temporary / p.name)
                           for p in destination.iterdir())):
                raise ValueError('Corrupt existing merged batch')
        else:
            os.rename(str(temporary), str(destination))
            temporary = None
        wide = build_candidates(destination)  # Existing exact wide-table roundtrip validation.
        return {'preview': preview, 'review_root': str(destination), 'wide': wide,
                'merged': True, 'difference_rows': difference}
    finally:
        if temporary is not None and temporary.exists():
            if temporary.resolve().parent != OUTPUT.resolve():
                raise ValueError('Temporary cleanup target escaped isolated output')
            shutil.rmtree(temporary)


def select_review(review_root=None, restore_previous=False):
    """Explicit local dashboard switch/rollback, retaining technical-candidate status."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pointer_path = OUTPUT / 'selected_review.json'
    lock = OUTPUT / 'selection.lock'
    try:
        handle = lock.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise ValueError('CEIC selection locked; inspect owner before recovery') from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        current = json.loads(pointer_path.read_text(encoding='utf-8')) if pointer_path.exists() else None
        if restore_previous:
            if not current or not current.get('previous'):
                raise ValueError('No previous isolated selection')
            target = current['previous']
            review_root = OUTPUT / target['review_root']
        else:
            target = None
            review_root = Path(review_root)
        review_root = Path(review_root)
        if (review_root.is_symlink() or review_root.resolve().parent != OUTPUT.resolve()
                or not re.fullmatch(r'web_review|(?:export|merge)_[a-f0-9]{16}', review_root.name)):
            raise ValueError('Selection must stay in isolated CEIC review directories')
        report = build_candidates(review_root)
        selected = {'review_root': review_root.name, 'source_sha256': report['source_jsonl_sha256'],
                    'audit_sha256': report['source_audit_sha256'], 'accepted': False, 'production_write': False}
        if target and any(target.get(key) != value for key, value in selected.items()):
            raise ValueError('Previous selection changed; rollback refused')
        if current and all(current.get(key) == value for key, value in selected.items()):
            return current
        # Bootstrap preserves the original fixed baseline as the rollback target.
        if current is None and review_root.name != 'web_review':
            baseline = build_candidates(OUTPUT / 'web_review')
            current = {'review_root': 'web_review', 'source_sha256': baseline['source_jsonl_sha256'],
                       'audit_sha256': baseline['source_audit_sha256'], 'accepted': False, 'production_write': False}
        selected['previous'] = {key: current[key] for key in selected} if current else None
        _atomic_write_json(pointer_path, selected)
        return selected
    finally:
        lock.unlink(missing_ok=True)


def rehearse_selection():
    """CLI-only recovery exercise on disposable copies of the two pinned versions."""
    global OUTPUT
    original = OUTPUT
    pointer_path = original / 'selected_review.json'
    pointer_bytes = pointer_path.read_bytes()
    selected = json.loads(pointer_bytes)
    versions = [selected, selected.get('previous')]
    if not all(isinstance(v, dict) for v in versions):
        raise ValueError('Recovery rehearsal requires current and previous pinned versions')
    protected = {pointer_path: _sha256_file(pointer_path)}
    for item in versions:
        name = item.get('review_root', '')
        root = original / name
        if (not re.fullmatch(r'web_review|(?:export|merge)_[a-f0-9]{16}', name)
                or root.is_symlink() or root.resolve().parent != original.resolve()
                or item.get('accepted') is not False or item.get('production_write') is not False):
            raise ValueError('Invalid isolated recovery source')
        for filename, key in [('candidates.jsonl', 'source_sha256'), ('audit.json', 'audit_sha256')]:
            path = root / filename
            if path.is_symlink() or _sha256_file(path) != item[key]:
                raise ValueError('Pinned recovery source hash mismatch')
            protected[path] = item[key]
    checks = {}
    try:
        with tempfile.TemporaryDirectory(prefix='ceic-rehearsal-') as folder:
            OUTPUT = Path(folder).resolve()
            if OUTPUT.parent != Path(tempfile.gettempdir()).resolve():
                raise ValueError('Rehearsal must stay inside the temporary directory')
            for path in protected:
                target = OUTPUT / path.relative_to(original)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            current = OUTPUT / selected['review_root']
            report = build_candidates(current)
            checks['wide_roundtrip'] = all(t['difference_rows'] == 0 for t in report['tables'])
            select_review(current)
            checks['same_version_noop'] = (OUTPUT/'selected_review.json').read_bytes() == pointer_bytes
            restored = select_review(restore_previous=True)
            checks['restore_previous'] = restored['review_root'] == versions[1]['review_root']
            select_review(current)
            checks['return_to_current'] = (OUTPUT/'selected_review.json').read_bytes() == pointer_bytes
            lock = OUTPUT/'selection.lock'
            lock.write_text('rehearsal', encoding='utf-8')
            try:
                select_review(current)
                checks['lock_rejected'] = False
            except ValueError as exc:
                checks['lock_rejected'] = 'locked' in str(exc)
            finally:
                lock.unlink()
            # Change only a copied previous audit; never touch the real saved source.
            previous_audit = OUTPUT/versions[1]['review_root']/'audit.json'
            previous_audit.write_bytes(previous_audit.read_bytes()+b'\n')
            try:
                select_review(restore_previous=True)
                checks['corrupt_previous_rejected'] = False
            except ValueError as exc:
                checks['corrupt_previous_rejected'] = 'Previous selection changed' in str(exc)
            checks['failure_keeps_pointer'] = (OUTPUT/'selected_review.json').read_bytes() == pointer_bytes
            checks['lock_released'] = not lock.exists()
            points = sum(i['observations'] for t in report['tables'] for i in t['indicators'])
    finally:
        OUTPUT = original
    checks['original_sources_unchanged'] = all(_sha256_file(p) == h for p, h in protected.items())
    checks['temporary_copy_removed'] = not Path(folder).exists()
    if not all(checks.values()):
        raise ValueError('Isolated recovery rehearsal failed: '+str(checks))
    return {'kind':'ceic_recovery_rehearsal', 'checks':checks, 'observations':points,
            'implementation_sha256':_sha256_file(__file__),
            'selected_pointer_sha256':hashlib.sha256(pointer_bytes).hexdigest(),
            'source_sha256':selected['source_sha256'], 'audit_sha256':selected['audit_sha256'],
            'review_root':selected['review_root'], 'previous':versions[1]['review_root'],
            'production_write':False, 'network_fetch':False, 'live_selection_changed':False}


def audit_exports(project, web_root):
    """Replay existing web exports without consuming inbox or calling publishers."""
    import pandas as pd
    parser_path = Path(project) / 'ceic_growth_web_export.py'
    parser_hash = _sha256_file(parser_path)
    spec = importlib.util.spec_from_file_location('ceic_readonly_parser', parser_path)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    files = sorted(Path(web_root).rglob('*.xlsx'))
    if not files:
        raise ValueError('No saved web exports')
    inventory, frames, errors = [], [], []
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    for path in files:
        try:
            head = pd.read_excel(path, header=None, nrows=20, dtype=object, keep_default_na=False)
            labels = head.iloc[:, 0].astype(str).str.strip()
            def row(label):
                hits = head.index[labels == label].tolist()
                if len(hits) != 1:
                    raise ValueError(f'Missing/duplicate metadata: {label}')
                return hits[0]
            positions = {label: row(label) for label in ('数列ID', '频率', '单位', '数据来源')}
            # Keep supplied metadata, not only the four fields needed to parse values.
            # Export update dates are snapshot attributes, not historical release times.
            for label in ('区域', '次国家', '状态', 'SR码', '贸易代码', '助记符', '函数说明',
                          '首次观测日期', '最后观测日期', '最后更新时间', '数列备注', '建议'):
                if (labels == label).any():
                    positions[label] = row(label)
            for col in range(1, head.shape[1]):
                inventory.append({'series_id': parser._series_id(head.iat[positions['数列ID'], col]),
                                  'name': str(head.iat[0, col]),
                                  **{label: str(head.iat[pos, col]) for label, pos in positions.items() if label != '数列ID'},
                                  'source_file': str(path), 'basis_eligible': False})
            for frequency in ('M', 'D'):
                try:
                    _, data = parser.parse_web_xlsx(path, expected_frequency=frequency, filter_other_frequencies=True)
                    frames.append(data.assign(frequency=frequency, source_file=str(path)))
                except ValueError as exc:
                    if '未包含任何' not in str(exc):
                        raise
        except Exception as exc:
            errors.append({'file': str(path), 'error': str(exc)})
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest for p, digest in hashes.items()):
        raise ValueError('Source export changed during audit')
    report = {'export_audit_version': 2, 'files': len(files), 'unique_series': len({r['series_id'] for r in inventory}),
              'inventory': inventory, 'errors': errors, 'source_sha256': hashes,
              'production_write': False, 'accepted': False,
              'parser_sha256': parser_hash,
              'unsupported_frequencies': sorted({r['频率'] for r in inventory} - {'月', '日', '日，每日', 'M', 'D', 'Monthly', 'Daily'})}
    payload = None
    if frames and not errors:
        data = pd.concat(frames, ignore_index=True)
        conflicts = data.groupby(['series_id', 'period_date'])['value'].nunique().gt(1).sum()
        report['conflicting_keys'] = int(conflicts)
        # Preserve separate file provenance, including conflicting observations; no acceptance pointer.
        payload = data.to_json(orient='records', lines=True, date_format='iso', force_ascii=False, double_precision=15)
        report['observations_with_file_provenance'] = len(data)
    # Each export batch is separate: web_review remains the pinned historical baseline.
    batch_hash = hashlib.sha256(json.dumps({'sources': hashes, 'parser': report['parser_sha256'],
                                          'export_audit_version': report['export_audit_version']}, sort_keys=True).encode()).hexdigest()
    destination = OUTPUT / ('export_' + batch_hash[:16])
    report['review_root'] = str(destination)
    expected = {'audit.json': (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8')}
    if payload is not None:
        expected['candidates.jsonl'] = payload.encode('utf-8')
    if (_sha256_file(parser_path) != parser_hash
            or any(_sha256_file(path) != digest for path, digest in hashes.items())):
        raise ValueError('Source export or parser changed during audit')
    if destination.exists() or destination.is_symlink():
        if (destination.is_symlink() or not destination.is_dir()
                or {p.name for p in destination.iterdir()} != set(expected)
                or any(p.is_symlink() or not p.is_file() or p.read_bytes() != expected[p.name]
                       for p in destination.iterdir())):
            raise ValueError('Corrupt existing export audit; preserved without overwrite')
        return report
    OUTPUT.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.export_', dir=str(OUTPUT)))
    try:
        for name, content in expected.items():
            (temporary / name).write_bytes(content)
        if (_sha256_file(parser_path) != report['parser_sha256']
                or any(_sha256_file(path) != digest for path, digest in hashes.items())):
            raise ValueError('Source export or parser changed during audit')
        os.rename(str(temporary), str(destination))
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            if temporary.resolve().parent != OUTPUT.resolve():
                raise ValueError('Temporary cleanup target escaped isolated output')
            shutil.rmtree(temporary)
    return report


def audit_dataset(source, registry_path, output):
    import duckdb
    source, registry_path, output = map(Path, (source, registry_path, output))
    if source.resolve() == output.resolve() or source.resolve() in output.resolve().parents:
        raise ValueError('Output must be isolated from source')
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    series = sorted((s for s in registry['series'] if s.get('is_active', True)), key=lambda s: s['ordinal'])
    columns = [s['column_name'] for s in series]
    if not columns or any(not re.fullmatch(r's_[a-z0-9_]+', c) for c in columns):
        raise ValueError('Invalid registry columns')
    for key in ('series_id', 'column_name', 'ordinal'):
        if len({s[key] for s in series}) != len(series):
            raise ValueError('Duplicate registry identity')
    files = sorted(source.glob('year=*/*.parquet'))
    if not files:
        raise ValueError('No saved Parquet files')
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [registry_path, *files]}
    with duckdb.connect() as con:
        con.from_parquet([str(p) for p in files], hive_partitioning=False).create_view('saved')
        schema = con.execute('DESCRIBE saved').fetchall()
        if [(r[0], r[1]) for r in schema] != [('period_date', 'DATE')] + [(c, 'DOUBLE') for c in columns]:
            raise ValueError('Schema/order differs from registry')
        count, unique, dated = con.execute('SELECT count(*), count(DISTINCT period_date), count(period_date) FROM saved').fetchone()
        if not count or count != unique or dated != count:
            raise ValueError('Empty, duplicate or null dates')
        indicators = []
        for item in series:
            col = '"' + item['column_name'] + '"'
            n, first, last, invalid = con.execute(f'SELECT count({col}), min(period_date) FILTER (WHERE {col} IS NOT NULL), max(period_date) FILTER (WHERE {col} IS NOT NULL), count(*) FILTER (WHERE {col} IS NOT NULL AND NOT isfinite({col})) FROM saved').fetchone()
            if invalid:
                raise ValueError('Nonfinite observations')
            indicators.append({**item, 'observations': n, 'first': str(first), 'last': str(last), 'basis_eligible': False})
        output.mkdir(parents=True, exist_ok=True)
        snapshot = output / 'review.parquet'
        con.execute('COPY (SELECT * FROM saved ORDER BY period_date) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(snapshot)])
        difference = con.execute('SELECT count(*) FROM ((SELECT * FROM saved EXCEPT ALL SELECT * FROM read_parquet(?)) UNION ALL (SELECT * FROM read_parquet(?) EXCEPT ALL SELECT * FROM saved))', [str(snapshot), str(snapshot)]).fetchone()[0]
        if difference:
            raise ValueError('Replay values differ')
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest for p, digest in hashes.items()):
        raise ValueError('Source changed during audit')
    report = {'dataset': registry['dataset_name'], 'rows': count, 'series_count': len(series), 'source_sha256': hashes,
              'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(), 'exact_difference_rows': difference,
              'production_write': False, 'network_fetch': False, 'indicators': indicators}
    (output / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(r'E:\阿里云数据库\wind更新'))
    parser.add_argument('--source-root', type=Path, default=Path(r'E:\交易数据\本地表'))
    parser.add_argument('--web-root', type=Path, help='Audit saved XLSX exports instead of replaying published wide tables')
    parser.add_argument('--build-candidates', action='store_true', help='Convert saved candidate JSONL to isolated wide Parquet; no fetch')
    parser.add_argument('--preview-append', type=Path, metavar='INCOMING_REVIEW', help='Read-only comparison of an audited incoming batch')
    parser.add_argument('--merge-append', action='store_true', help='With --preview-append, build isolated immutable union; no production publication')
    parser.add_argument('--reviewed-new-series', nargs='+', default=[], metavar='ID',
                        help='Explicit reviewed initial-load IDs; requires --preview-append; does not allow history revisions')
    parser.add_argument('--baseline-review', type=Path, help='Explicit baseline; defaults to pinned dashboard selection, or original web_review')
    parser.add_argument('--select-review', type=Path, help='Explicitly select isolated data for the dashboard; no business acceptance')
    parser.add_argument('--restore-previous', action='store_true', help='Verify and restore the previous isolated dashboard selection')
    parser.add_argument('--rehearse-selection', action='store_true', help='Exercise pinned recovery on temporary copies; never switch the live dashboard')
    args = parser.parse_args()
    if args.rehearse_selection:
        if any((args.select_review, args.restore_previous, args.web_root, args.preview_append,
                args.merge_append, args.build_candidates, args.baseline_review, args.reviewed_new_series)):
            parser.error('Recovery rehearsal must run as a separate operation')
        evidence = OUTPUT/'recovery_rehearsal.json'
        try:
            report = rehearse_selection()
        except Exception as exc:
            _atomic_write_json(evidence, {'kind':'ceic_recovery_rehearsal', 'checks':{'completed':False},
                'error':str(exc), 'production_write':False, 'network_fetch':False})
            raise
        _atomic_write_json(evidence, report)
        print(json.dumps(report, ensure_ascii=False))
        return
    if args.reviewed_new_series and not args.preview_append:
        parser.error('--reviewed-new-series requires --preview-append')
    if args.select_review or args.restore_previous:
        if (args.select_review and args.restore_previous) or args.web_root or args.preview_append or args.merge_append or args.build_candidates:
            parser.error('Selection/rollback must run as a separate operation')
        print(json.dumps(select_review(args.select_review, args.restore_previous), ensure_ascii=False))
        return
    if args.baseline_review is None:
        args.baseline_review = OUTPUT / 'web_review'
        pointer_path = OUTPUT / 'selected_review.json'
        if pointer_path.exists() and (args.preview_append or args.build_candidates):
            selected = json.loads(pointer_path.read_text(encoding='utf-8'))
            name = selected.get('review_root', '')
            if (not re.fullmatch(r'web_review|(?:export|merge)_[a-f0-9]{16}', name)
                    or selected.get('accepted') is not False or selected.get('production_write') is not False):
                parser.error('Invalid isolated baseline pointer')
            args.baseline_review = OUTPUT / name
            if args.baseline_review.is_symlink() or args.baseline_review.resolve().parent != OUTPUT.resolve():
                parser.error('Isolated baseline escaped output directory')
            _assert_candidate_inputs_unchanged(args.baseline_review / 'candidates.jsonl', args.baseline_review / 'audit.json',
                                              selected['source_sha256'], selected['audit_sha256'])
    if args.merge_append and not args.preview_append:
        parser.error('--merge-append requires --preview-append')
    if args.preview_append:
        if args.web_root or args.build_candidates:
            parser.error('--preview-append cannot be combined with conversion or export audit')
        if args.merge_append:
            print(json.dumps(merge_candidates(args.baseline_review, args.preview_append, args.reviewed_new_series), ensure_ascii=False))
            return
        report = preview_append(args.baseline_review, args.preview_append, args.reviewed_new_series)
        print(json.dumps(report, ensure_ascii=False))
        if report['status'] == 'blocked':
            raise SystemExit(2)
        return
    if args.build_candidates:
        if args.web_root:
            parser.error('--build-candidates does not reread XLSX; omit --web-root')
        report = build_candidates(args.baseline_review)
        print(json.dumps({**report, 'tables': [{k: v for k, v in t.items() if k != 'indicators'} for t in report['tables']]}, ensure_ascii=False))
        return
    if args.web_root:
        report = audit_exports(args.project, args.web_root)
        print(json.dumps({k: v for k, v in report.items() if k not in ('inventory', 'source_sha256')}, ensure_ascii=False))
        if report['errors'] or report.get('conflicting_keys') != 0 or not report.get('observations_with_file_provenance'):
            raise SystemExit(2)
        return
    for dataset, registry in [('ceicgrowthdata', 'ceic_growth_registry.json'), ('ceicgrowthdaily', 'ceic_daily_registry.json')]:
        report = audit_dataset(args.source_root / dataset, args.project / registry, OUTPUT / dataset)
        print(json.dumps({k: report[k] for k in ('dataset', 'rows', 'series_count', 'exact_difference_rows', 'production_write')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
