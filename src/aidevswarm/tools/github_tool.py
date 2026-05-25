"""GitHub publisher in pr_only mode.

Phase 0 only knows how to open a PR. ``auto_merge`` is configurable in
``Settings.github_mode`` but explicitly NOT exercised here — the operator
flips it on later, once trust is established.

The implementation uses the REST API directly via ``httpx`` to avoid
pulling another dependency.
"""

from __future__ import annotations

import httpx

from aidevswarm.logging_config import get_logger
from aidevswarm.settings import Settings

API_BASE = "https://api.github.com"


class GitHubError(RuntimeError):
    """Non-2xx response from the GitHub API."""


class GitHubPublisher:
    """Concrete :class:`aidevswarm.tools.protocols.GitHubTool`."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=15.0)
        self._log = get_logger(__name__)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.github_token.get_secret_value()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def open_pr(self, *, repo_url: str, branch: str, title: str, body: str) -> str:
        """Open a PR from ``branch`` into ``main``; return the HTML URL."""
        owner, repo = self._parse_repo_url(repo_url)
        response = self._client.post(
            f"{API_BASE}/repos/{owner}/{repo}/pulls",
            headers=self._headers(),
            json={"head": branch, "base": "main", "title": title, "body": body},
        )
        if response.status_code >= 300:
            raise GitHubError(
                f"POST /pulls failed: status={response.status_code} body={response.text}"
            )
        data = response.json()
        url = str(data.get("html_url", ""))
        self._log.info("github.pr_opened", url=url, repo=f"{owner}/{repo}")
        return url

    @staticmethod
    def _parse_repo_url(repo_url: str) -> tuple[str, str]:
        # Accept "owner/repo" or "https://github.com/owner/repo[.git]".
        cleaned = repo_url.rstrip("/").removesuffix(".git")
        if "/" not in cleaned:
            raise ValueError(f"invalid repo_url: {repo_url!r}")
        parts = cleaned.split("/")
        owner, repo = parts[-2], parts[-1]
        if not owner or not repo:
            raise ValueError(f"invalid repo_url: {repo_url!r}")
        return owner, repo


class NullGitHub:
    """No-op publisher used in tests and dry runs."""

    def open_pr(self, *, repo_url: str, branch: str, title: str, body: str) -> str:
        del repo_url, branch, title, body
        return "https://example.invalid/dry-run/pr/0"
