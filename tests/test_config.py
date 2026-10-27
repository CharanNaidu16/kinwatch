import httpx
import pytest
from pydantic import ValidationError

from backend.config import Settings
from tests.conftest import NetworkBlocked


def test_network_is_blocked_in_tests():
    with pytest.raises((NetworkBlocked, httpx.ConnectError)):
        httpx.get("https://api.meta.ai/v1/models", timeout=2)


def test_defaults_are_safe():
    s = Settings(_env_file=None)
    assert s.use_mocks is True
    assert s.mode == "live"
    assert s.effective_time_scale == 1.0
    assert s.effective_claim_timeout_sec == 120


def test_env_file_tolerates_spaces_around_equals(tmp_path):
    env = tmp_path / ".env"
    env.write_text("META_API_KEY = sk-test-123\nUSE_MOCKS = false\n", encoding="utf-8")
    s = Settings(_env_file=env)
    assert s.meta_api_key.get_secret_value() == "sk-test-123"
    assert s.use_mocks is False


def test_secrets_never_appear_in_repr():
    s = Settings(_env_file=None, meta_api_key="sk-secret-value", wa_access_token="EAAG-secret")
    assert "sk-secret-value" not in repr(s)
    assert "EAAG-secret" not in repr(s)


def test_replay_mode_refuses_a_meta_key():
    with pytest.raises(ValidationError, match="MODE=replay refuses"):
        Settings(_env_file=None, mode="replay", meta_api_key="sk-anything")
    assert Settings(_env_file=None, mode="replay").mode == "replay"


def test_demo_mode_scales_time_and_claims():
    s = Settings(_env_file=None, demo_mode=True, time_scale=20)
    assert s.effective_time_scale == 20
    assert s.effective_claim_timeout_sec == 30


def test_family_members_json(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        'FAMILY_MEMBERS_JSON=[{"name":"Priya","address":"910000000001","aliases":["प्रिया"],"is_admin":true},'
        '{"name":"Rahul","address":"910000000002"}]\n',
        encoding="utf-8",
    )
    s = Settings(_env_file=env)
    assert [m.name for m in s.family_members_json] == ["Priya", "Rahul"]
    assert s.family_members_json[0].aliases == ["प्रिया"]
    assert s.family_members_json[1].channel == "whatsapp"
