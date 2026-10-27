"""FastAPI app. Run with: uvicorn backend.main:app"""
from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from backend.budget import Budget
from backend.clock import Clock, SystemClock
from backend.config import Settings, get_settings
from backend.household import ensure_household
from backend.models import build_adapters
from backend.storage import Storage


def create_app(settings: Settings | None = None, clock: Clock | None = None, storage: Storage | None = None) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = storage or Storage(settings.db_path)
        db.migrate()
        app.state.settings = settings
        app.state.clock = clock
        app.state.storage = db
        app.state.budget = Budget(db, settings, clock)
        app.state.adapters = build_adapters(settings)
        app.state.household_id = ensure_household(db, settings)
        yield
        if storage is None:
            db.close()

    app = FastAPI(title="Kinwatch", lifespan=lifespan, docs_url=None, redoc_url=None)

    def require_bts_token(request: Request) -> None:
        given = request.headers.get("X-BTS-Token") or request.query_params.get("t") or ""
        expected = settings.bts_token.get_secret_value()
        if not hmac.compare_digest(given.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="bad or missing token")

    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        s: Settings = request.app.state.settings
        return {"ok": True, "mode": s.mode, "use_mocks": s.use_mocks, "demo_mode": s.demo_mode,
                "adapters": "live" if request.app.state.adapters.live else "mock"}

    @app.get("/api/budget")
    async def budget(request: Request) -> dict:
        require_bts_token(request)
        return request.app.state.budget.summary()

    return app


def __getattr__(name: str) -> FastAPI:
    # `uvicorn backend.main:app` builds the app on first lookup, so importing this module
    # (e.g. from tests) never reads the real .env.
    if name == "app":
        return create_app()
    raise AttributeError(name)
