"""Phase 6 ship-it acceptance gauntlet.

Runs alongside ``make verify`` to check the repository is publishable:

  1. Every file in ``git ls-files`` is a product file — no build-kit
     leakage (CLAUDE.md, docs/phase-prompts/, docs/plans/,
     docs/handoffs/, PROJECT_BRIEF, ARCHITECTURE, .claude/, .mcp.json,
     PROGRESS.md, BLOCKERS.md, OPERATOR_HANDBOOK.md, .devcontainer/).
  2. LICENSE exists at repo root.
  3. THREAT_MODEL.md + SECURITY.md exist at repo root.
  4. >= 6 ADRs under docs/adr/ (the MADR template + 5 backfilled
     decisions; we ship 6 ADRs by Phase 6 DoD).
  5. README.md has all 11 required sections from the phase prompt.
  6. .env.example mentions every Settings.* field.
  7. docker-compose.yml at repo root + include points at
     docker/compose.yml.
  8. examples/ has at least 2 sub-directories with the trio
     (README.md, spec.json, transcript.md).

Run via ``make verify-ship``. The script exits 0 if everything passes,
1 with a summary if anything fails. Intended for both human + CI use.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Patterns that, if matched by any tracked file, indicate the build kit
# has leaked into the published repo.
BUILD_KIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^CLAUDE\.md$"),
    re.compile(r"^PROGRESS\.md$"),
    re.compile(r"^BLOCKERS\.md$"),
    re.compile(r"^OPERATOR_HANDBOOK\.md$"),
    re.compile(r"^\.claude(/|$)"),
    re.compile(r"^\.mcp\.json$"),
    re.compile(r"^\.devcontainer(/|$)"),
    re.compile(r"^docs/PROJECT_BRIEF\.md$"),
    re.compile(r"^docs/ARCHITECTURE\.md$"),
    re.compile(r"^docs/phase-prompts(/|$)"),
    re.compile(r"^docs/plans(/|$)"),
    re.compile(r"^docs/handoffs(/|$)"),
)

REQUIRED_README_SECTIONS: tuple[str, ...] = (
    # Each entry is a fragment expected to appear as a heading or
    # heading-shaped phrase. Matched case-insensitively.
    "ai-dev-swarm",
    "what it does",
    "prerequisites",
    "quickstart",
    "getting your keys",
    "first run",
    "configuration",
    "operating",
    "troubleshooting",
    "comparison",
    "license",
)


class CheckFailed(Exception):
    """One Mandate-8 check failed."""


def _git_ls_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def check_no_build_kit_leak() -> None:
    files = _git_ls_files()
    offenders = [f for f in files if any(p.match(f) for p in BUILD_KIT_PATTERNS)]
    if offenders:
        raise CheckFailed(
            "build-kit files are tracked by git (must stay gitignored):\n  "
            + "\n  ".join(offenders)
        )


def check_license_exists() -> None:
    if not (ROOT / "LICENSE").is_file():
        raise CheckFailed("LICENSE missing at repo root")


def check_security_docs_exist() -> None:
    missing = [f for f in ("THREAT_MODEL.md", "SECURITY.md") if not (ROOT / f).is_file()]
    if missing:
        raise CheckFailed("missing security documents at repo root: " + ", ".join(missing))


def check_adrs() -> None:
    adr_dir = ROOT / "docs" / "adr"
    if not adr_dir.is_dir():
        raise CheckFailed("docs/adr/ missing")
    adrs = sorted(p for p in adr_dir.iterdir() if p.suffix == ".md" and p.name != "_template.md")
    if len(adrs) < 5:
        raise CheckFailed(f"need >= 5 ADRs (template + 5 backfilled), found {len(adrs)}")
    if not (adr_dir / "_template.md").is_file():
        raise CheckFailed("docs/adr/_template.md missing")


def check_readme_sections() -> None:
    readme = (ROOT / "README.md").read_text("utf-8").lower()
    missing = [s for s in REQUIRED_README_SECTIONS if s not in readme]
    if missing:
        raise CheckFailed("README.md missing required sections: " + ", ".join(missing))


def check_env_example_covers_settings() -> None:
    env_example = (ROOT / ".env.example").read_text("utf-8")
    # Pull env-var names from settings.py via a cheap regex.
    settings_py = (ROOT / "src" / "aidevswarm" / "settings.py").read_text("utf-8")
    aliases = set(re.findall(r'validation_alias="([^"]+)"', settings_py))
    # Required vars the operator MUST set; the rest are optional knobs
    # with sensible defaults but .env.example should still mention them.
    missing = [a for a in sorted(aliases) if a not in env_example]
    if missing:
        raise CheckFailed(".env.example missing entries for: " + ", ".join(missing))


def check_compose_at_root() -> None:
    top = ROOT / "docker-compose.yml"
    if not top.is_file():
        raise CheckFailed("docker-compose.yml missing at repo root")
    content = top.read_text("utf-8")
    if "docker/compose.yml" not in content:
        raise CheckFailed("docker-compose.yml at root must reference docker/compose.yml")


def check_examples() -> None:
    examples = ROOT / "examples"
    if not examples.is_dir():
        raise CheckFailed("examples/ missing")
    subdirs = [p for p in examples.iterdir() if p.is_dir()]
    if len(subdirs) < 2:
        raise CheckFailed(f"examples/ needs >= 2 sub-projects, found {len(subdirs)}")
    for sub in subdirs:
        for needed in ("README.md", "spec.json", "transcript.md"):
            if not (sub / needed).is_file():
                raise CheckFailed(f"examples/{sub.name}/ missing {needed}")


CHECKS = (
    ("no build-kit files tracked by git", check_no_build_kit_leak),
    ("LICENSE present", check_license_exists),
    ("THREAT_MODEL.md + SECURITY.md present", check_security_docs_exist),
    (">= 5 ADRs + MADR template", check_adrs),
    ("README has all 11 required sections", check_readme_sections),
    (".env.example covers every Settings env var", check_env_example_covers_settings),
    ("docker-compose.yml at repo root references docker/compose.yml", check_compose_at_root),
    ("examples/ has >= 2 sub-projects with the spec/transcript/README trio", check_examples),
)


def main() -> int:
    failures: list[tuple[str, str]] = []
    for label, check in CHECKS:
        try:
            check()
            print(f"  ✓ {label}")
        except CheckFailed as exc:
            print(f"  ✗ {label}")
            failures.append((label, str(exc)))
    if failures:
        print()
        print("ship-it gauntlet: FAIL")
        for label, msg in failures:
            print(f"\n  ✗ {label}:\n    {msg}")
        return 1
    print()
    print("ship-it gauntlet: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
