"""Bounded read-only HTTP reuse, shared by both APIs and their gateway."""
import asyncio
from collections import OrderedDict
from contextlib import nullcontext
import gzip
import hashlib
import json
import sys
from threading import RLock
from urllib.parse import parse_qsl, urlencode

from starlette.responses import Response
from starlette.concurrency import run_in_threadpool

DELTA = 'dashboard-json-delta'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def fingerprint(paths):
    """Published inputs must be immutable/atomically replaced, never preserve all stat fields."""
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        try:
            stat = path.stat()
            item = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
        except FileNotFoundError:
            item = (str(path), None)
        digest.update(repr(item).encode())
    return digest.hexdigest()


def memory_size(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(memory_size(k, seen) + memory_size(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list, set)):
        size += sum(memory_size(v, seen) for v in value)
    return size


class ByteCache:
    """One byte budget for decoded source objects and encoded HTTP responses."""
    def __init__(self, limit):
        self.limit, self.bytes = limit, 0
        self.items, self.lock = OrderedDict(), RLock()

    def get(self, key, default=None):
        with self.lock:
            entry = self.items.get(key)
            if entry is None:
                return default
            self.items.move_to_end(key)
            return entry[0]

    def __setitem__(self, key, value):
        size = memory_size((key, value)) + 128  # OrderedDict entry overhead.
        with self.lock:
            old = self.items.pop(key, None)
            if old:
                self.bytes -= old[1]
            if size > self.limit:
                return
            while self.items and self.bytes + size > self.limit:
                self.bytes -= self.items.popitem(last=False)[1][1]
            self.items[key] = (value, size)
            self.bytes += size

    def clear(self):
        with self.lock:
            self.items.clear()
            self.bytes = 0


def changes(before, after, path=()):
    """Index-addressed edits preserve null values and explicitly carry deletions."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = [['remove', [*path, key]] for key in before if key not in after]
        for key, value in after.items():
            result += changes(before[key], value, (*path, key)) if key in before else [['set', [*path, key], value]]
        return result
    if isinstance(before, list) and isinstance(after, list):
        result = []
        for i in range(min(len(before), len(after))):
            result += changes(before[i], after[i], (*path, i))
        result += [['remove', [*path, i]] for i in range(len(before)-1, len(after)-1, -1)]
        result += [['set', [*path, i], after[i]] for i in range(len(before), len(after))]
        return result
    return [['set', list(path), after]]


def accepts_gzip(header):
    weights = {}
    for item in header.lower().split(','):
        parts = item.strip().split(';')
        try:
            weights[parts[0]] = next((float(p.strip()[2:]) for p in parts[1:] if p.strip().startswith('q=')), 1)
        except ValueError:
            weights[parts[0]] = 0
    return weights.get('gzip', weights.get('*', 0)) > 0


class ResearchHTTP:
    def __init__(self, app, *, version=None, cache=None, paths=(), code_version='', concurrency=1):
        self.app, self.version, self.cache = app, version, cache
        self.paths, self.code_version = set(paths), code_version
        self.slots = asyncio.Semaphore(concurrency)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'GET' or not scope['path'].startswith('/api/'):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope['headers']}
        query = urlencode(sorted(parse_qsl(scope['query_string'].decode(), keep_blank_values=True)))
        request_key = scope['path'] + '?' + query
        use_cache = self.version is not None and scope['path'] in self.paths
        tag = None
        if use_cache:
            try:
                source = await run_in_threadpool(self.version, scope['path'])
                tag = 'W/"' + hashlib.sha256(encoded([self.code_version, source, request_key])).hexdigest() + '"'
            except (OSError, ValueError):
                use_cache = False  # Let the API report its normal unavailable/error state.
        if use_cache and tag in [v.strip() for v in headers.get('if-none-match', '').split(',')]:
            return await self.emit(scope, receive, send, headers, b'', 304, {'etag':tag, 'cache-control':'private, no-cache'})
        cached = self.cache.get(('http', request_key, tag)) if use_cache else None
        if cached is not None:
            return await self.respond(scope, receive, send, headers, request_key, tag, cached)
        async with self.slots if self.version else nullcontext():
            cached = self.cache.get(('http', request_key, tag)) if use_cache else None
            if cached is not None:
                return await self.respond(scope, receive, send, headers, request_key, tag, cached)
            start, chunks = None, []
            async def collect(message):
                nonlocal start
                if message['type'] == 'http.response.start':
                    start = message
                elif message['type'] == 'http.response.body':
                    chunks.append(message.get('body', b''))
            await self.app(scope, receive, collect)
            body = b''.join(chunks)
            response_headers = {k.decode(): v.decode() for k, v in start['headers']}
            response_headers.pop('content-length', None)
            status = start['status']
            reusable = use_cache and status == 200 and 'application/json' in response_headers.get('content-type', '')
            if reusable:
                try:
                    reusable = source == await run_in_threadpool(self.version, scope['path'])
                except (OSError, ValueError):
                    reusable = False
            if reusable:
                entry = (body, response_headers)
                self.cache[('http', request_key, tag)] = entry
                return await self.respond(scope, receive, send, headers, request_key, tag, entry)
            await self.emit(scope, receive, send, headers, body, status, response_headers)

    async def respond(self, scope, receive, send, request_headers, key, tag, entry):
        body, original = entry
        headers = {**original, 'etag': tag, 'cache-control': 'private, no-cache'}
        previous = request_headers.get('if-none-match', '')
        if any(value.strip().removeprefix('W/') in ('*', tag.removeprefix('W/')) for value in previous.split(',')):
            return await self.emit(scope, receive, send, request_headers, b'', 304, headers)
        status = 200
        if request_headers.get('a-im') == DELTA and previous:
            old = self.cache.get(('http', key, previous))
            if old is not None:
                delta = encoded({'base': previous, 'version': tag, 'ops': changes(json.loads(old[0]), json.loads(body))})
                if len(delta) < len(body) * .8:
                    body, status = delta, 226
                    headers.update({'im': DELTA, 'delta-base': previous, 'content-type': 'application/vnd.mingjiec.delta+json', 'cache-control': 'no-store'})
        await self.emit(scope, receive, send, request_headers, body, status, headers)

    @staticmethod
    async def emit(scope, receive, send, request_headers, body, status, headers):
        headers = dict(headers)
        vary = {v.strip() for v in headers.get('vary', '').split(',') if v.strip()}
        vary.update(('Accept-Encoding', 'A-IM'))
        headers['vary'] = ', '.join(sorted(vary))
        if status == 304:
            body = b''
            headers.pop('content-encoding', None)
            headers.pop('content-type', None)
        elif len(body) >= 1024 and not headers.get('content-encoding') and accepts_gzip(request_headers.get('accept-encoding', '')):
            body = gzip.compress(body, compresslevel=4, mtime=0)
            headers['content-encoding'] = 'gzip'
        await Response(body, status_code=status, headers=headers)(scope, receive, send)
