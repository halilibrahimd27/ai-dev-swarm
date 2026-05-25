"""Observability wiring.

Phase 1 ships a single function — :func:`bootstrap_phoenix` — that the
orchestrator calls once at startup to attach OpenTelemetry tracing to
every CrewAI agent via OpenInference instrumentation.
"""

from aidevswarm.observability.phoenix import bootstrap_phoenix

__all__ = ["bootstrap_phoenix"]
