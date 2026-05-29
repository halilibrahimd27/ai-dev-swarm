"""The boardroom decision helper (publish_decision)."""

from __future__ import annotations

from uuid import uuid4

from aidevswarm.observability import DECISION_KIND, TranscriptEntry, publish_decision


class _Sink:
    def __init__(self) -> None:
        self.entries: list[TranscriptEntry] = []

    def publish(self, entry: TranscriptEntry) -> None:
        self.entries.append(entry)


def test_publish_decision_emits_decision_kind() -> None:
    sink = _Sink()
    pid = uuid4()
    publish_decision(sink, project_id=pid, role="PM", text="Decomposed into 5 milestones")
    assert len(sink.entries) == 1
    e = sink.entries[0]
    assert e.kind == DECISION_KIND
    assert e.role == "PM"
    assert e.project_id == pid
    assert e.topic == "transcript"


def test_publish_decision_none_publisher_is_noop() -> None:
    publish_decision(None, project_id=uuid4(), role="PM", text="x")  # must not raise


def test_publish_decision_empty_text_skipped() -> None:
    sink = _Sink()
    publish_decision(sink, project_id=uuid4(), role="PM", text="")
    assert sink.entries == []


def test_publish_decision_swallows_sink_errors() -> None:
    class _Boom:
        def publish(self, entry: TranscriptEntry) -> None:
            raise RuntimeError("sink down")

    # Must not propagate — a UI sink can never break a crew.
    publish_decision(_Boom(), project_id=uuid4(), role="PM", text="x")
