"""Smoke check that ``.mcp.json`` parses and ``npx`` is reachable.

Phase 2 needs the tree-sitter MCP server to launch from the SDK
subprocess. The end-to-end reachability test (actually starting the
subprocess) lives in test_fizzbuzz_smoke.py and gates on
ANTHROPIC_API_KEY; this file only verifies the *configuration* is
sane so a missing/broken .mcp.json is caught even when no API key is
set.
"""

from __future__ import annotations

import shutil

import pytest

from aidevswarm.tools.mcp_config import DEFAULT_MCP_JSON, load_mcp_servers

pytestmark = pytest.mark.integration


def test_mcp_json_is_present_at_repo_root() -> None:
    assert (
        DEFAULT_MCP_JSON.is_file()
    ), f"{DEFAULT_MCP_JSON} missing — the SDK can't wire tree-sitter-mcp"


def test_tree_sitter_mcp_is_configured() -> None:
    servers = load_mcp_servers()
    assert "tree-sitter-mcp" in servers, (
        "tree-sitter-mcp must be in .mcp.json so the Developer/Tester SDK "
        "agents can navigate generated-project code."
    )
    entry = servers["tree-sitter-mcp"]
    assert entry["command"] == "npx"
    assert entry["args"] and "@nendo/tree-sitter-mcp" in " ".join(entry["args"])


def test_npx_is_reachable_on_host() -> None:
    """Skips when Node isn't installed; otherwise asserts npx is usable."""
    if shutil.which("npx") is None:
        pytest.skip("npx not on PATH (install Node 20+ to enable MCP)")
    # Reachability is enough — we don't actually start the server here
    # because that downloads ~50 MB the first time.
