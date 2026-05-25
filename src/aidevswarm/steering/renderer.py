"""Render role prompts with operator steering notes injected.

Every role prompt template includes a ``{{ steering_notes }}`` slot.
:func:`render_prompt` substitutes that slot with either an empty string
(no pending notes) or a Markdown-style "## Steering notes from the
operator:" block followed by a bulleted list.

Jinja2 is used in *strict* mode so a typo'd placeholder fails loudly
rather than silently rendering blank.
"""

from __future__ import annotations

from collections.abc import Sequence

from jinja2 import Environment, StrictUndefined

_env = Environment(
    autoescape=False,  # prompts are not HTML; bare text is correct
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def _format_notes(notes: Sequence[str]) -> str:
    if not notes:
        return ""
    bullets = "\n".join(f"- {note.strip()}" for note in notes if note.strip())
    if not bullets:
        return ""
    return "\n\n## Steering notes from the operator (pending):\n" + bullets


def render_prompt(template_text: str, *, steering_notes: Sequence[str]) -> str:
    """Render ``template_text`` with the steering-notes slot filled in."""
    return _env.from_string(template_text).render(
        steering_notes=_format_notes(steering_notes),
    )
