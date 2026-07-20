"""Partition test for the two specialists.

The Strands agent owns tvmaze; the LangGraph agent owns places. This asserts the
split is exact: the specialists share no tools, and together they cover all seven
MCP tools. MCP portability still holds — each server is consumed by a different
framework with no per-framework rewrites; which framework serves which server is
interchangeable.

Spawns the servers as subprocesses; needs no AWS (no model is invoked).
"""

import pytest

from agents.langgraph.agent import load_tools
from agents.strands.agent import build_agent, build_mcp_clients

TVMAZE_TOOLS = {"search_shows", "get_schedule", "get_episodes", "get_cast"}
PLACES_TOOLS = {"geocode", "find_nearby", "travel_time"}
ALL_TOOLS = TVMAZE_TOOLS | PLACES_TOOLS


def _strands_tool_names() -> set[str]:
    (tvmaze,) = build_mcp_clients()
    with tvmaze:
        return set(build_agent(tvmaze.list_tools_sync()).tool_names)


@pytest.mark.integration
async def test_specialists_partition_the_toolset():
    langgraph_names = {tool.name for tool in await load_tools()}
    strands_names = _strands_tool_names()

    assert langgraph_names == PLACES_TOOLS
    assert strands_names == TVMAZE_TOOLS
    assert not (langgraph_names & strands_names), "specialists must not share tools"
    assert langgraph_names | strands_names == ALL_TOOLS, "every MCP tool must have an owner"
