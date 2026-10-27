import importlib.util
from pathlib import Path

import httpx
import pytest

from backend.config import Settings

_spec = importlib.util.spec_from_file_location("dev_up", Path(__file__).resolve().parent.parent / "scripts" / "dev_up.py")
dev_up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dev_up)


def test_find_tunnel_url_in_cloudflared_output():
    line = "2026-10-28T14:00:00Z INF |  https://quiet-river-1234.trycloudflare.com                    |"
    assert dev_up.find_tunnel_url(line) == "https://quiet-river-1234.trycloudflare.com"
    assert dev_up.find_tunnel_url("INF Starting tunnel") is None


def test_ensure_tokens_appends_only_missing_ones(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"META_API_KEY = sk-x\nBTS_TOKEN=already-set")  # no trailing newline
    assert dev_up.ensure_tokens(env, Settings(_env_file=env)) is True
    s = Settings(_env_file=env)
    assert s.device_token.get_secret_value() not in ("change-me-device", "")
    assert len(s.device_token.get_secret_value()) >= 30
    assert s.bts_token.get_secret_value() == "already-set"
    assert s.meta_api_key.get_secret_value() == "sk-x"  # earlier lines untouched
    assert dev_up.ensure_tokens(env, s) is False


def _wa_settings(tmp_path, **kw) -> Settings:
    base = dict(_env_file=None, messenger="whatsapp", wa_phone_number_id="555", wa_business_account_id="777",
                wa_access_token="wa-token", wa_app_secret="secret", wa_verify_token="verify-me", wa_app_id="999",
                data_dir=tmp_path)
    return Settings(**{**base, **kw})


async def test_whatsapp_setup_checks_token_sets_webhook_and_subscribes(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"verified_name": "Test Number", "quality_rating": "GREEN"})
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        lines = await dev_up.whatsapp_setup(_wa_settings(tmp_path), "https://x.trycloudflare.com", http)

    assert [(c.method, c.url.path) for c in calls] == [
        ("GET", "/v25.0/555"), ("POST", "/v25.0/999/subscriptions"), ("POST", "/v25.0/777/subscribed_apps")]
    form = dict(httpx.QueryParams(calls[1].content.decode()))
    assert form["callback_url"] == "https://x.trycloudflare.com/webhooks/whatsapp"
    assert form["fields"] == "messages" and form["verify_token"] == "verify-me"
    assert calls[2].headers["authorization"] == "Bearer wa-token"
    assert "Webhook set to https://x.trycloudflare.com/webhooks/whatsapp" in lines
    assert "App subscribed to the WhatsApp Business Account" in lines


async def test_whatsapp_setup_without_app_id_explains_the_manual_step(tmp_path):
    handler = lambda r: httpx.Response(200, json={"success": True, "verified_name": "T"})  # noqa: E731
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        lines = await dev_up.whatsapp_setup(_wa_settings(tmp_path, wa_app_id=None), "https://x.trycloudflare.com", http)
    assert any("paste it by hand" in line for line in lines)


async def test_expired_token_stops_setup(tmp_path):
    handler = lambda r: httpx.Response(401, json={"error": {"code": 190, "message": "Session has expired"}})  # noqa: E731
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(RuntimeError, match="code 190"):
            await dev_up.whatsapp_setup(_wa_settings(tmp_path), "https://x.trycloudflare.com", http)
