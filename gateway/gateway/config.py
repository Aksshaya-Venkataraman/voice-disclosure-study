from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_WEAK_MARKERS = ("change", "secret", "password", "example", "xxxx")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    environment: Literal["development", "test", "production"] = "production"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: SecretStr
    # Bearer token for researcher endpoints (create participants, export data).
    researcher_token: SecretStr

    public_url: str = "https://localhost"
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "gateway"])

    study_title: str = "Interview study: how interviewer voices affect answers"
    contact_email: EmailStr = "researcher@example.org"
    # Showing the question text weakens the voice manipulation; off by default.
    show_prompt_text: bool = False

    interview_engine: Literal["rasa", "scripted"] = "rasa"
    rasa_url: str = "http://rasa:5005"
    rasa_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    asr_provider: Literal["google", "disabled"] = "google"
    asr_language: str = "en-GB"
    max_audio_bytes: int = Field(default=10 * 1024 * 1024, ge=10_000)
    max_audio_seconds: int = Field(default=180, ge=10, le=600)

    rate_limit_storage_uri: SecretStr = SecretStr("memory://")
    answer_rate_limit: str = "30/minute"
    researcher_rate_limit: str = "60/minute"

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @field_validator("researcher_token")
    @classmethod
    def _strong(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < 32:
            raise ValueError("RESEARCHER_TOKEN must be at least 32 characters")
        if any(marker in raw.lower() for marker in _WEAK_MARKERS):
            raise ValueError("RESEARCHER_TOKEN looks like a placeholder; generate a random value")
        return value

    @field_validator("database_url")
    @classmethod
    def _psycopg3(cls, value: SecretStr) -> SecretStr:
        url = value.get_secret_value()
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return SecretStr("postgresql+psycopg://" + url[len(prefix) :])
        return value

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
