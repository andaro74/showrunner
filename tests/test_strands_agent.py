"""Smoke test for the Strands show specialist.

Spawns the tvmaze MCP server over stdio, builds the agent, and asserts it
registered exactly the tvmaze tools — and none of the places tools, which belong
to the LangGraph specialist. Agent construction needs no AWS credentials; the
model is only contacted on invocation, which we don't do.
"""

import pytest

from agents.strands.agent import build_agent, build_mcp_clients

TVMAZE_TOOLS = {"search_shows", "get_schedule", "get_episodes", "get_cast"}
PLACES_TOOLS = {"geocode", "find_nearby", "travel_time"}


@pytest.mark.integration
def test_specialist_registers_exactly_the_tvmaze_tools():
    (tvmaze,) = build_mcp_clients()
    with tvmaze:
        tools = tvmaze.list_tools_sync()
        agent = build_agent(tools)
        registered = set(agent.tool_names)

    assert registered == TVMAZE_TOOLS
    assert not (registered & PLACES_TOOLS), "places tools leaked into the show specialist"
