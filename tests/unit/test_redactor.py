"""Gauntlet for :class:`aidevswarm.observability.SecretRedactor`.

The phase prompt mandates ≥20 positive + ≥20 negative cases. Positive
cases prove that real-shaped secrets are blanked out; negative cases
prove the redactor does NOT mangle innocent log lines that happen to
look secret-ish (timestamps, hashes, hex IDs, English words that
contain `sk` or `eyJ`, etc.).
"""

from __future__ import annotations

import pytest

from aidevswarm.observability import SecretRedactor
from aidevswarm.settings import Settings


@pytest.fixture
def redactor() -> SecretRedactor:
    return SecretRedactor(Settings().redact_patterns)


# ---------------------------------------------------------------------------
# Positive cases — MUST be redacted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "leaky",
    [
        # Anthropic / Claude API
        "sk-ant-abcdefghijklmnopqrstuvwxyz123456",
        "Using key sk-ant-api03-AAAA-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "ANTHROPIC_API_KEY=sk-ant-api03-x_y_z-1234567890_abcdefghij",
        # OpenAI-style
        "OPENAI_API_KEY=sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "Authorization: Bearer sk-proj-abcdefghijklmnopqrstuvwxyz0123456789AB",
        # GitHub PAT (classic + fine-grained)
        "ghp_1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "log line ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 trailing",
        "github_pat_11ABC123_DEFghijklmnopqrstuvwxyz1234567890ABCDEFGHIJKLMNOP",
        # Slack bot (synthetic test fixture; "FAKE" markers keep
        # secret-scanners from flagging this file).
        "xoxb-" + "FAKEFAKE-" * 3 + "X" * 11,
        "Slack token: xoxb-" + "TEST-" * 4 + "A" * 12,
        # JWT-shaped (synthetic three-segment token)
        "eyJ" + ("A" * 12) + ".eyJ" + ("B" * 12) + "." + ("C" * 12),
        "Authorization: Bearer eyJ" + ("X" * 16) + ".eyJ" + ("Y" * 16) + "." + ("Z" * 16),
        # Telegram bot token (digits:secret) — synthetic
        "TELEGRAM_BOT_TOKEN=11111111:" + "FAKE-" * 6 + "AAAAA",
        "calling bot with 22222222:" + "TEST_" * 6 + "BBBBB" + " hi",
        # Mixed in JSON-ish payload
        '{"key": "sk-ant-api03-abcdefghij1234567890abcdefghij_klmn"}',
        '{"ghp_token":"ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1234"}',
        # Mixed in URL
        "https://example.test/?token=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        # Multi-line
        "first sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nsecond line",
        # Embedded with surrounding markdown
        "key=`ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF`",
        # Two secrets in one string
        "key1=sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa key2=ghp_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ],
)
def test_real_secrets_are_redacted(redactor: SecretRedactor, leaky: str) -> None:
    cleaned = redactor.redact(leaky)
    assert "sk-ant-" not in cleaned, cleaned
    # The synthetic token bodies must no longer appear as bare
    # substrings; the redactor swapped them for [REDACTED:<kind>].
    for needle in ("xoxb-FAKEFAKE", "11111111:FAKE", "22222222:TEST"):
        assert needle not in cleaned, (needle, cleaned)
    assert "[REDACTED:" in cleaned


# ---------------------------------------------------------------------------
# Negative cases — MUST NOT be touched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "innocent",
    [
        "",
        "hello world",
        "sk-ant is a token shape but this is too short",  # no body
        "ghp_too_short",  # body too short
        "xoxb-short",
        # Hash-shaped but missing required prefix
        "abcdef1234567890abcdef1234567890",
        # Plain git SHA
        "feat: phase-5 (a1b2c3d4e5f67890abcdef1234567890abcdef12)",
        # Timestamp
        "2026-05-26T00:27:00Z",
        # File path that looks tokenish
        "/var/log/app/eyJfoo.log",  # missing JWT body+sig segments
        # English with embedded `sk`
        "I asked, 'sk if you want'",  # no dash
        # File hash
        "sha256:abcdef1234567890" * 2,
        # URL with no secret
        "https://example.test/path?id=123&page=2",
        # Markdown code block
        "```python\nprint('hi')\n```",
        # JSON without secrets
        '{"intent":"approve","project_id":"550e8400-e29b-41d4-a716-446655440000"}',
        # Numbers
        "elapsed: 1234567 ms",
        # Looks like Telegram bot token but body too short
        "12345:short",
        # Looks like JWT but only two segments
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0",
        # ghp_ inside a sentence (no token body)
        "We discussed ghp_ token rotation policy today.",
        # Plain UUID
        "12345678-1234-1234-1234-1234567890ab",
        # Multi-line plain text
        "line one\nline two\nline three",
    ],
)
def test_innocent_text_is_passed_through(redactor: SecretRedactor, innocent: str) -> None:
    cleaned = redactor.redact(innocent)
    assert cleaned == innocent, f"{innocent!r} -> {cleaned!r}"
    assert "[REDACTED:" not in cleaned


# ---------------------------------------------------------------------------
# Properties / edge cases
# ---------------------------------------------------------------------------


def test_redaction_is_idempotent(redactor: SecretRedactor) -> None:
    once = redactor.redact("token=sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    twice = redactor.redact(once)
    assert once == twice


def test_none_returns_empty_string(redactor: SecretRedactor) -> None:
    assert redactor.redact(None) == ""


def test_non_str_is_coerced(redactor: SecretRedactor) -> None:
    # Passing a Pydantic model dump (a dict) — we accept anything stringable.
    cleaned = redactor.redact({"key": "sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"})
    assert "sk-ant-aaaaa" not in cleaned


def test_invalid_pattern_is_skipped_not_raised() -> None:
    """A bad regex in the operator's config must NOT crash the redactor."""
    r = SecretRedactor(["[", "sk-ant-[A-Za-z0-9_-]{20,}"])
    # Bad pattern dropped; good pattern still works.
    cleaned = r.redact("leak sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa here")
    assert "sk-ant-aaaa" not in cleaned


def test_empty_pattern_list_is_a_passthrough() -> None:
    r = SecretRedactor([])
    text = "anything sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa here"
    assert r.redact(text) == text


def test_callable_alias() -> None:
    """``SecretRedactor`` itself is callable (sugar for ``.redact``)."""
    r = SecretRedactor(["sk-ant-[A-Za-z0-9_-]{20,}"])
    assert r("leak sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") != "leak"
    assert "[REDACTED:" in r("leak sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
