"""Centralised configuration via pydantic-settings.

Reads `.env` then process env vars; never logs secrets. The settings
object is constructed once at startup and passed by reference.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration. All values are env-driven."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM --------------------------------------------------------------
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="ANTHROPIC_API_KEY"
    )
    model_strong: str = Field(default="claude-opus-4-7", validation_alias="AIDEVSWARM_MODEL_STRONG")
    model_fast: str = Field(default="claude-haiku-4-5", validation_alias="AIDEVSWARM_MODEL_FAST")

    # --- GitHub -----------------------------------------------------------
    github_token: SecretStr = Field(default=SecretStr(""), validation_alias="GITHUB_TOKEN")
    github_owner: str = Field(default="", validation_alias="GITHUB_OWNER")
    github_mode: Literal["pr_only", "auto_merge"] = Field(
        default="pr_only", validation_alias="AIDEVSWARM_GITHUB_MODE"
    )

    # --- Telegram ---------------------------------------------------------
    telegram_bot_token: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_chat_id: str = Field(default="", validation_alias="TELEGRAM_CHAT_ID")

    # --- Postgres ---------------------------------------------------------
    postgres_user: str = Field(default="aidevswarm", validation_alias="POSTGRES_USER")
    postgres_password: SecretStr = Field(
        default=SecretStr("change-me"), validation_alias="POSTGRES_PASSWORD"
    )
    postgres_db: str = Field(default="aidevswarm", validation_alias="POSTGRES_DB")
    pg_host: str = Field(default="postgres", validation_alias="AIDEVSWARM_PG_HOST")
    pg_port: int = Field(default=5432, validation_alias="AIDEVSWARM_PG_PORT")

    # psycopg_pool.ConnectionPool tuning. Defaults match Phase 1 mandate.
    pg_pool_min: int = Field(default=4, validation_alias="AIDEVSWARM_PG_POOL_MIN")
    pg_pool_max: int = Field(default=20, validation_alias="AIDEVSWARM_PG_POOL_MAX")
    pg_pool_timeout: int = Field(default=10, validation_alias="AIDEVSWARM_PG_POOL_TIMEOUT")
    pg_pool_max_lifetime: int = Field(
        default=30 * 60, validation_alias="AIDEVSWARM_PG_POOL_MAX_LIFETIME"
    )

    # --- Redis ------------------------------------------------------------
    redis_host: str = Field(default="redis", validation_alias="AIDEVSWARM_REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="AIDEVSWARM_REDIS_PORT")

    # --- Budgets / scheduling --------------------------------------------
    daily_token_budget: int = Field(
        default=2_000_000, validation_alias="AIDEVSWARM_DAILY_TOKEN_BUDGET"
    )
    per_milestone_token_budget: int = Field(
        default=400_000, validation_alias="AIDEVSWARM_PER_MILESTONE_TOKEN_BUDGET"
    )
    build_concurrency: int = Field(default=1, validation_alias="AIDEVSWARM_BUILD_CONCURRENCY")
    milestone_retry_limit: int = Field(
        default=3, validation_alias="AIDEVSWARM_MILESTONE_RETRY_LIMIT"
    )
    require_approval: bool = Field(default=True, validation_alias="AIDEVSWARM_REQUIRE_APPROVAL")
    tick_seconds: int = Field(default=30, validation_alias="AIDEVSWARM_TICK_SECONDS")

    # --- Workspace --------------------------------------------------------
    workspaces_dir: Path = Field(
        default=Path("/workspace/workspaces"),
        validation_alias="AIDEVSWARM_WORKSPACES_DIR",
    )

    @property
    def pg_dsn(self) -> str:
        """psycopg-compatible DSN; password is unwrapped only here."""
        pwd = self.postgres_password.get_secret_value()
        return (
            f"host={self.pg_host} port={self.pg_port} "
            f"dbname={self.postgres_db} user={self.postgres_user} "
            f"password={pwd}"
        )


def load_settings() -> Settings:
    """Return a freshly-parsed Settings object."""
    return Settings()
