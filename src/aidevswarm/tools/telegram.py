"""One-way Telegram notifier.

Phase 0 only sends; Phase 5 adds inbound commands + intent parsing.

If the bot token or chat id are blank, the notifier degrades to a logger
call so local development works without a real bot configured.
"""

from __future__ import annotations

import httpx

from aidevswarm.logging_config import get_logger
from aidevswarm.settings import Settings

API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    """Concrete :class:`aidevswarm.tools.protocols.Telegram`."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=10.0)
        self._log = get_logger(__name__)

    def send(self, message: str) -> None:
        token = self._settings.telegram_bot_token.get_secret_value()
        chat_id = self._settings.telegram_chat_id
        if not token or not chat_id:
            self._log.info("telegram.local", message=message)
            return
        try:
            response = self._client.post(
                f"{API_BASE}/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._log.warning("telegram.failed", error=str(exc))


class NullTelegram:
    """Notifier that swallows every message; useful in tests."""

    def send(self, message: str) -> None:
        return None
