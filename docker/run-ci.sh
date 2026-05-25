#!/usr/bin/env bash
# Default CI gate for generated projects. Runs inside the ephemeral
# sandbox container with the workspace bind-mounted at /workspace.
#
# Each step is fatal — any failure stops the pipeline and propagates
# its exit code so the orchestrator can mark the milestone failed.

set -euo pipefail

echo "::group::ruff"
ruff check .
ruff format --check .
echo "::endgroup::"

echo "::group::mypy"
if [ -d src ]; then
  mypy --strict src
else
  mypy --strict .
fi
echo "::endgroup::"

echo "::group::pytest"
pytest -q
echo "::endgroup::"

echo "ci: OK"
