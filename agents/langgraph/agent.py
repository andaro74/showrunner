"""LangGraph movie-night agent (variant).

Loads the SAME two MCP servers (tvmaze + places) as the Strands agent, but via
`langchain-mcp-adapters` instead of Strands' `MCPClient`. This is the portability
demo: one pair of servers, two frameworks, and no per-framework tool rewrites —
`MultiServerMCPClient.get_tools()` adapts the identical MCP tools into LangChain
tools that a LangGraph ReAct agent consumes directly.
"""

from __future__ import annotations

import os
import sys

from langchain_aws import ChatBedrockConverse
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StdioConnection, StreamableHttpConnection
from langgraph.prebuilt import create_react_agent

from agents.langgraph.prompts import SYSTEM_PROMPT

# Same MCP server entrypoints the Strands agent uses, launched over stdio.
TVMAZE_SERVER = "mcp_servers.tvmaze.server"
PLACES_SERVER = "mcp_servers.places.server"

# Same env contract as the Strands agent — see agents/strands/agent.py.
TVMAZE_URL_ENV = "TVMAZE_MCP_URL"
PLACES_URL_ENV = "PLACES_MCP_URL"
BEARER_TOKEN_ENV = "MCP_BEARER_TOKEN"

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-5"
DEFAULT_REGION = "us-west-2"


def _stdio(module: str) -> StdioConnection:
    """Stdio connection spec that runs `python -m module` in the current venv."""
    return StdioConnection(transport="stdio", command=sys.executable, args=["-m", module])


def _http(url: str) -> StreamableHttpConnection:
    """HTTP connection spec for a server running as its own AgentCore Runtime."""
    token = os.environ.get(BEARER_TOKEN_ENV)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return StreamableHttpConnection(transport="streamable_http", url=url, headers=headers)


def connection_for(module: str, url: str | None) -> StdioConnection | StreamableHttpConnection:
    """HTTP when a URL is configured, else spawn the server on stdio."""
    return _http(url) if url else _stdio(module)


def build_mcp_client() -> MultiServerMCPClient:
    """MultiServerMCPClient wired to both backing servers, on either transport."""
    return MultiServerMCPClient(
        {
            "tvmaze": connection_for(TVMAZE_SERVER, os.environ.get(TVMAZE_URL_ENV)),
            "places": connection_for(PLACES_SERVER, os.environ.get(PLACES_URL_ENV)),
        }
    )


async def load_tools() -> list:
    """Adapt the MCP tools from both servers into LangChain tools."""
    return await build_mcp_client().get_tools()


def build_model() -> ChatBedrockConverse:
    """Bedrock Converse chat model; ids/region from env, else defaults."""
    return ChatBedrockConverse(
        model_id=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
        region_name=os.environ.get("AWS_REGION", DEFAULT_REGION),
    )


async def build_agent():
    """Assemble the LangGraph ReAct agent over the shared MCP tools."""
    tools = await load_tools()
    return create_react_agent(build_model(), tools, prompt=SYSTEM_PROMPT)


async def invoke(prompt: str) -> dict:
    """Run the agent on a single user prompt and return its final state."""
    agent = await build_agent()
    return await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
