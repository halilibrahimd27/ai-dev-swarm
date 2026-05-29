"""Operator-editable operational settings.

A CURATED allow-list of operational knobs (budgets, caps, modes, flags)
that the web UI can change at runtime. Everything else — API keys, the
Postgres password, hosts/ports, pool sizes, model identifiers, redaction
patterns — is deliberately NOT here: secrets and infra stay env-only.

Overrides live in the ``settings_overrides`` table and are applied onto
the process-wide :class:`Settings` object:

  * at startup (so a saved override survives a restart), and
  * immediately when changed, for keys the orchestrator reads live each
    tick (``restart_required=False``). The two ``restart_required`` keys
    (``build_concurrency``, ``sandbox_mode``) are consumed once at startup,
    so a change persists but only takes effect on the next restart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from psycopg_pool import ConnectionPool

from aidevswarm.logging_config import get_logger
from aidevswarm.settings import Settings

SettingKind = Literal["int", "float", "bool", "enum"]


@dataclass(frozen=True)
class EditableSetting:
    """Metadata for one operator-editable setting (drives API + UI)."""

    key: str  # the Settings field name
    label: str
    group: str
    kind: SettingKind
    restart_required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = field(default_factory=tuple)
    help: str = ""


# The allow-list. ONLY these keys are ever readable/writable via the API.
EDITABLE_SETTINGS: tuple[EditableSetting, ...] = (
    EditableSetting(
        "daily_token_budget",
        "Daily token budget",
        "Budget & throttle",
        "int",
        minimum=0,
        help="Soft cap on tokens spent per UTC day across all projects. Paces the system; never kills a project.",
    ),
    EditableSetting(
        "per_milestone_token_budget",
        "Per-milestone token cap",
        "Budget & throttle",
        "int",
        minimum=0,
        help="Circuit breaker per milestone — if one blows it, the swarm is looping; stop it.",
    ),
    EditableSetting(
        "require_approval",
        "Require plan approval",
        "Build",
        "bool",
        help="On: a planned project parks at awaiting_approval until you approve. Off: fully autonomous.",
    ),
    EditableSetting(
        "milestone_retry_limit",
        "Milestone retry limit",
        "Build",
        "int",
        minimum=1,
        help="How many times a milestone may fail before the project is blocked.",
    ),
    EditableSetting(
        "build_concurrency",
        "Build concurrency",
        "Build",
        "int",
        minimum=1,
        restart_required=True,
        help="How many projects build in parallel. Read once at startup.",
    ),
    EditableSetting(
        "sandbox_mode",
        "CI sandbox mode",
        "Build",
        "enum",
        choices=("docker", "subprocess", "inmemory"),
        restart_required=True,
        help="docker = isolated container; subprocess = in-process venv (compose default); inmemory = no CI. Read once at startup.",
    ),
    EditableSetting(
        "ideation_min_score",
        "Ideation min score",
        "Ideation",
        "int",
        minimum=0,
        maximum=100,
        help="An idea must score at least this (and be novel) to become a project.",
    ),
    EditableSetting(
        "ideation_max_rounds",
        "Ideation max rounds",
        "Ideation",
        "int",
        minimum=1,
        help="If a round yields nothing past the gate, re-ideate up to this many times.",
    ),
    EditableSetting(
        "auto_split_max_turns",
        "Auto-split max turns",
        "Replanner",
        "int",
        minimum=1,
        help="Auto-split fires when a milestone's predicted SDK turns exceed this.",
    ),
    EditableSetting(
        "auto_split_max_cost_usd",
        "Auto-split max cost ($)",
        "Replanner",
        "float",
        minimum=0,
        help="Auto-split fires when a milestone's predicted cost exceeds this.",
    ),
    EditableSetting(
        "consolidation_every",
        "Consolidation cadence",
        "Replanner",
        "int",
        minimum=1,
        help="Every Nth completed milestone is followed by a tidy + verify milestone.",
    ),
    EditableSetting(
        "sdk_max_turns",
        "SDK max turns",
        "Build",
        "int",
        minimum=1,
        help="Turn cap the Claude Agent SDK enforces per Developer/Tester invocation.",
    ),
    EditableSetting(
        "sdk_max_budget_usd",
        "SDK max budget ($)",
        "Build",
        "float",
        minimum=0,
        help="USD cap the SDK enforces per invocation (it aborts at this).",
    ),
)

_BY_KEY: dict[str, EditableSetting] = {s.key: s for s in EDITABLE_SETTINGS}


def is_editable(key: str) -> bool:
    return key in _BY_KEY


def get_spec(key: str) -> EditableSetting | None:
    return _BY_KEY.get(key)


_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off"})


def coerce(spec: EditableSetting, raw: str) -> int | float | bool | str:
    """Parse + range-check a raw string into the setting's typed value.

    Raises ``ValueError`` on a bad type/range/choice — the only place
    operator input is validated before it touches the Settings object.
    """
    text = raw.strip()
    if spec.kind == "bool":
        return _coerce_bool(spec, text, raw)
    if spec.kind == "enum":
        return _coerce_enum(spec, text, raw)
    return _coerce_numeric(spec, text, raw)


def _coerce_bool(spec: EditableSetting, text: str, raw: str) -> bool:
    low = text.lower()
    if low in _BOOL_TRUE:
        return True
    if low in _BOOL_FALSE:
        return False
    raise ValueError(f"{spec.key}: expected a boolean, got {raw!r}")


def _coerce_enum(spec: EditableSetting, text: str, raw: str) -> str:
    if text not in spec.choices:
        raise ValueError(f"{spec.key}: must be one of {list(spec.choices)}, got {raw!r}")
    return text


def _coerce_numeric(spec: EditableSetting, text: str, raw: str) -> int | float:
    is_int = spec.kind == "int"
    try:
        value: int | float = int(text) if is_int else float(text)
    except ValueError as exc:
        what = "an integer" if is_int else "a number"
        raise ValueError(f"{spec.key}: expected {what}, got {raw!r}") from exc
    if spec.minimum is not None and value < spec.minimum:
        raise ValueError(f"{spec.key}: must be >= {spec.minimum}")
    if spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"{spec.key}: must be <= {spec.maximum}")
    return value


def apply_override(settings: Settings, key: str, raw: str) -> int | float | bool | str:
    """Validate + set one override onto the live Settings object."""
    spec = _BY_KEY[key]  # caller guarantees key is editable
    value = coerce(spec, raw)
    setattr(settings, key, value)
    return value


def apply_all(settings: Settings, overrides: dict[str, str]) -> None:
    """Apply every known, valid override onto ``settings`` (best-effort).

    Unknown or malformed rows are skipped with a warning — a single bad
    override must never stop the orchestrator from starting.
    """
    log = get_logger(__name__)
    for key, raw in overrides.items():
        if key not in _BY_KEY:
            continue
        try:
            apply_override(settings, key, raw)
        except ValueError as exc:
            log.warning("settings.override_skipped", key=key, error=str(exc))


def snapshot(settings: Settings) -> list[dict[str, Any]]:
    """Current value + metadata for every editable setting (no secrets)."""
    out: list[dict[str, Any]] = []
    for spec in EDITABLE_SETTINGS:
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "group": spec.group,
                "kind": spec.kind,
                "value": getattr(settings, spec.key),
                "restart_required": spec.restart_required,
                "choices": list(spec.choices),
                "minimum": spec.minimum,
                "maximum": spec.maximum,
                "help": spec.help,
            }
        )
    return out


class SettingsOverrideRepo(Protocol):
    """Persistence slice for operator overrides."""

    def get_all(self) -> dict[str, str]: ...

    def upsert(self, key: str, value: str) -> None: ...


class PsycopgSettingsOverrideRepo:
    """Concrete :class:`SettingsOverrideRepo` (pool-based)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def get_all(self) -> dict[str, str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT key, value FROM settings_overrides")
            return {str(k): str(v) for k, v in cur.fetchall()}

    def upsert(self, key: str, value: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO settings_overrides (key, value, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                (key, value),
            )


__all__ = [
    "EDITABLE_SETTINGS",
    "EditableSetting",
    "PsycopgSettingsOverrideRepo",
    "SettingsOverrideRepo",
    "apply_all",
    "apply_override",
    "coerce",
    "is_editable",
    "snapshot",
]
