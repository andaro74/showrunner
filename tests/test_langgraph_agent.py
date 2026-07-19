"""Portability test for the LangGraph agent — build-order step 7.

Loads the SAME two MCP servers through both frameworks and asserts they expose
identical tool names: one server pair, two frameworks, no per-framework rewrites.
Spawns the servers as subprocesses; needs no AWS (no model is invoked).
"""

import pytest

from agents.langgraph.agent import load_tools
from agents.strands.agent import build_agent, build_mcp_clients

EXPECTED_TOOLS = {
    "search_shows",
    "get_schedule",
    "get_episodes",
    "get_cast",
    "geocode",
    "find_nearby",
    "travel_time",
}


def _strands_tool_names() -> set[str]:
    tvmaze, places = build_mcp_clients()
    with tvmaze, places:
        tools = tvmaze.list_tools_sync() + places.list_tools_sync()
        return set(build_agent(tools).tool_names)


@pytest.mark.integration
async def test_langgraph_matches_strands_tool_names():
    langgraph_names = {tool.name for tool in await load_tools()}
    strands_names = _strands_tool_names()

    assert langgraph_names == EXPECTED_TOOLS
    assert langgraph_names == strands_names
