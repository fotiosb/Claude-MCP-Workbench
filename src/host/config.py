"""Environment-driven settings for MCP Workbench."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    host: str = "127.0.0.1"
    port: int = 8000
    public_base_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias=AliasChoices(
            "APP_BASE_URL", "PUBLIC_BASE_URL", "public_base_url", "app_base_url"
        ),
    )
    mcp_server_name: str = "mcp-workbench"

    data_dir: str = "./data"
    database_path: str = ""
    clone_dir: str = ""

    max_clone_bytes: int = 157286400
    max_files: int = 8000
    clone_wall_seconds: int = 60
    clone_wall_seconds_warm: int = 90
    max_read_bytes: int = 262144

    max_concurrent_per_ip: int = 1
    max_runs_per_ip_day: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "DAILY_RUNS_PER_IP", "MAX_RUNS_PER_IP_DAY", "max_runs_per_ip_day"
        ),
    )
    daily_llm_calls_per_ip: int = 20
    max_url_length: int = 512
    trusted_proxy: int = 0

    cache_ttl_hours: int = 6
    warm_examples: int = 1
    elicit_file_threshold: int = 1500
    elicit_timeout_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "ELICIT_WAIT_SECONDS", "ELICIT_TIMEOUT_SECONDS", "elicit_timeout_seconds"
        ),
    )
    artifact_ttl_hours: int = 24

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_effort: str = "medium"
    settings_session_secret: str = ""

    log_level: str = "INFO"

    def data_root(self) -> Path:
        # Empty DATA_DIR would otherwise resolve to cwd via Path("").
        raw = (self.data_dir or "").strip() or "./data"
        return Path(raw).expanduser().resolve()

    def database_file(self) -> Path:
        if (self.database_path or "").strip():
            return Path(self.database_path).expanduser().resolve()
        return (self.data_root() / "db" / "workbench.db")

    def clones_root(self) -> Path:
        if (self.clone_dir or "").strip():
            return Path(self.clone_dir).expanduser().resolve()
        return self.data_root() / "clones"

    def artifacts_root(self) -> Path:
        return self.data_root() / "artifacts"

    def cache_root(self) -> Path:
        return self.data_root() / "cache"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> Settings:
    global _settings
    _settings = Settings()
    return _settings
