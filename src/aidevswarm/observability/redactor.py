"""Outbound-string redaction.

Every Phase 5 surface that leaves the trust boundary (SSE messages
to the web UI, Telegram bot messages) MUST pass its text through
:class:`SecretRedactor` first. The patterns come from
``Settings.redact_patterns`` (defaults cover the common token
shapes — Anthropic, OpenAI, GitHub, Slack, JWTs, Telegram bot
tokens).

Design:

  * One compiled pattern per regex; compilation happens once in
    ``__init__`` so the hot path is just ``re.sub``.
  * Each match is replaced by ``[REDACTED:<kind>]`` where ``<kind>``
    is a short tag derived from the pattern itself — readable for
    operators, useless for an attacker.
  * Idempotent: running the redactor twice changes nothing on the
    second pass (because the replacement contains no secret-shaped
    substrings).
  * Pure / sync. The redactor never logs the input; never raises;
    treats ``None``/empty as no-op.

The phase prompt mandates a unit-test gauntlet of ≥20 positive +
≥20 negative cases — see ``tests/unit/test_redactor.py``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_REDACTION = "[REDACTED:{kind}]"


def _kind_from_pattern(pat: str) -> str:
    """Derive a short, operator-readable tag from the pattern string.

    The tag is NOT security-sensitive; its only job is to tell the
    operator *what kind* of secret was redacted (a JWT? a GitHub
    token?). Keep it short and stable.
    """
    p = pat.lower()
    if "sk-ant" in p:
        return "anthropic"
    if "ghp_" in p or "github_pat" in p:
        return "github"
    if "xoxb" in p:
        return "slack"
    if "eyj" in p:
        return "jwt"
    if "sk-" in p:
        return "openai"
    if ":" in p and "0-9" in p:
        return "telegram"
    return "secret"


class SecretRedactor:
    """Apply a list of regex patterns to outbound strings."""

    __slots__ = ("_patterns",)

    def __init__(self, patterns: Iterable[str]) -> None:
        compiled: list[tuple[re.Pattern[str], str]] = []
        for raw in patterns:
            try:
                rx = re.compile(raw)
            except re.error:
                # A bad pattern in the operator's config must NEVER
                # take the control plane down. Skip it; the operator
                # sees the others still working.
                continue
            compiled.append((rx, _REDACTION.format(kind=_kind_from_pattern(raw))))
        self._patterns: tuple[tuple[re.Pattern[str], str], ...] = tuple(compiled)

    def __call__(self, text: object) -> str:
        return self.redact(text)

    def redact(self, text: object) -> str:
        """Return ``text`` with every secret-shaped substring replaced.

        ``None`` returns ``""``. Non-str inputs are coerced with
        ``str(...)`` so callers can safely feed e.g. Pydantic models'
        ``model_dump_json()`` output.
        """
        if text is None:
            return ""
        s = text if isinstance(text, str) else str(text)
        for rx, replacement in self._patterns:
            s = rx.sub(replacement, s)
        return s


__all__ = ["SecretRedactor"]
