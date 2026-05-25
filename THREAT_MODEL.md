# ai-dev-swarm — Threat Model

This document is the STRIDE-shaped threat surface for ai-dev-swarm
as of Phase 6. It's the operator-facing answer to "what can go
wrong, and what stops it?"

The product runs on **one developer's PC** and **executes LLM-
generated code**. Three threat sources matter:

1. **A malicious operator on the same machine** (or a malicious
   process running as the same user).
2. **The LLM itself** — Claude is non-malicious but unreliable;
   generated code is treated as untrusted by construction.
3. **A network attacker** — bounded by the fact that nothing
   listens on a public port. The Telegram bot is the only outbound
   bidirectional channel; the FastAPI server is loopback-only.

Out of scope: nation-state adversaries with persistent access to
the operator's machine, supply-chain attacks on Anthropic /
Docker / pip (mitigated by pinned versions + `pip-audit`).

## Trust boundaries

```
┌──────────────────── operator's PC ───────────────────────────────┐
│                                                                  │
│   .env (secrets)  CLAUDE.md (build kit, gitignored)              │
│        │                                                         │
│        ▼                                                         │
│   ┌───────────────────────────────────────────────────────────┐  │
│   │  docker network (bridge, no public exposure)              │  │
│   │                                                           │  │
│   │   postgres ─── redis ─── phoenix                          │  │
│   │       ▲           ▲          ▲                            │  │
│   │       └───────────┼──────────┘                            │  │
│   │                   │                                       │  │
│   │              orchestrator  ─►  CrewAI agents              │  │
│   │              + FastAPI         ─► Claude Agent SDK ───┐   │  │
│   │              + Telegram bot       (Developer/Tester)  │   │  │
│   │                   │                                   ▼   │  │
│   │                   │                            sandbox    │  │
│   │                   │                            container  │  │
│   │                   ▼                            (untrusted │  │
│   │             127.0.0.1:8080                      gen code) │  │
│   │             web UI                                        │  │
│   └───────────────────────────────────────────────────────────┘  │
│                       │                            │             │
│                       │                            ▼             │
│                       ▼                       api.anthropic.com  │
│              api.telegram.org                 (LLM calls)        │
│              (polling, outbound)                                 │
└──────────────────────────────────────────────────────────────────┘
```

## Per-component STRIDE table

Format: `(S)poofing | (T)ampering | (R)epudiation | (I)nfo
disclosure | (D)oS | (E)levation of privilege`.

### Orchestrator process

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (E) untrusted gen code escapes | Developer SDK writes to `cwd` | `permission_mode="acceptEdits"` (NEVER `bypassPermissions`), `disallowed_tools=("WebFetch", "WebSearch")`, `max_turns` + `max_budget_usd` caps per call — see `claude_agent_sdk_tool.py:_DISALLOWED_TOOLS` |
| (D) runaway LLM cost | Loop in the build crew | Per-milestone token budget (`AIDEVSWARM_PER_MILESTONE_TOKEN_BUDGET`), daily cap (`AIDEVSWARM_DAILY_TOKEN_BUDGET`), AutoSplit predictor short-circuit before LLM replanner |
| (D) operator can't stop a runaway | — | Per-project + global Redis kill switch (`aidevswarm:kill:<id>` + `aidevswarm:kill_switch`); checked every tick |
| (I) secret leakage in log/UI/Telegram | Stack trace includes API key | `SecretRedactor` (Phase 5) wraps every outbound SSE + Telegram message; bandit B105/106 gauntlet on the source |

### CrewAI agents

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (T) prompt injection | LLM output flows back into next role's prompt | Discriminated-union schemas at every role boundary (`Idea`, `ReplannerAction`, `MilestoneSpec`) — off-schema replies are rejected, not executed |
| (E) prompt injection asks for shell | — | The Developer/Tester SDK tool's `allowed_tools` is a fixed allow-list per role; an inserted "use Bash to ..." instruction still can't reach disallowed tools |

### Claude Agent SDK subprocess

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (E) sandboxed gen code escapes the workspace | — | SDK enforces `cwd` + `permission_mode`; the workspace is a per-project git repo on disk, never `/etc` or `~` |
| (T) operator note injection abused | PreToolUse hook surfaces operator steering | The hook reads from `steering_notes`, written ONLY via the typed `Command` bus — schema-validated; nothing arbitrary lands there |
| (I) credentials in generated code | Developer writes a key into a file | Detected at the L1 `bandit` step (secret scanner in `make verify`); production push gate ensures it never reaches GitHub |

