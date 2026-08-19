"""The public application: REST API plus the built-in dashboard.

This is the only service exposed to the internet. The trading bot runs as a
Render private service with no public ingress, so nothing outside can reach it
except this API over the private network.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import ConfigError, get_settings
from app.core.supabase import SupabaseError

log = logging.getLogger(__name__)

STATIC_DIR = __import__("pathlib").Path(__file__).parent / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = FastAPI(
        title="Freqtrade Platform",
        description=(
            "Strategy builder, backtesting and wallet verification for a freqtrade "
            "deployment. The trading bot itself is not reachable from the internet."
        ),
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # Default deny: with no CORS_ORIGINS set, only same-origin calls work, which
    # is all the built-in UI needs.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # The UI is self-contained apart from the Supabase JS client.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self' https://*.supabase.co; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.exception_handler(SupabaseError)
    async def handle_supabase_error(_request: Request, exc: SupabaseError):
        # PostgREST's 401/403 usually means RLS refused, not that auth is broken.
        code = 403 if exc.status in (401, 403) else 502
        log.warning("supabase error %s: %s", exc.status, exc.body[:400])
        return JSONResponse(
            status_code=code,
            content={"detail": exc.hint or "the database rejected this request"},
        )

    @app.exception_handler(ConfigError)
    async def handle_config_error(_request: Request, exc: ConfigError):
        log.error("configuration problem: %s", exc)
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    from app.api.routers import (
        accounts, backtests, bots, catalog, health, live, strategies,
    )

    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(strategies.router)
    app.include_router(backtests.router)
    app.include_router(accounts.router)
    app.include_router(bots.router)
    app.include_router(live.router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
