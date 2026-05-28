"""Telegram bot — bidirectional control surface.

Polling mode (no webhook, no port). Every handler runs the
allow-list gate first; non-allow-listed users are silently denied.

Routing:
  * ``/start``, ``/help``                 — usage hint.
  * ``/list``                             — emit a ``ListState`` command.
  * ``/kill``                              — emit a global ``KillSwitch``.
  * inline button callbacks               — Approve / Drop / Comment +
    the ``[Yes][No]`` confirmation flow for destructive intents.
  * free-form text                        — :class:`HaikuIntentParser`
    parses into a ``Command``, the bot echoes destructive intents for
    confirmation, and dispatches via :class:`CommandRouter`.

Every outbound message passes through :class:`SecretRedactor` so
secrets in agent transcripts never reach Telegram.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from aidevswarm.logging_config import get_logger
from aidevswarm.observability import SecretRedactor
from aidevswarm.orchestrator.command_router import CommandRouter
from aidevswarm.schemas import (
    DESTRUCTIVE_INTENTS,
    Approve,
    Command,
    KillSwitch,
    ListState,
)
from aidevswarm.settings import Settings
from aidevswarm.telegram.intent import HaikuIntentParser, IntentParseError


@dataclass
class TelegramBot:
    """Polling-mode bot wiring inline keyboards + Haiku-parsed text."""

    settings: Settings
    router: CommandRouter
    parser: HaikuIntentParser
    redactor: SecretRedactor

    def __post_init__(self) -> None:
        self._log = get_logger(__name__)
        self._app: Application | None = None  # type: ignore[type-arg]
        # Pending destructive commands awaiting a [Yes][No] tap, keyed by
        # a short token. Telegram caps callback_data at 64 bytes, so we
        # CANNOT round-trip the full command JSON through the button —
        # we stash it here and pass only the token.
        self._pending: dict[str, Command] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def build_application(self) -> Application:  # type: ignore[type-arg]  # pragma: no cover — live PTB only
        """Wire the Application + handlers. Tests can call this directly."""
        token = self.settings.telegram_bot_token.get_secret_value()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is empty; cannot start the bot")
        app: Application = Application.builder().token(token).build()  # type: ignore[type-arg]
        app.add_handler(CommandHandler("start", self._cmd_help))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("list", self._cmd_list))
        app.add_handler(CommandHandler("kill", self._cmd_kill))
        app.add_handler(CallbackQueryHandler(self._on_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self._app = app
        return app

    async def run_polling(self) -> None:  # pragma: no cover — live PTB only
        """Run the bot's polling loop inside the orchestrator's gather.

        ``Application.run_polling`` in python-telegram-bot 21 is a
        BLOCKING call (it runs its own loop). We invoke it from a
        worker thread via ``asyncio.to_thread`` so the orchestrator's
        ``asyncio.gather`` over Scheduler + ProjectPool + FastAPI
        keeps progressing.
        """
        import asyncio

        app = self.build_application()
        # close_loop=False is critical — the orchestrator owns its
        # own loop, but app.run_polling runs in the worker thread
        # under a fresh loop of its own (PTB convention).
        await asyncio.to_thread(app.run_polling)

    # ------------------------------------------------------------------
    # Allow-list gate
    # ------------------------------------------------------------------

    def _is_allowed(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        allowed = self.settings.telegram_allowed_user_ids
        # An empty allow-list locks the bot down completely. The
        # operator MUST set AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS.
        return user_id in allowed

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    async def _cmd_help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update.effective_user.id if update.effective_user else None):
            return
        text = (
            "ai-dev-swarm bot — commands:\n"
            "/list    — list active projects\n"
            "/kill    — global kill switch (requires confirm)\n"
            "Or just type what you want:\n"
            "  'approve project <id>', 'pause this project',\n"
            "  'rescope to <new scope>', 'note: focus on tests', …"
        )
        await self._reply(update, text)

    async def _cmd_list(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update.effective_user.id if update.effective_user else None):
            return
        result = self.router.dispatch(ListState())
        await self._reply(update, f"list_state: {result.detail}")

    async def _cmd_kill(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update.effective_user.id if update.effective_user else None):
            return
        await self._echo_for_confirmation(
            update,
            KillSwitch(reason="operator /kill"),
        )

    # ------------------------------------------------------------------
    # Free-form text -> Haiku intent
    # ------------------------------------------------------------------

    async def _on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update.effective_user.id if update.effective_user else None):
            return
        text = update.message.text if update.message else ""
        if not text:
            return
        try:
            command = await self.parser.parse(text)
        except IntentParseError as exc:
            self._log.info("bot.intent_parse_failed", error=str(exc))
            await self._reply(
                update,
                "I didn't understand that. Try a known command (/help) or rephrase.",
            )
            return
        await self._dispatch(update, command)

    # ------------------------------------------------------------------
    # Inline keyboard callbacks
    # ------------------------------------------------------------------

    async def _on_callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update.effective_user.id if update.effective_user else None):
            return
        query = update.callback_query
        if query is None or not query.data:
            return
        await query.answer()
        # Callback data: "approve:<id>" / "drop:<id>" / "yes:<token>" / "no:<token>".
        prefix, _sep, payload = query.data.partition(":")
        handler = self._callback_handlers().get(prefix)
        if handler is not None:
            await handler(query, payload)

    def _callback_handlers(self) -> dict[str, Any]:
        return {
            "no": self._cb_no,
            "yes": self._cb_yes,
            "approve": self._cb_approve,
            "drop": self._cb_drop,
        }

    async def _cb_no(self, query: Any, payload: str) -> None:
        self._pending.pop(payload, None)
        await self._reply_via_query(query, "cancelled.")

    async def _cb_yes(self, query: Any, payload: str) -> None:
        command = self._pending.pop(payload, None)
        if command is None:
            await self._reply_via_query(
                query, "this confirmation expired — please re-issue the command"
            )
            return
        confirmed = command.model_copy(update={"confirmed": True})
        result = self.router.dispatch(confirmed)
        await self._reply_via_query(query, self.redactor(f"{confirmed.intent}: {result.detail}"))

    async def _cb_approve(self, query: Any, payload: str) -> None:
        try:
            pid = UUID(payload)
        except ValueError:
            return
        result = self.router.dispatch(Approve(project_id=pid))
        await self._reply_via_query(query, f"approve: {result.detail}")

    async def _cb_drop(self, query: Any, _payload: str) -> None:
        await self._reply_via_query(query, "drop button is a UI hint only; type 'drop project'")

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    async def _dispatch(self, update: Update, command: Command) -> None:
        if command.intent in DESTRUCTIVE_INTENTS:
            await self._echo_for_confirmation(update, command)
            return
        result = self.router.dispatch(command)
        await self._reply(update, self.redactor(f"{command.intent}: {result.detail}"))

    async def _echo_for_confirmation(self, update: Update, command: Command) -> None:
        """Send the parsed command back with a [Yes][No] inline keyboard.

        The command is stashed under a short token; only the token rides
        in ``callback_data`` (Telegram's 64-byte cap can't fit the JSON).
        """
        token = uuid4().hex
        self._pending[token] = command
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Yes", callback_data=f"yes:{token}"),
                    InlineKeyboardButton("No", callback_data=f"no:{token}"),
                ]
            ]
        )
        pretty = json.dumps(command.model_dump(mode="json"))
        text = self.redactor(f"I understood: **{command.intent}**\n```\n{pretty}\n```\nConfirm?")
        if update.message is not None:
            await update.message.reply_text(text, reply_markup=keyboard)

    async def _reply(self, update: Update, text: str) -> None:
        if update.message is not None:
            await update.message.reply_text(self.redactor(text))

    async def _reply_via_query(self, query: Any, text: str) -> None:
        with contextlib.suppress(Exception):
            await query.edit_message_text(self.redactor(text))


__all__ = ["TelegramBot"]
