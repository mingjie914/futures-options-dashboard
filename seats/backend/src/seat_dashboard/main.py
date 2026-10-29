from __future__ import annotations

import logging
import uuid
from datetime import date
from functools import wraps
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from seat_dashboard.adapters.store.duckdb.reader import DuckDBReader
from seat_dashboard.core.config import Settings
from seat_dashboard.core.errors import InvalidDashboardQuery, SnapshotUnavailable
from seat_dashboard.core.logging import configure_logging
from seat_dashboard.core.response_cache import ResponseCache
from seat_dashboard.ports.dashboard_reader import DashboardReadPort

logger = logging.getLogger(__name__)


def _default_reader(settings: Settings) -> DashboardReadPort:
    if settings.read_store != "duckdb":
        raise RuntimeError(f"Unsupported DASHBOARD_READ_STORE: {settings.read_store}")
    return DuckDBReader(
        settings.runtime_root,
        snapshot_path=settings.snapshot_path,
        backend_label="demo" if settings.demo_mode else "local",
        threads=settings.duckdb_threads,
        memory_mb=settings.duckdb_memory_mb,
    )


def _meta(reader: DashboardReadPort, selected_date: date | None = None) -> dict[str, Any]:
    status = reader.snapshot_status()
    if selected_date is not None:
        status["trade_date"] = selected_date
    return status


def _envelope(reader: DashboardReadPort, data: Any, selected_date: date | None = None) -> dict[str, Any]:
    return {"meta": _meta(reader, selected_date), "data": data}


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, SnapshotUnavailable):
        raise HTTPException(status_code=503, detail={"code": "SNAPSHOT_UNAVAILABLE", "message": str(exc)}) from exc
    if isinstance(exc, (InvalidDashboardQuery, ValueError)):
        raise HTTPException(status_code=400, detail={"code": "INVALID_QUERY", "message": str(exc)}) from exc
    logger.exception("dashboard request failed")
    raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "dashboard request failed"}) from exc


def create_app(reader: DashboardReadPort | None = None, settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings.from_env()
    active_reader = reader or _default_reader(active_settings)
    app = FastAPI(title="期货席位行为监控 API", version="0.1.0")
    app.state.reader = active_reader
    router = APIRouter()
    cache = app.state.response_cache = ResponseCache(active_settings.cache_bytes, active_settings.query_concurrency)

    def cached(handler):
        @wraps(handler)
        def response(*args, **kwargs):
            request = kwargs["request"]
            try:
                with active_reader.read_session() as version:
                    return cache.response(request, version, lambda: JSONResponse(jsonable_encoder(handler(*args, **kwargs))).body)
            except HTTPException:
                raise
            except Exception as exc:
                _raise_http(exc)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(active_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Request-ID", "If-None-Match", "A-IM"],
        expose_headers=["X-Request-ID", "ETag", "X-Data-Version", "IM", "Delta-Base"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def health_ready() -> dict[str, Any]:
        try:
            status = active_reader.snapshot_status()
        except Exception as exc:
            _raise_http(exc)
        if not status.get("complete"):
            raise HTTPException(status_code=503, detail={"code": "NOT_READY", "message": "no complete snapshot"})
        return {"status": "ready", "meta": status}

    @router.get("/meta/status")
    @cached
    def meta_status(request: Request) -> dict[str, Any]:
        try:
            return _envelope(active_reader, active_reader.snapshot_status())
        except Exception as exc:
            _raise_http(exc)

    @router.get("/meta/options")
    @cached
    def meta_options(request: Request) -> dict[str, Any]:
        try:
            return _envelope(active_reader, active_reader.options())
        except Exception as exc:
            _raise_http(exc)

    def view_route(
        view_name: str,
        trade_date: date | None,
        product_code: str | None,
        scope: str,
        limit: int,
        min_leg_position: int = 300,
        detail: str = "all",
    ) -> dict[str, Any]:
        try:
            result = active_reader.view(
                view_name,
                trade_date=trade_date,
                product_code=product_code,
                scope=scope,
                limit=limit,
                min_leg_position=min_leg_position,
                detail=detail,
            )
            return _envelope(active_reader, result, result.get("selected_date", trade_date))
        except Exception as exc:
            _raise_http(exc)

    @router.get("/views/combination")
    @cached
    def combination_view(
        request: Request,
        trade_date: date | None = None,
        product_code: str | None = None,
        scope: str = "all",
        limit: int = Query(default=20, ge=1, le=100),
        detail: str = Query(default="all", pattern="^(all|groups|positions)$"),
    ) -> dict[str, Any]:
        return view_route("combination", trade_date, product_code, scope, limit, detail=detail)

    @router.get("/views/market-signals")
    @cached
    def market_signals_view(
        request: Request,
        trade_date: date | None = None,
        product_code: str | None = None,
        scope: str = "all",
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return view_route("market-signals", trade_date, product_code, scope, limit)

    @router.get("/views/term-structure")
    @cached
    def term_structure_view(
        request: Request,
        trade_date: date | None = None,
        product_code: str | None = None,
        scope: str = "all",
        limit: int = Query(default=50, ge=1, le=100),
        min_leg_position: int = Query(default=300, ge=0, le=10_000_000),
        detail: str = Query(default="all", pattern="^(all|contracts)$"),
    ) -> dict[str, Any]:
        return view_route("term-structure", trade_date, product_code, scope, limit, min_leg_position, detail)

    @router.get("/views/validation")
    @cached
    def validation_view(
        request: Request,
        trade_date: date | None = None,
        product_code: str | None = None,
        scope: str = "all",
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> dict[str, Any]:
        return view_route("validation", trade_date, product_code, scope, limit)

    @router.get("/seats/{seat_id}/history")
    @cached
    def seat_history_view(
        request: Request,
        seat_id: str,
        product_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = Query(default=500, ge=1, le=10_000),
    ) -> dict[str, Any]:
        try:
            rows = active_reader.history(
                "seat",
                seat_id,
                product_code=product_code,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
            return _envelope(active_reader, {"entity": "seat", "entity_id": seat_id, "rows": rows})
        except Exception as exc:
            _raise_http(exc)

    @router.get("/products/{product_code}/history")
    @cached
    def product_history_view(
        request: Request,
        product_code: str,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = Query(default=500, ge=1, le=10_000),
    ) -> dict[str, Any]:
        try:
            rows = active_reader.history(
                "product",
                product_code,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
            return _envelope(active_reader, {"entity": "product", "entity_id": product_code, "rows": rows})
        except Exception as exc:
            _raise_http(exc)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        request_id = request.headers.get("X-Request-ID", "")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail, "request_id": request_id},
        )

    app.include_router(router, prefix="/api/seats/v1")
    app.include_router(router, prefix="/api/v1", include_in_schema=False)
    return app


settings = Settings.from_env()
app = create_app(settings=settings)


def run() -> None:
    import uvicorn

    configure_logging()
    uvicorn.run("seat_dashboard.main:app", host=settings.api_host, port=settings.api_port)
