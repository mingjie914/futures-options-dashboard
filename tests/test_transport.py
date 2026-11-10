import asyncio
import json
import os

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from futures.backend.transport import ByteCache, DELTA, ResearchHTTP, changes, fingerprint


def test_static_content_validator_survives_equal_size_epoch_mtime(monkeypatch, tmp_path):
    from backend import gateway
    entry = tmp_path / 'index.html'
    entry.write_text('entry-a')
    os.utime(entry, (0, 0))
    monkeypatch.setattr(gateway, 'dist', tmp_path)
    monkeypatch.setattr(gateway, 'asset_roots', [tmp_path])
    gateway.content_tag.cache_clear()
    with TestClient(gateway.app) as client:
        first = client.get('/')
        tag = first.headers['etag']
        assert first.text == 'entry-a' and 'last-modified' not in first.headers
        assert client.get('/', headers={'If-None-Match': tag}).status_code == 304
        replacement = tmp_path / 'next.html'
        replacement.write_text('entry-b')
        os.utime(replacement, (0, 0))
        replacement.replace(entry)
        changed = client.get('/', headers={'If-None-Match': tag, 'If-Modified-Since': 'Thu, 01 Jan 1970 00:00:00 GMT'})
        assert changed.status_code == 200 and changed.text == 'entry-b'
        assert changed.headers['etag'] != tag
        assert client.get('/', headers={'If-None-Match': '"other", '+changed.headers['etag'].removeprefix('W/')}).status_code == 304
        asset = client.get('/assets/index.html')
        unchanged = client.get('/assets/index.html', headers={'If-None-Match': asset.headers['etag']})
        assert unchanged.status_code == 304 and unchanged.content == b''
        assert unchanged.headers['cache-control'].endswith('immutable')
        assert gateway.content_tag.cache_info().maxsize == 128


def example():
    state = {'revision': 'v1', 'calls': 0, 'data': {'rows': [{'id': i, 'value': i} for i in range(200)]}}
    cache = ByteCache(128 * 1024)
    app = FastAPI()
    app.add_middleware(ResearchHTTP, version=lambda _: state['revision'], cache=cache,
                       paths=['/api/data'], code_version='formula-v1')

    @app.get('/api/data')
    def data(q: str = ''):
        state['calls'] += 1
        return {**state['data'], 'query': q}
    return TestClient(app), state, cache


def test_gzip_identity_empty_304_and_query_version():
    client, state, cache = example()
    identity = client.get('/api/data', headers={'Accept-Encoding': 'identity'})
    compressed = client.get('/api/data', headers={'Accept-Encoding': 'gzip'})
    assert compressed.headers['content-encoding'] == 'gzip'
    assert compressed.content == identity.content  # httpx transparently decodes.
    assert int(compressed.headers['content-length']) < len(identity.content)
    assert compressed.headers['etag'] == identity.headers['etag']
    assert {'Accept-Encoding', 'A-IM'} <= set(compressed.headers['vary'].split(', '))
    disabled = client.get('/api/data', headers={'Accept-Encoding': 'gzip;q=0, *;q=1'})
    assert 'content-encoding' not in disabled.headers
    cache.clear()
    unchanged = client.get('/api/data', headers={'If-None-Match': identity.headers['etag']})
    assert unchanged.status_code == 304 and unchanged.content == b''
    assert 'content-encoding' not in unchanged.headers
    assert state['calls'] == 1  # No query, even after body-cache eviction.
    changed_query = client.get('/api/data?q=another', headers={'If-None-Match': identity.headers['etag']})
    assert changed_query.status_code == 200 and changed_query.json()['query'] == 'another'
    assert client.get('/api/data?a=1&b=2').headers['etag'] == client.get('/api/data?b=2&a=1').headers['etag']


def test_actual_delta_corrections_deletions_and_evicted_base():
    client, state, cache = example()
    original = client.get('/api/data')
    state['data']['rows'][0]['value'] = None
    state['data']['rows'].pop()
    state['revision'] = 'same-day-correction'
    headers = {'If-None-Match': original.headers['etag'], 'A-IM': DELTA}
    delta = client.get('/api/data', headers=headers)
    assert delta.status_code == 226 and delta.headers['delta-base'] == original.headers['etag']
    body = original.json()
    for op, path, *value in delta.json()['ops']:
        target = body
        for key in path[:-1]:
            target = target[key]
        if op == 'set':
            target[path[-1]] = value[0]
        elif isinstance(target, list):
            target.pop(path[-1])
        else:
            del target[path[-1]]
    full = client.get('/api/data')
    assert body == full.json()
    assert len(delta.content) < len(full.content) * .8
    cache.clear()
    assert client.get('/api/data', headers=headers).status_code == 200
    assert changes({'a': 1}, {'a': None}) == [['set', ['a'], None]]


def test_response_cpu_work_runs_outside_event_loop(monkeypatch):
    from threading import get_ident
    from futures.backend import transport

    calls = set()
    for name, owner in [('memory_size', transport), ('changes', transport), ('compress', transport.gzip)]:
        original = getattr(owner, name)
        def checked(*args, _original=original, _name=name, **kwargs):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                calls.add(_name)
            else:
                raise AssertionError(f'{_name} blocked the event loop on thread {get_ident()}')
            return _original(*args, **kwargs)
        monkeypatch.setattr(owner, name, checked)
    client, state, _ = example()
    with client:
        first = client.get('/api/data')
        state['revision'] = 'v2'
        state['data']['rows'][0]['value'] = 100
        response = client.get('/api/data', headers={'If-None-Match': first.headers['etag'], 'A-IM': DELTA})
        assert response.status_code == 226
    assert calls == {'memory_size', 'changes', 'compress'}


