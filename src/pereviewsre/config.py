"""Typed configuration loaded from environment / .env. No secrets in code."""

import os
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def detect_environment() -> str:
  """'local' or 'production'. PEREVIEWSRE_ENV wins; otherwise Azure hosting implies production."""
  explicit = os.environ.get("PEREVIEWSRE_ENV", "").strip().lower()
  if explicit in ("local", "production"):
    return explicit
  if os.environ.get("WEBSITE_INSTANCE_ID"):
    return "production"
  return "local"


class Settings(BaseSettings):
  model_config = SettingsConfigDict(env_prefix="PEREVIEWSRE_", env_file=".env", extra="ignore")

  env: str = Field(default_factory=detect_environment)

  appinsights_resource_id: str = ""

  aoai_endpoint: str = ""
  aoai_deployment: str = ""
  aoai_api_version: str = "2024-10-21"
  aoai_api_key: SecretStr = SecretStr("")

  max_rows: int = Field(200, ge=1, le=2000)
  query_timeout_s: int = Field(60, ge=5, le=300)
  max_agent_steps: int = Field(8, ge=1, le=20)
  default_lookback_hours: int = Field(24, ge=1, le=720)

  # Episodic (per-chat, in-process) memory
  memory_ttl_s: int = Field(1800, ge=60, le=86400)  # idle expiry
  memory_max_episodes: int = Field(10, ge=1, le=50)  # per chat
  memory_max_sessions: int = Field(500, ge=1, le=10000)

  web_host: str = "127.0.0.1"
  web_port: int = 8080
  web_token: SecretStr = SecretStr("")

  log_level: str = "INFO"
  log_dir: str = "logs"
  log_max_bytes: int = 5_000_000
  log_backup_count: int = 5

  @property
  def is_production(self) -> bool:
    return self.env == "production"

  def validate_required(self) -> None:
    required = {
      "PEREVIEWSRE_APPINSIGHTS_RESOURCE_ID": self.appinsights_resource_id,
      "PEREVIEWSRE_AOAI_ENDPOINT": self.aoai_endpoint,
      "PEREVIEWSRE_AOAI_DEPLOYMENT": self.aoai_deployment,
    }
    if self.is_production:
      required["PEREVIEWSRE_WEB_TOKEN"] = self.web_token.get_secret_value()
    missing = [name for name, val in required.items() if not val]
    if missing:
      raise RuntimeError(f"Missing required settings: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
  # Layering (lowest -> highest): .env, .env.<environment>, real environment variables.
  # Under `func start`, local.settings.json values are real environment variables.
  env = detect_environment()
  return Settings(_env_file=(".env", f".env.{env}"))
