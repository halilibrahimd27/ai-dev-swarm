"""Unit tests for the Phase 6 startup pre-flight.

The integration with Alembic + the real orchestrator entry is
exercised by the docker-compose fresh-clone test; here we cover the
pure pre-flight logic: required-key validation, friendly errors,
and the main_with_preflight exit-1 path.
"""

from __future__ import annotations

from typing import Any

import pytest

from aidevswarm.bootstrap import (
    REQUIRED_KEYS,
    MissingRequiredEnv,
    main_with_preflight,
    validate_required_env,
)
from aidevswarm.settings import Settings


def test_required_keys_at_least_lists_anthropic_and_postgres() -> None:
    """Future maintainers MUST keep these two in the required list."""
    assert "ANTHROPIC_API_KEY" in REQUIRED_KEYS
    assert "POSTGRES_PASSWORD" in REQUIRED_KEYS


def test_validate_accepts_a_fully_populated_settings() -> None:
    settings = Settings(ANTHROPIC_API_KEY="sk-ant-test", POSTGRES_PASSWORD="topsecret")
    # No exception = success.
    validate_required_env(settings)


def test_validate_refuses_empty_anthropic_key() -> None:
    settings = Settings(ANTHROPIC_API_KEY="", POSTGRES_PASSWORD="topsecret")
    with pytest.raises(MissingRequiredEnv) as exc_info:
        validate_required_env(settings)
    assert exc_info.value.key == "ANTHROPIC_API_KEY"
    msg = str(exc_info.value)
    assert "ai-dev-swarm:" in msg
    assert "ANTHROPIC_API_KEY" in msg
    assert "console.anthropic.com" in msg


def test_validate_refuses_whitespace_only_anthropic_key() -> None:
    settings = Settings(ANTHROPIC_API_KEY="   ", POSTGRES_PASSWORD="topsecret")
    with pytest.raises(MissingRequiredEnv):
        validate_required_env(settings)


def test_validate_refuses_empty_postgres_password() -> None:
    settings = Settings(ANTHROPIC_API_KEY="sk-ant-test", POSTGRES_PASSWORD="")
    with pytest.raises(MissingRequiredEnv) as exc_info:
        validate_required_env(settings)
    assert exc_info.value.key == "POSTGRES_PASSWORD"


def test_missing_env_error_lists_remediation_steps() -> None:
    """Each required key has an associated `_KEY_HELP` entry."""
    settings = Settings(ANTHROPIC_API_KEY="", POSTGRES_PASSWORD="x")
    with pytest.raises(MissingRequiredEnv) as exc_info:
        validate_required_env(settings)
    msg = str(exc_info.value)
    # Three numbered steps (cf. _KEY_HELP['ANTHROPIC_API_KEY']).
    assert "1)" in msg
    assert "2)" in msg
    assert "3)" in msg


def test_main_with_preflight_exits_1_on_missing_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing key path: clean exit 1 + friendly stderr (no traceback)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    with pytest.raises(SystemExit) as exc_info:
        main_with_preflight()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "ai-dev-swarm: ANTHROPIC_API_KEY is empty" in err
    # Should be ONE friendly block, no python traceback markers.
    assert "Traceback" not in err


def test_main_with_preflight_exits_1_with_friendly_message_on_db_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Migration failure path: also friendly, no traceback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "topsecret")
    # Force run_migrations to raise — simulates "Postgres not up yet".
    from aidevswarm import bootstrap

    def boom(settings: Any) -> None:
        raise ConnectionRefusedError("simulated")

    monkeypatch.setattr(bootstrap, "run_migrations", boom)
    with pytest.raises(SystemExit) as exc_info:
        main_with_preflight()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "ai-dev-swarm: startup failed during pre-flight" in err
    assert "Postgres is running" in err
    assert "Traceback" not in err


def test_main_with_preflight_delegates_to_orchestrator_main_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: pre-flight passes, then orchestrator.main is called."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "topsecret")
    from aidevswarm import bootstrap

    called: list[bool] = []
    monkeypatch.setattr(bootstrap, "run_migrations", lambda s: None)

    import aidevswarm.orchestrator.orchestrator as orchestrator_mod

    monkeypatch.setattr(orchestrator_mod, "main", lambda: called.append(True))

    main_with_preflight()
    assert called == [True]
