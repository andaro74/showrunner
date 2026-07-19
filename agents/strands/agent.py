"""Strands movie-night agent (primary).

Connects to BOTH MCP servers (tvmaze + places) over stdio via Strands `MCPClient`,
and is wrapped in a `BedrockAgentCoreApp` for the runtime entrypoint. The MCP
servers are launched as subprocesses using the current interpreter, so the same
virtualenv (and its deps) is reused.
"""

from __future__ import annotations

import base64
import json
import os
import sys

from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.tools.mcp import MCPClient

from agents.strands import memory_config
from agents.strands.prompts import SYSTEM_PROMPT

# MCP server entrypoints, launched over stdio as `python -m <module>`.
TVMAZE_SERVER = "mcp_servers.tvmaze.server"
PLACES_SERVER = "mcp_servers.places.server"

# When these are set, the servers are reached over HTTP (each its own AgentCore
# Runtime, behind the Gateway) instead of being spawned as local subprocesses.
TVMAZE_URL_ENV = "TVMAZE_MCP_URL"
PLACES_URL_ENV = "PLACES_MCP_URL"
# Bearer token presented to the Gateway (its CUSTOM_JWT authorizer validates it).
BEARER_TOKEN_ENV = "MCP_BEARER_TOKEN"

app = BedrockAgentCoreApp()


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
    """One MCPClient per backing server (tvmaze, places).

    Transport is per-server: set TVMAZE_MCP_URL / PLACES_MCP_URL to reach them
    over HTTP through the Gateway; unset means spawn them locally on stdio.
    """
    return [
        mcp_client_for(TVMAZE_SERVER, os.environ.get(TVMAZE_URL_ENV)),
        mcp_client_for(PLACES_SERVER, os.environ.get(PLACES_URL_ENV)),
    ]


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


def resolve_actor_id(payload: dict, context: object | None = None) -> str:
    """Identify the caller, preferring Identity's inbound JWT `sub` claim.

    The JWT is *verified upstream* by the gateway's CUSTOM_JWT authorizer, so we
    only decode the claims here — never trust this token without that authorizer.
    Falls back to an explicit payload actor_id, then to a local-dev default.
    """
    headers = getattr(context, "request_headers", None) or {}
    token = _bearer_token(headers)
    if token:
        subject = _jwt_subject(token)
        if subject:
            return subject
    return payload.get("actor_id") or memory_config.DEFAULT_ACTOR_ID


def _bearer_token(headers: dict) -> str | None:
    for key, value in headers.items():
        if key.lower() == "authorization" and str(value).lower().startswith("bearer "):
            return str(value).split(" ", 1)[1].strip()
    return None


def _jwt_subject(token: str) -> str | None:
    """Read the `sub` claim from a JWT payload segment (no signature check)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    segment = parts[1]
    padded = segment + "=" * (-len(segment) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError, json.JSONDecodeError:
        return None
    return claims.get("sub")


def _record_text(record: object) -> str:
    """Pull display text out of a dict-like MemoryRecord/EventMessage."""
    getter = getattr(record, "get", None)
    if getter is None:
        return str(record)
    content = getter("content")
    if isinstance(content, dict):
        return str(content.get("text", "")).strip()
    return str(content or "").strip()


def recall(manager, actor_id: str, session_id: str, prompt: str) -> str:
    """Short-term turns + long-term preferences/picks, as a context block.

    Memory must never break a turn, so retrieval failures degrade to no context.
    """
    sections: list[str] = []

    try:
        turns = manager.get_last_k_turns(
            actor_id=actor_id, session_id=session_id, k=memory_config.SHORT_TERM_TURNS
        )
    except Exception:  # noqa: BLE001 - memory is best-effort
        turns = []
    recent = [_record_text(m) for turn in turns for m in turn]
    if any(recent):
        sections.append("Earlier in this session:\n" + "\n".join(t for t in recent if t))

    for label, template in (
        ("Known preferences", memory_config.GENRE_PREFERENCES),
        ("Previously suggested", memory_config.REMEMBERED_PICKS),
    ):
        try:
            records = manager.search_long_term_memories(
                query=prompt,
                namespace_prefix=memory_config.namespace_for(template, actor_id),
                top_k=memory_config.LONG_TERM_TOP_K,
            )
        except Exception:  # noqa: BLE001 - memory is best-effort
            continue
        texts = [t for t in (_record_text(r) for r in records) if t]
        if texts:
            sections.append(f"{label}:\n" + "\n".join(texts))

    return "\n\n".join(sections)


def remember(manager, actor_id: str, session_id: str, prompt: str, reply: str) -> None:
    """Persist this turn as short-term memory; strategies distil it long-term."""
    try:
        manager.add_turns(
            actor_id=actor_id,
            session_id=session_id,
            messages=[
                ConversationalMessage(prompt, MessageRole.USER),
                ConversationalMessage(reply, MessageRole.ASSISTANT),
            ],
        )
    except Exception:  # noqa: BLE001 - memory is best-effort
        return


@app.entrypoint
def invoke(payload: dict, context: object | None = None) -> dict:
    """Runtime entrypoint: plan a movie night for the prompt in `payload`.

    When AgentCore Memory is configured, the caller's prior session turns and
    long-term preferences are recalled first and the turn is persisted after.
    """
    prompt = payload.get("prompt", "")
    actor_id = resolve_actor_id(payload, context)
    session_id = getattr(context, "session_id", None) or payload.get("session_id") or actor_id

    manager = memory_config.build_session_manager()
    remembered = recall(manager, actor_id, session_id, prompt) if manager else ""
    turn_input = f"{remembered}\n\n{prompt}".strip() if remembered else prompt

    tvmaze, places = build_mcp_clients()
    # Clients must stay connected while the agent runs — tool calls proxy over stdio.
    with tvmaze, places:
        tools = tvmaze.list_tools_sync() + places.list_tools_sync()
        agent = build_agent(tools)
        result = agent(turn_input)

    reply = str(result)
    if manager:
        remember(manager, actor_id, session_id, prompt, reply)
    return {"result": reply, "actor_id": actor_id, "session_id": session_id}


if __name__ == "__main__":
    app.run()
