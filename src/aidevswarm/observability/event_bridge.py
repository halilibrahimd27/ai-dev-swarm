"""CrewAI EventBus → asyncio fan-out for the Phase 5 SSE surface.

The web panel's three SSE topics (``/sse/projects``,
``/sse/transcript/{id}``, ``/sse/metrics``) all read from queues
managed here. One subscriber per `(topic, project_id)` pair receives
a single message per CrewAI event — the bridge does the broadcasting
so the API server itself stays stateless.

Design:

  * The bridge owns a registry: ``dict[str, dict[ConnId, Queue]]``.
    Each SSE handler ``subscribe()``s, gets back a queue, and
    ``unsubscribe()``s when the client disconnects.
  * CrewAI emits events from worker threads (LiteLLM streams, tool
    execution). We use ``loop.call_soon_threadsafe`` so the bridge
    is safe regardless of which thread fires the event.
  * If a queue is full (slow client), we drop the OLDEST entry, not
    the new one — the live transcript should always show what's
    happening NOW.
  * The bridge does NOT redact. Redaction is the API server's
    responsibility (the SSE handler wraps every outbound write).

Topics:

  * ``projects`` — project + milestone state changes (one entry
    per Tick state transition; produced by ``Tick._move`` indirectly
    via a hook the orchestrator installs).
  * ``transcript`` — every inter-agent message, tool call, hand-off.
    Filtered per project_id at subscribe time.
  * ``metrics`` — token spend, cost, phase timing snapshots.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aidevswarm._time import utc_now
from aidevswarm.logging_config import get_logger

Topic = Literal["projects", "transcript", "metrics"]
TOPICS: tuple[Topic, ...] = ("projects", "transcript", "metrics")

_QUEUE_MAXSIZE = 200  # per subscriber; oldest dropped on overflow


class TranscriptEntry(BaseModel):
    """One message in the live transcript stream.

    The Pydantic model is what's serialised over SSE — the schema
    must stay stable because the static web UI deserialises it.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    topic: Topic
    project_id: UUID | None = None
    role: str | None = None
    kind: str  # short tag: "agent_start", "tool_use", "llm_chunk", "state", ...
    text: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utc_now)

    @field_validator("text", mode="before")
    @classmethod
    def _coerce_text(cls, v: object) -> str:
        # CrewAI events sometimes hand us None (e.g. a task with no name)
        # or a non-str; never let that raise inside an event-bus handler
        # and silently drop the transcript entry.
        if v is None:
            return ""
        return v if isinstance(v, str) else str(v)


