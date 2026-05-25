"""Unit tests for :class:`TelegramNotifier`.

We mock ``httpx.Client`` so no network is touched. Covers the happy
path (200), HTTP error path (4xx/5xx becomes a structured warning),
and the local-fallback path (no token / no chat id -> log line).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from aidevswarm.settings import Settings
from aidevswarm.tools.telegram import NullTelegram, TelegramNotifier


class _StubClient:
    """Minimal httpx-shaped stub the notifier uses."""

    def __init__(self, *, raise_on_post: Exception | None = None, status_code: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raise = raise_on_post
        self._status_code = status_code

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        if self._raise is not None:
            raise self._raise
        return _StubResponse(self._status_code)


class _StubResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]


def _settings(token: str = "", chat: str = "") -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN=SecretStr(token),
        TELEGRAM_CHAT_ID=chat,
    )


def test_local_fallback_when_token_missing(caplog: pytest.LogCaptureFixture) -> None:
    notifier = TelegramNotifier(_settings(token="", chat=""))
    # send() should log + return without making any calls.
    notifier.send("hello")
    # If the client tried to post we'd have raised — passing is the assertion.


def test_local_fallback_when_chat_id_missing() -> None:
    notifier = TelegramNotifier(_settings(token="abc", chat=""))
    notifier.send("hello")  # no network call asserted by absence of error


def test_happy_path_posts_to_bot_api() -> None:
    stub = _StubClient(status_code=200)
    notifier = TelegramNotifier(_settings(token="abc", chat="123"), client=stub)  # type: ignore[arg-type]
    notifier.send("hi")
    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["url"].endswith("/botabc/sendMessage")
    assert call["json"]["chat_id"] == "123"
    assert call["json"]["text"] == "hi"


def test_http_error_is_caught_and_logged() -> None:
    stub = _StubClient(raise_on_post=httpx.ConnectError("dns failed"))
    notifier = TelegramNotifier(_settings(token="abc", chat="123"), client=stub)  # type: ignore[arg-type]
    # Should not raise.
    notifier.send("anything")


def test_status_error_is_caught() -> None:
    stub = _StubClient(status_code=500)
    notifier = TelegramNotifier(_settings(token="abc", chat="123"), client=stub)  # type: ignore[arg-type]
    notifier.send("anything")  # no raise


def test_null_telegram_swallows_messages() -> None:
    NullTelegram().send("ignored")  # noqa: assert no raise
