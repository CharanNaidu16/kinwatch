"""Behind-the-scenes page: access, what it shows, and what it must never show."""
import json

from tests import wa
from tests.test_router import HINDI_RULE, PRIYA, RAHUL, app, camera, client, post  # noqa: F401 (fixtures)


def token(client) -> str:
    return client.app.state.settings.bts_token.get_secret_value()


def test_page_and_data_need_the_token(client):
    assert client.get("/bts").status_code == 401
    assert client.get("/api/bts/state").status_code == 401
    assert client.get("/api/bts/frame.jpg").status_code == 401
    assert client.get("/bts?t=wrong", follow_redirects=False).status_code == 401


def test_token_link_becomes_a_cookie_and_leaves_the_url(client):
    r = client.get(f"/bts?t={token(client)}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/bts"
    assert "kinwatch_bts" in r.headers["set-cookie"] and "httponly" in r.headers["set-cookie"].lower()
    page = client.get("/bts")  # the client kept the cookie
    assert page.status_code == 200 and "behind the scenes" in page.text
    assert client.get("/api/bts/state").status_code == 200


def test_state_shows_the_rule_from_sentence_to_engine(client):
    camera(client)
    post(client, wa.text(PRIYA, HINDI_RULE, "m1"))
    rule_id = client.app.state.storage.query_one("SELECT id FROM rules")["id"]
    post(client, wa.button(PRIYA, f"yes:{rule_id}", msg_id="m2"))
    s = client.get("/api/bts/state", headers={"X-BTS-Token": token(client)}).json()
    [rule] = s["rules"]
    assert rule["status"] == "armed" and rule["original_text"] == HINDI_RULE
    assert rule["rule_json"]["condition"] == "ABSENT_FROM_ZONE" and rule["rule_json"]["notify"] == ["Priya", "Rahul"]
    assert rule["spark_output"]["zone_object"] == "bed" and rule["sam_concept"] == "bed"
    assert rule["engine"]["phase"] in {"OUT_OF_WINDOW", "WAITING_BASELINE", "WATCHING"}
    assert s["family"] == ["Priya", "Rahul"] and s["camera"]["connected"]
    assert s["mode"]["adapters"] == "mock"
    kinds = [t["kind"] for t in s["timeline"]]
    assert "rule_armed" in kinds and "Muse Spark" in s["timeline"][kinds.index("rule_parsed")]["models"]
    assert {"total_usd", "by_purpose", "caps"} <= set(s["budget"])


def test_state_never_contains_addresses_or_tokens(client):
    camera(client)
    post(client, wa.text(PRIYA, HINDI_RULE, "m1"))
    raw = client.get("/api/bts/state", headers={"X-BTS-Token": token(client)}).text
    settings = client.app.state.settings
    for secret in (PRIYA, RAHUL, token(client), settings.device_token.get_secret_value()):
        assert secret not in raw
    json.loads(raw)


def test_frame_is_the_latest_camera_picture(client):
    headers = {"X-BTS-Token": token(client)}
    assert client.get("/api/bts/frame.jpg", headers=headers).status_code == 204  # no camera yet
    camera(client)
    r = client.get("/api/bts/frame.jpg", headers=headers)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == bytes([0xFF, 0xD8])
