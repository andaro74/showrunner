"""Tests for tvmaze/server.py — build-order step 3.

Exercises the FastMCP server end-to-end against the live (keyless) TVmaze API:
asserts the 4 tools register and that "Breaking Bad" resolves to its first
episode, "Pilot".
"""

import pytest

from mcp_servers.tvmaze.server import mcp

EXPECTED_TOOLS = {"search_shows", "get_schedule", "get_episodes", "get_cast"}


def _payload(result):
    """Unwrap FastMCP call_tool output.

    Returns `(content_blocks, structured_content)`; list results are wrapped as
    `{"result": [...]}` in the structured dict.
    """
    structured = result[1] if isinstance(result, tuple) else result
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


async def test_registers_four_tools():
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


@pytest.mark.network
async def test_breaking_bad_first_episode():
    result = await mcp.call_tool("get_episodes", {"show": "Breaking Bad"})
    episodes = _payload(result)
    assert episodes, "expected a non-empty episode list"
    first = episodes[0]
    assert first["season"] == 1
    assert first["number"] == 1
    assert first["name"] == "Pilot"
