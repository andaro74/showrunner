"""LangGraph places specialist.

Owns exactly one MCP server: `places` (geocode, find_nearby, travel_time), loaded
via `langchain-mcp-adapters`. The Strands specialist owns `tvmaze` the same way —
together they partition the seven MCP tools with no overlap, and the orchestrator
in `agents/orchestrator/` composes them. One protocol, two frameworks, and each
server still moves between frameworks without a rewrite.
"""

from __future__ import annotations

import os
import sys

from langchain_aws import ChatBedrockConverse
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StdioConnection, StreamableHttpConnection
from langgraph.prebuilt import create_react_agent

from agents.langgraph.prompts import SYSTEM_PROMPT

# The ONE MCP server this specialist owns, launched over stdio by default.
PLACES_SERVER = "mcp_servers.places.server"

# Same env contract as the Strands specialist — see agents/strands/agent.py.
PLACES_URL_ENV = "PLACES_MCP_URL"
BEARER_TOKEN_ENV = "MCP_BEARER_TOKEN"

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
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
    """MultiServerMCPClient wired to the places server, on either transport."""
    return MultiServerMCPClient(
        {"places": connection_for(PLACES_SERVER, os.environ.get(PLACES_URL_ENV))}
    )


async def load_tools() -> list:
    """Adapt the places MCP tools into LangChain tools."""
    return await build_mcp_client().get_tools()


def build_model() -> ChatBedrockConverse:
    """Bedrock Converse chat model; ids/region from env, else defaults."""
    return ChatBedrockConverse(
        model_id=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
        region_name=os.environ.get("AWS_REGION", DEFAULT_REGION),
    )


async def build_agent():
    """Assemble the LangGraph ReAct agent over the places tools."""
    tools = await load_tools()
    return create_react_agent(build_model(), tools, prompt=SYSTEM_PROMPT)


async def invoke(prompt: str) -> dict:
    """Run the specialist on a single prompt and return its final state."""
    agent = await build_agent()
    return await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})


def _message_text(message: object) -> str:
    """Flatten a LangChain message's content — Bedrock Converse may return a
    list of content blocks instead of a plain string."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


async def answer(question: str) -> str:
    """Answer one places question end to end; the orchestrator's delegate."""
    state = await invoke(question)
    return _message_text(state["messages"][-1])
