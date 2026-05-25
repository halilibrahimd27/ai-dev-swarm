# Ephemeral CI sandbox for generated projects.
#
# Mounted with --network=none and the generated workspace bind-mounted
# read-only. The image deliberately has only what's needed to run the
# default CI gate (ruff + mypy + pytest); generated projects that need
# more must declare it and trigger a sandbox rebuild.

# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git build-essential ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --upgrade pip \
 && pip install ruff mypy pytest hypothesis

COPY docker/run-ci.sh /usr/local/bin/run-ci.sh
RUN chmod +x /usr/local/bin/run-ci.sh

WORKDIR /workspace

CMD ["/usr/local/bin/run-ci.sh"]
