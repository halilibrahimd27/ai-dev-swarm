"""Global + per-project kill switch + per-project pause.

The global switch halts the entire orchestrator. Per-project switches
let the operator stop one project's progression without disturbing
others — set by the Phase 5 Telegram ``/kill <project_id>`` command;
the API ships here so the rest of Phase 4 can rely on it.

**Kill** is transient: a runtime emergency signal that should be fast
to set and read. It lives in Redis.

**Pause** is recoverable and must SURVIVE A RESTART: it used to live in
Redis too, but a container reset wiped the key and the project would
have resumed had the daily-budget guard not also been exhausted. Pause
is now stored on the ``projects`` row (``is_paused``) — durable across
restarts and visible in the same place the rest of project state lives.

Key layout (Redis):
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


class _RedisClient(Protocol):
    """Trimmed down view of the redis-py client surface we use."""

    def get(self, name: str) -> bytes | None: ...
    def set(self, name: str, value: str) -> bool | None: ...
    def delete(self, *names: str) -> int: ...


class PauseRepo(Protocol):
    """The narrow project-pause slice the kill switch needs.

    A subset of :class:`aidevswarm.db.protocols.ProjectRepo` — kept narrow
    so the kill switch doesn't import the full repo interface (and so a
    test fake can satisfy it with two methods).
    """

    def set_paused(self, project_id: UUID, paused: bool) -> None: ...
    def is_paused(self, project_id: UUID) -> bool: ...


class RedisKillSwitch:
    """Concrete :class:`aidevswarm.tools.protocols.KillSwitch`.

    Kill (global + per-project) lives in Redis; pause is delegated to the
    injected :class:`PauseRepo` so it persists across restarts.
    """

    def __init__(self, client: _RedisClient, pause_repo: PauseRepo) -> None:
        self._client = client
        self._pause = pause_repo

    @classmethod
    def from_settings(cls, settings: Settings, pause_repo: PauseRepo) -> RedisKillSwitch:
        """Build one from the env-driven :class:`Settings` object."""
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=False,
        )
        return cls(client, pause_repo)

    # ------------- global -------------

    def is_tripped(self) -> bool:
        return self._client.get(KEY_FLAG) == b"1"

    def trip(self, reason: str = "") -> None:
        self._client.set(KEY_FLAG, "1")
        if reason:
            self._client.set(KEY_REASON, reason)

    def reset(self) -> None:
        self._client.delete(KEY_FLAG, KEY_REASON)

    # ------------- per-project kill (Phase 4) -------------

    def is_tripped_for(self, project_id: UUID) -> bool:
        return self._client.get(_project_key(project_id)) == b"1"

    def trip_for(self, project_id: UUID, reason: str = "") -> None:
        self._client.set(_project_key(project_id), "1")
        if reason:
            self._client.set(_project_key(project_id) + ":reason", reason)

    def reset_for(self, project_id: UUID) -> None:
        self._client.delete(_project_key(project_id), _project_key(project_id) + ":reason")

    # ------------- per-project pause (durable via Postgres) -------------

    def is_paused_for(self, project_id: UUID) -> bool:
        return self._pause.is_paused(project_id)

    def pause_for(self, project_id: UUID) -> None:
        self._pause.set_paused(project_id, True)

    def unpause_for(self, project_id: UUID) -> None:
        self._pause.set_paused(project_id, False)


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
