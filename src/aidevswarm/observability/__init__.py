"""Observability wiring.

Phase 1 ships a single function — :func:`bootstrap_phoenix` — that the
orchestrator calls once at startup to attach OpenTelemetry tracing to
every CrewAI agent via OpenInference instrumentation.
"""

from aidevswarm.observability.event_bridge import (
    TOPICS,
    EventBridge,
    Topic,
    TranscriptEntry,
    TranscriptPublisher,
)
from aidevswarm.observability.phoenix import bootstrap_phoenix
from aidevswarm.observability.redactor import SecretRedactor
from aidevswarm.observability.tracing import get_tracer

__all__ = [
    "TOPICS",
    "EventBridge",
    "SecretRedactor",
    "Topic",
    "TranscriptEntry",
    "TranscriptPublisher",
    "bootstrap_phoenix",
    "get_tracer",
]
