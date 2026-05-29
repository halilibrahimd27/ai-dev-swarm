"""Centralised configuration via pydantic-settings.

Reads `.env` then process env vars; never logs secrets. The settings
object is constructed once at startup and passed by reference.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DEFAULT_REDACT_PATTERNS: tuple[str, ...] = (
    r"sk-ant-[A-Za-z0-9_-]{20,}",  # Anthropic
    r"sk-[A-Za-z0-9_-]{32,}",  # OpenAI-style
    r"ghp_[A-Za-z0-9]{30,}",  # GitHub personal access token
    r"github_pat_[A-Za-z0-9_]{50,}",
    r"xoxb-[A-Za-z0-9-]{20,}",  # Slack bot token
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",  # JWT
    r"\b[0-9]{8,}:[A-Za-z0-9_-]{30,}\b",  # Telegram bot token (digits:secret)
)


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
    # NOTE: LiteLLM (the model router CrewAI uses) needs the `anthropic/`
    # prefix to route through the Anthropic Messages API; otherwise it
    # falls through to OpenAI and demands OPENAI_API_KEY.
    model_strong: str = Field(
        default="anthropic/claude-opus-4-7",
        validation_alias="AIDEVSWARM_MODEL_STRONG",
    )
    model_fast: str = Field(
        default="anthropic/claude-haiku-4-5",
        validation_alias="AIDEVSWARM_MODEL_FAST",
    )
    # Max output tokens for the CrewAI JSON-emitting agents (PM/Architect,
    # Ideator/Critic, Reviewer, Replanner). CrewAI's default is low enough
    # that a full milestone-graph JSON gets truncated mid-string and fails
    # to parse — which blocks the project after paying for the call. Opus
    # supports far more; 16k comfortably fits a milestone graph.
    max_output_tokens: int = Field(default=16000, validation_alias="AIDEVSWARM_MAX_OUTPUT_TOKENS")

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
    # Default OFF: the operator chose fully-autonomous operation — an
    # accepted idea goes straight to building + GitHub push, no human
    # gate. Set AIDEVSWARM_REQUIRE_APPROVAL=true to re-enable the
    # one-click checkpoint.
    require_approval: bool = Field(default=False, validation_alias="AIDEVSWARM_REQUIRE_APPROVAL")
    tick_seconds: int = Field(default=30, validation_alias="AIDEVSWARM_TICK_SECONDS")

    # --- Ideation gate / loop --------------------------------------------
    # An idea must score >= min_score (and be novel) to become a project.
    # If a round produces nothing that clears the bar, re-ideate up to
    # ideation_max_rounds times before giving up (bounds runaway spend).
    ideation_min_score: int = Field(default=80, validation_alias="AIDEVSWARM_IDEATION_MIN_SCORE")
    ideation_max_rounds: int = Field(default=5, validation_alias="AIDEVSWARM_IDEATION_MAX_ROUNDS")

    # --- Phase 4 replanner ------------------------------------------------
    # Auto-split fires BEFORE the LLM-driven replanner crew runs. It's a
    # cheap circuit breaker — if a milestone's predicted turns/cost
    # exceeds these caps, the milestone is mechanically split into two.
    auto_split_max_turns: int = Field(
        default=40, validation_alias="AIDEVSWARM_AUTO_SPLIT_MAX_TURNS"
    )
    auto_split_max_cost_usd: float = Field(
        default=3.0, validation_alias="AIDEVSWARM_AUTO_SPLIT_MAX_COST_USD"
    )
    # Consolidation cadence (Phase 4). Every Nth completed milestone is
    # followed by a no-features-allowed "tidy + verify" milestone.
    consolidation_every: int = Field(default=5, validation_alias="AIDEVSWARM_CONSOLIDATION_EVERY")

    # --- Workspace --------------------------------------------------------
    workspaces_dir: Path = Field(
        default=Path("/workspace/workspaces"),
        validation_alias="AIDEVSWARM_WORKSPACES_DIR",
    )

    # --- Observability (Arize Phoenix) ------------------------------------
    phoenix_enabled: bool = Field(default=True, validation_alias="AIDEVSWARM_PHOENIX_ENABLED")
    phoenix_endpoint: str = Field(
        default="http://phoenix:6006/v1/traces",
        validation_alias="AIDEVSWARM_PHOENIX_ENDPOINT",
    )

    # --- Phase 5 control plane -------------------------------------------
    # Loopback ONLY. The startup validator below refuses any non-loopback
    # value; there's no opt-out. Telegram bot uses polling (no port).
    api_host: str = Field(default="127.0.0.1", validation_alias="AIDEVSWARM_API_HOST")
    api_port: int = Field(default=8080, validation_alias="AIDEVSWARM_API_PORT")
    # Comma-separated list in the env var; the field_validator below
    # parses it into list[int]. NoDecode disables pydantic-settings's
    # built-in JSON list decoder so an empty string `""` doesn't error
    # before our validator runs (matters because `.env.example` ships
    # this field as `AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS=`).
    telegram_allowed_user_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        validation_alias="AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS",
    )
    haiku_model: str = Field(
        default="claude-haiku-4-5",
        validation_alias="AIDEVSWARM_HAIKU_MODEL",
    )
    redact_patterns: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(_DEFAULT_REDACT_PATTERNS),
        validation_alias="AIDEVSWARM_REDACT_PATTERNS",
    )

    @field_validator("api_host")
    @classmethod
    def _enforce_loopback(cls, v: str) -> str:
        # Inside a docker container, uvicorn must bind 0.0.0.0 so docker
        # can publish the port — the security guarantee comes from the
        # docker publish line `127.0.0.1:8080:8080` which binds only the
        # host's loopback (see docker-compose.yml). Outside docker the
        # operator should keep 127.0.0.1 to prevent LAN exposure.
        allowed = {"127.0.0.1", "localhost", "0.0.0.0"}  # nosec B104
        if v not in allowed:
            raise ValueError(
                f"AIDEVSWARM_API_HOST must be one of {sorted(allowed)} "
                f"(loopback or 0.0.0.0 — the latter only inside a docker "
                f"container that publishes ports to 127.0.0.1). Got {v!r}."
            )
        return v

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def _split_allowed_ids(cls, v: object) -> object:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(p.strip()) for p in v.split(",") if p.strip()]
        return v

    @field_validator("redact_patterns", mode="before")
    @classmethod
    def _split_redact_patterns(cls, v: object) -> object:
        if v is None or v == "":
            # Empty env var -> fall back to the default pattern set.
            return list(_DEFAULT_REDACT_PATTERNS)
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

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
