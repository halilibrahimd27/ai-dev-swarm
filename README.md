# ai-dev-swarm

Local autonomous multi-agent development system that designs, builds,
tests, and ships niche software projects to GitHub on its own.

> **Status:** Phase 0 (foundation). The full onboarding README, ADRs,
> threat model, and security policy land in Phase 6. This file is a
> placeholder.

## Quick start (developer-facing)

```bash
# 1. Install uv + Python 3.12 if you don't already have them.
brew install uv && uv python install 3.12

# 2. Install dependencies.
uv sync --extra dev

# 3. Run the verification gauntlet.
make lint typecheck test

# 4. Bring up the stack (Postgres + Redis + orchestrator).
cp .env.example .env       # then fill in real values
make up
make logs                  # tail the orchestrator
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## License

Apache-2.0. See `LICENSE` (added in Phase 6).
