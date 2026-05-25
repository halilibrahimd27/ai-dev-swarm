"""``python -m aidevswarm`` entry point.

Phase 6 wraps the orchestrator main with a startup pre-flight:

  1. validate the REQUIRED env vars are non-empty (friendly error
     naming the offender if not),
  2. apply Alembic migrations to head (idempotent),
  3. then call the orchestrator main.

A first-time operator with an empty ``.env`` sees a one-line named
error and three-step fix, NOT a CrewAI stack trace.
"""

from aidevswarm.bootstrap import main_with_preflight


def cli() -> None:
    main_with_preflight()


if __name__ == "__main__":
    cli()
