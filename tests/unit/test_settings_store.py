"""Unit tests for the operator-editable settings store.

Covers coercion/validation (the only place operator input is checked),
applying overrides onto a live Settings object, best-effort apply_all,
and the snapshot (which must never expose secrets).
"""

from __future__ import annotations

import pytest

from aidevswarm.db.settings_store import (
    EDITABLE_SETTINGS,
    apply_all,
    apply_override,
    coerce,
    get_spec,
    is_editable,
    snapshot,
)
from aidevswarm.settings import Settings

# Keys that must NEVER be editable from the UI (secrets / infra).
_FORBIDDEN = {
    "anthropic_api_key",
    "github_token",
    "postgres_password",
    "pg_host",
    "pg_port",
    "api_host",
    "api_port",
    "redact_patterns",
    "telegram_bot_token",
    "model_strong",
    "model_fast",
}


def _spec(key: str):
    spec = get_spec(key)
    assert spec is not None
    return spec


def test_coerce_int_with_range() -> None:
    assert coerce(_spec("ideation_min_score"), "80") == 80
    with pytest.raises(ValueError):
        coerce(_spec("ideation_min_score"), "120")  # > max 100
    with pytest.raises(ValueError):
        coerce(_spec("ideation_min_score"), "-1")  # < min 0
    with pytest.raises(ValueError):
        coerce(_spec("ideation_min_score"), "notanint")


def test_coerce_float() -> None:
    assert coerce(_spec("auto_split_max_cost_usd"), "2.5") == 2.5
    with pytest.raises(ValueError):
        coerce(_spec("auto_split_max_cost_usd"), "-1")  # < min 0


def test_coerce_bool() -> None:
    assert coerce(_spec("require_approval"), "true") is True
    assert coerce(_spec("require_approval"), "off") is False
    with pytest.raises(ValueError):
        coerce(_spec("require_approval"), "maybe")


def test_coerce_enum() -> None:
    assert coerce(_spec("sandbox_mode"), "docker") == "docker"
    with pytest.raises(ValueError):
        coerce(_spec("sandbox_mode"), "rocket")


def test_apply_override_mutates_live_settings() -> None:
    s = Settings()
    value = apply_override(s, "daily_token_budget", "750000")
    assert value == 750000
    assert s.daily_token_budget == 750000  # the live object reflects it


def test_apply_all_skips_unknown_and_bad() -> None:
    s = Settings()
    apply_all(
        s,
        {
            "daily_token_budget": "123456",  # good
            "not_a_real_key": "x",  # unknown -> skipped
            "ideation_min_score": "999",  # out of range -> skipped
        },
    )
    assert s.daily_token_budget == 123456
    assert s.ideation_min_score == 80  # unchanged default (bad value skipped)


def test_snapshot_exposes_no_secrets() -> None:
    keys = {row["key"] for row in snapshot(Settings())}
    assert keys == {s.key for s in EDITABLE_SETTINGS}
    assert keys.isdisjoint(_FORBIDDEN)


def test_snapshot_carries_metadata_and_value() -> None:
    rows = {r["key"]: r for r in snapshot(Settings())}
    row = rows["sandbox_mode"]
    assert row["kind"] == "enum"
    assert row["restart_required"] is True
    assert "docker" in row["choices"]
    assert rows["daily_token_budget"]["restart_required"] is False


def test_is_editable() -> None:
    assert is_editable("daily_token_budget") is True
    assert is_editable("anthropic_api_key") is False
