"""Settings loaded from .env (and the process environment).

Secrets are SecretStr so they never show up in reprs or logs.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FamilyMember(BaseModel):
    """One household member as configured in FAMILY_MEMBERS_JSON (list order = escalation order)."""

    name: str
    address: str  # WhatsApp wa_id (digits with country code); stays in .env and the database only
    channel: Literal["whatsapp", "telegram", "mock"] = "whatsapp"
    aliases: list[str] = Field(default_factory=list)
    is_admin: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Literal["live", "replay"] = "live"
    use_mocks: bool = True
    demo_mode: bool = False
    time_scale: float = Field(20.0, gt=0)

    meta_api_key: SecretStr | None = None
    meta_base_url: str = "https://api.meta.ai/v1"
    spark_model: str = "muse-spark-1.3"
    sam_model: str = "sam-3.1"
    asr_model: str = "muse-voice-transcribe-1.0"
    record_fixtures: bool = False  # save real responses under fixtures/recorded/ (names scrubbed)

    messenger: Literal["mock", "whatsapp", "telegram"] = "mock"
    telegram_bot_token: SecretStr | None = None
    wa_graph_version: str = "v25.0"
    wa_app_id: str | None = None  # lets scripts/dev_up.py update the webhook URL on each run
    wa_phone_number_id: str | None = None
    wa_business_account_id: str | None = None
    wa_access_token: SecretStr | None = None
    wa_app_secret: SecretStr | None = None
    wa_verify_token: SecretStr | None = None
    wa_test_recipient: str | None = None

    family_members_json: list[FamilyMember] = Field(default_factory=list)
    household_language: Literal["hi", "kn", "en"] = "hi"
    # What SAM looks for when a rule names a zone, e.g. {"bed": "sofa"} when Papa sleeps on a sofa.
    # Messages keep the family's word ("bed"); only the camera concept changes.
    zone_concepts_json: dict[str, str] = Field(default_factory=dict)

    device_token: SecretStr = SecretStr("change-me-device")
    bts_token: SecretStr = SecretStr("change-me-bts")

    budget_total_usd: float = 20.0
    budget_daily_usd: float = 4.0
    alert_reserve_usd: float = 1.0
    sam_max_per_hour: int = 300
    sam_max_per_min: int = 20
    spark_max_per_hour: int = 120
    asr_max_sec_per_day: int = 1800

    frame_interval_ms: int = Field(1500, ge=500, le=10000)  # how often the phone page uploads a frame

    claim_timeout_sec: int = 120
    demo_claim_timeout_sec: int = 30

    data_dir: Path = Path("data")

    @model_validator(mode="after")
    def _replay_never_holds_a_key(self) -> Settings:
        # The judges' replay deployment must not be able to reach the Meta API, even by mistake.
        if self.mode == "replay" and self.meta_api_key and self.meta_api_key.get_secret_value():
            raise ValueError("MODE=replay refuses to start while META_API_KEY is set; remove it from this deployment")
        return self

    @property
    def data_path(self) -> Path:
        return self.data_dir if self.data_dir.is_absolute() else PROJECT_ROOT / self.data_dir

    @property
    def db_path(self) -> Path:
        return self.data_path / "kinwatch.db"

    @property
    def kill_switch_path(self) -> Path:
        return self.data_path / "STOP_LIVE"

    @property
    def effective_time_scale(self) -> float:
        """Divisor applied to rule durations: 1 normally, TIME_SCALE in demo mode."""
        return self.time_scale if self.demo_mode else 1.0

    @property
    def effective_claim_timeout_sec(self) -> int:
        return self.demo_claim_timeout_sec if self.demo_mode else self.claim_timeout_sec

    def sam_concept(self, zone_object: str) -> str:
        """The SAM text prompt for a rule's zone; the rule's own word unless ZONE_CONCEPTS_JSON maps it."""
        zone = " ".join(zone_object.lower().split())
        return self.zone_concepts_json.get(zone, zone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
