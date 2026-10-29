import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('freeze_artifacts', ROOT/'tools/freeze_backend.py')
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)


def test_release_packages_only_runtime_inputs_and_is_reproducible(tmp_path, monkeypatch):
    root = tmp_path/'source'
    files = {
        'backend/app.py': 'app', 'backend/gateway.py': 'gateway',
        'futures/backend/api.py': 'api', 'futures/backend/dashboard_json_delta.py': 'shared',
        'seats/backend/src/seat_dashboard/main.py': 'main',
        'seats/backend/src/seat_dashboard/schema.sql': 'schema',
        'seats/config/products.csv': 'products', 'seats/config/combinations.json': '{}',
        'seats/backend/pyproject.toml': 'project', 'seats/backend/README.md': 'readme',
        'frontend/dist/index.html': '<html/>', 'frontend/dist/assets/main.js': 'web',
        'backend/__pycache__/secret.py': 'exclude', 'backend/.env': 'secret',
        'runtime/data.parquet': 'data', 'seats/backend/tests/test.py': 'test',
        **{name: name for name in freeze.LOCKS},
    }
    for name, content in files.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    monkeypatch.setattr(freeze, 'ROOT', root)
    output = tmp_path/'release'
    manifest = freeze.release_archives(output, 'a'*40)
    assert manifest == json.loads((output/'dashboard-release.json').read_text())
    assert manifest['schema'] == 1 and manifest['commit'] == 'a'*40
    for kind, asset in manifest['assets'].items():
        body = (output/asset['name']).read_bytes()
        assert asset['bytes'] == len(body)
        assert asset['sha256'] == hashlib.sha256(body).hexdigest()
        with tarfile.open(output/asset['name']) as archive:
            names = archive.getnames()
            assert all(member.isfile() for member in archive)
            if kind == 'code':
                assert not any(part in name for name in names for part in ('__pycache__', '.env', 'tests/', 'runtime/'))
                assert archive.extractfile('seats/backend/src/dashboard_json_delta.py').read() == b'shared'
                for lock, digest in manifest['dependencies'].items():
                    assert hashlib.sha256(archive.extractfile(lock).read()).hexdigest() == digest
            else:
                assert names == ['assets/main.js', 'index.html']
    before = {p.name:p.read_bytes() for p in output.iterdir()}
    for path in root.rglob('*'):
        if path.is_file():path.touch()
    freeze.release_archives(output, 'a'*40)
    assert before == {p.name:p.read_bytes() for p in output.iterdir()}


def test_release_requires_full_commit_and_built_web(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='Full commit'):
        freeze.release_archives(tmp_path, 'abc')
    monkeypatch.setattr(freeze, 'ROOT', ROOT)
    monkeypatch.setattr(freeze, 'review_hashes', lambda _: {})
    with pytest.raises(ValueError, match='frontend'):
        freeze.release_archives(tmp_path, 'a'*40)
