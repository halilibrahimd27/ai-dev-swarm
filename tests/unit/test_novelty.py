"""Unit tests for :class:`NoveltyChecker`.

``respx`` intercepts every httpx request so no live network calls are
made. The two anchor tests prove the DoD behaviour:

  * A known-clone idea ("yet another todo app") scores < 0.6
    because GitHub returns close matches.
  * A genuinely niche idea scores > 0.6 because no close matches
    surface.

Additional tests cover the Jaccard helper, the 429 retry path, the
PyPI lookup, and graceful HTTP-error degradation.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from aidevswarm.crews.ideation.novelty import (
    GITHUB_SEARCH_URL,
    NoveltyChecker,
    SelfHistoryDedup,
    _jaccard,
    _tokenise,
)
from aidevswarm.schemas import Idea


def _idea(title: str, summary: str = "x", rationale: str = "x") -> Idea:
    return Idea(title=title, summary=summary, rationale=rationale)


# ---------------------------------------------------------------------------
# SelfHistoryDedup — reject ideas too close to the swarm's OWN projects
# ---------------------------------------------------------------------------


def test_self_dedup_flags_near_duplicate_of_own_project() -> None:
    history = [("Living API contract guardian", "mines client AST call sites")]
    dedup = SelfHistoryDedup(lambda: history, threshold=0.6)
    dup = dedup.find_duplicate(_idea("Living API contract guardian", "mines client AST call sites"))
    assert dup is not None
    assert dup.title == "Living API contract guardian"
    assert dup.similarity >= 0.6


def test_self_dedup_passes_a_distinct_idea() -> None:
    history = [("Living API contract guardian", "mines client AST call sites")]
    dedup = SelfHistoryDedup(lambda: history, threshold=0.6)
    assert dedup.find_duplicate(_idea("Quantum recipe organizer", "stores recipes")) is None


def test_self_dedup_empty_history_passes() -> None:
    dedup = SelfHistoryDedup(lambda: [], threshold=0.6)
    assert dedup.find_duplicate(_idea("anything", "at all")) is None


def test_self_dedup_provider_error_does_not_crash() -> None:
    def boom() -> list[tuple[str, str]]:
        raise RuntimeError("db down")

    dedup = SelfHistoryDedup(boom, threshold=0.6)
    assert dedup.find_duplicate(_idea("x", "y")) is None


def test_tokenise_alnum_only() -> None:
    assert _tokenise("Hello, World!") == {"hello", "world"}


def test_jaccard_overlap() -> None:
    assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard({"a"}, {"a"}) == 1.0


@respx.mock
def test_similar_purpose_different_name_is_caught_via_description() -> None:
    """A repo whose NAME shares no tokens with the idea title, but whose
    DESCRIPTION matches the idea summary, is now flagged (title-only would
    have missed it)."""
    respx.get(GITHUB_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "html_url": "https://github.com/x/zephyr",
                        "full_name": "x/zephyr",  # no overlap with the title tokens
                        "description": "reverse engineer api contracts from client repos "
                        "by static analysis and replay recorded traffic",
                    }
                ]
            },
        )
    )
    respx.get(url__regex=r"https://pypi\.org/.*").mock(return_value=httpx.Response(404))
    idea = _idea(
        "Quux",  # title shares nothing with "zephyr"
        summary="reverse engineer api contracts from client repos by static "
        "analysis and replay recorded traffic",
    )
    report = NoveltyChecker().check(idea)
    assert not report.is_novel  # caught via name+description vs title+summary
    assert report.top_matches and report.top_matches[0].similarity > 0.4


@respx.mock
def test_known_clone_scores_below_threshold() -> None:
    """The "yet another todo app" idea must be rejected by the Critic."""
    respx.get(GITHUB_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "html_url": "https://github.com/a/yet-another-todo-app",
                        "full_name": "a/yet-another-todo-app",
                    },
                    {
                        "html_url": "https://github.com/b/todo-app",
                        "full_name": "b/todo-app",
                    },
                ]
            },
        )
    )
    # Stub PyPI: every name returns 404.
    respx.get(url__regex=r"https://pypi\.org/.*").mock(return_value=httpx.Response(404))
    checker = NoveltyChecker()
    report = checker.check(_idea("yet another todo app"))
    assert not report.is_novel
    assert report.score < 0.6
    assert len(report.top_matches) >= 1
    assert any("yet-another-todo-app" in m.url for m in report.top_matches)


@respx.mock
def test_niche_idea_scores_above_threshold() -> None:
    respx.get(GITHUB_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    # Totally unrelated repo.
                    {
                        "html_url": "https://github.com/foo/baz",
                        "full_name": "foo/baz",
                    }
                ]
            },
        )
    )
    respx.get(url__regex=r"https://pypi\.org/.*").mock(return_value=httpx.Response(404))
    checker = NoveltyChecker()
    report = checker.check(_idea("autonomous postgres anomaly detective"))
    assert report.is_novel
    assert report.score > 0.6


@respx.mock
def test_pypi_hit_lowers_novelty() -> None:
    respx.get(GITHUB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"items": []}))
    # The 'redis' candidate exists on PyPI.
    respx.get(url__regex=r"https://pypi\.org/simple/redis/?").mock(return_value=httpx.Response(200))
    respx.get(url__regex=r"https://pypi\.org/.*").mock(return_value=httpx.Response(404))
    checker = NoveltyChecker()
    report = checker.check(_idea("redis"))
    # The PyPI 'redis' hit should appear and pull the score below the
    # vacuum (1.0) reference.
    assert any(m.source == "pypi" for m in report.top_matches)
    assert report.score < 1.0


@respx.mock
def test_github_429_triggers_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "aidevswarm.crews.ideation.novelty.time.sleep",
        lambda s: sleep_calls.append(s),
    )
    route = respx.get(GITHUB_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(200, json={"items": []}),
        ]
    )
    respx.get(url__regex=r"https://pypi\.org/.*").mock(return_value=httpx.Response(404))
    NoveltyChecker().check(_idea("anything"))
    assert route.call_count == 3
    assert len(sleep_calls) == 2  # one per retry


@respx.mock
def test_github_failure_does_not_crash() -> None:
    respx.get(GITHUB_SEARCH_URL).mock(side_effect=httpx.ConnectError("dns"))
    respx.get(url__regex=r"https://pypi\.org/.*").mock(return_value=httpx.Response(404))
    report = NoveltyChecker().check(_idea("niche project"))
    # No GitHub matches; PyPI also empty; score is the vacuum max.
    assert report.score == 1.0
    assert report.top_matches == []


@respx.mock
def test_github_token_attaches_authorization_header() -> None:
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"items": []}))
    respx.get(url__regex=r"https://pypi\.org/.*").mock(return_value=httpx.Response(404))
    NoveltyChecker(github_token="ghp_token").check(_idea("anything"))
    request = route.calls[0].request
    assert request.headers.get("Authorization") == "Bearer ghp_token"


def test_novelty_report_is_novel_flag() -> None:
    from aidevswarm.schemas import NoveltyReport

    assert NoveltyReport(score=0.65).is_novel is True
    assert NoveltyReport(score=0.6).is_novel is True
    assert NoveltyReport(score=0.59).is_novel is False
