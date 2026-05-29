"""Smoke tests for the pydantic-settings layer."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aidevswarm.settings import Settings, load_settings


def test_defaults_are_sane(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.daily_token_budget > 0
    assert settings.per_milestone_token_budget > 0
    assert settings.build_concurrency >= 1
    # Default is autonomous (no human approval gate); see Settings.
    assert settings.require_approval is False
    assert settings.ideation_min_score == 80
    assert settings.ideation_max_rounds == 5


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIDEVSWARM_DAILY_TOKEN_BUDGET", "12345")
    monkeypatch.setenv("AIDEVSWARM_REQUIRE_APPROVAL", "false")
    settings = Settings()
    assert settings.daily_token_budget == 12345
    assert settings.require_approval is False


def test_secret_str_is_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc")
    settings = Settings()
    # SecretStr's repr deliberately hides the value.
    assert "sk-ant-abc" not in repr(settings.anthropic_api_key)
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-abc"


def test_pg_dsn_includes_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "topsecret")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_DB", "db")
    settings = Settings()
    dsn = settings.pg_dsn
    assert "user=u" in dsn
    assert "dbname=db" in dsn
    assert "password=topsecret" in dsn


def test_api_host_loopback_or_zero() -> None:
    """The validator allows loopback always + 0.0.0.0 inside containers.

    The security guarantee for the docker-compose path comes from the
    `127.0.0.1:8080:8080` publish line in docker-compose.yml, NOT from
    uvicorn's bind address — see Phase 6 ADR / THREAT_MODEL. The
    validator still refuses LAN IPs so a misconfigured `.env` on bare
    metal can't accidentally expose the API.
    """
    assert Settings(AIDEVSWARM_API_HOST="127.0.0.1").api_host == "127.0.0.1"
    assert Settings(AIDEVSWARM_API_HOST="localhost").api_host == "localhost"
    assert Settings(AIDEVSWARM_API_HOST="0.0.0.0").api_host == "0.0.0.0"
    with pytest.raises(ValidationError):
        Settings(AIDEVSWARM_API_HOST="10.0.0.5")
    with pytest.raises(ValidationError):
        Settings(AIDEVSWARM_API_HOST="192.168.1.1")


def test_telegram_allowed_user_ids_parses_csv() -> None:
    s = Settings(AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS="111,222,333")
    assert s.telegram_allowed_user_ids == [111, 222, 333]
    # Empty -> empty list, not [0]
    assert Settings(AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS="").telegram_allowed_user_ids == []


def test_redact_patterns_default_covers_common_secrets() -> None:
    """Defaults must catch anthropic, github, slack, jwt, telegram tokens."""
    patterns = Settings().redact_patterns
    joined = " | ".join(patterns)
    assert "sk-ant" in joined
    assert "ghp_" in joined
    assert "eyJ" in joined  # JWT prefix


def test_redact_patterns_csv_override() -> None:
    """Operators can replace the default list via env var."""
    s = Settings(AIDEVSWARM_REDACT_PATTERNS="foo,bar")
    assert s.redact_patterns == ["foo", "bar"]
