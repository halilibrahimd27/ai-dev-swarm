"""Arize Phoenix bootstrap.

Called once from the orchestrator's startup hook *before* any CrewAI
agent is constructed, so every agent call lands in the trace tree.

Failure is non-fatal: tracing being unreachable should NEVER take the
production orchestrator down. We log a warning and return.
"""

from __future__ import annotations

from aidevswarm.logging_config import get_logger
from aidevswarm.settings import Settings


def bootstrap_phoenix(settings: Settings) -> None:
    """Register Phoenix + instrument CrewAI. No-op if disabled."""
    log = get_logger(__name__)
    if not settings.phoenix_enabled:
        log.info("phoenix.disabled")
        return

    try:
        from openinference.instrumentation.crewai import CrewAIInstrumentor
        from phoenix.otel import register
    except ImportError as exc:
        log.warning("phoenix.import_failed", error=str(exc))
        return

    try:
        tracer_provider = register(
            project_name="ai-dev-swarm",
            endpoint=settings.phoenix_endpoint,
            auto_instrument=True,
            batch=True,
        )
        CrewAIInstrumentor().instrument(
            skip_dep_check=True, tracer_provider=tracer_provider
        )
        log.info("phoenix.bootstrap_ok", endpoint=settings.phoenix_endpoint)
    except Exception as exc:  # pragma: no cover - external service path
        # Tracing collector unreachable / misconfigured -> keep running.
        log.warning("phoenix.bootstrap_failed", error=str(exc))
