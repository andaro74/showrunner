"""Smoke test for the Strands agent — build-order step 6.

Spawns both MCP servers over stdio, builds the agent, and asserts it registered
the tools from BOTH servers (4 tvmaze + 3 places). Agent construction needs no
AWS credentials; the model is only contacted on invocation, which we don't do.
"""

import pytest

from agents.strands.agent import build_agent, build_mcp_clients

TVMAZE_TOOLS = {"search_shows", "get_schedule", "get_episodes", "get_cast"}
PLACES_TOOLS = {"geocode", "find_nearby", "travel_time"}


@pytest.mark.integration
def test_agent_registers_tools_from_both_servers():
    tvmaze, places = build_mcp_clients()
    with tvmaze, places:
        tools = tvmaze.list_tools_sync() + places.list_tools_sync()
        agent = build_agent(tools)
        registered = set(agent.tool_names)

    assert TVMAZE_TOOLS <= registered, f"missing tvmaze tools: {TVMAZE_TOOLS - registered}"
    assert PLACES_TOOLS <= registered, f"missing places tools: {PLACES_TOOLS - registered}"
    assert registered == TVMAZE_TOOLS | PLACES_TOOLS
