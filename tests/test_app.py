from fastapi.testclient import TestClient

from backend.config import FamilyMember
from backend.main import create_app


def test_app_boots_with_mocks_and_seeds_household(settings, storage, clock):
    settings.family_members_json = [
        FamilyMember(name="Priya", address="910000000001", is_admin=True),
        FamilyMember(name="Rahul", address="910000000002"),
    ]
    app = create_app(settings, clock=clock, storage=storage)
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {
            "ok": True, "mode": "live", "use_mocks": True, "demo_mode": False, "adapters": "mock"}
        hid = app.state.household_id
        assert [m.name for m in storage.members(hid)] == ["Priya", "Rahul"]
    # restarting doesn't duplicate members
    with TestClient(create_app(settings, clock=clock, storage=storage)):
        assert len(storage.members(hid)) == 2


def test_budget_endpoint_needs_the_token(settings, storage, clock):
    with TestClient(create_app(settings, clock=clock, storage=storage)) as client:
        assert client.get("/api/budget").status_code == 401
        assert client.get("/api/budget", params={"t": "wrong"}).status_code == 401
        r = client.get("/api/budget", headers={"X-BTS-Token": settings.bts_token.get_secret_value()})
        assert r.status_code == 200
        assert r.json()["mode"] == "MOCK" and r.json()["total_usd"] == 0.0


def _device_headers(settings) -> dict:
    return {"X-Device-Token": settings.device_token.get_secret_value(), "Content-Type": "image/jpeg"}


def test_phone_page_is_served(settings, storage, clock):
    with TestClient(create_app(settings, clock=clock, storage=storage)) as client:
        r = client.get("/phone")
        assert r.status_code == 200 and "Kinwatch camera" in r.text
        assert r.headers["cache-control"] == "no-store"


def test_frame_upload_needs_the_device_token_and_a_real_image(settings, storage, clock):
    from backend.models.mock_sam import synthetic_frame

    with TestClient(create_app(settings, clock=clock, storage=storage)) as client:
        jpeg = synthetic_frame([])
        assert client.post("/api/frame", content=jpeg).status_code == 401
        assert client.post("/api/frame", content=jpeg, headers={"X-Device-Token": "wrong"}).status_code == 401
        assert client.post("/api/frame", content=b"junk", headers=_device_headers(settings)).status_code == 400

        r = client.post("/api/frame", content=jpeg, headers=_device_headers(settings))
        assert r.status_code == 200
        assert r.json() == {"next_interval_ms": 1500, "command": None, "changed": True, "dark": False,
                            "armed_rules": 0}
        assert client.post("/api/frame", content=jpeg, headers=_device_headers(settings)).json()["changed"] is False
        assert len(client.app.state.frames) == 2
        device = storage.query_one("SELECT last_seen_at, last_frame_shape, token_hash FROM devices")
        assert device["last_frame_shape"] == "640x480" and device["last_seen_at"] == clock.now()
        assert settings.device_token.get_secret_value() not in device["token_hash"]


def test_replay_mode_has_no_camera_routes(tmp_path, storage, clock):
    from backend.config import Settings

    replay = Settings(_env_file=None, mode="replay", data_dir=tmp_path)
    with TestClient(create_app(replay, clock=clock, storage=storage)) as client:
        assert client.get("/phone").status_code == 404
        assert client.post("/api/frame", content=b"x").status_code == 404


def test_live_mode_refuses_default_tokens(tmp_path):
    import pytest

    from backend.config import Settings

    live = Settings(_env_file=None, use_mocks=False, meta_api_key="sk-test", data_dir=tmp_path)
    with pytest.raises(RuntimeError, match="DEVICE_TOKEN"):
        create_app(live)