### Postgres

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (E) container escape via pgvector ext | — | pgvector + pgcrypto are official Postgres extensions; we run them with the default least-privilege role |
| (I) DB credentials in compose logs | `POSTGRES_PASSWORD` env var | Operator-set; `.env` is gitignored; orchestrator never logs the value |

### Redis

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (T) attacker writes to the kill switch | — | Redis is on the docker bridge network; not exposed to LAN by default |
| (D) Redis full | — | Kill-switch keys are tiny (~50 B each); not a realistic OOM vector |

### Phoenix observability

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (I) trace tree contains secret | LLM call payload | The orchestrator never sends secret-shaped strings to Phoenix; OpenInference instrumentation captures spans, not raw API keys. Operators can disable Phoenix via `AIDEVSWARM_PHOENIX_ENABLED=false`. |
| (I) Phoenix UI exposed | — | Phoenix listens on `0.0.0.0:6006` inside the docker bridge; docker compose publishes to `0.0.0.0:6006` on host. Operators wanting strict loopback can edit `docker/compose.yml` to use `127.0.0.1:6006:6006`. **Not** loopback-locked by default because Phoenix has no authentication and would be useless behind one. |

### Telegram bot

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (S) attacker spoofs operator | DM to bot | Hard allow-list of Telegram `user_id`s (`AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS`); non-listed users SILENTLY denied at every handler — see `telegram/bot.py::_is_allowed` |
| (T) attacker tampers with intent | Free-form message → Haiku parser | Haiku output is JSON-only + schema-validated by Pydantic `TypeAdapter[Command]`; off-list intents bounce |
| (E) destructive command without confirm | `abort_project` arrives unconfirmed | `CommandRouter.dispatch` REFUSES `requires_confirmation()=True` commands regardless of surface; the bot enforces `[Yes][No]` echo |
| (I) bot reveals secret in reply | — | Every outbound `_reply` + `_reply_via_query` goes through `SecretRedactor` |

### Web UI / FastAPI

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (S) network attacker reaches API | LAN | `AIDEVSWARM_API_HOST` validator refuses LAN IPs; docker publish is `127.0.0.1:8080:8080` only — see `settings.py::_enforce_loopback` |
| (T) cross-site request forgery | Browser pages on same host | Strict CSP (`default-src 'self'`); no inline `eval`; no third-party scripts |
| (T) inline scripts | — | CSP rejects them; the UI is vanilla JS with no eval |
| (I) SSE leaks secret | — | `SecretRedactor` wraps every outbound SSE event in `_emit` |

### MCP servers (tree-sitter-mcp)

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (E) MCP server runs untrusted code | — | tree-sitter-mcp is read-only (parser, no exec); `mcp_config.py` only configures `npx @nendo/tree-sitter-mcp` |
| (S) operator's `.mcp.json` swapped | — | `.mcp.example.json` is the committed template; on fresh clones the operator copies it manually |

## Operator-facing security controls (recap)

1. **Kill switch**: global (`AIDEVSWARM_API_PORT/api/commands` POST
   with `intent=kill_switch`, requires `confirmed=true`) +
   per-project (`abort_project`).
2. **Plan-approval checkpoint**: `AIDEVSWARM_REQUIRE_APPROVAL=true`
   stops every project at `awaiting_approval` for explicit operator
   sign-off before the multi-day build begins.
3. **Telegram allow-list**: empty list = locked down.
4. **Loopback-only control plane**: host-side guarantee comes from
   docker's `127.0.0.1:`-prefixed publish line; uvicorn binds
   `0.0.0.0` inside the container (necessary for docker port
   publishing to work). See `docker/compose.yml`.
5. **`SecretRedactor`**: every outbound SSE + Telegram message is
   filtered. 20+20 positive/negative tests in
   `tests/unit/test_redactor.py`.
6. **`bandit` + `pip-audit` + `semgrep`** in `make verify`: L1
   catches HIGH bandit; L2 catches known CVEs (with an explicit
   allowlist in `ci/audit_allowlist.txt`).

## Reporting a vulnerability

See [SECURITY.md](SECURITY.md).
