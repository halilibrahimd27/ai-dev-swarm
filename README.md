# ai-dev-swarm

[![verify](https://github.com/halilibrahimd27/ai-dev-swarm/actions/workflows/verify.yml/badge.svg?branch=main)](https://github.com/halilibrahimd27/ai-dev-swarm/actions/workflows/verify.yml)

**Run a senior-level autonomous software-engineering swarm on your own
laptop.** Three CrewAI crews — ideation, planning, build — pick deep,
niche projects, decompose them into milestones, and ship them to
GitHub. You watch it work over SSE, steer it with one-line notes,
and stop it with one button.

```
            ┌──────────────────────────────────────────────────────┐
            │            ORCHESTRATOR  (24/7 loop, asyncio)         │
            │   scheduler ▸ project pool ▸ replanner ▸ kill switch  │
            └──────────────────────────────────────────────────────┘
                  ▲                ▲                          ▲
                  │                │                          │
        ┌─────────┴────┐   ┌───────┴──────┐    ┌──────────────┴──────┐
        │  IDEATION    │   │  PLANNING    │    │  MILESTONE BUILD    │
        │  Scout       │   │  PM          │    │  Developer (SDK)    │
        │  Ideator     │   │  Architect   │    │  Tester    (SDK)    │
        │  Critic      │   │              │    │  Reviewer           │
        └──────────────┘   └──────────────┘    └─────────────────────┘
                  │                │                          │
                  └────────────────┴──────────────────────────┘
                                   │
                ┌──────────────────┴──────────────────────┐
                │  postgres + pgvector │ redis │ phoenix  │
                └─────────────────────────────────────────┘
                                   │
                          web UI + Telegram bot
                          (loopback only)
```

---

## What it does

ai-dev-swarm is a 24/7 process on your machine that:

1. **Ideates** ambitious, deep, niche project ideas via a Scout +
   Ideator + Critic crew. The Critic rejects clones of existing
   projects via a respx-mocked novelty check against GitHub
   Search + PyPI.
2. **Plans** each accepted idea into an ordered graph of small,
   independently testable milestones. An optional one-time human
   approval checkpoint sits between plan and build.
3. **Builds** one milestone at a time using the Claude Agent SDK
   for the Developer + Tester roles. Each milestone is committed
   to a persistent per-project git repo on disk, passes a CI gate
   (lint + types + tests), and is reviewed before the next
   milestone starts.
4. **Replans** between every milestone — a typed
   `Noop | Amend | Split | Escalate` action either advances the
   project or restructures the upcoming work. A cheap
   `AutoSplitPredictor` short-circuits the LLM call when a
   milestone is predicted to blow the budget.
5. **Ships** finished projects to GitHub as PRs (default) or
   merges them itself (`AIDEVSWARM_GITHUB_MODE=auto_merge`).

A "project" survives **across days** — token caps pause it
mid-graph, not mid-milestone, and persistent workspaces +
checkpointed SDK sessions resume cleanly the next day.

---

## Prerequisites

| Requirement | Version | Notes |
| --- | --- | --- |
| Docker | 24+ | With Compose v2.20+ (`include:` directive) |
| Free RAM | 4 GB | 8 GB recommended (Phoenix + Postgres + Redis + orchestrator) |
| Free disk | 10 GB | + extra for the projects ai-dev-swarm builds |
| Free host ports | 5432, 6006, 6379, 8080 | All override-able via env |
| OS | macOS 14+, Linux | Tested on Darwin 25 and Ubuntu 22.04. Windows works via WSL2. |
| Anthropic API key | — | https://console.anthropic.com |

You do NOT need Python, `uv`, Node, or any global toolchain on your
host — everything runs in containers.

---

## Quickstart

A first-time reader following ONLY this section reaches a running
system in 5 minutes.

```bash
# 1) Clone the repo.
git clone https://github.com/halilibrahimd27/ai-dev-swarm.git
cd ai-dev-swarm

# 2) Copy the env template and fill 5 required keys.
cp .env.example .env
$EDITOR .env
#   ANTHROPIC_API_KEY=sk-ant-...        (console.anthropic.com)
#   POSTGRES_PASSWORD=<anything-secret>
#   GITHUB_TOKEN=ghp_...                (optional but recommended)
#   GITHUB_OWNER=your-github-username   (required iff GITHUB_TOKEN set)
#   TELEGRAM_BOT_TOKEN=<from @BotFather> (optional)

# 3) Materialise the MCP config (gitignored — copy from template).
cp .mcp.example.json .mcp.json

# 4) Start everything.
docker compose up -d

# 5) Watch it work.
open http://127.0.0.1:8080         # web panel
open http://localhost:6006         # Phoenix traces (no auth)
docker compose logs -f orchestrator
```

When all 4 services report healthy (`docker compose ps`), the
orchestrator starts ticking. The first idea lands within a few
minutes (Critic + novelty check take a couple of LLM calls).

---

## Getting your keys

### Anthropic API key

1. Sign in at https://console.anthropic.com.
2. **Settings → API Keys → Create Key**.
3. Paste into `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-api03-...
   ```

Anthropic's free credit covers a couple of milestones. Real
24/7 use costs money — set
`AIDEVSWARM_DAILY_TOKEN_BUDGET` to a number you're comfortable
with. The Critic + novelty check use Haiku (cheap); the
Developer/Tester use Opus (expensive).

### GitHub personal access token (optional)

1. Open https://github.com/settings/tokens.
2. **Generate new token (classic)**.
3. Scopes: `public_repo` (or `repo` for private repos),
   `read:user`.
4. Paste into `.env`:
   ```
   GITHUB_TOKEN=ghp_...
   GITHUB_OWNER=your-github-username
   ```

Without these, the orchestrator still builds projects — it just
can't publish them. They live in `./workspaces/<project>/` until
you decide what to do with them.

### Telegram bot (optional)

1. DM **@BotFather** on Telegram. Send `/newbot`. It gives you a
   token like `1234567:ABC-DEF...`.
2. Send `/start` to your new bot.
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy
   `result[0].message.chat.id`. That's your `TELEGRAM_CHAT_ID`.
4. To use the bidirectional control bot (free-form natural-language
   commands), find your user ID by chatting with **@userinfobot**
   and add it to the allow-list:
   ```
   TELEGRAM_BOT_TOKEN=1234567:ABC-DEF...
   TELEGRAM_CHAT_ID=<your-chat-id>
   AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS=<your-user-id>
   ```

Without these, ai-dev-swarm runs fine — it just logs to stdout
instead of sending you notifications.

---

## First run

Within a minute of `docker compose up -d`:

1. Open http://127.0.0.1:8080 — the **state pane** lists active
   projects, the **transcript pane** streams every inter-agent
   message live, the **controls pane** has approve/abort/kill
   buttons.
2. Open http://localhost:6006 — Phoenix shows the full agent
   trace tree (every CrewAI task → SDK invocation → tool call →
   MCP call).
3. If `AIDEVSWARM_REQUIRE_APPROVAL=true` (the default), the first
   ideation pass picks an idea, the planning crew decomposes it,
   and the project sits in `awaiting_approval` waiting for your
   "go". Click **approve** in the web panel — or, if Telegram
   is wired, tap the **Approve** button on the message the bot
   sends you.
4. The build crew takes over. Watch the transcript: PM ↔ Architect
   negotiate, the Developer SDK runs `Read`/`Write`/`Bash`,
   the Tester writes property tests, the Reviewer goes through
   the acceptance criteria.
5. Each finished milestone is committed to
   `./workspaces/<project>/`. Open it in your editor any time.

Tap the **steer** text box at the bottom of the transcript pane
to drop a one-line note ("focus on test coverage", "use
dataclasses, not pydantic"). The Developer SDK picks it up on the
NEXT tool call without restarting the session — the
`PreToolUseHookInput` hook (Phase 5) is what makes this work.

---

## Configuration

Every knob is an env var. Defaults are sensible — the ones you'll
realistically tune:

| Variable | Default | What it does |
| --- | --- | --- |
| `AIDEVSWARM_DAILY_TOKEN_BUDGET` | `2_000_000` | Soft cap on tokens spent per UTC day across ALL projects. |
| `AIDEVSWARM_PER_MILESTONE_TOKEN_BUDGET` | `400_000` | Hard cap per milestone — circuit breaker, not a deadline. |
| `AIDEVSWARM_BUILD_CONCURRENCY` | `1` | How many projects build in parallel. Cost scales linearly. |
| `AIDEVSWARM_REQUIRE_APPROVAL` | `true` | Set to `false` to skip the plan-approval checkpoint. |
| `AIDEVSWARM_GITHUB_MODE` | `pr_only` | `pr_only` opens PRs; `auto_merge` lands them. Stay in `pr_only` until you trust the swarm. |
| `AIDEVSWARM_AUTO_SPLIT_MAX_TURNS` | `40` | Auto-split fires when predicted SDK turns exceed this. |
| `AIDEVSWARM_AUTO_SPLIT_MAX_COST_USD` | `3.0` | Auto-split fires when predicted milestone cost exceeds this. |
| `AIDEVSWARM_CONSOLIDATION_EVERY` | `5` | Every Nth success, inject a tidy + verify milestone. |
| `AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS` | (empty) | Comma-separated Telegram user IDs that can issue commands. Empty = locked. |

See [`.env.example`](.env.example) for the full list (34 variables).

---

## Operating

| Action | How |
| --- | --- |
| **Stop everything** | `docker compose down` |
| **Wipe + restart** | `docker compose down -v && docker compose up -d` |
| **Tail logs** | `docker compose logs -f orchestrator` |
| **Kill one project** | Web panel → select project → **Abort** (confirms). |
| **Global kill switch** | Web panel → **Kill switch** (confirms) — or set `aidevswarm:kill_switch` directly in Redis. |
| **Pause** | Web panel → **Pause**. Reversible; the project stays in its current state until **Resume**. |
| **Steer mid-flight** | Web panel → transcript pane → "steer" text box. Or `/note <text>` to the Telegram bot. |
| **Approve a plan** | Web panel → **Approve**. Or `/approve` in Telegram. |
| **Re-scope** | Web panel → controls → enter new scope → **Rescope** (destructive; confirms). Writes an `[OPERATOR RESCOPE]` steering note that the replanner picks up next pass. |
| **Switch to a different idea** | Telegram → free-form text "switch to idea <id>" → confirm. |
| **Look at a project's code** | `cd ./workspaces/<project>` — it's a real git repo. |
| **Look at the trace tree** | Phoenix at http://localhost:6006. |

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `docker compose up` fails with "port 5432 is already in use" | Another Postgres is bound on the host | `POSTGRES_HOST_PORT=5433 docker compose up -d` (the orchestrator container still uses 5432 internally; only the host-side port changes). Same for `REDIS_HOST_PORT`. |
| `ai-dev-swarm: ANTHROPIC_API_KEY is empty` on startup | `.env` missing or empty | Edit `.env` and re-run `docker compose up -d`. The friendly error means the pre-flight is doing its job. |
| `ai-dev-swarm: startup failed during pre-flight (OperationalError: ...)` | Postgres isn't healthy yet | Run `docker compose ps postgres` — if status is `starting`, just wait. Re-run `docker compose up -d` once it's healthy. |
| `docker: Cannot connect to the Docker daemon` | Docker Desktop isn't running | Start Docker Desktop / `systemctl start docker`. |
| Telegram bot never replies | (a) `TELEGRAM_BOT_TOKEN` is empty; (b) your user ID isn't in `AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS` | Check `.env`; the bot SILENTLY denies non-allow-listed users (by design). |
| Web panel loads but transcript is empty | No project is building yet | Wait for the ideation crew to pick an idea (a couple of minutes). Or check `docker compose logs -f orchestrator` for an LLM API error. |
| `permission denied: ./workspaces` | Docker can't write to the bind mount | `chmod 755 workspaces/` on the host. |
| Phoenix UI is empty | No traces yet | The trace tree fills in as agents run; first traces land seconds after the first LLM call. If still empty after 5 minutes, set `AIDEVSWARM_PHOENIX_ENABLED=true` (it's the default) and check `docker compose logs phoenix`. |

---

## Comparison

|  | local-only | multi-project | property-tested | mutation-tested | observable | kill-switchable |
| --- | --- | --- | --- | --- | --- | --- |
| **ai-dev-swarm** | ✅ | ✅ (`build_concurrency`) | ✅ (Hypothesis) | ✅ (`make verify-l5`) | ✅ (Phoenix + SSE) | ✅ (per-project + global) |
| AutoGPT | partial (cloud LLM) | ❌ | ❌ | ❌ | partial | partial |
| Devin-style hosted | ❌ | ✅ | ❌ | ❌ | ✅ | partial |
| Plain CrewAI | depends | ❌ | ❌ | ❌ | partial | ❌ |
| Plain Claude Code | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## Architecture + ADRs

The high-level shape is in the diagram above. The decisions
behind each choice are in [`docs/adr/`](docs/adr/):

- [ADR-0001 — Use MADR for ADRs](docs/adr/0001-use-madr.md)
- [ADR-0002 — CrewAI for role coordination + custom orchestrator](docs/adr/0002-crewai-plus-custom-orchestrator.md)
- [ADR-0003 — Claude Agent SDK for the Developer + Tester roles](docs/adr/0003-claude-agent-sdk-for-developer.md)
- [ADR-0004 — psycopg3 + ConnectionPool over psycopg2](docs/adr/0004-psycopg3-over-psycopg2.md)
- [ADR-0005 — Self-hosted Arize Phoenix over LangSmith](docs/adr/0005-arize-phoenix-over-langsmith.md)
- [ADR-0006 — Milestone-graph state machine as the multi-day primitive](docs/adr/0006-milestone-graph-state-machine.md)

Security: [THREAT_MODEL.md](THREAT_MODEL.md) (STRIDE table per
component), [SECURITY.md](SECURITY.md) (disclosure + supported
versions + the untrusted-code note).

Example projects ai-dev-swarm has built: [`examples/`](examples/).

---

## Development

Quickstart for hacking on the orchestrator itself (NOT needed to
run the system):

```bash
brew install uv && uv python install 3.12  # macOS; Linux: see uv docs
uv sync --extra dev
cp .mcp.example.json .mcp.json
make verify          # L0..L6, L8 — lint, types, security, deps,
                     # tests (>=85% coverage), property tests,
                     # complexity caps, architectural layers
make verify-l5       # opt-in: mutmut mutation testing (20-40 min)
make verify-ship     # Phase 6 acceptance checks (LICENSE, ADRs,
                     # README sections, .env coverage, examples/)
```

CI (GitHub Actions) runs `make verify` on every PR and push to
main. The fresh-clone proof test (Phase 6 DoD Mandate 8) is
documented in `docs/handoffs/phase-6.md` (gitignored — the
build-kit handoff).

---

## License

MIT. See [LICENSE](LICENSE).

## Contributing

Issues + PRs welcome. The orchestrator is built phase-by-phase
following a fixed plan; the build-kit (CLAUDE.md, phase prompts,
plans, handoffs) is gitignored on purpose — it's how the product
was built, not part of the product. If you want to extend ai-dev-
swarm, file an ADR under `docs/adr/` first.
