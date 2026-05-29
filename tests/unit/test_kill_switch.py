"""Unit tests for the kill switch.

Uses :class:`InMemoryKillSwitch` directly (no Redis) and verifies the
Redis-backed impl via a tiny fake redis client implementing the same
slice of the protocol.
"""

from __future__ import annotations

from uuid import uuid4

from aidevswarm.tools.kill_switch import (
    KEY_FLAG,
    KEY_REASON,
    InMemoryKillSwitch,
    RedisKillSwitch,
)


class _FakePauseRepo:
    """In-memory PauseRepo — pause now lives in Postgres in production."""

    def __init__(self) -> None:
        self.paused: set[object] = set()

    def set_paused(self, project_id: object, paused: bool) -> None:
        if paused:
            self.paused.add(project_id)
        else:
            self.paused.discard(project_id)

    def is_paused(self, project_id: object) -> bool:
        return project_id in self.paused


class _FakeRedis:
    """Just enough of the redis-py surface to back the kill switch."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, name: str) -> bytes | None:
        value = self.store.get(name)
        return value.encode() if value is not None else None

    def set(self, name: str, value: str) -> bool:
        self.store[name] = value
        return True

    def delete(self, *names: str) -> int:
        removed = 0
        for n in names:
            if n in self.store:
                del self.store[n]
                removed += 1
        return removed


def test_in_memory_kill_switch_round_trip() -> None:
    ks = InMemoryKillSwitch()
    assert ks.is_tripped() is False
    ks.trip("budget runaway")
    assert ks.is_tripped() is True
    ks.reset()
    assert ks.is_tripped() is False


def test_pause_is_independent_of_kill() -> None:
    """Pause and kill are separate signals — pausing must not 'kill'."""
    switches = (InMemoryKillSwitch(), RedisKillSwitch(_FakeRedis(), _FakePauseRepo()))
    for ks in switches:
        pid = uuid4()
        ks.pause_for(pid)
        assert ks.is_paused_for(pid) is True
        assert ks.is_tripped_for(pid) is False  # pause != kill
        ks.unpause_for(pid)
        assert ks.is_paused_for(pid) is False


def test_redis_pause_delegates_to_pause_repo() -> None:
    """Pause is durable (Postgres) — Redis is no longer the source of truth."""
    fake_redis = _FakeRedis()
    pauses = _FakePauseRepo()
    ks = RedisKillSwitch(fake_redis, pauses)
    pid = uuid4()
    ks.pause_for(pid)
    assert pauses.is_paused(pid) is True  # written via the repo, not Redis
    assert all("pause" not in key for key in fake_redis.store)  # no Redis pause key
    ks.unpause_for(pid)
    assert pauses.is_paused(pid) is False


def test_redis_kill_switch_writes_flag_and_reason() -> None:
    fake = _FakeRedis()
    ks = RedisKillSwitch(fake, _FakePauseRepo())
    assert ks.is_tripped() is False
    ks.trip("manual halt")
    assert fake.store[KEY_FLAG] == "1"
    assert fake.store[KEY_REASON] == "manual halt"
    assert ks.is_tripped() is True
    ks.reset()
    assert fake.store == {}
    assert ks.is_tripped() is False


def test_redis_kill_switch_trip_without_reason_does_not_write_reason() -> None:
    fake = _FakeRedis()
    ks = RedisKillSwitch(fake, _FakePauseRepo())
    ks.trip()
    assert KEY_FLAG in fake.store
    assert KEY_REASON not in fake.store
