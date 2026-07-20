"""Strands show specialist.

Owns exactly one MCP server: `tvmaze` (search_shows, get_schedule, get_episodes,
get_cast), connected over stdio via Strands `MCPClient`. The LangGraph specialist
owns `places` the same way — together they partition the seven MCP tools with no
overlap. The orchestrator in `agents/orchestrator/` composes them and owns every
user-facing concern (entrypoint, memory, identity); this module stays a plain
domain agent that answers one question at a time via `answer()`.

The server is launched as a subprocess using the current interpreter, so the same
virtualenv (and its deps) is reused.
"""

from __future__ import annotations

import os
import sys

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.tools.mcp import MCPClient

from agents.strands.prompts import SYSTEM_PROMPT

# The ONE MCP server this specialist owns, launched over stdio as `python -m <module>`.
TVMAZE_SERVER = "mcp_servers.tvmaze.server"

# When set, the server is reached over HTTP (its own AgentCore Runtime, behind
# the Gateway) instead of being spawned as a local subprocess.
TVMAZE_URL_ENV = "TVMAZE_MCP_URL"
# Bearer token presented to the Gateway (its CUSTOM_JWT authorizer validates it).
BEARER_TOKEN_ENV = "MCP_BEARER_TOKEN"


def _auth_headers() -> dict[str, str] | None:
    token = os.environ.get(BEARER_TOKEN_ENV)
    return {"Authorization": f"Bearer {token}"} if token else None


def mcp_client_for(module: str, url: str | None = None) -> MCPClient:
    """MCPClient for one server: HTTP when `url` is given, else a stdio subprocess."""
    if url:
        headers = _auth_headers()
        return MCPClient(lambda: streamablehttp_client(url, headers=headers))
    return MCPClient(
        lambda: stdio_client(StdioServerParameters(command=sys.executable, args=["-m", module]))
    )


def build_mcp_clients() -> list[MCPClient]:
    """The specialist's MCP clients — exactly one, for the tvmaze server.

    Set TVMAZE_MCP_URL to reach it over HTTP through the Gateway; unset means
    spawn it locally on stdio.
    """
    return [mcp_client_for(TVMAZE_SERVER, os.environ.get(TVMAZE_URL_ENV))]


def build_agent(tools: list) -> Agent:
    """Assemble the specialist over the given MCP tools.

    `tools` must be collected from MCPClients that are currently connected.
    Model id comes from BEDROCK_MODEL_ID when set, else Strands' Bedrock default.
    """
    return Agent(
        model=os.environ.get("BEDROCK_MODEL_ID"),
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
    )


def answer(question: str) -> str:
    """Answer one show question end to end; the orchestrator's delegate.

    Opens the MCP connection for the duration of the run — tool calls proxy
    over stdio, so the client must stay connected while the agent works.
    """
    (tvmaze,) = build_mcp_clients()
    with tvmaze:
        agent = build_agent(tvmaze.list_tools_sync())
        return str(agent(question))
