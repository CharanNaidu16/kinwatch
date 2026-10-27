"""FastAPI app. Run with: uvicorn backend.main:app"""
from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from backend.budget import Budget
from backend.clock import Clock, SystemClock
from backend.config import Settings, get_settings
from backend.household import ensure_device, ensure_household
from backend.messaging import build_messenger
from backend.messaging.base import Inbound, WebhookRejected
from backend.models import build_adapters
from backend.router import Router
from backend.storage import Storage
from backend.vision import ChangeDetector, FrameBuffer, FrameError, load_frame

PHONE_PAGE = Path(__file__).resolve().parent.parent / "phone" / "index.html"
DEFAULT_TOKENS = {"change-me-device", "change-me-bts"}


def _configure_logging() -> None:
    """Kinwatch's own log lines (statuses, failures) at INFO; uvicorn configures only its own loggers."""
    log = logging.getLogger("kinwatch")
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)


def _token_ok(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


def create_app(settings: Settings | None = None, clock: Clock | None = None, storage: Storage | None = None) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()
    _configure_logging()
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
        app.state.messenger = build_messenger(settings)
        app.state.router = Router(storage=db, settings=settings, clock=clock, adapters=app.state.adapters,
                                  messenger=app.state.messenger, household_id=app.state.household_id)
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

    def accept_inbound(request: Request, inbound: list[Inbound], background: BackgroundTasks) -> int:
        """Store each new message (webhook retries are deduped) and process it after the response."""
        state = request.app.state
        now = state.clock.now()
        accepted = 0
        for msg in inbound:
            member = state.storage.member_by_address(msg.channel, msg.sender_address)
            cur = state.storage.execute(
                "INSERT OR IGNORE INTO inbound_messages (provider_msg_id, channel, member_id, kind, body, received_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (msg.provider_msg_id, msg.channel, member.id if member else None, msg.kind,
                 msg.text or msg.button_payload, now))
            if cur.rowcount == 0 or member is None:
                continue  # duplicate delivery, or not a household member
            state.storage.execute("UPDATE members SET last_inbound_at = ? WHERE id = ?", (now, member.id))
            background.add_task(state.router.handle, msg)
            accepted += 1
        return accepted

    if settings.mode == "live" and settings.messenger == "whatsapp":
        @app.get("/webhooks/whatsapp", include_in_schema=False)
        async def whatsapp_verify(request: Request) -> PlainTextResponse:
            q = request.query_params
            expected = settings.wa_verify_token.get_secret_value() if settings.wa_verify_token else ""
            if q.get("hub.mode") == "subscribe" and expected and _token_ok(q.get("hub.verify_token", ""), expected):
                return PlainTextResponse(q.get("hub.challenge", ""))
            raise HTTPException(status_code=403, detail="verification failed")

        @app.post("/webhooks/whatsapp", include_in_schema=False)
        async def whatsapp_webhook(request: Request, background: BackgroundTasks) -> dict:
            state = request.app.state
            try:
                inbound, statuses = state.messenger.verify_and_parse(request.headers, await request.body())
            except WebhookRejected as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            for update in statuses:
                state.router.apply_status(update)
            return {"ok": True, "accepted": accept_inbound(request, inbound, background)}

    if settings.mode == "live" and settings.messenger == "telegram":
        @app.post("/webhooks/telegram", include_in_schema=False)
        async def telegram_webhook(request: Request, background: BackgroundTasks) -> dict:
            from backend.messaging.telegram import callback_query_id

            state = request.app.state
            try:
                inbound, _ = state.messenger.verify_and_parse(request.headers, await request.body())
            except WebhookRejected as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            for msg in inbound:
                if cq := callback_query_id(msg):
                    background.add_task(state.messenger.acknowledge, cq)
                if state.storage.member_by_address(msg.channel, msg.sender_address) is None and msg.kind == "text":
                    # Telegram ids aren't something people know; tell a new user theirs so the admin can add it.
                    background.add_task(state.router.introduce, msg)
            return {"ok": True, "accepted": accept_inbound(request, inbound, background)}

    if settings.mode == "live" and settings.messenger == "mock":
        @app.post("/dev/inbound", include_in_schema=False)
        async def dev_inbound(request: Request, background: BackgroundTasks) -> dict:
            """Simulated chat messages in WhatsApp's webhook JSON shape (development and tests only)."""
            require_bts_token(request)
            state = request.app.state
            try:
                inbound, statuses = state.messenger.verify_and_parse(request.headers, await request.body())
            except WebhookRejected as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            for update in statuses:
                state.router.apply_status(update)
            return {"ok": True, "accepted": accept_inbound(request, inbound, background)}

    return app


def __getattr__(name: str) -> FastAPI:
    # `uvicorn backend.main:app` builds the app on first lookup, so importing this module
    # (e.g. from tests) never reads the real .env.
    if name == "app":
        return create_app()
    raise AttributeError(name)
