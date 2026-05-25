"""FastAPI + SSE control plane (Phase 5).

The :func:`build_app` factory wires the dependencies. The orchestrator
calls it once at startup; tests call it with in-memory fakes.
"""

from aidevswarm.api.server import build_app, run_server

__all__ = ["build_app", "run_server"]
