"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prevent a real ``.env`` from leaking into tests.

    Pydantic-settings would pick up the developer's ``.env`` otherwise,
    which makes test outcomes depend on local configuration.
    """
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if (
            key.startswith(("AIDEVSWARM_", "POSTGRES_", "TELEGRAM_", "GITHUB_"))
            or key == "ANTHROPIC_API_KEY"
        ):
            monkeypatch.delenv(key, raising=False)
