from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import gzip
import json
import shutil
from threading import Event, Lock

import pytest

from fastapi import Request
from fastapi.testclient import TestClient

from seat_dashboard.core.config import Settings
from seat_dashboard.core.response_cache import ResponseCache
from seat_dashboard.main import create_app


def test_namespace_gzip_conditional_and_cache_isolation(demo_reader, monkeypatch):
    calls = []
    original = demo_reader._connect
    def connect():
        calls.append(1)
        return original()
    monkeypatch.setattr(demo_reader, "_connect", connect)
    client = TestClient(create_app(reader=demo_reader))
    path = "/api/seats/v1/views/combination?product_code=RB.SHFE"
    plain = client.get(path, headers={"Accept-Encoding": "identity"})
    assert plain.status_code == 200 and len(plain.content) >= 1024
    etag = plain.headers["etag"]
    count = len(calls)
    with client.stream("GET", path, headers={"Accept-Encoding": "gzip"}) as compressed:
        assert compressed.headers["content-encoding"] == "gzip"
        assert gzip.decompress(b"".join(compressed.iter_raw())) == plain.content
    assert len(calls) == count
    assert client.get(path.replace("/api/seats/v1", "/api/v1")).headers["etag"] == etag
    unchanged = client.get(path, headers={"If-None-Match": '"unrelated", ' + etag.removeprefix("W/")})
    assert unchanged.status_code == 304 and unchanged.content == b""
    assert "content-encoding" not in unchanged.headers
    assert "accept-encoding" in unchanged.headers["vary"].lower()
    assert len(calls) == count
    for encoding in ("gzip;q=0", "gzip;q=0, *;q=1", "br", "gzip;q=invalid"):
        assert "content-encoding" not in client.get(path, headers={"Accept-Encoding": encoding}).headers
    for suffix in ("&scope=main", "&limit=1", "&trade_date=2026-07-27"):
        other = client.get(path + suffix, headers={"If-None-Match": etag})
        assert other.status_code == 200 and other.headers["etag"] != etag
    other = client.get(path.replace("RB.SHFE", "CF.CZCE"), headers={"If-None-Match": etag})
    assert other.status_code == 200 and other.headers["etag"] != etag
    preflight = client.options(path, headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "if-none-match"})
    assert preflight.status_code == 200
    assert "/api/seats/v1/views/combination" in client.get("/openapi.json").json()["paths"]


def test_snapshot_pinning_batch_same_day_and_formula_invalidation(demo_reader, monkeypatch):
    client = TestClient(create_app(reader=demo_reader))
    path = "/api/seats/v1/views/combination"
    manifest_path = demo_reader.manifest_path
    original_manifest = json.loads(manifest_path.read_text())
    first = client.get(path)
    for field, value in (("published_at", "2026-07-28T12:00:00+00:00"), ("metric_version", "revision-test"), ("rule_version", "rule-test")):
        manifest = json.loads(manifest_path.read_text())
        manifest[field] = value
        manifest_path.write_text(json.dumps(manifest))
        changed = client.get(path, headers={"If-None-Match": first.headers["etag"]})
        assert changed.status_code == 200
        assert changed.json()["meta"]["data_version"] != first.json()["meta"]["data_version"]
        first = changed
    monkeypatch.setattr(demo_reader, "QUERY_VERSION", "query-revision-test")
    revised = client.get(path, headers={"If-None-Match": first.headers["etag"]})
    assert revised.status_code == 200
    assert revised.json()["meta"]["data_version"] != first.json()["meta"]["data_version"]
    first = revised
    # Switch publication during SQL: response metadata and SQL remain on the pinned snapshot.
    original_view = demo_reader.view
    def switching_view(*args, **kwargs):
        manifest = json.loads(manifest_path.read_text())
        manifest["batch_id"] = original_manifest["batch_id"] + 1
        target = demo_reader.runtime_root / "snapshots/seat_dashboard_b00000002.duckdb"
        shutil.copyfile(demo_reader.runtime_root / manifest["database_path"], target)
        manifest["database_path"] = str(target)
        manifest_path.write_text(json.dumps(manifest))
        monkeypatch.setattr(demo_reader, "view", original_view)
        return original_view(*args, **kwargs)
    monkeypatch.setattr(demo_reader, "view", switching_view)
    pinned = client.get(path + "?limit=1")
    assert pinned.json()["meta"]["batch_id"] == original_manifest["batch_id"]
    updated = client.get(path, headers={"If-None-Match": first.headers["etag"]})
    assert updated.status_code == 200
    assert updated.json()["meta"]["batch_id"] == original_manifest["batch_id"] + 1


@pytest.mark.parametrize("concurrency", [1, 2])
def test_resource_limits_and_singleflight_do_not_block_health(demo_reader, monkeypatch, concurrency):
    connection, _ = demo_reader._connect()
    try:
        assert connection.execute("SELECT current_setting('threads')").fetchone()[0] == 1
        memory = connection.execute("SELECT current_setting('memory_limit')").fetchone()[0].split()
        assert memory[1] == "MiB" and 240 <= float(memory[0]) <= 256
    finally:
        connection.close()
    entered, release = Event(), Event()
    counts = {"calls": 0, "active": 0, "max": 0}
    lock = Lock()
    original = demo_reader.view
    def slow(*args, **kwargs):
        with lock:
            counts["calls"] += 1; counts["active"] += 1
            counts["max"] = max(counts["max"], counts["active"])
        entered.set()
        assert release.wait(5)
        try:
            return original(*args, **kwargs)
        finally:
            with lock:
                counts["active"] -= 1
    monkeypatch.setattr(demo_reader, "view", slow)
    app = create_app(reader=demo_reader, settings=replace(Settings.from_env(), query_concurrency=concurrency))
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=5) as pool:
        pending = [pool.submit(client.get, "/api/seats/v1/views/combination?limit=1") for _ in range(3)]
        assert entered.wait(2)
        other = pool.submit(client.get, "/api/seats/v1/views/term-structure")
        live = pool.submit(client.get, "/health/live")
        try:
            assert live.result(timeout=2).status_code == 200
        finally:
            release.set()
        assert all(future.result().status_code == 200 for future in [*pending, other])
    assert counts["calls"] == 2 and counts["active"] == 0
    assert 1 <= counts["max"] <= concurrency


def test_byte_limit_lru_oversize_small_gzip_and_failure_recovery():
    cache = ResponseCache(1200, 1)
    def request(path, headers=()):
        return Request({"type": "http", "method": "GET", "scheme": "http", "server": ("test", 80), "path": path, "query_string": b"", "headers": headers})
    for index in range(10):
        response = cache.response(request(f"/{index}"), "v1", lambda: b'"' + b"a" * 200 + b'"')
        assert "content-encoding" not in response.headers
        assert cache.bytes <= cache.max_bytes
    assert len(cache.entries) < 10
    cache.response(request("/huge"), "v1", lambda: b"x" * 2000)
    assert not any("/huge" in key for key in cache.entries)
    try:
        cache.response(request("/fail"), "v1", lambda: 1 / 0)
    except ZeroDivisionError:
        pass
    assert not cache.pending
    assert cache.response(request("/fail"), "v1", lambda: b"{}").status_code == 200