def test_byte_budget_and_atomic_same_day_revision(tmp_path):
    cache = ByteCache(4096)
    for i in range(20):
        cache[i] = {'text': 'x' * 1000}
        assert cache.bytes <= cache.limit
    assert cache.get(0) is None and cache.get(19)
    cache['too-large'] = b'x' * 8192
    assert cache.get('too-large') is None
    source = tmp_path / 'source.json'
    source.write_text('100')
    before = fingerprint([source])
    replacement = tmp_path / 'new.json'
    replacement.write_text('101')
    replacement.replace(source)
    assert fingerprint([source]) != before
    source.unlink()
    assert fingerprint([source]) != before


def test_heavy_requests_serialized_and_errors_not_cached():
    async def run():
        state = {'active': 0, 'max': 0, 'calls': 0}
        app = FastAPI()
        app.add_middleware(ResearchHTTP, version=lambda _: 'v1', cache=ByteCache(10000), paths=['/api/data'])

        @app.get('/api/data')
        async def data(q: int):
            state['active'] += 1
            state['calls'] += 1
            state['max'] = max(state['max'], state['active'])
            await asyncio.sleep(.01)
            state['active'] -= 1
            return {'q': q}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
            responses = await asyncio.gather(*(client.get('/api/data?q=' + str(i % 2)) for i in range(8)))
            assert all(r.status_code == 200 for r in responses)
            assert state['max'] == 1 and state['calls'] == 2
            assert (await client.get('/api/data?q=bad')).status_code == 422
    asyncio.run(run())


def test_gateway_leaves_compression_to_edge_and_keeps_security(monkeypatch):
    from backend.gateway import app
    payload = json.dumps({'rows': ['market'] * 1000}).encode()
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.headers['accept-encoding'] == 'identity'
        if request.headers.get('if-none-match') == 'W/"same"':
            return httpx.Response(304, headers={'etag': 'W/"same"'})
        if request.headers.get('a-im') == DELTA:
            return httpx.Response(226, json={'base':'W/"old"','version':'W/"same"','ops':[['remove',['rows',1]]]},
                                  headers={'etag':'W/"same"','im':DELTA,'delta-base':'W/"old"','cache-control':'no-store'})
        return httpx.Response(200, content=payload, headers={'content-type': 'application/json', 'etag': 'W/"same"'})

    monkeypatch.setenv('DASHBOARD_PUBLIC_READONLY', '1')
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        for encoding in ('gzip', 'gzip, deflate, br, zstd', 'identity', 'gzip;q=0, zstd;q=0'):
            result = client.get('/api/futures/v1/products', headers={'Accept-Encoding': encoding})
            assert 'content-encoding' not in result.headers and result.content == payload
        unchanged = client.get('/api/futures/v1/products', headers={'If-None-Match': 'W/"same"', 'A-IM': DELTA})
        assert unchanged.status_code == 304 and unchanged.content == b''
        assert requests[-1].headers['a-im'] == DELTA
        delta = client.get('/api/seats/v1/views/combination', headers={'If-None-Match':'W/"old"','A-IM':DELTA})
        assert delta.status_code == 226 and delta.headers['delta-base'] == 'W/"old"'
        assert delta.headers['im'] == DELTA and delta.json()['ops'] == [['remove',['rows',1]]]
        count = len(requests)
        assert client.get('/api/futures/v1/products', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/options/v1/refresh').status_code == 403
        assert len(requests) == count


def test_options_lazy_health_and_same_day_revision(monkeypatch, tmp_path):
    from backend import app as module
    source=tmp_path/'snapshot.json'
    source.write_text('{"value":1}')
    calls=[]
    def normalize(folder):
        calls.append(folder)
        return json.loads(source.read_text())
    monkeypatch.setattr(module,'_active_folder',lambda:tmp_path)
    monkeypatch.setattr(module,'runtime',tmp_path)
    monkeypatch.setattr(module,'state',{'data':None,'revision':None})
    monkeypatch.setattr(module,'normalize',normalize)
    module.cache.clear()
    with TestClient(module.app) as client:
        assert client.get('/api/options/v1/health').status_code==200 and calls==[]
        first=client.get('/api/options/v1/snapshot')
        assert first.json()=={'value':1}
        assert client.get('/api/options/v1/snapshot',headers={'If-None-Match':first.headers['etag']}).status_code==304
        assert len(calls)==1
        replacement=tmp_path/'new.tmp';replacement.write_text('{"value":2}');replacement.replace(source)
        corrected=client.get('/api/options/v1/snapshot',headers={'If-None-Match':first.headers['etag']})
        assert corrected.json()=={'value':2} and corrected.headers['etag']!=first.headers['etag']
        assert len(calls)==2
    module.cache.clear()


def test_edge_mode_keeps_large_custom_delta_compressed():
    from starlette.responses import Response
    app = FastAPI()
    app.add_middleware(ResearchHTTP, compress=False)
    payload = json.dumps({'ops': [['set', ['rows', 0], 'market' * 1000]]}).encode()

    @app.get('/api/delta')
    def delta():
        return Response(payload, status_code=226, media_type='application/vnd.mingjiec.delta+json',
                        headers={'im': DELTA, 'delta-base': 'W/"previous"', 'cache-control': 'no-store'})

    with TestClient(app) as client:
        result = client.get('/api/delta', headers={'Accept-Encoding': 'gzip, br, zstd'})
        assert result.status_code == 226 and result.headers['content-encoding'] == 'gzip'
        assert result.content == payload and result.headers['delta-base'] == 'W/"previous"'
