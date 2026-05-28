"""Unit tests for :class:`aidevswarm.observability.EventBridge`.

The CrewAI-handler registration path is exercised end-to-end via the
Phase 5 integration tests. These unit tests cover the pure fan-out
plumbing: subscribe, unsubscribe, project filtering, overflow
behaviour, and the cross-thread ``publish``.
"""

from __future__ import annotations

import asyncio
import threading
from uuid import uuid4

import pytest

from aidevswarm.observability import EventBridge, TranscriptEntry


def test_transcript_entry_coerces_none_text_to_empty() -> None:
    """A CrewAI event with no task name (text=None) must not raise.

    Regression: the _task_started handler passed None and the entry
    failed validation inside the event-bus handler, dropping the entry.
    """
    entry = TranscriptEntry(topic="transcript", kind="task_start", text=None)  # type: ignore[arg-type]
    assert entry.text == ""
    # Non-str is coerced too.
    entry2 = TranscriptEntry(topic="metrics", kind="llm_done", text=123)  # type: ignore[arg-type]
    assert entry2.text == "123"


@pytest.mark.asyncio
async def test_publish_reaches_a_matching_subscriber() -> None:
    bridge = EventBridge()
    bridge.attach(asyncio.get_running_loop())
    _, queue = bridge.subscribe("transcript")
    bridge.publish(TranscriptEntry(topic="transcript", kind="agent_start", text="hi"))
    entry = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert entry.kind == "agent_start"
    assert entry.text == "hi"


@pytest.mark.asyncio
async def test_project_filter_only_delivers_matching_entries() -> None:
    bridge = EventBridge()
    bridge.attach(asyncio.get_running_loop())
    pid_a = uuid4()
    pid_b = uuid4()
    _, qa = bridge.subscribe("transcript", project_id=pid_a)
    _, qb = bridge.subscribe("transcript", project_id=pid_b)

    bridge.publish(TranscriptEntry(topic="transcript", project_id=pid_a, kind="x", text="A"))
    bridge.publish(TranscriptEntry(topic="transcript", project_id=pid_b, kind="x", text="B"))

    a = await asyncio.wait_for(qa.get(), timeout=1.0)
    b = await asyncio.wait_for(qb.get(), timeout=1.0)
    assert a.text == "A"
    assert b.text == "B"
    assert qa.empty()
    assert qb.empty()


@pytest.mark.asyncio
async def test_topic_isolation() -> None:
    bridge = EventBridge()
    bridge.attach(asyncio.get_running_loop())
    _, qt = bridge.subscribe("transcript")
    _, qm = bridge.subscribe("metrics")

    bridge.publish(TranscriptEntry(topic="transcript", kind="t", text="t"))
    bridge.publish(TranscriptEntry(topic="metrics", kind="m", text="m"))

    t = await asyncio.wait_for(qt.get(), timeout=1.0)
    m = await asyncio.wait_for(qm.get(), timeout=1.0)
    assert t.kind == "t"
    assert m.kind == "m"
    assert qt.empty()
    assert qm.empty()


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    bridge = EventBridge()
    bridge.attach(asyncio.get_running_loop())
    conn_id, queue = bridge.subscribe("transcript")
    bridge.unsubscribe("transcript", conn_id)
    bridge.publish(TranscriptEntry(topic="transcript", kind="x", text="x"))
    # Give the loop a tick to dispatch (it won't deliver, but we wait
    # to ensure no race).
    await asyncio.sleep(0.02)
    assert queue.empty()


@pytest.mark.asyncio
async def test_overflow_drops_oldest_entries() -> None:
    """A slow client must not block the producer."""
    bridge = EventBridge()
    bridge.attach(asyncio.get_running_loop())
    _, queue = bridge.subscribe("transcript")
    # Push more than the maxsize (200) to force eviction.
    for i in range(250):
        bridge.publish(TranscriptEntry(topic="transcript", kind="x", text=str(i)))
    await asyncio.sleep(0.02)
    # Drain — the oldest items were dropped; the latest must be present.
    seen: list[str] = []
    while not queue.empty():
        seen.append(queue.get_nowait().text)
    assert seen[-1] == "249"
    assert "0" not in seen
    assert "49" not in seen


@pytest.mark.asyncio
async def test_publish_is_safe_from_a_background_thread() -> None:
    """CrewAI emits from worker threads — the bridge must handle it."""
    bridge = EventBridge()
    bridge.attach(asyncio.get_running_loop())
    _, queue = bridge.subscribe("transcript")

    def emit() -> None:
        bridge.publish(TranscriptEntry(topic="transcript", kind="off_thread", text="hello"))

    threading.Thread(target=emit, daemon=True).start()
    entry = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert entry.kind == "off_thread"


@pytest.mark.asyncio
async def test_stream_iterator_cleans_up_on_break() -> None:
    bridge = EventBridge()
    bridge.attach(asyncio.get_running_loop())
    pid = uuid4()

    async def consume() -> list[TranscriptEntry]:
        out: list[TranscriptEntry] = []
        async for entry in bridge.stream("transcript", project_id=pid):
            out.append(entry)
            if len(out) == 2:
                break
        return out

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    bridge.publish(TranscriptEntry(topic="transcript", project_id=pid, kind="x", text="1"))
    bridge.publish(TranscriptEntry(topic="transcript", project_id=pid, kind="x", text="2"))
    got = await asyncio.wait_for(task, timeout=1.0)
    assert [e.text for e in got] == ["1", "2"]
    # After break the async generator's finally clause must
    # have unsubscribed. The cleanup happens when the generator
    # is GC'd / closed; force it by also dropping our reference
    # and giving the loop a tick.
    del task
    await asyncio.sleep(0.02)
    assert bridge._subs["transcript"] == {}


@pytest.mark.asyncio
async def test_publish_without_attached_loop_is_a_silent_no_op() -> None:
    """The bridge must fail closed before attach() is called."""
    bridge = EventBridge()
    # No attach() yet.
    bridge.publish(TranscriptEntry(topic="transcript", kind="x", text="x"))
    # Now attach + subscribe; the old publish should not arrive.
    bridge.attach(asyncio.get_running_loop())
    _, queue = bridge.subscribe("transcript")
    await asyncio.sleep(0.02)
    assert queue.empty()
