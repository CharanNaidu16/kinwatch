"""FastAPI app. Run with: uvicorn backend.main:app"""
from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from backend.budget import Budget
from backend.clock import Clock, SystemClock
from backend.config import Settings, get_settings
from backend.household import ensure_device, ensure_household
from backend.models import build_adapters
from backend.storage import Storage
from backend.vision import ChangeDetector, FrameBuffer, FrameError, load_frame

PHONE_PAGE = Path(__file__).resolve().parent.parent / "phone" / "index.html"
DEFAULT_TOKENS = {"change-me-device", "change-me-bts"}


def _token_ok(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


def create_app(settings: Settings | None = None, clock: Clock | None = None, storage: Storage | None = None) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()
    if not settings.use_mocks and settings.mode == "live" and {
            settings.device_token.get_secret_value(), settings.bts_token.get_secret_value()} & DEFAULT_TOKENS:
        # The live app is reachable through a public tunnel; guessable tokens would expose the camera.
        raise RuntimeError("set DEVICE_TOKEN and BTS_TOKEN in .env to long random values before running live")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = storage or Storage(settings.db_path)
        db.migrate()
        app.state.settings = settings
        app.state.clock = clock
        app.state.storage = db
        app.state.budget = Budget(db, settings, clock)
        app.state.adapters = build_adapters(settings, app.state.budget)
        app.state.household_id = ensure_household(db, settings)
        app.state.device_id = ensure_device(db, app.state.household_id, settings)
        app.state.frames = FrameBuffer()
        app.state.changes = ChangeDetector()
        yield
        if storage is None:
            db.close()

    app = FastAPI(title="Kinwatch", lifespan=lifespan, docs_url=None, redoc_url=None)

    def require_bts_token(request: Request) -> None:
        given = request.headers.get("X-BTS-Token") or request.query_params.get("t") or ""
        if not _token_ok(given, settings.bts_token.get_secret_value()):
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

    if settings.mode == "live":  # replay deployments have no camera (PLAN.md §3.10)
        @app.get("/phone", include_in_schema=False)
        async def phone_page() -> FileResponse:
            return FileResponse(PHONE_PAGE, media_type="text/html", headers={"Cache-Control": "no-store"})

        @app.post("/api/frame")
        async def upload_frame(request: Request) -> dict:
            if not _token_ok(request.headers.get("X-Device-Token", ""), settings.device_token.get_secret_value()):
                raise HTTPException(status_code=401, detail="bad or missing device token")
            state = request.app.state
            now = state.clock.now()
            try:
                captured = float(request.headers.get("X-Captured-At", "")) / 1000
            except ValueError:
                captured = None
            try:
                frame = load_frame(await request.body(), now, captured)
            except FrameError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            state.frames.add(frame)
            changed = state.changes.changed(frame)
            if changed:  # stand-in until the vision loop (Thu 29 Oct) marks the frames it sends to SAM
                state.changes.mark_analysed(frame)
            state.storage.execute("UPDATE devices SET last_seen_at = ?, last_frame_shape = ? WHERE id = ?",
                                  (now, frame.shape, state.device_id))
            armed = state.storage.query_one(
                "SELECT COUNT(*) AS n FROM rules WHERE household_id = ? AND status = 'armed'", (state.household_id,))
            return {"next_interval_ms": state.settings.frame_interval_ms, "command": None, "changed": changed,
                    "dark": frame.dark, "armed_rules": armed["n"] if armed else 0}

    return app


def __getattr__(name: str) -> FastAPI:
    # `uvicorn backend.main:app` builds the app on first lookup, so importing this module
    # (e.g. from tests) never reads the real .env.
    if name == "app":
        return create_app()
    raise AttributeError(name)
