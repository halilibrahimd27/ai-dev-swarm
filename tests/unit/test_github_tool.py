"""Unit tests for :class:`GitHubPublisher`.

httpx is mocked so the tests never hit the live GitHub API. Covers
the happy 201 path, error >=300 path, and ``_parse_repo_url`` shapes.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from aidevswarm.settings import Settings
from aidevswarm.tools.github_tool import GitHubError, GitHubPublisher, NullGitHub


class _StubClient:
    def __init__(self, *, status: int, body: dict[str, Any] | None = None) -> None:
        self._status = status
        self._body = body or {}
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> Any:
        self.posts.append({"url": url, **kwargs})
        return _StubResponse(self._status, self._body)


class _StubResponse:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self.text = str(body)
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def _settings(token: str = "ghp_xxx") -> Settings:
    return Settings(GITHUB_TOKEN=SecretStr(token))


def test_open_pr_happy_returns_html_url() -> None:
    stub = _StubClient(
        status=201,
        body={"html_url": "https://github.com/owner/repo/pull/1"},
    )
    pub = GitHubPublisher(_settings(), client=stub)  # type: ignore[arg-type]
    url = pub.open_pr(
        repo_url="https://github.com/owner/repo",
        branch="main",
        title="t",
        body="b",
    )
    assert url == "https://github.com/owner/repo/pull/1"
    call = stub.posts[0]
    assert call["url"].endswith("/repos/owner/repo/pulls")
    assert call["json"]["title"] == "t"


def test_open_pr_non_2xx_raises_githuberror() -> None:
    stub = _StubClient(status=422, body={"message": "validation failed"})
    pub = GitHubPublisher(_settings(), client=stub)  # type: ignore[arg-type]
    with pytest.raises(GitHubError) as excinfo:
        pub.open_pr(repo_url="o/r", branch="main", title="x", body="y")
    assert "422" in str(excinfo.value)


def test_open_pr_accepts_owner_slash_repo_shorthand() -> None:
    stub = _StubClient(status=201, body={"html_url": "https://example.invalid/pr/1"})
    pub = GitHubPublisher(_settings(), client=stub)  # type: ignore[arg-type]
    pub.open_pr(repo_url="owner/repo", branch="main", title="t", body="b")
    assert stub.posts[0]["url"].endswith("/repos/owner/repo/pulls")


def test_open_pr_strips_trailing_slash_and_git_suffix() -> None:
    stub = _StubClient(status=201, body={"html_url": "https://x.invalid/pr/1"})
    pub = GitHubPublisher(_settings(), client=stub)  # type: ignore[arg-type]
    pub.open_pr(
        repo_url="https://github.com/owner/repo.git/",
        branch="main",
        title="t",
        body="b",
    )
    assert stub.posts[0]["url"].endswith("/repos/owner/repo/pulls")


def test_parse_repo_url_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        GitHubPublisher._parse_repo_url("not-a-repo")


def test_parse_repo_url_rejects_empty_owner_or_repo() -> None:
    with pytest.raises(ValueError):
        GitHubPublisher._parse_repo_url("/repo")


def test_authorization_header_uses_bearer_secret() -> None:
    stub = _StubClient(status=201, body={"html_url": "https://x.invalid/1"})
    pub = GitHubPublisher(_settings(token="sek-ret"), client=stub)  # type: ignore[arg-type]
    pub.open_pr(repo_url="o/r", branch="main", title="t", body="b")
    headers = stub.posts[0]["headers"]
    assert headers["Authorization"] == "Bearer sek-ret"
    assert headers["Accept"].startswith("application/vnd.github")


def test_null_github_returns_dry_run_url() -> None:
    url = NullGitHub().open_pr(repo_url="o/r", branch="main", title="t", body="b")
    assert "example.invalid" in url
