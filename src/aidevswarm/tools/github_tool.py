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
from aidevswarm.tools.protocols import CreatedRepo

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

    def create_repo(self, *, name: str, description: str = "", private: bool = True) -> CreatedRepo:
        """Create a repo under the authenticated user (or ``github_owner`` org).

        Idempotent-friendly: if the repo already exists (HTTP 422), we
        look it up and return its coordinates instead of failing, so a
        project resumed across days re-attaches to its existing repo.
        """
        owner = self._settings.github_owner.strip()
        # POST /orgs/{org}/repos when an org owner is configured AND it
        # isn't the token's own user; otherwise POST /user/repos.
        endpoint = f"{API_BASE}/user/repos"
        payload = {
            "name": name,
            "description": description[:350],
            "private": private,
            "auto_init": False,
        }
        response = self._client.post(endpoint, headers=self._headers(), json=payload)
        if response.status_code == 422:
            # Most likely "name already exists on this account".
            self._log.info("github.repo_exists", name=name)
            return self._lookup_repo(owner or self._login(), name)
        if response.status_code >= 300:
            raise GitHubError(
                f"POST /user/repos failed: status={response.status_code} body={response.text}"
            )
        data = response.json()
        return self._created_from(data)

    def _lookup_repo(self, owner: str, name: str) -> CreatedRepo:
        response = self._client.get(f"{API_BASE}/repos/{owner}/{name}", headers=self._headers())
        if response.status_code >= 300:
            raise GitHubError(f"GET /repos/{owner}/{name} failed: status={response.status_code}")
        return self._created_from(response.json())

    def _login(self) -> str:
        response = self._client.get(f"{API_BASE}/user", headers=self._headers())
        if response.status_code >= 300:
            raise GitHubError(f"GET /user failed: status={response.status_code}")
        return str(response.json().get("login", ""))

    def _created_from(self, data: dict[str, object]) -> CreatedRepo:
        full_name = str(data.get("full_name", ""))
        html_url = str(data.get("html_url", ""))
        # Credential-less remote: the token is injected at push time via
        # GIT_ASKPASS, never persisted here.
        push_remote = f"https://x-access-token@github.com/{full_name}.git"
        self._log.info("github.repo_ready", repo=full_name, url=html_url)
        return CreatedRepo(full_name=full_name, html_url=html_url, push_remote=push_remote)

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

    def create_repo(self, *, name: str, description: str = "", private: bool = True) -> CreatedRepo:
        del description, private
        return CreatedRepo(
            full_name=f"dry-run/{name}",
            html_url=f"https://example.invalid/dry-run/{name}",
            push_remote=f"https://x-access-token@github.com/dry-run/{name}.git",
        )

    def open_pr(self, *, repo_url: str, branch: str, title: str, body: str) -> str:
        del repo_url, branch, title, body
        return "https://example.invalid/dry-run/pr/0"
