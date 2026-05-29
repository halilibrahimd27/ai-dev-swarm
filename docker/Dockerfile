# ai-dev-swarm orchestrator image
#
# Multi-stage build to keep the runtime image lean. The build stage
# installs uv + project deps; the runtime stage copies only the venv
# and the package source.

# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates build-essential \
 && rm -rf /var/lib/apt/lists/* \
 && curl -LsSf https://astral.sh/uv/install.sh | sh \
 && mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml README.md /app/
# Lockfile may not exist on first build; uv handles either case.
COPY uv.lock* /app/
# Hatchling validates the project README during the editable install
# below, so a stub of the package src tree must be present.
COPY src /app/src

# `uv sync` builds + installs both the runtime deps and the project
# itself in one shot, using /opt/venv (see UV_PROJECT_ENVIRONMENT). No
# follow-up editable-install step is needed.
RUN uv venv /opt/venv \
 && uv sync --no-dev --frozen 2>/dev/null \
 || uv sync --no-dev

# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/workspace/src

# git: workspace commits/pushes. node + the Claude Code CLI: the
# claude-agent-sdk spawns the `claude` binary to drive the Developer /
# Tester roles — WITHOUT it every milestone build fails instantly
# (0 tokens, stop_reason=stop_sequence). Node 20 from NodeSource.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @anthropic-ai/claude-code \
 && apt-get purge -y gnupg \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 aidevswarm

WORKDIR /workspace

COPY --from=builder /opt/venv /opt/venv
# uv drives the "subprocess" CI sandbox: it builds a throwaway venv per
# milestone and installs the generated project to actually run its tests.
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=builder /app/src /workspace/src
# Phase 6 auto-migrate path: the bootstrap.run_migrations() helper
# walks up from src/aidevswarm/ looking for alembic.ini, then loads
# the migration scripts from ./alembic/. Both must be in the image.
COPY alembic.ini /workspace/alembic.ini
COPY alembic /workspace/alembic
# Phase 5 static web UI served by FastAPI at "/". The orchestrator
# resolves this path relative to src/aidevswarm/orchestrator/.
COPY ui /workspace/ui

USER aidevswarm

CMD ["python", "-m", "aidevswarm"]
