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

from agents.mcp_env import bare_tool_name, server_url
from agents.strands.prompts import SYSTEM_PROMPT

# The ONE MCP server this specialist owns, launched over stdio as `python -m <module>`.
TVMAZE_SERVER = "mcp_servers.tvmaze.server"
# The tools that server exposes — the ownership filter for gateway mode.
TVMAZE_TOOLS = frozenset({"search_shows", "get_schedule", "get_episodes", "get_cast"})

# When set, the server is reached over HTTP (through the Gateway) instead of
# being spawned as a local subprocess. A deployed runtime needs no env config:
# the CLI-injected gateway URL is picked up automatically (see agents/mcp_env.py).
TVMAZE_URL_ENV = "TVMAZE_MCP_URL"
# Bearer token presented to the Gateway (its CUSTOM_JWT authorizer validates it).
# Local-dev fallback only — deployed, the caller's token is passed per request.
BEARER_TOKEN_ENV = "MCP_BEARER_TOKEN"


def _auth_headers(token: str | None = None) -> dict[str, str] | None:
    token = token or os.environ.get(BEARER_TOKEN_ENV)
    return {"Authorization": f"Bearer {token}"} if token else None


def mcp_client_for(module: str, url: str | None = None, token: str | None = None) -> MCPClient:
    """MCPClient for one server: HTTP when `url` is given, else a stdio subprocess."""
    if url:
        headers = _auth_headers(token)
        return MCPClient(lambda: streamablehttp_client(url, headers=headers))
    return MCPClient(
        lambda: stdio_client(StdioServerParameters(command=sys.executable, args=["-m", module]))
    )


def build_mcp_clients(token: str | None = None) -> list[MCPClient]:
    """The specialist's MCP clients — exactly one, for the tvmaze server."""
    return [mcp_client_for(TVMAZE_SERVER, server_url(TVMAZE_URL_ENV), token)]


def owned_tools(tools: list) -> list:
    """Only the tools this specialist owns.

    The Gateway exposes ALL seven tools at one endpoint (prefixed names, e.g.
    TvmazeMcpTarget___search_shows). Without this filter, gateway mode would
    register the places tools here too and silently break the specialist
    partition that the stdio tests assert.
    """
    return [t for t in tools if bare_tool_name(t.tool_name) in TVMAZE_TOOLS]


def build_agent(tools: list) -> Agent:
    """Assemble the specialist over the given MCP tools.

    `tools` must be collected from MCPClients that are currently connected.
    Model id comes from BEDROCK_MODEL_ID when set, else Strands' Bedrock default.
    """
    return Agent(
        model=os.environ.get("BEDROCK_MODEL_ID"),
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        # No streaming printer: answer() returns the text, and the default
        # handler crashes on emoji under cp1252 consoles (see orchestrator).
        callback_handler=None,
    )


def answer(question: str, bearer_token: str | None = None) -> str:
    """Answer one show question end to end; the orchestrator's delegate.

    Opens the MCP connection for the duration of the run — tool calls proxy
    over the connection, so the client must stay connected while the agent
    works. `bearer_token` is the caller's JWT, forwarded per request so the
    Gateway's Cedar policies see the real user.
    """
    (tvmaze,) = build_mcp_clients(bearer_token)
    with tvmaze:
        agent = build_agent(owned_tools(tvmaze.list_tools_sync()))
        return str(agent(question))
