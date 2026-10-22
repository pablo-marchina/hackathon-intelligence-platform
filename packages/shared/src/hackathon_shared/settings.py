from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated process configuration shared by API and workers."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "hackathon-intelligence-platform"
    log_level: str = "INFO"

    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    web_port: int = Field(default=3000, ge=1, le=65535)

    database_url: str = "postgresql+psycopg://hackathon:hackathon@localhost:5432/hackathon"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "hackathon-raw"
    s3_region: str = "us-east-1"

    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    cors_origins: list[str] = ["http://localhost:3000"]
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    admin_api_key: str | None = None

    public_api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:3000"
    auth_state_secret: str | None = None
    session_cookie_name: str = "hip_session"
    csrf_cookie_name: str = "hip_csrf"
    session_ttl_seconds: int = Field(default=2_592_000, ge=3600, le=7_776_000)
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = None

    smtp_host: str = "localhost"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_from_address: str = "alerts@hackathon-intelligence.local"
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False
    smtp_ssl: bool = False  # implicit TLS, usually port 465
    alert_max_attempts: int = Field(default=4, ge=1, le=10)
    # Opt in only after migration 0008, a configured SMTP transport and a running
    # alert worker. The scheduler never sends emails when this flag is false.
    # Saved-search matches are generated only if explicitly enabled. The first
    # run establishes a watermark; historical catalog items are not backfilled.
    alert_match_enabled: bool = False
    alert_match_batch_size: int = Field(default=25, ge=1, le=100)
    alert_match_min_age_seconds: int = Field(default=60, ge=0, le=3600)
    alert_dispatch_enabled: bool = False
    alert_dispatch_batch_size: int = Field(default=25, ge=1, le=100)
    alert_dispatch_claim_seconds: int = Field(default=180, ge=60, le=900)

    source_scheduler_enabled: bool = False
    source_scheduler_batch_size: int = Field(default=3, ge=1, le=20)
    source_scheduler_max_pages: int = Field(default=5, ge=1, le=20)

    max_request_body_bytes: int = Field(default=1_048_576, ge=1, le=20_000_000)

    @model_validator(mode="after")
    def validate_smtp_security(self) -> Settings:
        if self.smtp_ssl and self.smtp_starttls:
            raise ValueError("SMTP_SSL and SMTP_STARTTLS are mutually exclusive")
        if self.smtp_username and not (self.smtp_ssl or self.smtp_starttls):
            raise ValueError("SMTP authentication requires SMTP_SSL or SMTP_STARTTLS")
        if self.smtp_password and not self.smtp_username:
            raise ValueError("SMTP_PASSWORD requires SMTP_USERNAME")
        if (
            self.alert_dispatch_enabled
            and self.app_env in {"staging", "production"}
            and not (self.smtp_ssl or self.smtp_starttls)
        ):
            raise ValueError("Production alert delivery requires verified SMTP TLS")
        return self

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
