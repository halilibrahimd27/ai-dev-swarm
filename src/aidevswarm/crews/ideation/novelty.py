"""Prior-art / novelty check for ideation.

For each candidate :class:`aidevswarm.schemas.Idea`, queries the
GitHub Search API (repos by name + readme) and the PyPI JSON API,
scores the closest match via title-token Jaccard similarity, and
returns a :class:`aidevswarm.schemas.NoveltyReport`. The Critic role
rejects ideas that score below the configured threshold (default 0.6).

The HTTP layer is intentionally thin: ``httpx`` with a retry-on-429
loop and an in-process LRU cache. Phase 3 doesn't persist the cache
to pgvector — the Phase 4 replanner is the natural home for that.

`AIDEVSWARM_NOVELTY_LIVE=1` in env enables live network calls in
integration tests; unit tests use ``respx`` to record fixed responses.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import httpx

from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import Idea, Match, NoveltyReport

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
PYPI_SEARCH_URL = "https://pypi.org/simple/{name}/"


def _tokenise(text: str) -> set[str]:
    """Cheap title-token Jaccard helper — lowercase + alnum-only."""
    return {tok for tok in "".join(c.lower() if c.isalnum() else " " for c in text).split() if tok}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union)


class SelfHistoryDuplicate:
    """A candidate idea that duplicates one of the swarm's own projects."""

    __slots__ = ("title", "similarity")

    def __init__(self, title: str, similarity: float) -> None:
        self.title = title
        self.similarity = similarity


class SelfHistoryDedup:
    """Reject ideas too similar to the swarm's OWN past/in-flight projects.

    ARCHITECTURE §5.7: the swarm must never re-pitch a project it already
    built. The prior-art :class:`NoveltyChecker` only dedups against the
    *public world* (GitHub/PyPI); this dedups against *our own* history.

    Deliberately uses the same cheap title+summary token Jaccard as the
    prior-art checker — no embeddings, no pgvector, no extra dependency.
    ``history_provider`` returns ``(title, summary)`` pairs for every
    project the swarm already has (any non-terminal-failed state); it is
    called fresh on each ``find_duplicate`` so new projects count
    immediately.
    """

    def __init__(
        self,
        history_provider: Callable[[], Sequence[tuple[str, str]]],
        *,
        threshold: float = 0.6,
    ) -> None:
        self._history = history_provider
        self._threshold = threshold
        self._log = get_logger(__name__)

    def find_duplicate(self, idea: Idea) -> SelfHistoryDuplicate | None:
        """The closest own-history match at/above threshold, or None."""
        cand = _tokenise(f"{idea.title} {idea.summary}")
        if not cand:
            return None
        best: SelfHistoryDuplicate | None = None
        try:
            history = self._history()
        except Exception as exc:  # a dedup lookup must never crash ideation
            self._log.warning("novelty.self_history_failed", error=str(exc))
            return None
        for title, summary in history:
            sim = _jaccard(cand, _tokenise(f"{title} {summary}"))
            if sim >= self._threshold and (best is None or sim > best.similarity):
                best = SelfHistoryDuplicate(title=title, similarity=sim)
        return best


class NoveltyChecker:
    """Look up an idea against GitHub + PyPI and score its novelty."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        github_token: str | None = None,
        max_matches: int = 5,
    ) -> None:
        self._client = client or httpx.Client(timeout=15.0)
        self._github_token = github_token or ""
        self._max_matches = max_matches
        self._log = get_logger(__name__)

    def check(self, idea: Idea) -> NoveltyReport:
        """Score ``idea`` against GitHub + PyPI prior art.

        Compares the idea's title AND summary tokens (not just the title)
        against each candidate's name AND description, so a project that does
        the same thing under a different name is still caught — less brittle
        than a pure title match, without needing embeddings.
        """
        idea_tokens = _tokenise(f"{idea.title} {idea.summary}")
        matches = list(self._github_matches(idea_tokens, idea.title))
        matches.extend(self._pypi_matches(idea_tokens, idea.title))
        matches.sort(key=lambda m: m.similarity, reverse=True)
        top = matches[: self._max_matches]
        highest = top[0].similarity if top else 0.0
        return NoveltyReport(score=max(0.0, min(1.0, 1.0 - highest)), top_matches=top)

    def _github_matches(self, idea_tokens: set[str], idea_title: str) -> list[Match]:
        try:
            response = self._get_with_retry(
                GITHUB_SEARCH_URL,
                params={"q": idea_title, "per_page": str(self._max_matches)},
                headers=self._github_headers(),
            )
        except httpx.HTTPError as exc:
            self._log.warning("novelty.github_failed", error=str(exc))
            return []
        if response is None:
            return []
        items: list[dict[str, object]] = (response.json() or {}).get("items", [])
        return [
            Match(
                source="github",
                url=str(item.get("html_url", "")),
                title=str(item.get("full_name", "")),
                # Compare against name + description so a similarly-purposed
                # repo with a different name still scores as a match.
                similarity=_jaccard(
                    idea_tokens,
                    _tokenise(f"{item.get('full_name', '')} {item.get('description') or ''}"),
                ),
            )
            for item in items[: self._max_matches]
        ]

    def _pypi_matches(self, idea_tokens: set[str], idea_title: str) -> list[Match]:
        """One name-shot per token (cheap), plus the original title slug.

        PyPI doesn't expose a public search endpoint; we look up by
        candidate-name only. A 200 response means a package with that
        exact name exists.
        """
        candidates = {
            *self._normalise_candidates(idea_title),
            *(t for t in idea_tokens if len(t) > 3),
        }
        out: list[Match] = []
        for name in list(candidates)[: self._max_matches]:
            url = PYPI_SEARCH_URL.format(name=name)
            try:
                response = self._get_with_retry(url)
            except httpx.HTTPError as exc:
                self._log.debug("novelty.pypi_lookup_failed", name=name, error=str(exc))
                continue
            if response is None or response.status_code != 200:
                continue
            out.append(
                Match(
                    source="pypi",
                    url=url,
                    title=name,
                    similarity=_jaccard(idea_tokens, _tokenise(name)),
                )
            )
        return out

    @staticmethod
    def _normalise_candidates(title: str) -> set[str]:
        """A few obvious package-name candidates for a title."""
        slug = "-".join(_tokenise(title))
        return {slug, slug.replace("-", "_"), slug.replace("-", "")} - {""}

    def _github_headers(self) -> dict[str, str]:
        hdrs = {"Accept": "application/vnd.github+json"}
        if self._github_token:
            hdrs["Authorization"] = f"Bearer {self._github_token}"
        return hdrs

    def _get_with_retry(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        attempts: int = 3,
    ) -> httpx.Response | None:
        backoff = 1.0
        for attempt in range(attempts):
            response = self._client.get(url, params=params, headers=headers)
            if response.status_code == 429 and attempt + 1 < attempts:
                self._log.info(
                    "novelty.rate_limited", url=url, attempt=attempt + 1, backoff=backoff
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            return response
        return None
