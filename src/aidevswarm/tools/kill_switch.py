"""Redis-backed kill switch.

The orchestrator checks :meth:`RedisKillSwitch.is_tripped` every tick.
Tripping the flag never aborts an in-progress milestone — it only
prevents the next state-machine step.

The key layout is intentionally trivial: a single string at
``aidevswarm:kill_switch`` (``1`` = tripped, anything else = clear) plus
an optional reason at ``aidevswarm:kill_switch:reason``.
"""

from __future__ import annotations

from typing import Protocol

import redis

from aidevswarm.settings import Settings

KEY_FLAG = "aidevswarm:kill_switch"
KEY_REASON = "aidevswarm:kill_switch:reason"


class _RedisClient(Protocol):
    """Trimmed down view of the redis-py client surface we use."""

    def get(self, name: str) -> bytes | None: ...
    def set(self, name: str, value: str) -> bool | None: ...
    def delete(self, *names: str) -> int: ...


class RedisKillSwitch:
    """Concrete :class:`aidevswarm.tools.protocols.KillSwitch`."""

    def __init__(self, client: _RedisClient) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisKillSwitch:
        """Build one from the env-driven :class:`Settings` object."""
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=False,
        )
        return cls(client)

    def is_tripped(self) -> bool:
        return self._client.get(KEY_FLAG) == b"1"

    def trip(self, reason: str = "") -> None:
        self._client.set(KEY_FLAG, "1")
        if reason:
            self._client.set(KEY_REASON, reason)

    def reset(self) -> None:
        self._client.delete(KEY_FLAG, KEY_REASON)


class InMemoryKillSwitch:
    """Process-local fallback used in tests and on first boot.

    Satisfies the same :class:`aidevswarm.tools.protocols.KillSwitch`
    interface without contacting Redis.
    """

    def __init__(self) -> None:
        self._tripped = False
        self._reason = ""

    def is_tripped(self) -> bool:
        return self._tripped

    def trip(self, reason: str = "") -> None:
        self._tripped = True
        self._reason = reason

    def reset(self) -> None:
        self._tripped = False
        self._reason = ""
