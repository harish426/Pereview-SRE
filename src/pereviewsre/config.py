"""Typed configuration loaded from environment / .env. No secrets in code."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PEREVIEWSRE_", env_file=".env", extra="ignore")

    appinsights_resource_id: str = ""

    aoai_endpoint: str = ""
    aoai_deployment: str = ""
    aoai_api_version: str = "2024-10-21"
    aoai_api_key: SecretStr = SecretStr("")

    max_rows: int = Field(200, ge=1, le=2000)
    query_timeout_s: int = Field(60, ge=5, le=300)
    max_agent_steps: int = Field(8, ge=1, le=20)
    default_lookback_hours: int = Field(24, ge=1, le=720)

    web_host: str = "127.0.0.1"
    web_port: int = 8080
    web_token: SecretStr = SecretStr("")

    log_level: str = "INFO"
    log_dir: str = "logs"
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 5

    def validate_required(self) -> None:
        missing = [
            name
            for name, val in {
                "PEREVIEWSRE_APPINSIGHTS_RESOURCE_ID": self.appinsights_resource_id,
                "PEREVIEWSRE_AOAI_ENDPOINT": self.aoai_endpoint,
                "PEREVIEWSRE_AOAI_DEPLOYMENT": self.aoai_deployment,
            }.items()
            if not val
        ]
        if missing:
            raise RuntimeError(f"Missing required settings: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
