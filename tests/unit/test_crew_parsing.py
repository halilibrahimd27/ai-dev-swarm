"""The LLM-boundary tolerant parsing: drop stray keys, keep the plan.

Regression: the planning Architect emits an ``"id": "m1"`` on every
milestone; MilestoneSpec's ``extra='forbid'`` rejected each one, the
plan parsed to zero milestones, and the project blocked right after we
paid for the (correct, complete) call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from aidevswarm.crews._parsing import keep_known, loads_lenient
from aidevswarm.crews.ideation.crew import CrewaiIdeationCrew
from aidevswarm.crews.planning.crew import CrewaiPlanningCrew
from aidevswarm.schemas import MilestoneSpec


@dataclass
class _Raw:
    raw: str


def test_keep_known_drops_unknown_keys() -> None:
    cleaned = keep_known(MilestoneSpec, {"id": "m1", "title": "t", "bogus": 1})
    assert cleaned == {"title": "t"}


def test_loads_lenient_repairs_missing_comma() -> None:
    # The exact live failure shape: a missing comma between two fields.
    data = loads_lenient('{"milestones": [{"title": "A" "description": "d"}]}')
    assert data["milestones"][0]["title"] == "A"


def test_loads_lenient_repairs_truncated_and_fenced() -> None:
    # Truncated mid-string -> json_repair closes the structure.
    trunc = loads_lenient('{"milestones": [{"title": "A", "description": "cut off here')
    assert trunc["milestones"][0]["title"] == "A"
    # Markdown-fenced JSON is unwrapped.
    fenced = loads_lenient('```json\n{"milestones": [{"title": "A"}]}\n```')
    assert fenced["milestones"][0]["title"] == "A"


def test_planning_parse_survives_malformed_json() -> None:
    """A missing comma in the Architect's output must NOT zero the plan."""
    bad = (
        '{"milestones": [{"id": "M1", "title": "Bootstrap" '  # <- missing comma
        '"description": "set up repo", '
        '"acceptance_criteria": [{"description": "builds", "verifier": "pytest"}]}]}'
    )
    specs = CrewaiPlanningCrew._parse_specs(_Raw(bad))
    assert len(specs) == 1
    assert specs[0].title == "Bootstrap"


def test_planning_parse_strips_llm_id_field() -> None:
    payload = {
        "milestones": [
            {
                "id": "m1",  # LLM extra that used to break the whole parse
                "title": "Bootstrap repo",
                "description": "set up the monorepo",
                "acceptance_criteria": [
                    {"id": "c1", "description": "repo builds", "verifier": "pytest"}
                ],
                "technical_note": "use uv",
            }
        ]
    }
    specs = CrewaiPlanningCrew._parse_specs(_Raw(json.dumps(payload)))
    assert len(specs) == 1
    assert specs[0].title == "Bootstrap repo"
    assert specs[0].acceptance_criteria[0].verifier == "pytest"


def test_ideation_parse_strips_llm_extras() -> None:
    payload = [
        {
            "idea": {
                "id": "i1",  # stray
                "title": "x",
                "summary": "s",
                "rationale": "r",
                "stack": ["python"],
                "tags": ["cli"],
            },
            "scores": {
                "depth_ambition": 85,
                "usefulness_niche": 80,
                "novelty": 75,
                "decomposability": 85,
                "buildability": 80,
                "weighted": 999,  # stray
            },
            "total": 81,
            "rejected_reason": None,
        }
    ]
    scored = CrewaiIdeationCrew._parse(_Raw(json.dumps(payload)))
    assert len(scored) == 1
    assert scored[0].idea.title == "x"
    assert scored[0].total == 81
