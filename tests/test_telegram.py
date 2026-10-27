import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import FamilyMember, Settings
from backend.household import ensure_household
from backend.main import create_app
from backend.messaging.base import Button, SendError, WebhookRejected
from backend.messaging.telegram import TelegramMessenger, callback_query_id, parse_update, webhook_secret
from backend.schemas import Member

TOKEN = "123456:ABCdefGhIJKlmNoPQRsTUVwxyZ0123456789"
CHAT = "5550001"
ME = Member(id=1, household_id=1, name="Charan", channel="telegram", address=CHAT)
HINDI_RULE = "पापा अगर रात को उठें और दस मिनट में वापस न आएँ तो मुझे बताना"


def _settings(tmp_path, **kw) -> Settings:
    base = dict(_env_file=None, use_mocks=True, messenger="telegram", telegram_bot_token=TOKEN,
                device_token="d" * 32, bts_token="b" * 32, data_dir=tmp_path,
                family_members_json=[FamilyMember(name="Charan", address=CHAT, channel="telegram", is_admin=True)])
    return Settings(**{**base, **kw})


def _text(chat=CHAT, body="hello", message_id=1, chat_type="private") -> dict:
    return {"update_id": message_id, "message": {"message_id": message_id, "date": 1793300000,
                                                 "chat": {"id": int(chat), "type": chat_type},
                                                 "from": {"id": int(chat)}, "text": body}}


def _tap(data: str, cq_id="777") -> dict:
    return {"update_id": 9, "callback_query": {"id": cq_id, "from": {"id": int(CHAT)}, "data": data,
                                               "message": {"message_id": 5, "date": 1, "chat": {"id": int(CHAT)}}}}


def test_parse_text_voice_button_and_ignore_groups():
    [t] = parse_update(_text(body="hi"))
    assert (t.kind, t.text, t.sender_address, t.provider_msg_id) == ("text", "hi", CHAT, f"tg.{CHAT}.1")
    voice = _text()
    del voice["message"]["text"]
    voice["message"]["voice"] = {"file_id": "F1", "mime_type": "audio/ogg", "duration": 9}
    [v] = parse_update(voice)
    assert (v.kind, v.media_ref) == ("voice", "F1")
    [b] = parse_update(_tap("yes:3"))
    assert (b.kind, b.button_payload, b.sender_address) == ("button", "yes:3", CHAT)
    assert callback_query_id(b) == "777" and callback_query_id(t) is None
    assert parse_update(_text(chat_type="group")) == []


def test_secret_token_is_checked(tmp_path):
    s = _settings(tmp_path)
    m = TelegramMessenger(s)
    body = json.dumps(_text()).encode()
    with pytest.raises(WebhookRejected):
        m.verify_and_parse({"x-telegram-bot-api-secret-token": "nope"}, body)
    inbound, statuses = m.verify_and_parse({"x-telegram-bot-api-secret-token": webhook_secret(s)}, body)
    assert len(inbound) == 1 and statuses == []
    assert webhook_secret(s) != s.bts_token.get_secret_value()


