"""Global + per-project kill switch (Phase 4 expanded).

The global switch halts the entire orchestrator. Per-project switches
let the operator stop one project's progression without disturbing
others — set by the Phase 5 Telegram ``/kill <project_id>`` command;
the API ships here so the rest of Phase 4 can rely on it.

Key layout:
  * Global       : ``aidevswarm:kill_switch``      (``1`` = tripped)
  * Global reason: ``aidevswarm:kill_switch:reason``
  * Per-project  : ``aidevswarm:kill:<project_id>`` (``1`` = tripped)
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import redis

from aidevswarm.settings import Settings

KEY_FLAG = "aidevswarm:kill_switch"
KEY_REASON = "aidevswarm:kill_switch:reason"


def _project_key(project_id: UUID) -> str:
    return f"aidevswarm:kill:{project_id}"


def _pause_key(project_id: UUID) -> str:
    return f"aidevswarm:pause:{project_id}"


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

    # ------------- global -------------

    def is_tripped(self) -> bool:
        return self._client.get(KEY_FLAG) == b"1"

    def trip(self, reason: str = "") -> None:
        self._client.set(KEY_FLAG, "1")
        if reason:
            self._client.set(KEY_REASON, reason)

    def reset(self) -> None:
        self._client.delete(KEY_FLAG, KEY_REASON)

    # ------------- per-project (Phase 4) -------------

    def is_tripped_for(self, project_id: UUID) -> bool:
        return self._client.get(_project_key(project_id)) == b"1"

    def trip_for(self, project_id: UUID, reason: str = "") -> None:
        self._client.set(_project_key(project_id), "1")
        if reason:
            self._client.set(_project_key(project_id) + ":reason", reason)

    def reset_for(self, project_id: UUID) -> None:
        self._client.delete(_project_key(project_id), _project_key(project_id) + ":reason")

    # ------------- per-project pause (recoverable, NOT terminal) -------------

    def is_paused_for(self, project_id: UUID) -> bool:
        return self._client.get(_pause_key(project_id)) == b"1"

    def pause_for(self, project_id: UUID) -> None:
        self._client.set(_pause_key(project_id), "1")

    def unpause_for(self, project_id: UUID) -> None:
        self._client.delete(_pause_key(project_id))


class InMemoryKillSwitch:
    """Process-local fallback used in tests and on first boot.

    Satisfies the same :class:`aidevswarm.tools.protocols.KillSwitch`
    interface without contacting Redis.
    """

    def __init__(self) -> None:
        self._tripped = False
        self._reason = ""
        self._per_project: dict[UUID, str] = {}
        self._paused: set[UUID] = set()

    def is_tripped(self) -> bool:
        return self._tripped

    def trip(self, reason: str = "") -> None:
        self._tripped = True
        self._reason = reason

    def reset(self) -> None:
        self._tripped = False
        self._reason = ""

    def is_tripped_for(self, project_id: UUID) -> bool:
        return project_id in self._per_project

    def trip_for(self, project_id: UUID, reason: str = "") -> None:
        self._per_project[project_id] = reason

    def reset_for(self, project_id: UUID) -> None:
        self._per_project.pop(project_id, None)

    def is_paused_for(self, project_id: UUID) -> bool:
        return project_id in self._paused

    def pause_for(self, project_id: UUID) -> None:
        self._paused.add(project_id)

    def unpause_for(self, project_id: UUID) -> None:
        self._paused.discard(project_id)
