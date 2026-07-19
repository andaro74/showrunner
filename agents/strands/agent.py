"""Strands movie-night agent (primary).

Connects to BOTH MCP servers (tvmaze + places) over stdio via Strands `MCPClient`,
and is wrapped in a `BedrockAgentCoreApp` for the runtime entrypoint. The MCP
servers are launched as subprocesses using the current interpreter, so the same
virtualenv (and its deps) is reused.
"""

from __future__ import annotations

import os
import sys

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands import Agent
from strands.tools.mcp import MCPClient

from agents.strands.prompts import SYSTEM_PROMPT

# MCP server entrypoints, launched over stdio as `python -m <module>`.
TVMAZE_SERVER = "mcp_servers.tvmaze.server"
PLACES_SERVER = "mcp_servers.places.server"

app = BedrockAgentCoreApp()


def mcp_client_for(module: str) -> MCPClient:
    """Build an MCPClient that spawns `python -m module` and speaks stdio to it."""
    return MCPClient(
        lambda: stdio_client(StdioServerParameters(command=sys.executable, args=["-m", module]))
    )


def build_mcp_clients() -> list[MCPClient]:
    """One MCPClient per backing server (tvmaze, places)."""
    return [mcp_client_for(TVMAZE_SERVER), mcp_client_for(PLACES_SERVER)]


def build_agent(tools: list) -> Agent:
    """Assemble the Strands agent over the given MCP tools.

    `tools` must be collected from MCPClients that are currently connected.
    Model id comes from BEDROCK_MODEL_ID when set, else Strands' Bedrock default.
    """
    return Agent(
        model=os.environ.get("BEDROCK_MODEL_ID"),
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
    )


@app.entrypoint
def invoke(payload: dict) -> dict:
    """Runtime entrypoint: plan a movie night for the prompt in `payload`."""
    prompt = payload.get("prompt", "")
    tvmaze, places = build_mcp_clients()
    # Clients must stay connected while the agent runs — tool calls proxy over stdio.
    with tvmaze, places:
        tools = tvmaze.list_tools_sync() + places.list_tools_sync()
        agent = build_agent(tools)
        result = agent(prompt)
    return {"result": str(result)}


if __name__ == "__main__":
    app.run()
