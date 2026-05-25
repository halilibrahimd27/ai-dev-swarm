"""Hypothesis property tests for the steering-notes prompt renderer.

Invariants:
- An empty notes list NEVER emits the "## Steering notes…" header.
- Every non-whitespace note appears verbatim in the rendered output.
- Whitespace-only notes are silently dropped.
- The template's body always appears in the output (renderer doesn't
  eat the user-supplied body).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aidevswarm.steering import render_prompt

pytestmark = pytest.mark.property

# Arbitrary printable note bodies, occasionally including whitespace.
note_text = st.text(
    alphabet=st.characters(blacklist_categories=["Cs", "Cc"]),
    min_size=0,
    max_size=80,
)

note_list = st.lists(note_text, min_size=0, max_size=6)


@given(notes=note_list)
def test_empty_or_whitespace_only_notes_omit_header(notes: list[str]) -> None:
    rendered = render_prompt("body\n{{ steering_notes }}\n", steering_notes=notes)
    has_content = any(n.strip() for n in notes)
    if not has_content:
        assert "Steering notes from the operator" not in rendered


@given(notes=note_list)
def test_every_non_whitespace_note_appears_in_output(notes: list[str]) -> None:
    rendered = render_prompt("X\n{{ steering_notes }}\n", steering_notes=notes)
    for note in notes:
        stripped = note.strip()
        if stripped:
            assert stripped in rendered


@given(
    notes=note_list,
    body=st.text(
        alphabet=st.characters(
            whitelist_categories=["L", "N", "P", "Zs"],
            blacklist_characters="{}%",
        ),
        min_size=0,
        max_size=40,
    ),
)
def test_template_body_always_survives_rendering(notes: list[str], body: str) -> None:
    template = body + "\n{{ steering_notes }}\n"
    rendered = render_prompt(template, steering_notes=notes)
    assert body in rendered


@given(notes=note_list)
def test_bullet_count_equals_non_whitespace_notes(notes: list[str]) -> None:
    rendered = render_prompt("Y\n{{ steering_notes }}\n", steering_notes=notes)
    expected = sum(1 for n in notes if n.strip())
    # Each rendered note is a Markdown bullet "- …"
    bullet_lines = [line for line in rendered.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == expected
