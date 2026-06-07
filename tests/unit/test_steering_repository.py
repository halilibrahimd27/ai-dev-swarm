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


def test_broadcast_note_reaches_each_role_once() -> None:
    """A note with no target_role (None) is delivered to EVERY role exactly
    once — the documented "visible to all roles" semantic. Each role sees it
    on its first pull and never again."""
    repo = FakeSteeringRepo()
    pid = uuid4()
    repo.add_note(pid, "n")  # target_role defaults to None == all roles
    assert repo.pull_unconsumed(pid, "Developer") == ["n"]
    assert repo.pull_unconsumed(pid, "Tester") == ["n"]
    assert repo.pull_unconsumed(pid, "Reviewer") == ["n"]
    # ...but only once per role.
    assert repo.pull_unconsumed(pid, "Developer") == []


def test_targeted_note_only_reaches_its_role() -> None:
    """A note addressed to one role is delivered to that role only — other
    roles never see it (the role-targeting bug: it used to be consumed by
    whichever role pulled first)."""
    repo = FakeSteeringRepo()
    pid = uuid4()
    repo.add_note(pid, "for the reviewer only", target_role="Reviewer")
    assert repo.pull_unconsumed(pid, "Developer") == []
    assert repo.pull_unconsumed(pid, "Reviewer") == ["for the reviewer only"]
    assert repo.pull_unconsumed(pid, "Reviewer") == []


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
