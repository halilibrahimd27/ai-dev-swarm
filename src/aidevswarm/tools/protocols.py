"""Protocols for the supporting tools.

The orchestrator depends on these interfaces; tests substitute in-memory
fakes that satisfy them without touching Redis, Docker, GitHub, or
Telegram.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID


class KillSwitch(Protocol):
    """A boolean flag the operator can flip to halt the orchestrator."""

    def is_tripped(self) -> bool: ...
    def trip(self, reason: str = "") -> None: ...
    def reset(self) -> None: ...


class TokenBudget(Protocol):
    """Per-milestone sanity cap + daily throttle."""

    def can_spend(self, *, milestone_id: UUID | None, requested: int) -> bool: ...
    def record_spend(
        self,
        *,
        project_id: UUID | None,
        milestone_id: UUID | None,
        role: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None: ...


class MemoryStore(Protocol):
    """pgvector-backed dedup memory for ideas."""

    def remember(self, project_id: UUID, embedding: Sequence[float]) -> None: ...
    def is_duplicate(
        self, embedding: Sequence[float], *, threshold: float = 0.92
    ) -> bool: ...


class Telegram(Protocol):
    """One-way notification channel (Phase 0)."""

    def send(self, message: str) -> None: ...


class GitHubTool(Protocol):
    """Publishes finished projects to GitHub."""

    def open_pr(
        self, *, repo_url: str, branch: str, title: str, body: str
    ) -> str: ...


class Sandbox(Protocol):
    """Ephemeral CI runner for generated projects."""

    def run_ci(self, workspace_dir: str) -> SandboxResult: ...


class SandboxResult(Protocol):
    @property
    def passed(self) -> bool: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...
    @property
    def exit_code(self) -> int: ...
