"""Unit tests for the steering repository contract.

Exercises the :class:`FakeSteeringRepo` (in-memory implementation of
:class:`aidevswarm.steering.protocols.SteeringRepo`). The
:class:`PsycopgSteeringRepo` is covered separately by the integration
test, which needs a live Postgres.
"""

from __future__ import annotations

from uuid import uuid4

from tests.fakes import FakeSteeringRepo


def test_add_then_pull_returns_body() -> None:
    repo = FakeSteeringRepo()
    pid = uuid4()
    repo.add_note(pid, "favour the depth-first ideator")
    assert repo.pull_unconsumed(pid, "Ideator") == ["favour the depth-first ideator"]


def test_pull_twice_returns_empty_second_time() -> None:
    repo = FakeSteeringRepo()
    pid = uuid4()
    repo.add_note(pid, "x")
    assert repo.pull_unconsumed(pid, "PM") == ["x"]
    assert repo.pull_unconsumed(pid, "PM") == []


def test_each_role_consumes_independently_only_when_unconsumed() -> None:
    """Once consumed by one role, a note is gone for everyone.

    This matches the production semantics: the row's ``consumed_by`` is
    a single attribution string. Multi-role broadcast is intentionally
    NOT a feature of Phase 1; operators who want a note delivered to
    every role can add it once per role.
    """
    repo = FakeSteeringRepo()
    pid = uuid4()
    repo.add_note(pid, "n")
    assert repo.pull_unconsumed(pid, "Ideator") == ["n"]
    assert repo.pull_unconsumed(pid, "PM") == []


def test_per_project_isolation() -> None:
    repo = FakeSteeringRepo()
    p1, p2 = uuid4(), uuid4()
    repo.add_note(p1, "for p1")
    repo.add_note(p2, "for p2")
    assert repo.pull_unconsumed(p1, "Ideator") == ["for p1"]
    assert repo.pull_unconsumed(p2, "Ideator") == ["for p2"]


def test_insertion_order_preserved() -> None:
    repo = FakeSteeringRepo()
    pid = uuid4()
    repo.add_note(pid, "first")
    repo.add_note(pid, "second")
    repo.add_note(pid, "third")
    assert repo.pull_unconsumed(pid, "Critic") == ["first", "second", "third"]


def test_empty_pull_returns_empty_list() -> None:
    repo = FakeSteeringRepo()
    assert repo.pull_unconsumed(uuid4(), "Architect") == []
