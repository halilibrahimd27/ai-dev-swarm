"""Claude Haiku-driven intent parser for free-form Telegram messages.

Every Telegram message that is NOT a known slash command or button
callback is fed to Haiku with a STRICT system prompt that asks for
JSON-only output keyed by the same ``intent`` discriminator the
web UI uses. Pydantic's ``TypeAdapter[Command].validate_python``
then enforces the schema — anything off-list bounces.

Design choices:
  * One Haiku call per message. No batching, no streaming.
  * The system prompt enumerates every legal intent + required
    fields. Off-list intents are rejected at the LLM AND at the
    schema layer.
  * The parser DOES NOT execute anything. It returns a
    :class:`Command` (or raises :class:`IntentParseError`). The
    bot consumes the result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx
from pydantic import TypeAdapter, ValidationError

from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import Command
from aidevswarm.settings import Settings

_COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)

_SYSTEM_PROMPT = """You are the command-intent parser for ai-dev-swarm.
Reply with ONE JSON object that matches the schema below. NO prose,
NO markdown, NO comments — only the JSON.

Allowed values for the ``intent`` field (others are forbidden):
  approve              {"intent":"approve","project_id":"<UUID>"}
  inject_note          {"intent":"inject_note","project_id":"<UUID>","body":"<text>","role":null}
  pause_project        {"intent":"pause_project","project_id":"<UUID>"}
  resume_project       {"intent":"resume_project","project_id":"<UUID>"}
  abort_project        {"intent":"abort_project","project_id":"<UUID>","reason":"<text>"}
  rescope              {"intent":"rescope","project_id":"<UUID>","new_scope":"<text>"}
  transform_project    {"intent":"transform_project","project_id":"<UUID>","new_direction":"<text>"}
  drop_and_start_new   {"intent":"drop_and_start_new","project_id":"<UUID>"}
  switch_to_idea       {"intent":"switch_to_idea","current_project_id":"<UUID>","new_idea_id":"<UUID>"}
  reject_idea          {"intent":"reject_idea","idea_id":"<UUID>"}
  kill_switch          {"intent":"kill_switch","reason":"<text>"}
  list_state           {"intent":"list_state"}
  show_transcript      {"intent":"show_transcript","project_id":"<UUID>"}

Rules:
  - If you can't determine the right intent or required fields, reply
    with {"intent":"list_state"}.
  - NEVER invent UUIDs. If a UUID is required but not given, reply
    with {"intent":"list_state"}.
  - For destructive intents (abort_project, rescope, transform_project,
    drop_and_start_new, switch_to_idea, reject_idea, kill_switch), DO
    NOT set ``confirmed`` — the bot handles confirmation.
  - Output JSON only. No backticks.

The operator's message follows.
"""


class IntentParseError(ValueError):
    """Raised when the Haiku response can't be coerced into a Command."""


@dataclass
class HaikuIntentParser:
    """Wrap one Anthropic Messages API call into a typed Command."""

    settings: Settings

    def __post_init__(self) -> None:
        self._log = get_logger(__name__)

    async def parse(self, text: str, *, context_project_id: str | None = None) -> Command:
        """Return a typed Command for ``text``; raise on schema mismatch."""
        key = self.settings.anthropic_api_key.get_secret_value()
        if not key:
            # No key in dev / tests — surface a clear error rather than
            # making a phantom HTTP call.
            raise IntentParseError("ANTHROPIC_API_KEY is empty; cannot parse intent")

        user_msg = text.strip()
        if context_project_id:
            user_msg = f"(current project_id: {context_project_id})\n{user_msg}"

        body = {
            "model": self.settings.haiku_model,
            "max_tokens": 256,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_msg}],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            )
        response.raise_for_status()
        data = response.json()
        raw = _extract_text(data)
        return _validate(raw)


def _extract_text(payload: dict[str, object]) -> str:
    """Pull the first text block out of an Anthropic Messages response."""
    content = payload.get("content") or []
    if not isinstance(content, list):
        raise IntentParseError(f"unexpected response shape: {payload!r}")
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                return text
    raise IntentParseError(f"no text block in response: {payload!r}")


def _validate(raw: str) -> Command:
    """JSON-decode + schema-validate; recover ONLY from formatting wobble."""
    # Some models wrap output in ```json ... ``` even when told not to.
    cleaned = _strip_fences(raw).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise IntentParseError(f"haiku did not return JSON: {cleaned!r}") from exc
    try:
        return _COMMAND_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise IntentParseError(f"haiku produced an off-schema command: {data!r}") from exc


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text)


__all__ = ["HaikuIntentParser", "IntentParseError"]
