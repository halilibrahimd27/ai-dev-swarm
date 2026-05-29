"""Unit tests for :class:`PersistingTranscriptPublisher`.

Persists transcript-topic entries (best-effort) then forwards every
entry to the live sink. A persistence failure must never break the
fan-out — a build keeps running even if the DB write hiccups.
"""

from __future__ import annotations

from uuid import uuid4

from aidevswarm.db.transcript import PersistingTranscriptPublisher
from aidevswarm.observability import TranscriptEntry


class _FakeRepo:
    def __init__(self, *, boom: bool = False) -> None:
        self.appended: list[TranscriptEntry] = []
        self._boom = boom

    def append(self, entry: TranscriptEntry) -> None:
        if self._boom:
            raise RuntimeError("db down")
        self.appended.append(entry)

    def list_for_project(self, project_id: object, *, limit: int = 5000) -> list[TranscriptEntry]:
        return list(self.appended)


class _FakeSink:
    def __init__(self) -> None:
        self.published: list[TranscriptEntry] = []

    def publish(self, entry: TranscriptEntry) -> None:
        self.published.append(entry)


def _entry(topic: str = "transcript") -> TranscriptEntry:
    return TranscriptEntry(
        topic=topic, project_id=uuid4(), role="Developer", kind="assistant", text="hi"
    )  # type: ignore[arg-type]


def test_transcript_entry_is_persisted_and_forwarded() -> None:
    repo, sink = _FakeRepo(), _FakeSink()
    pub = PersistingTranscriptPublisher(repo, sink)
    e = _entry()
    pub.publish(e)
    assert repo.appended == [e]
    assert sink.published == [e]


def test_non_transcript_topic_is_forwarded_but_not_persisted() -> None:
    repo, sink = _FakeRepo(), _FakeSink()
    pub = PersistingTranscriptPublisher(repo, sink)
    e = _entry(topic="projects")
    pub.publish(e)
    assert repo.appended == []  # only transcript-topic entries are stored
    assert sink.published == [e]


def test_persistence_failure_still_forwards() -> None:
    repo, sink = _FakeRepo(boom=True), _FakeSink()
    pub = PersistingTranscriptPublisher(repo, sink)
    e = _entry()
    pub.publish(e)  # must not raise
    assert sink.published == [e]
