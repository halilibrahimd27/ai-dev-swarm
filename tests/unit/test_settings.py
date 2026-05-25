"""Smoke tests for the pydantic-settings layer."""

from __future__ import annotations

import pytest

from aidevswarm.settings import Settings, load_settings


def test_defaults_are_sane(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.daily_token_budget > 0
    assert settings.per_milestone_token_budget > 0
    assert settings.build_concurrency >= 1
    assert settings.require_approval is True


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