class EventBridge:
    """Fan CrewAI/Tick events out to per-topic asyncio queues."""

    def __init__(self) -> None:
        self._log = get_logger(__name__)
        # topic -> connection_id -> (queue, project_filter)
        self._subs: dict[Topic, dict[UUID, tuple[asyncio.Queue[TranscriptEntry], UUID | None]]] = {
            t: {} for t in TOPICS
        }
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the bridge to the running event loop.

        Called once from the orchestrator's ``_async_main`` before
        any CrewAI work starts. Without this, ``publish`` falls back
        to ``asyncio.get_event_loop_policy().get_event_loop()`` which
        is unsafe across threads.
        """
        self._loop = loop

    # ------------------------------------------------------------------
    # Producer side
    # ------------------------------------------------------------------

    def publish(self, entry: TranscriptEntry) -> None:
        """Thread-safe enqueue across all matching subscribers."""
        loop = self._loop
        if loop is None:
            # Fail closed: better to drop than to .put_nowait on the
            # wrong loop. The first attach() call removes this branch.
            return
        loop.call_soon_threadsafe(self._dispatch, entry)

    def _dispatch(self, entry: TranscriptEntry) -> None:
        """Runs on the event loop thread; pushes into per-conn queues."""
        for conn_id, (queue, project_filter) in list(self._subs[entry.topic].items()):
            if project_filter is not None and entry.project_id != project_filter:
                continue
            if queue.full():
                # Drop the oldest entry so the latest one fits.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:  # pragma: no cover — guarded above
                self._log.warning("event_bridge.full", topic=entry.topic, conn=str(conn_id))

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------

    def subscribe(
        self,
        topic: Topic,
        *,
        project_id: UUID | None = None,
    ) -> tuple[UUID, asyncio.Queue[TranscriptEntry]]:
        """Open a queue for one SSE client. Pair with ``unsubscribe``."""
        conn_id = uuid4()
        queue: asyncio.Queue[TranscriptEntry] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subs[topic][conn_id] = (queue, project_id)
        return conn_id, queue

    def unsubscribe(self, topic: Topic, conn_id: UUID) -> None:
        self._subs[topic].pop(conn_id, None)

    async def stream(
        self,
        topic: Topic,
        *,
        project_id: UUID | None = None,
    ) -> AsyncIterator[TranscriptEntry]:
        """Async iterator over entries for one client; auto-cleans up."""
        conn_id, queue = self.subscribe(topic, project_id=project_id)
        try:
            while True:
                entry = await queue.get()
                yield entry
        finally:
            self.unsubscribe(topic, conn_id)

    # ------------------------------------------------------------------
    # CrewAI wiring
    # ------------------------------------------------------------------

    def install_crewai_handlers(self) -> None:  # pragma: no cover — live CrewAI only
        """Register handlers on the global CrewAI EventBus.

        Lazy import so test stacks that don't touch CrewAI don't pay
        the import cost. CrewAI's ``register_handler(EventType, fn)``
        takes a 2-arg callable ``(source, event)`` — we adapt each
        event into a ``TranscriptEntry`` and ``publish`` it.
        """
        from crewai.events import (
            AgentExecutionCompletedEvent,
            AgentExecutionStartedEvent,
            LLMCallCompletedEvent,
            LLMCallStartedEvent,
            LLMStreamChunkEvent,
            TaskCompletedEvent,
            TaskStartedEvent,
            ToolUsageFinishedEvent,
            ToolUsageStartedEvent,
            crewai_event_bus,
        )

        def _agent_started(_source: Any, event: Any) -> None:
            self.publish(
                TranscriptEntry(
                    topic="transcript",
                    role=getattr(event, "agent_role", None),
                    kind="agent_start",
                    text=f"{getattr(event, 'agent_role', 'agent')} started: "
                    f"{getattr(event, 'task_name', '')}",
                )
            )

        def _agent_done(_source: Any, event: Any) -> None:
            self.publish(
                TranscriptEntry(
                    topic="transcript",
                    role=getattr(event, "agent_role", None),
                    kind="agent_done",
                    text=str(getattr(event, "output", ""))[:500],
                )
            )

        def _task_started(_source: Any, event: Any) -> None:
            self.publish(
                TranscriptEntry(
                    topic="transcript",
                    kind="task_start",
                    text=getattr(event, "task_name", "task"),
                )
            )

        def _task_done(_source: Any, event: Any) -> None:
            self.publish(
                TranscriptEntry(
                    topic="transcript",
                    kind="task_done",
                    text=str(getattr(event, "output", ""))[:500],
                )
            )

        def _tool_started(_source: Any, event: Any) -> None:
            self.publish(
                TranscriptEntry(
                    topic="transcript",
                    kind="tool_use",
                    text=getattr(event, "tool_name", "tool"),
                    extra={"args": str(getattr(event, "tool_args", ""))[:200]},
                )
            )

        def _tool_done(_source: Any, event: Any) -> None:
            self.publish(
                TranscriptEntry(
                    topic="transcript",
                    kind="tool_done",
                    text=getattr(event, "tool_name", "tool"),
                )
            )

        def _llm_chunk(_source: Any, event: Any) -> None:
            self.publish(
                TranscriptEntry(
                    topic="transcript",
                    kind="llm_chunk",
                    text=getattr(event, "chunk", ""),
                )
            )

        def _llm_started(_source: Any, event: Any) -> None:
            self.publish(
                TranscriptEntry(
                    topic="metrics",
                    kind="llm_started",
                    extra={"model": getattr(event, "model", "")},
                )
            )

        def _llm_done(_source: Any, event: Any) -> None:
            usage = getattr(event, "usage", None)
            self.publish(
                TranscriptEntry(
                    topic="metrics",
                    kind="llm_done",
                    extra={
                        "model": getattr(event, "model", ""),
                        "input_tokens": getattr(usage, "input_tokens", 0),
                        "output_tokens": getattr(usage, "output_tokens", 0),
                    },
                )
            )

        crewai_event_bus.register_handler(AgentExecutionStartedEvent, _agent_started)
        crewai_event_bus.register_handler(AgentExecutionCompletedEvent, _agent_done)
        crewai_event_bus.register_handler(TaskStartedEvent, _task_started)
        crewai_event_bus.register_handler(TaskCompletedEvent, _task_done)
        crewai_event_bus.register_handler(ToolUsageStartedEvent, _tool_started)
        crewai_event_bus.register_handler(ToolUsageFinishedEvent, _tool_done)
        crewai_event_bus.register_handler(LLMStreamChunkEvent, _llm_chunk)
        crewai_event_bus.register_handler(LLMCallStartedEvent, _llm_started)
        crewai_event_bus.register_handler(LLMCallCompletedEvent, _llm_done)


__all__ = ["EventBridge", "TranscriptEntry", "TOPICS", "Topic"]
