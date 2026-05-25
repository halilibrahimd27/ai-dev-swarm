"""Helper: load role prompt files shipped alongside each crew."""

from __future__ import annotations

from pathlib import Path


def load_prompt(crew_dir: Path, role: str) -> str:
    """Read ``<crew_dir>/prompts/<role>.txt`` and return the body."""
    path = crew_dir / "prompts" / f"{role}.txt"
    return path.read_text(encoding="utf-8")
