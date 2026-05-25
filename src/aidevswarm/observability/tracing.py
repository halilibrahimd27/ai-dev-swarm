"""OpenTelemetry tracing helpers.

Used by the SDK tool to emit ``sdk.<role>`` spans around every Claude
Agent SDK invocation. Phoenix's OpenInference auto-instrumentation
picks up nested calls (Anthropic API, CrewAI, MCP tool-use) under
these spans automatically.

The module is import-safe even when Phoenix isn't bootstrapped: a
``NoOpTracer`` is returned and the spans become no-ops.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.trace import Tracer

_TRACER_NAME = "aidevswarm"


def get_tracer() -> Tracer:
    """Return the process-wide aidevswarm tracer."""
    return trace.get_tracer(_TRACER_NAME)
