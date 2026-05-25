"""Unit tests for :class:`TelegramBot` helpers.

The PTB ``Application`` lifecycle (``build_application``,
``run_polling``) needs a real Telegram bot token and a live polling
loop — those paths are pragma-excluded from coverage. The helper
methods (allow-list gate, destructive-confirm echo, callback prefix
parsing) are pure logic and tested here with hand-rolled fakes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from aidevswarm.observability import SecretRedactor
from aidevswarm.orchestrator.command_router import CommandRouter
from aidevswarm.schemas import (
    AbortProject,
    Approve,
    InjectNote,
    Project,
    ProjectSpec,
)
from aidevswarm.settings import Settings
from aidevswarm.telegram import HaikuIntentParser, TelegramBot
from aidevswarm.telegram.intent import IntentParseError
from aidevswarm.tools.kill_switch import InMemoryKillSwitch
from tests.fakes import InMemoryProjectRepo


@dataclass
class _FakeSteering:
    notes: list[dict[str, Any]] = field(default_factory=list)
    _ctr: int = 0

    def add_note(self, project_id: UUID, body: str, *, author: str = "human") -> int:
        self._ctr += 1
        self.notes.append({"id": self._ctr, "project_id": project_id, "body": body})
        return self._ctr

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        return []


def _bot(*, allowed: list[int] | None = None) -> TelegramBot:
    settings = Settings(
        ANTHROPIC_API_KEY="sk-ant-test",
        TELEGRAM_BOT_TOKEN="123:abc",
        AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS=",".join(str(i) for i in (allowed or [])),
    )
    router = CommandRouter(
        project_repo=InMemoryProjectRepo(),
        steering_repo=_FakeSteering(),
        kill_switch=InMemoryKillSwitch(),
    )
    parser = HaikuIntentParser(settings)
    redactor = SecretRedactor(settings.redact_patterns)
    return TelegramBot(settings=settings, router=router, parser=parser, redactor=redactor)


# ---------------------------------------------------------------------------
# Allow-list gate
# ---------------------------------------------------------------------------


def test_allow_list_empty_denies_everyone() -> None:
    """An empty AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS locks the bot down."""
    bot = _bot(allowed=[])
    assert bot._is_allowed(12345) is False
    assert bot._is_allowed(None) is False


def test_allow_list_admits_listed_user_id() -> None:
    bot = _bot(allowed=[12345, 67890])
    assert bot._is_allowed(12345) is True
    assert bot._is_allowed(67890) is True
    assert bot._is_allowed(99999) is False


# ---------------------------------------------------------------------------
# Confirmation flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_echo_for_confirmation_sends_yes_no_keyboard() -> None:
    """Destructive commands are bounced back as a [Yes][No] inline keyboard."""
    bot = _bot(allowed=[12345])
    project_id = uuid4()
    command = AbortProject(project_id=project_id)

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    await bot._echo_for_confirmation(update, command)

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.call_args
    text = args[0]
    keyboard = kwargs["reply_markup"]
    assert "abort_project" in text
    # The Yes button carries the JSON payload so the callback can re-dispatch.
    yes_btn = keyboard.inline_keyboard[0][0]
    no_btn = keyboard.inline_keyboard[0][1]
    assert yes_btn.text == "Yes"
    assert no_btn.text == "No"
    assert yes_btn.callback_data.startswith("yes:")
    payload = json.loads(yes_btn.callback_data[len("yes:") :])
    assert payload["intent"] == "abort_project"
    assert str(payload["project_id"]) == str(project_id)


@pytest.mark.asyncio
async def test_non_destructive_dispatches_directly() -> None:
    """Approve / InjectNote etc. do NOT echo for confirmation."""
    bot = _bot(allowed=[1])
    # Seed a project so Approve has something to act on.
    bot.router.project_repo.create(  # type: ignore[attr-defined]
        Project(
            name="p",
            spec=ProjectSpec(
                title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
        )
    )
    project = next(iter(bot.router.project_repo.rows.values()))  # type: ignore[attr-defined]
    # InjectNote is non-destructive -> direct dispatch.
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    await bot._dispatch(update, InjectNote(project_id=project.id, body="ok"))
    update.message.reply_text.assert_awaited_once()
    # The reply must mention the intent name.
    text = update.message.reply_text.call_args[0][0]
    assert "inject_note" in text


# ---------------------------------------------------------------------------
# Callback handling: yes / no / approve / drop prefixes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_no_cancels() -> None:
    bot = _bot(allowed=[1])
    update = MagicMock()
    update.effective_user.id = 1
    update.callback_query.data = "no"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    await bot._on_callback(update, MagicMock())
    update.callback_query.edit_message_text.assert_awaited_once()
    text = update.callback_query.edit_message_text.call_args[0][0]
    assert "cancelled" in text


@pytest.mark.asyncio
async def test_callback_yes_dispatches_confirmed() -> None:
    bot = _bot(allowed=[1])
    bot.router.project_repo.create(  # type: ignore[attr-defined]
        Project(
            name="p",
            spec=ProjectSpec(
                title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
        )
    )
    project = next(iter(bot.router.project_repo.rows.values()))  # type: ignore[attr-defined]
    payload = json.dumps({"intent": "abort_project", "project_id": str(project.id)})
    update = MagicMock()
    update.effective_user.id = 1
    update.callback_query.data = "yes:" + payload
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    await bot._on_callback(update, MagicMock())
    # The kill switch should now be tripped for this project.
    assert bot.router.kill_switch.is_tripped_for(project.id)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_callback_approve_button() -> None:
    bot = _bot(allowed=[1])
    bot.router.project_repo.create(  # type: ignore[attr-defined]
        Project(
            name="p",
            spec=ProjectSpec(
                title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
            # The Approve router handler refuses non-awaiting_approval states.
        )
    )
    project = next(iter(bot.router.project_repo.rows.values()))  # type: ignore[attr-defined]
    update = MagicMock()
    update.effective_user.id = 1
    update.callback_query.data = "approve:" + str(project.id)
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    await bot._on_callback(update, MagicMock())
    # The handler should have run -> edit_message_text was called.
    update.callback_query.edit_message_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# Free-text path uses the Haiku parser; we stub the parser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_text_parser_failure_replies_friendly_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _bot(allowed=[1])

    async def boom(text: str, *, context_project_id: str | None = None) -> Any:
        raise IntentParseError("nope")

    monkeypatch.setattr(bot.parser, "parse", boom)
    update = MagicMock()
    update.effective_user.id = 1
    update.message.text = "do the thing"
    update.message.reply_text = AsyncMock()
    await bot._on_text(update, MagicMock())
    update.message.reply_text.assert_awaited_once()
    assert "didn't understand" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_on_text_non_destructive_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _bot(allowed=[1])
    bot.router.project_repo.create(  # type: ignore[attr-defined]
        Project(
            name="p",
            spec=ProjectSpec(
                title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
        )
    )
    project = next(iter(bot.router.project_repo.rows.values()))  # type: ignore[attr-defined]

    async def parsed(text: str, *, context_project_id: str | None = None) -> Any:
        return InjectNote(project_id=project.id, body="focus")

    monkeypatch.setattr(bot.parser, "parse", parsed)
    update = MagicMock()
    update.effective_user.id = 1
    update.message.text = "note focus on tests"
    update.message.reply_text = AsyncMock()
    await bot._on_text(update, MagicMock())
    # The router wrote the note via the fake steering repo.
    steering = bot.router.steering_repo
    assert any(n["body"] == "focus" for n in steering.notes)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unallowed_user_silently_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _bot(allowed=[1])
    update = MagicMock()
    update.effective_user.id = 999  # NOT in allow-list
    update.message.text = "anything"
    update.message.reply_text = AsyncMock()

    async def should_not_run(text: str, *, context_project_id: str | None = None) -> Any:
        raise AssertionError("parser must not be called for unallowed users")

    monkeypatch.setattr(bot.parser, "parse", should_not_run)
    await bot._on_text(update, MagicMock())
    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_list_runs_for_allowed_user() -> None:
    bot = _bot(allowed=[1])
    update = MagicMock()
    update.effective_user.id = 1
    update.message.reply_text = AsyncMock()
    await bot._cmd_list(update, MagicMock())
    update.message.reply_text.assert_awaited_once()
    assert "list_state" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_help_runs_for_allowed_user() -> None:
    bot = _bot(allowed=[1])
    update = MagicMock()
    update.effective_user.id = 1
    update.message.reply_text = AsyncMock()
    await bot._cmd_help(update, MagicMock())
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_kill_echoes_for_confirmation() -> None:
    bot = _bot(allowed=[1])
    update = MagicMock()
    update.effective_user.id = 1
    update.message.reply_text = AsyncMock()
    await bot._cmd_kill(update, MagicMock())
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "kill_switch" in text


@pytest.mark.asyncio
async def test_callback_yes_with_invalid_json_responds_gracefully() -> None:
    bot = _bot(allowed=[1])
    update = MagicMock()
    update.effective_user.id = 1
    update.callback_query.data = "yes:not-json"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    await bot._on_callback(update, MagicMock())
    update.callback_query.edit_message_text.assert_awaited_once()
    text = update.callback_query.edit_message_text.call_args[0][0]
    assert "invalid" in text


# Suppress unused import warnings for objects only used inside the
# stubbed handlers above.
_USED = (Approve,)
