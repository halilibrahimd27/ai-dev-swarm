"""Protocols for the supporting tools.

The orchestrator depends on these interfaces; tests substitute in-memory
fakes that satisfy them without touching Redis, Docker, GitHub, or
Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class KillSwitch(Protocol):
    """Global + per-project kill flags the operator can flip.

    A *kill* is terminal (the tick moves the project to KILLED). A
    *pause* is recoverable: the tick skips the project, leaving its state
    untouched, so ``resume`` continues it where it left off.
    """

    def is_tripped(self) -> bool: ...
    def is_tripped_for(self, project_id: UUID) -> bool: ...
    def trip(self, reason: str = "") -> None: ...
    def trip_for(self, project_id: UUID, reason: str = "") -> None: ...
    def reset(self) -> None: ...
    def reset_for(self, project_id: UUID) -> None: ...
    # Pause is distinct from kill — recoverable, never terminal.
    def is_paused_for(self, project_id: UUID) -> bool: ...
    def pause_for(self, project_id: UUID) -> None: ...
    def unpause_for(self, project_id: UUID) -> None: ...


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


class Telegram(Protocol):
    """One-way notification channel (Phase 0)."""

    def send(self, message: str) -> None: ...


@dataclass(frozen=True)
class CreatedRepo:
    """A freshly-created GitHub repository.

    ``push_remote`` carries the credential-less remote URL
    (``https://x-access-token@github.com/<full_name>.git``) — the token
    is supplied at push time via ``GIT_ASKPASS`` so it never lands in
    ``.git/config``, argv, or logs.
    """

    full_name: str
    html_url: str
    push_remote: str


class GitHubTool(Protocol):
    """Publishes finished projects to GitHub."""

    def create_repo(self, *, name: str, description: str = "", private: bool = True) -> CreatedRepo:
        """Create a repo for the project; return its coordinates."""

    def open_pr(self, *, repo_url: str, branch: str, title: str, body: str) -> str: ...


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
