"""Phase 6 startup pre-flight.

Validates required env vars + applies Alembic migrations BEFORE the
orchestrator imports CrewAI / the SDK. The whole point of this
module is to fail-fast with a *friendly* error so a stranger
running ``docker compose up`` for the first time sees:

    ai-dev-swarm: ANTHROPIC_API_KEY is empty.
      1) Sign in at https://console.anthropic.com
      2) Create an API key
      3) Add to .env: ANTHROPIC_API_KEY=sk-ant-...

instead of a 30-line CrewAI / litellm stack trace from inside the
first agent call.

Two responsibilities, both idempotent:
  * ``validate_required_env(settings)`` — checks the required
    settings have non-empty values; raises :class:`MissingRequiredEnv`
    naming the offender + how to fix it.
  * ``run_migrations(settings)`` — calls ``alembic upgrade head`` via
    the in-process API. Idempotent (alembic skips applied revisions).

``main_with_preflight()`` is the new orchestrator entry point — it
runs both, prints the named error on failure, exits 1, and otherwise
delegates to ``orchestrator.orchestrator.main``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from aidevswarm.settings import Settings

# Settings keys that MUST be non-empty for the system to start at all.
# Everything else has a sensible default or is feature-flagged off
# until you wire it.
REQUIRED_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "POSTGRES_PASSWORD",
)

_KEY_HELP: dict[str, tuple[str, ...]] = {
    "ANTHROPIC_API_KEY": (
        "Sign in at https://console.anthropic.com",
        "Settings → API Keys → Create Key",
        "Add to .env: ANTHROPIC_API_KEY=sk-ant-...",
    ),
    "POSTGRES_PASSWORD": (
        "Any non-empty string (it's only used by your local DB).",
        "Add to .env: POSTGRES_PASSWORD=<something-secret>",
    ),
}


@dataclass
class MissingRequiredEnv(RuntimeError):
    """One required env var is unset / empty."""

    key: str

    def __str__(self) -> str:  # pragma: no cover — covered via main path
        steps = _KEY_HELP.get(self.key, ("Set it in .env.",))
        bullets = "\n".join(f"  {i + 1}) {step}" for i, step in enumerate(steps))
        return f"ai-dev-swarm: {self.key} is empty.\n{bullets}"


def validate_required_env(settings: Settings) -> None:
    """Raise if any required env var is empty.

    Reads each ``REQUIRED_KEYS`` entry from ``settings``. Empty
    ``SecretStr`` is treated the same as a missing value.
    """
    for key in REQUIRED_KEYS:
        attr = key.lower()
        if key.startswith("ANTHROPIC"):
            attr = "anthropic_api_key"
        elif key == "POSTGRES_PASSWORD":
            attr = "postgres_password"
        value = getattr(settings, attr, None)
        if value is None:
            raise MissingRequiredEnv(key=key)
        # Pydantic SecretStr gives an empty string from get_secret_value().
        raw = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
        if not raw.strip():
            raise MissingRequiredEnv(key=key)


def run_migrations(settings: Settings) -> None:  # pragma: no cover — IO-heavy
    """Apply Alembic migrations to head. Idempotent."""
    from alembic import command
    from alembic.config import Config

    # alembic.ini sits at the repo root; when we run inside the docker
    # container the CWD is /workspace, when run via `python -m
    # aidevswarm` it's the repo root.
    cfg_path = _find_alembic_ini()
    cfg = Config(str(cfg_path))
    # Inject the DSN derived from Settings so migrations work without
    # the operator needing to re-export PG_* env vars for alembic.
    cfg.set_main_option(
        "sqlalchemy.url",
        _alembic_url(settings),
    )
    command.upgrade(cfg, "head")


def _find_alembic_ini() -> Path:  # pragma: no cover — IO-heavy
    here = Path(__file__).resolve().parent
    for parent in (here, *here.parents):
        candidate = parent / "alembic.ini"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("alembic.ini not found upward from " + str(here))


def _alembic_url(settings: Settings) -> str:
    pwd = settings.postgres_password.get_secret_value()
    return (
        f"postgresql+psycopg://{settings.postgres_user}:{pwd}"
        f"@{settings.pg_host}:{settings.pg_port}/{settings.postgres_db}"
    )


def main_with_preflight() -> None:
    """Validate + migrate + delegate to the orchestrator main.

    Wrapped in a friendly error handler so missing keys or migration
    failures NEVER print a stack trace to a first-time operator.
    """
    try:
        settings = Settings()
        validate_required_env(settings)
        run_migrations(settings)
    except MissingRequiredEnv as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Most likely: postgres isn't up yet, or .env is malformed.
        # Print a single-line hint, no traceback.
        print(
            f"ai-dev-swarm: startup failed during pre-flight ({type(exc).__name__}: {exc}).\n"
            "  Check that Postgres is running (docker compose ps postgres)\n"
            "  and that .env has POSTGRES_PASSWORD set.",
            file=sys.stderr,
        )
        sys.exit(1)

    from aidevswarm.orchestrator.orchestrator import main as orchestrator_main

    orchestrator_main()


__all__ = [
    "REQUIRED_KEYS",
    "MissingRequiredEnv",
    "main_with_preflight",
    "run_migrations",
    "validate_required_env",
]
