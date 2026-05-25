"""Unit tests for the steering-notes prompt renderer.

Covers the renderer in isolation plus an end-to-end flow that proves
the operator workflow works: ``add_note → pull_unconsumed → render``
produces the note body in the final prompt text.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from aidevswarm.steering import render_prompt
from tests.fakes import FakeSteeringRepo

_CREW_ROOT = Path(__file__).resolve().parents[2] / "src" / "aidevswarm" / "crews"


def test_empty_notes_renders_blank_slot() -> None:
    template = "Hello.\n\n{{ steering_notes }}\n"
    rendered = render_prompt(template, steering_notes=[])
    assert "Hello." in rendered
    # No bullet section when there are no notes.
    assert "Steering notes from the operator" not in rendered


def test_single_note_renders_bullet() -> None:
    template = "Hi.\n\n{{ steering_notes }}\n"
    rendered = render_prompt(template, steering_notes=["be terse"])
    assert "## Steering notes from the operator" in rendered
    assert "- be terse" in rendered


def test_multiple_notes_each_get_a_bullet() -> None:
    rendered = render_prompt("X\n{{ steering_notes }}\n", steering_notes=["one", "two", "three"])
    assert "- one" in rendered
    assert "- two" in rendered
    assert "- three" in rendered


def test_whitespace_only_notes_are_skipped() -> None:
    rendered = render_prompt("X\n{{ steering_notes }}\n", steering_notes=["", "   ", "real"])
    assert rendered.count("- ") == 1
    assert "- real" in rendered


def test_unknown_template_variable_fails_strict() -> None:
    """StrictUndefined catches typo'd placeholders at render time."""
    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        render_prompt("oops {{ stering_notes }}", steering_notes=[])


@pytest.mark.parametrize(
    "role,prompt_path",
    [
        ("Trend Scout", _CREW_ROOT / "ideation/prompts/trend_scout.txt"),
        ("Ideator", _CREW_ROOT / "ideation/prompts/ideator.txt"),
        ("Critic", _CREW_ROOT / "ideation/prompts/critic.txt"),
        ("PM", _CREW_ROOT / "planning/prompts/pm.txt"),
        ("Architect", _CREW_ROOT / "planning/prompts/architect.txt"),
        ("Developer", _CREW_ROOT / "build/prompts/developer.txt"),
        ("Tester", _CREW_ROOT / "build/prompts/tester.txt"),
        ("Reviewer", _CREW_ROOT / "build/prompts/reviewer.txt"),
    ],
)
def test_every_role_prompt_has_the_slot(role: str, prompt_path: Path) -> None:
    body = prompt_path.read_text(encoding="utf-8")
    assert "{{ steering_notes }}" in body, (
        f"Prompt for {role} at {prompt_path} is missing the "
        "{{ steering_notes }} slot — operator notes won't reach this role."
    )


def test_operator_workflow_note_flows_into_rendered_prompt() -> None:
    """End-to-end at unit level: add_note -> pull -> render contains body."""
    repo = FakeSteeringRepo()
    project_id = uuid4()
    repo.add_note(project_id, "prefer dataclasses to TypedDict")

    template = (_CREW_ROOT / "build/prompts/developer.txt").read_text("utf-8")
    notes = repo.pull_unconsumed(project_id, "Developer")
    rendered = render_prompt(template, steering_notes=notes)

    assert "prefer dataclasses to TypedDict" in rendered
    assert "## Steering notes from the operator" in rendered
    # Second pull is empty -> next render has no notes section.
    second_notes = repo.pull_unconsumed(project_id, "Developer")
    second_render = render_prompt(template, steering_notes=second_notes)
    assert "## Steering notes from the operator" not in second_render
