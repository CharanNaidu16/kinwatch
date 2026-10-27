import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import FamilyMember, Settings
from backend.main import create_app
from backend.messaging.base import Button, SendError, WebhookRejected
from backend.messaging.whatsapp import WhatsAppMessenger, parse_webhook, signature_ok
from backend.schemas import Member
from tests import wa

SECRET = "app-secret"
PRIYA = Member(id=1, household_id=1, name="Priya", address="910000000001")


def _settings(tmp_path, **kw) -> Settings:
    base = dict(_env_file=None, use_mocks=True, messenger="whatsapp", wa_phone_number_id="555",
                wa_access_token="wa-token", wa_app_secret=SECRET, wa_verify_token="verify-me",
                device_token="d" * 32, bts_token="b" * 32, data_dir=tmp_path)
    return Settings(**{**base, **kw})


def test_signature_check():
    body, headers = wa.signed(SECRET, wa.envelope())
    assert signature_ok(SECRET, body, headers["X-Hub-Signature-256"])
    assert not signature_ok(SECRET, body + b" ", headers["X-Hub-Signature-256"])
    assert not signature_ok("other", body, headers["X-Hub-Signature-256"])
    assert not signature_ok(SECRET, body, None)


def test_parse_every_inbound_kind_and_status():
    payload = wa.envelope(
        messages=[wa.text("911", "hello", "m1"), wa.voice("911", "media9", "m2"), wa.button("911", "yes:4", msg_id="m3"),
                  {"from": "911", "id": "m4", "timestamp": "1", "type": "sticker"}],
        statuses=[wa.status("out1", "delivered"), wa.status("out2", "failed", code=131026)])
    inbound, statuses = parse_webhook(payload)
    assert [(i.kind, i.text, i.media_ref, i.button_payload) for i in inbound] == [
        ("text", "hello", None, None), ("voice", None, "media9", None), ("button", "Yes", None, "yes:4"),
        ("other", None, None, None)]
    assert inbound[1].mime == "audio/ogg; codecs=opus"
    assert [(s.provider_msg_id, s.status, s.error_code) for s in statuses] == [
        ("out1", "delivered", None), ("out2", "failed", 131026)]
    assert "911" not in repr(inbound[0])  # no phone numbers in logs


def _messenger(tmp_path, handler) -> WhatsAppMessenger:
    return WhatsAppMessenger(_settings(tmp_path), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_send_text_and_buttons(tmp_path):
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content), request.headers["authorization"]))
        return httpx.Response(200, json={"messaging_product": "whatsapp", "contacts": [{"wa_id": "910000000001"}],
                                         "messages": [{"id": f"wamid.out{len(seen)}"}]})

    m = _messenger(tmp_path, handler)
    assert (await m.send_text(PRIYA, "hi")).provider_msg_id == "wamid.out1"
    await m.send_text(PRIYA, "Correct?", [Button("yes:1", "Yes ✓"), Button("no:1", "A very long button title here")])
    path, body, auth = seen[0]
    assert path == "/v25.0/555/messages" and auth == "Bearer wa-token"
    assert body == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "910000000001",
                    "type": "text", "text": {"body": "hi", "preview_url": False}}
    interactive = seen[1][1]["interactive"]
    assert interactive["type"] == "button" and interactive["body"] == {"text": "Correct?"}
    assert [b["reply"] for b in interactive["action"]["buttons"]] == [
        {"id": "yes:1", "title": "Yes ✓"}, {"id": "no:1", "title": "A very long button t"}]


async def test_send_image_uploads_then_sends_by_id(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "media42"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.img"}]})

    m = _messenger(tmp_path, handler)
    ref = await m.send_image(PRIYA, b"\xff\xd8jpeg", "this bed", [Button("yes:2", "Yes")])
    assert ref.provider_msg_id == "wamid.img"
    assert seen[0].url.path == "/v25.0/555/media" and b'name="file"; filename="kinwatch.jpg"' in seen[0].content
    body = json.loads(seen[1].content)["interactive"]
    assert body["header"] == {"type": "image", "image": {"id": "media42"}}


async def test_send_error_carries_the_graph_code(tmp_path):
    m = _messenger(tmp_path, lambda r: httpx.Response(400, json={"error": {"message": "Re-engagement message",
                                                                            "code": 131047}}))
    with pytest.raises(SendError) as err:
        await m.send_text(PRIYA, "hi")
    assert err.value.code == 131047 and err.value.status_code == 400


async def test_download_media_two_steps_with_auth(tmp_path):
    def handler(request):
        assert request.headers["authorization"] == "Bearer wa-token"
        if request.url.path == "/v25.0/media9":
            return httpx.Response(200, json={"url": "https://lookaside.example/abc", "mime_type": "audio/ogg"})
        return httpx.Response(200, content=b"OggS...")

    assert await _messenger(tmp_path, handler).download_media("media9") == (b"OggS...", "audio/ogg")


def test_verify_and_parse_rejects_bad_signature(tmp_path):
    m = _messenger(tmp_path, lambda r: httpx.Response(500))
    body, headers = wa.signed("wrong-secret", wa.envelope())
    with pytest.raises(WebhookRejected):
        m.verify_and_parse({k.lower(): v for k, v in headers.items()}, body)


def test_whatsapp_needs_its_settings(tmp_path):
    with pytest.raises(RuntimeError, match="WA_ACCESS_TOKEN"):
        WhatsAppMessenger(_settings(tmp_path, wa_access_token=None))


# --- webhook routes ----------------------------------------------------------------------------

def test_webhook_verification_handshake(tmp_path, storage, clock):
    with TestClient(create_app(_settings(tmp_path), clock=clock, storage=storage)) as client:
        ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                                                      "hub.challenge": "12345"})
        assert ok.status_code == 200 and ok.text == "12345"
        bad = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                       "hub.challenge": "12345"})
        assert bad.status_code == 403


def test_webhook_rejects_unsigned_posts_and_stores_statuses(tmp_path, storage, clock):
    settings = _settings(tmp_path, family_members_json=[FamilyMember(name="Priya", address="910000000001")])
    with TestClient(create_app(settings, clock=clock, storage=storage)) as client:
        body, headers = wa.signed(SECRET, wa.envelope(statuses=[wa.status("wamid.x", "delivered")]))
        assert client.post("/webhooks/whatsapp", content=body,
                           headers={"Content-Type": "application/json"}).status_code == 401
        member_id = storage.members(client.app.state.household_id)[0].id
        storage.execute("INSERT INTO deliveries (member_id, kind, provider_msg_id, sent_at) VALUES (?, 'info', 'wamid.x', 0)",
                        (member_id,))
        assert client.post("/webhooks/whatsapp", content=body, headers=headers).json() == {"ok": True, "accepted": 0}
        assert storage.query_one("SELECT status FROM deliveries")["status"] == "delivered"
        # a late "sent" never moves it backwards; "failed" is recorded with its code and on the timeline
        for update in (wa.status("wamid.x", "sent"), wa.status("wamid.x", "failed", code=131026)):
            body, headers = wa.signed(SECRET, wa.envelope(statuses=[update]))
            client.post("/webhooks/whatsapp", content=body, headers=headers)
        row = storage.query_one("SELECT status, error FROM deliveries")
        assert row["status"] == "failed" and row["error"].startswith("131026")
        assert storage.timeline(client.app.state.household_id)[0]["kind"] == "delivery_failed"


def test_mock_messenger_has_no_whatsapp_webhook(settings, storage, clock):
    with TestClient(create_app(settings, clock=clock, storage=storage)) as client:
        assert client.post("/webhooks/whatsapp", content=b"{}").status_code == 404
