"""Unit tests for :class:`aidevswarm.telegram.HaikuIntentParser`.

Phase 5 Mandate 4: free-form Telegram messages → one typed Command
via a Claude Haiku call. The phase prompt requires a fixture-per-
intent table; we cover the full vocabulary plus the error paths
(off-schema replies, non-JSON output, empty key, fenced markdown).

The Anthropic API is stubbed via respx so the gauntlet runs offline.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
import respx

from aidevswarm.schemas import (
    AbortProject,
    Approve,
    DropAndStartNew,
    InjectNote,
    KillSwitch,
    ListState,
    PauseProject,
    RejectIdea,
    Rescope,
    ResumeProject,
    ShowTranscript,
    SwitchToIdea,
    TransformProject,
)
from aidevswarm.settings import Settings
from aidevswarm.telegram import HaikuIntentParser, IntentParseError

_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _settings() -> Settings:
    return Settings(ANTHROPIC_API_KEY="sk-ant-test")


def _stub(json_obj: dict[str, object]) -> respx.Route:
    return respx.post(_MESSAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"type": "text", "text": json.dumps(json_obj)}]},
        )
    )


@respx.mock
@pytest.mark.asyncio
async def test_parse_approve() -> None:
    pid = str(uuid4())
    _stub({"intent": "approve", "project_id": pid})
    parser = HaikuIntentParser(_settings())
    cmd = await parser.parse("approve current project")
    assert isinstance(cmd, Approve)
    assert str(cmd.project_id) == pid


@respx.mock
@pytest.mark.asyncio
async def test_parse_inject_note() -> None:
    pid = str(uuid4())
    _stub({"intent": "inject_note", "project_id": pid, "body": "focus on tests"})
    cmd = await HaikuIntentParser(_settings()).parse("note: focus on tests")
    assert isinstance(cmd, InjectNote)
    assert cmd.body == "focus on tests"


@respx.mock
@pytest.mark.asyncio
async def test_parse_pause() -> None:
    pid = str(uuid4())
    _stub({"intent": "pause_project", "project_id": pid})
    cmd = await HaikuIntentParser(_settings()).parse("pause this project")
    assert isinstance(cmd, PauseProject)


@respx.mock
@pytest.mark.asyncio
async def test_parse_resume() -> None:
    pid = str(uuid4())
    _stub({"intent": "resume_project", "project_id": pid})
    cmd = await HaikuIntentParser(_settings()).parse("resume project")
    assert isinstance(cmd, ResumeProject)


@respx.mock
@pytest.mark.asyncio
async def test_parse_abort() -> None:
    pid = str(uuid4())
    _stub({"intent": "abort_project", "project_id": pid, "reason": "stuck"})
    cmd = await HaikuIntentParser(_settings()).parse("kill project, stuck")
    assert isinstance(cmd, AbortProject)
    assert cmd.confirmed is False  # parser MUST NOT pre-confirm destructive


@respx.mock
@pytest.mark.asyncio
async def test_parse_rescope() -> None:
    pid = str(uuid4())
    _stub({"intent": "rescope", "project_id": pid, "new_scope": "tiny v0"})
    cmd = await HaikuIntentParser(_settings()).parse("rescope to tiny v0")
    assert isinstance(cmd, Rescope)


@respx.mock
@pytest.mark.asyncio
async def test_parse_transform() -> None:
    pid = str(uuid4())
    _stub({"intent": "transform_project", "project_id": pid, "new_direction": "data tool"})
    cmd = await HaikuIntentParser(_settings()).parse("change to data tool")
    assert isinstance(cmd, TransformProject)


@respx.mock
@pytest.mark.asyncio
async def test_parse_drop_and_start_new() -> None:
    pid = str(uuid4())
    _stub({"intent": "drop_and_start_new", "project_id": pid})
    cmd = await HaikuIntentParser(_settings()).parse("drop this, pick a new one")
    assert isinstance(cmd, DropAndStartNew)


@respx.mock
@pytest.mark.asyncio
async def test_parse_switch_to_idea() -> None:
    pid = str(uuid4())
    iid = str(uuid4())
    _stub({"intent": "switch_to_idea", "current_project_id": pid, "new_idea_id": iid})
    cmd = await HaikuIntentParser(_settings()).parse(f"switch to idea {iid}")
    assert isinstance(cmd, SwitchToIdea)


@respx.mock
@pytest.mark.asyncio
async def test_parse_reject_idea() -> None:
    iid = str(uuid4())
    _stub({"intent": "reject_idea", "idea_id": iid})
    cmd = await HaikuIntentParser(_settings()).parse(f"reject idea {iid}")
    assert isinstance(cmd, RejectIdea)


@respx.mock
@pytest.mark.asyncio
async def test_parse_kill_switch() -> None:
    _stub({"intent": "kill_switch", "reason": "emergency"})
    cmd = await HaikuIntentParser(_settings()).parse("stop everything")
    assert isinstance(cmd, KillSwitch)


@respx.mock
@pytest.mark.asyncio
async def test_parse_list_state() -> None:
    _stub({"intent": "list_state"})
    cmd = await HaikuIntentParser(_settings()).parse("what's running?")
    assert isinstance(cmd, ListState)


@respx.mock
@pytest.mark.asyncio
async def test_parse_show_transcript() -> None:
    pid = str(uuid4())
    _stub({"intent": "show_transcript", "project_id": pid})
    cmd = await HaikuIntentParser(_settings()).parse("show me the chatter")
    assert isinstance(cmd, ShowTranscript)


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_off_schema_intent_is_rejected() -> None:
    _stub({"intent": "delete_everything", "scope": "all"})
    with pytest.raises(IntentParseError):
        await HaikuIntentParser(_settings()).parse("hose the whole DB")


@respx.mock
@pytest.mark.asyncio
async def test_non_json_response_is_rejected() -> None:
    respx.post(_MESSAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "I cannot help with that."}]},
        )
    )
    with pytest.raises(IntentParseError):
        await HaikuIntentParser(_settings()).parse("hi")


@respx.mock
@pytest.mark.asyncio
async def test_markdown_fenced_json_is_recovered() -> None:
    """LLMs sometimes wrap JSON in ```json ... ``` even when told not to."""
    pid = str(uuid4())
    fenced = "```json\n" + json.dumps({"intent": "approve", "project_id": pid}) + "\n```"
    respx.post(_MESSAGES_URL).mock(
        return_value=httpx.Response(200, json={"content": [{"type": "text", "text": fenced}]})
    )
    cmd = await HaikuIntentParser(_settings()).parse("approve it")
    assert isinstance(cmd, Approve)


@pytest.mark.asyncio
async def test_empty_api_key_raises_clearly() -> None:
    parser = HaikuIntentParser(Settings())  # no key
    with pytest.raises(IntentParseError, match="ANTHROPIC_API_KEY is empty"):
        await parser.parse("any input")
