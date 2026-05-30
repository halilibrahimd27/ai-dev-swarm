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
    # The Developer's DEFAULT model. Building is ~83% of spend and Opus is
    # ~5x Sonnet, so the Developer runs on Sonnet for the first attempt at a
    # milestone and only ESCALATES to model_strong (Opus) on a retry (when a
    # milestone failed and needs more horsepower). Easy milestones stay cheap;
    # hard ones get the strong model. Repoint via AIDEVSWARM_MODEL_DEV.
    model_dev: str = Field(
        default="anthropic/claude-sonnet-4-6",
        validation_alias="AIDEVSWARM_MODEL_DEV",
    )
    # Max output tokens for the CrewAI JSON-emitting agents (PM/Architect,
    # Ideator/Critic, Reviewer, Replanner). CrewAI's default is low enough
    # that a full milestone-graph JSON gets truncated mid-string and fails
    # to parse — which blocks the project after paying for the call. Opus
    # supports far more; 16k comfortably fits a milestone graph.
    max_output_tokens: int = Field(default=16000, validation_alias="AIDEVSWARM_MAX_OUTPUT_TOKENS")

    # --- GitHub -----------------------------------------------------------
    # The publisher creates a PRIVATE repo per project and pushes the
    # project's `main` branch milestone-by-milestone as the build runs.
    # There is no PR / auto-merge mode — the operator owns the repo and
    # reviews on the diff.
    github_token: SecretStr = Field(default=SecretStr(""), validation_alias="GITHUB_TOKEN")
    github_owner: str = Field(default="", validation_alias="GITHUB_OWNER")

    # --- Git authorship (commits land under YOUR GitHub identity) ---------
    # Every commit in a generated project's workspace is authored with
    # these. Leave both blank to fall back to GITHUB_OWNER (name) +
    # `<owner>@users.noreply.github.com` (email). For GitHub to attribute
    # the commits to your account, set git_author_email to a *verified*
    # email on your account (or your `<id>+<user>@users.noreply.github.com`
    # form). The Claude co-author trailer is disabled separately at the
    # SDK layer (see claude_agent_sdk_tool).
    git_author_name: str = Field(default="", validation_alias="AIDEVSWARM_GIT_AUTHOR_NAME")
    git_author_email: str = Field(default="", validation_alias="AIDEVSWARM_GIT_AUTHOR_EMAIL")

    @property
    def workspace_author_name(self) -> str:
        """Git ``user.name`` for generated-project commits."""
        return self.git_author_name or self.github_owner or "ai-dev-swarm"

    @property
    def workspace_author_email(self) -> str:
        """Git ``user.email`` for generated-project commits.

        Falls back to the GitHub no-reply form so commits still attribute
        to the owner's account when no explicit email is set.
        """
        if self.git_author_email:
            return self.git_author_email
        if self.github_owner:
            return f"{self.github_owner}@users.noreply.github.com"
        return "ai-dev-swarm@local"

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
    # A multi-turn SDK build legitimately spends a lot of (new) tokens;
    # this is a runaway circuit breaker, not a deadline. 1M leaves room
    # for a couple of retries before tripping.
    per_milestone_token_budget: int = Field(
        default=1_000_000, validation_alias="AIDEVSWARM_PER_MILESTONE_TOKEN_BUDGET"
    )
    build_concurrency: int = Field(default=1, validation_alias="AIDEVSWARM_BUILD_CONCURRENCY")
    milestone_retry_limit: int = Field(
        default=3, validation_alias="AIDEVSWARM_MILESTONE_RETRY_LIMIT"
    )
    # Default ON: an accepted idea is decomposed into a milestone graph
    # then PARKS at awaiting_approval until the operator approves it (web
    # UI or Telegram). Only then does it start coding + pushing. Set
    # AIDEVSWARM_REQUIRE_APPROVAL=false for fully-autonomous operation
    # (no human gate) — documented in the README.
    require_approval: bool = Field(default=True, validation_alias="AIDEVSWARM_REQUIRE_APPROVAL")
    tick_seconds: int = Field(default=30, validation_alias="AIDEVSWARM_TICK_SECONDS")

    # --- Ideation gate / loop --------------------------------------------
    # An idea must score >= min_score (and be novel) to become a project.
    # If a round produces nothing that clears the bar, re-ideate up to
    # ideation_max_rounds times before giving up (bounds runaway spend).
    ideation_min_score: int = Field(default=80, validation_alias="AIDEVSWARM_IDEATION_MIN_SCORE")
    ideation_max_rounds: int = Field(default=5, validation_alias="AIDEVSWARM_IDEATION_MAX_ROUNDS")

    # --- SDK build caps (Developer / Tester) -----------------------------
    # Turns + per-call USD cap the Claude Agent SDK enforces. 40 turns is
    # too few to FINISH a real scaffold milestone (it kept hitting the cap
    # and auto-splitting forever). 80 gives room to complete; the SDK
    # aborts at the budget cap regardless.
    sdk_max_turns: int = Field(default=80, validation_alias="AIDEVSWARM_SDK_MAX_TURNS")
    sdk_max_budget_usd: float = Field(default=5.0, validation_alias="AIDEVSWARM_SDK_MAX_BUDGET_USD")
    # The Tester's turn cap. Testing is more bounded than building, so a
    # lower cap than sdk_max_turns trims the recurring per-milestone Tester
    # spend (it ran to ~2.3M tokens on the first project) without starving it.
    tester_max_turns: int = Field(default=40, validation_alias="AIDEVSWARM_TESTER_MAX_TURNS")
    # When the CI gate fails, re-invoke the Developer with the exact
    # lint/type/test errors and re-run CI, up to this many times, BEFORE
    # the milestone counts a failed attempt. A trivial fix (an unused
    # import the Tester left behind) then self-heals in-attempt instead of
    # burning a whole retry on an unchanged prompt. 0 disables the loop.
    ci_repair_attempts: int = Field(default=2, validation_alias="AIDEVSWARM_CI_REPAIR_ATTEMPTS")

    # --- Phase 4 replanner ------------------------------------------------
    # Auto-split fires BEFORE the LLM-driven replanner crew runs. It's a
    # cheap circuit breaker — if a milestone's predicted turns/cost
    # exceeds these caps, the milestone is mechanically split into two.
    # Below the SDK's max_turns (40): a milestone that burned ~all its
    # turns and still failed is too big — bisect it on the next replan
    # instead of retrying the same oversized scope.
    auto_split_max_turns: int = Field(
        default=30, validation_alias="AIDEVSWARM_AUTO_SPLIT_MAX_TURNS"
    )
    auto_split_max_cost_usd: float = Field(
        default=3.0, validation_alias="AIDEVSWARM_AUTO_SPLIT_MAX_COST_USD"
    )
    # Consolidation cadence (Phase 4). Every Nth completed milestone is
    # followed by a no-features-allowed "tidy + verify" milestone.
    consolidation_every: int = Field(default=5, validation_alias="AIDEVSWARM_CONSOLIDATION_EVERY")
    # Drift / scope guardrail. A long-running project can sprawl (the
    # milestone graph grows as the replanner splits work) and quietly burn
    # money. If a project exceeds either cap it is BLOCKED for a one-time
    # operator review (resume to continue, or rescope/abort). 0 disables a
    # cap. max_project_cost_usd defaults to 0 (off) since the daily budget
    # already paces spend; set it to hard-stop a runaway project.
    max_project_milestones: int = Field(
        default=25, validation_alias="AIDEVSWARM_MAX_PROJECT_MILESTONES"
    )
    max_project_cost_usd: float = Field(
        default=0.0, validation_alias="AIDEVSWARM_MAX_PROJECT_COST_USD"
    )

    # --- Workspace --------------------------------------------------------
    workspaces_dir: Path = Field(
        default=Path("/workspace/workspaces"),
        validation_alias="AIDEVSWARM_WORKSPACES_DIR",
    )

    # CI sandbox for generated code.
    #  - "docker"     runs the milestone's tests in an ephemeral,
    #    network-less container (most isolated — needs the host Docker
    #    socket + the sandbox image).
    #  - "subprocess" installs the generated project into a throwaway uv
    #    venv and runs ruff + mypy --strict + pytest in-process. Real
    #    tests, no Docker socket needed; less isolated than "docker" but
    #    the production default for the compose stack (the orchestrator
    #    container has no socket).
    #  - "inmemory"   treats CI as pass WITHOUT running anything — last
    #    resort; quality then rests on the Reviewer alone.
    sandbox_mode: Literal["docker", "subprocess", "inmemory"] = Field(
        default="docker", validation_alias="AIDEVSWARM_SANDBOX_MODE"
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
    # Optional shared bearer token. When set, every state-changing request
    # (POST/PUT/PATCH/DELETE — i.e. /api/commands) must carry
    # `Authorization: Bearer <token>`. Leave blank to rely on the loopback
    # bind + Origin guard alone. It is a SECRET — never returned by
    # /api/settings, never in the editable allow-list. The web UI receives
    # it via a <meta> tag injected into index.html at serve time (loopback
    # only), so the operator's own browser can authenticate.
    api_token: SecretStr | None = Field(default=None, validation_alias="AIDEVSWARM_API_TOKEN")
    # Rate limit for state-changing API requests (POST /api/commands), as a
    # fixed window per minute. Guards against a runaway script or an
    # accidental command flood. 0 disables.
    api_rate_limit_per_min: int = Field(
        default=60, validation_alias="AIDEVSWARM_API_RATE_LIMIT_PER_MIN"
    )
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

    @field_validator("api_token", mode="before")
    @classmethod
    def _empty_token_is_none(cls, v: object) -> object:
        # An unset env var arrives as "" — treat that as "no token".
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

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
