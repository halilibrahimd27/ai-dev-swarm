# ai-dev-swarm

Local autonomous multi-agent development system that designs, builds,
tests, and ships niche software projects to GitHub on its own.

> **Status:** Phase 1 (stop the bleeding). The full onboarding README,
> ADRs, threat model, and security policy land in Phase 6. This file
> is a placeholder.

## Quick start (developer-facing)

```bash
# 1. Install uv + Python 3.12 if you don't already have them.
brew install uv && uv python install 3.12

# 2. Install dependencies.
uv sync --extra dev

# 3. Run the verification gauntlet.
make lint typecheck test

# 4. Bring up the stack (Postgres + Redis + Phoenix + orchestrator).
cp .env.example .env       # then fill in real values
make up
make logs                  # tail the orchestrator

# 5. Apply database migrations (steering_notes etc).
make migrate

# 6. Open Phoenix to watch CrewAI traces.
open http://localhost:6006
```

### Host port collisions

`make up` defaults to publishing Postgres on `5432` and Redis on
`6379`. If another container or local service already owns one of
those, override before running:

```bash
POSTGRES_HOST_PORT=5433 REDIS_HOST_PORT=6380 make up
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## License

Apache-2.0. See `LICENSE` (added in Phase 6).
