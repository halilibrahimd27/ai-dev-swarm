"""Smoke checks for the Pydantic v2 schema layer.

These tests verify that the public schema module is importable, that
required fields are enforced, and that the enums advertise the values
the rest of the system relies on.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aidevswarm.schemas import (
    TERMINAL_MILESTONE_STATES,
    TERMINAL_PROJECT_STATES,
    CriticScores,
    Idea,
    Milestone,
    MilestoneGraph,
    MilestoneSpec,
    MilestoneState,
    Project,
    ProjectSpec,
    ProjectState,
    ScoredIdea,
)


def test_project_state_values_match_architecture() -> None:
    """The state enum is the authoritative vocabulary."""
    expected = {
        "queued",
        "planning",
        "awaiting_approval",
        "building",
        "integration",
        "done",
        "blocked",
        "killed",
    }
    assert {s.value for s in ProjectState} == expected


def test_milestone_state_values() -> None:
    assert {s.value for s in MilestoneState} == {"pending", "building", "done", "failed"}


def test_terminal_sets_are_subsets_of_their_enums() -> None:
    assert TERMINAL_PROJECT_STATES <= set(ProjectState)
    assert TERMINAL_MILESTONE_STATES <= set(MilestoneState)


def _spec() -> ProjectSpec:
    return ProjectSpec(
        title="t",
        summary="s",
        rationale="r",
        stack=["python"],
        tags=["niche"],
        score=85,
    )


def test_project_defaults_to_queued_and_has_uuid() -> None:
    project = Project(name="p", spec=_spec())
    assert project.state is ProjectState.QUEUED
    assert project.id is not None
    assert project.is_terminal() is False


def test_project_terminal_flag_for_done_and_killed() -> None:
    project = Project(name="p", spec=_spec(), state=ProjectState.DONE)
    assert project.is_terminal()
    killed = Project(name="p", spec=_spec(), state=ProjectState.KILLED)
    assert killed.is_terminal()


def test_scored_idea_clamps_score_range() -> None:
    with pytest.raises(ValidationError):
        ScoredIdea(
            idea=Idea(title="x", summary="y", rationale="z"),
            scores=CriticScores(
                depth_ambition=10,
                usefulness_niche=10,
                novelty=10,
                decomposability=10,
                buildability=10,
            ),
            total=101,
        )


def test_milestone_graph_requires_at_least_one_milestone() -> None:
    with pytest.raises(ValidationError):
        MilestoneGraph(milestones=[])


def test_milestone_ordinal_must_be_non_negative() -> None:
    import uuid

    with pytest.raises(ValidationError):
        Milestone(
            project_id=uuid.uuid4(),
            ordinal=-1,
            title="m",
            spec=MilestoneSpec(title="m", description="d"),
        )


def test_extra_fields_rejected_on_project_spec() -> None:
    with pytest.raises(ValidationError):
        ProjectSpec.model_validate(
            {
                "title": "t",
                "summary": "s",
                "rationale": "r",
                "score": 80,
                "unexpected": True,
            }
        )
