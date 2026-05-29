"""Router handling of the update_setting command.

Validates + persists an override and applies it onto the live Settings
object; rejects unknown keys, bad values, and the not-wired case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from aidevswarm.orchestrator.command_router import CommandRouter
from aidevswarm.schemas import UpdateSetting
from aidevswarm.settings import Settings
from aidevswarm.tools.kill_switch import InMemoryKillSwitch
from tests.fakes import InMemoryProjectRepo


@dataclass
class _FakeSteering:
    def add_note(self, project_id: UUID, body: str, *, author: str = "human") -> int:
        return 1

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        return []


@dataclass
class _FakeOverrideRepo:
    saved: dict[str, str] = field(default_factory=dict)

    def get_all(self) -> dict[str, str]:
        return dict(self.saved)

    def upsert(self, key: str, value: str) -> None:
        self.saved[key] = value


def _router(*, wired: bool = True) -> tuple[CommandRouter, Settings, _FakeOverrideRepo]:
    settings = Settings()
    repo = _FakeOverrideRepo()
    kwargs: dict[str, Any] = {}
    if wired:
        kwargs = {"settings": settings, "settings_repo": repo}
    router = CommandRouter(
        project_repo=InMemoryProjectRepo(),
        steering_repo=_FakeSteering(),
        kill_switch=InMemoryKillSwitch(),
        **kwargs,
    )
    return router, settings, repo


def test_update_setting_applies_live_and_persists() -> None:
    router, settings, repo = _router()
    res = router.dispatch(UpdateSetting(key="daily_token_budget", value="500000"))
    assert res.ok is True
    assert settings.daily_token_budget == 500000  # applied to the live object
    assert repo.saved["daily_token_budget"] == "500000"  # persisted
    assert "applied live" in res.detail


def test_update_setting_restart_required_is_noted() -> None:
    router, settings, _ = _router()
    res = router.dispatch(UpdateSetting(key="sandbox_mode", value="docker"))
    assert res.ok is True
    assert "restart" in res.detail.lower()


def test_update_setting_rejects_unknown_key() -> None:
    router, settings, repo = _router()
    res = router.dispatch(UpdateSetting(key="anthropic_api_key", value="sk-ant-x"))
    assert res.ok is False
    assert "not an editable setting" in res.detail
    assert repo.saved == {}  # nothing persisted


def test_update_setting_rejects_bad_value() -> None:
    router, settings, repo = _router()
    res = router.dispatch(UpdateSetting(key="daily_token_budget", value="notanumber"))
    assert res.ok is False
    assert repo.saved == {}


def test_update_setting_not_wired_is_soft_error() -> None:
    router, _, _ = _router(wired=False)
    res = router.dispatch(UpdateSetting(key="daily_token_budget", value="1"))
    assert res.ok is False
    assert "not wired" in res.detail
