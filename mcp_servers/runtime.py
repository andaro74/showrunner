"""Transport configuration shared by the MCP servers (framework-agnostic).

Two deployment shapes, one codebase:

- **stdio** (default) — the agent spawns the server as a private subprocess.
  This is what the tests and local dev use; no network, no auth, no infra.
- **streamable-http** — the server runs standalone as its own AgentCore Runtime
  (`--protocol MCP`) and is reached over HTTP through the Gateway, which is what
  makes Identity and the Cedar policies in `policies/` apply to tool calls.

Selected by `MCP_TRANSPORT`; stdio stays the default so nothing existing changes.
No Strands/LangChain imports here (CLAUDE.md hard rule #1).
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

TRANSPORT_ENV = "MCP_TRANSPORT"
HOST_ENV = "MCP_HOST"
PORT_ENV = "MCP_PORT"

DEFAULT_TRANSPORT = "stdio"
# AgentCore Runtime serves on 8080 — same default as BedrockAgentCoreApp.run().
DEFAULT_PORT = 8080

VALID_TRANSPORTS = ("stdio", "sse", "streamable-http")


def transport() -> str:
    """Configured transport; defaults to stdio."""
    value = os.environ.get(TRANSPORT_ENV) or DEFAULT_TRANSPORT
    if value not in VALID_TRANSPORTS:
        raise ValueError(f"{TRANSPORT_ENV}={value!r} is not one of {', '.join(VALID_TRANSPORTS)}")
    return value


def in_container() -> bool:
    """True when running inside a container (same probe BedrockAgentCoreApp uses)."""
    return Path("/.dockerenv").exists() or bool(os.environ.get("DOCKER_CONTAINER"))


def host() -> str:
    """Bind address: 0.0.0.0 in a container so the runtime can expose the port."""
    configured = os.environ.get(HOST_ENV)
    if configured:
        return configured
    return "0.0.0.0" if in_container() else "127.0.0.1"  # noqa: S104 - container needs this


def port() -> int:
    return int(os.environ.get(PORT_ENV) or DEFAULT_PORT)


def run_server(mcp: FastMCP) -> None:
    """Run `mcp` on the configured transport.

    stdio ignores host/port; the HTTP transports bind them. The streamable-http
    endpoint is served at `mcp.settings.streamable_http_path` (default `/mcp`),
    which is the URL a gateway target points at.
    """
    selected = transport()
    if selected == "stdio":
        mcp.run()
        return

    mcp.settings.host = host()
    mcp.settings.port = port()
    mcp.run(transport=selected)
