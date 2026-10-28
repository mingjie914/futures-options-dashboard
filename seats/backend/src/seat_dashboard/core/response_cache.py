from __future__ import annotations

import gzip
import hashlib
from collections import OrderedDict
from threading import BoundedSemaphore, Event, Lock
from typing import Callable

from fastapi import Request, Response


API_VERSION = "seats-json-v2"


def _accepts_gzip(value: str) -> bool:
    weights = {}
    for part in value.lower().split(","):
        name, *parameters = part.strip().split(";")
        quality = 1.0
        for parameter in parameters:
            if parameter.strip().startswith("q="):
                try:
                    quality = float(parameter.strip()[2:])
                except ValueError:
                    quality = 0.0
        weights[name] = quality
    return weights.get("gzip", weights.get("*", 0)) > 0


class ResponseCache:
    """Byte-bounded JSON LRU, shared by page/history routes in one API process."""

    def __init__(self, max_bytes: int, concurrency: int):
        if max_bytes < 0 or concurrency not in (1, 2):
            raise ValueError("cache bytes must be nonnegative; query concurrency must be 1 or 2")
        self.max_bytes = max_bytes
        self.bytes = 0
        self.entries = OrderedDict()
        self.pending: dict[str, Event] = {}
        self.lock = Lock()
        self.slots = BoundedSemaphore(concurrency)

    def response(self, request: Request, version: str, build: Callable[[], bytes]) -> Response:
        # Sorting preserves duplicate query values; every filter belongs to the key.
        route = request.url.path.replace("/api/seats/v1/", "/api/v1/", 1)
        key = repr((API_VERSION, version, route, sorted(request.query_params.multi_items())))
        while True:
            with self.lock:
                entry = self.entries.get(key)
                if entry is not None:
                    self.entries.move_to_end(key)
                    break
                event = self.pending.get(key)
                if event is None:
                    event = self.pending[key] = Event()
                    break
            # All callers are synchronous FastAPI routes, so waiting stays in worker threads.
            event.wait()
        if entry is None:
            try:
                with self.slots:
                    body = build()
                    compressed = gzip.compress(body, compresslevel=5, mtime=0) if len(body) >= 1024 else None
                    etag = 'W/"' + hashlib.sha256(key.encode() + body).hexdigest() + '"'
                    size = len(body) + len(compressed or b"") + len(key.encode()) + len(etag) + 256
                    entry = (body, compressed, etag, size)
                with self.lock:
                    if size <= self.max_bytes:
                        while self.bytes + size > self.max_bytes:
                            _, removed = self.entries.popitem(last=False)
                            self.bytes -= removed[3]
                        self.entries[key] = entry
                        self.bytes += size
            finally:
                with self.lock:
                    self.pending.pop(key).set()
        body, compressed, etag, _ = entry
        headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Accept-Encoding", "X-Data-Version": version}
        candidates = (part.strip().removeprefix("W/") for part in request.headers.get("if-none-match", "").split(","))
        if any(candidate in ("*", etag.removeprefix("W/")) for candidate in candidates):
            return Response(status_code=304, headers=headers)
        if compressed is not None and _accepts_gzip(request.headers.get("accept-encoding", "")):
            body = compressed
            headers["Content-Encoding"] = "gzip"
        return Response(body, media_type="application/json", headers=headers)
