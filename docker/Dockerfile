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

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 aidevswarm

WORKDIR /workspace

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/src /workspace/src

USER aidevswarm

CMD ["python", "-m", "aidevswarm"]