def _messenger(tmp_path, handler) -> TelegramMessenger:
    return TelegramMessenger(_settings(tmp_path), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_send_text_with_inline_buttons(tmp_path):
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    ref = await _messenger(tmp_path, handler).send_text(ME, "सही है?", [Button("yes:1", "हाँ ✓"), Button("no:1", "नहीं ✗")])
    assert ref.provider_msg_id == f"tg.{CHAT}.42"
    path, body = seen[0]
    assert path == f"/bot{TOKEN}/sendMessage"
    assert body == {"chat_id": CHAT, "text": "सही है?", "reply_markup": {"inline_keyboard": [[
        {"text": "हाँ ✓", "callback_data": "yes:1"}, {"text": "नहीं ✗", "callback_data": "no:1"}]]}}


async def test_send_photo_is_multipart(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    await _messenger(tmp_path, handler).send_image(ME, b"\xff\xd8jpeg", "this bed", [Button("yes:2", "Yes")])
    req = seen[0]
    assert req.url.path.endswith("/sendPhoto")
    assert b'name="photo"; filename="kinwatch.jpg"' in req.content and b'"callback_data": "yes:2"' in req.content


async def test_errors_and_download(tmp_path):
    blocked = _messenger(tmp_path, lambda r: httpx.Response(403, json={
        "ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}))
    with pytest.raises(SendError, match="blocked"):
        await blocked.send_text(ME, "hi")

    def handler(request):
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/file_1.oga"}})
        assert request.url.path == f"/file/bot{TOKEN}/voice/file_1.oga"
        return httpx.Response(200, content=b"OggS", headers={"content-type": "audio/ogg"})

    assert await _messenger(tmp_path, handler).download_media("F1") == (b"OggS", "audio/ogg")


# --- through the app ---------------------------------------------------------------------------

@pytest.fixture
def tg_app(tmp_path, storage, clock):
    sent: list[tuple[str, dict]] = []

    def handler(request):
        payload = json.loads(request.content) if request.headers.get("content-type", "").startswith("application/json") else {}
        sent.append((request.url.path.rsplit("/", 1)[-1], payload))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    settings = _settings(tmp_path)
    app = create_app(settings, clock=clock, storage=storage)
    with TestClient(app) as client:
        fake = TelegramMessenger(settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        app.state.messenger = app.state.router.messenger = fake
        yield client, sent, webhook_secret(settings)


def _post(client, secret, update):
    return client.post("/webhooks/telegram", content=json.dumps(update),
                       headers={"X-Telegram-Bot-Api-Secret-Token": secret, "Content-Type": "application/json"})


def test_rule_flow_over_telegram(tg_app, storage):
    from backend.models.mock_sam import PALETTE, synthetic_frame

    client, sent, secret = tg_app
    frame = synthetic_frame([(PALETTE["bed"][0], (300, 250, 620, 420))])
    client.post("/api/frame", content=frame, headers={"X-Device-Token": "d" * 32})
    assert _post(client, "wrong", _text(body=HINDI_RULE)).status_code == 401
    assert _post(client, secret, _text(body=HINDI_RULE)).json() == {"ok": True, "accepted": 1}
    assert sent[-1][0] == "sendPhoto"  # the confirmation photo with the bed outlined
    rule_id = storage.query_one("SELECT id FROM rules")["id"]

    _post(client, secret, _tap(f"yes:{rule_id}"))
    assert ("answerCallbackQuery", {"callback_query_id": "777"}) in sent
    assert sent[-1] == ("sendMessage", {"chat_id": CHAT, "text": "✅ नियम चालू।"})  # ack runs first
    assert storage.query_one("SELECT status FROM rules")["status"] == "armed"


def test_unknown_user_is_told_their_id_once(tg_app, storage):
    client, sent, secret = tg_app
    for i in (1, 2):
        assert _post(client, secret, _text(chat="999", body="hi", message_id=i)).json()["accepted"] == 0
    intros = [b for m, b in sent if m == "sendMessage"]
    assert len(intros) == 1 and "999" in intros[0]["text"] and intros[0]["chat_id"] == "999"
    assert storage.query_one("SELECT COUNT(*) AS n FROM rules")["n"] == 0


def test_switching_a_members_channel_updates_their_row(tmp_path, storage):
    s = _settings(tmp_path, family_members_json=[FamilyMember(name="Charan", address="919000000000", is_admin=True)])
    hid = ensure_household(storage, s)
    [before] = storage.members(hid)
    s2 = _settings(tmp_path)  # same name, now on Telegram
    ensure_household(storage, s2)
    [after] = storage.members(hid)
    assert after.id == before.id and (after.channel, after.address) == ("telegram", CHAT)


async def test_dev_up_registers_the_telegram_webhook(tmp_path):
    spec = importlib.util.spec_from_file_location("dev_up", Path(__file__).resolve().parent.parent / "scripts" / "dev_up.py")
    dev_up = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dev_up)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "kinwatch_family_bot"}})
        return httpx.Response(200, json={"ok": True, "result": True})

    s = _settings(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        lines = await dev_up.telegram_setup(s, "https://x.trycloudflare.com", http)
    body = json.loads(calls[1].content)
    assert body["url"] == "https://x.trycloudflare.com/webhooks/telegram"
    assert body["secret_token"] == webhook_secret(s) and body["allowed_updates"] == ["message", "callback_query"]
    assert lines[0].startswith("Telegram bot OK: @kinwatch_family_bot")
