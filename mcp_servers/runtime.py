"""Transport configuration shared by the MCP servers (framework-agnostic).

Two deployment shapes, one codebase:

- **stdio** (default) — the agent spawns the server as a private subprocess.
  This is what the tests and local dev use; no network, no auth, no infra.
- **streamable-http** — the server runs standalone as its own AgentCore Runtime
  (`--protocol MCP`) and is reached over HTTP through the Gateway, which is what
  makes Identity and the Cedar policies in `policies/` apply to tool calls.

Precedence: `MCP_TRANSPORT` wins if set, else the caller's `default`, else stdio.
The deploy entrypoints (`serve_*.py`) pass `default=DEPLOYED_TRANSPORT`, because a
server running as an AgentCore Runtime has no stdio peer — nothing is attached to
its stdin. Defaulting to stdio there binds no port, so the gateway cannot fetch
tools and the target fails with "Runtime initialization time exceeded".

The caller states this rather than the module detecting it: the AgentCore CLI has
no flag for setting runtime env vars, and container probes (`/.dockerenv`) do not
hold in the AgentCore sandbox — a deployed runtime produced no logs at all while
silently waiting on stdin. `serve_*.py` exists only for deployment, so it is the
one place that can say so without guessing.

No Strands/LangChain imports here (CLAUDE.md hard rule #1).
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

TRANSPORT_ENV = "MCP_TRANSPORT"
HOST_ENV = "MCP_HOST"
PORT_ENV = "MCP_PORT"

DEFAULT_TRANSPORT = "stdio"
# What serve_*.py asks for: an AgentCore Runtime is reached over HTTP, never stdio.
DEPLOYED_TRANSPORT = "streamable-http"
# AgentCore's MCP protocol contract: the platform probes 0.0.0.0:8000/mcp. Port 8080
# belongs to the *HTTP* protocol contract (BedrockAgentCoreApp's /invocations +
# /ping) — binding it here leaves the prober with nothing to reach, and every call
# fails with "Runtime initialization time exceeded" while uvicorn sits healthy.
# 8000 is also FastMCP's own default, as is the /mcp path.
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp-protocol-contract.html
DEFAULT_PORT = 8000

VALID_TRANSPORTS = ("stdio", "sse", "streamable-http")


def in_container() -> bool:
    """True when running inside a container.

    Used only for the bind address. It is NOT reliable for choosing a transport:
    the AgentCore sandbox has no /.dockerenv, so this returns False there.
    """
    return Path("/.dockerenv").exists() or bool(os.environ.get("DOCKER_CONTAINER"))


def transport(default: str = DEFAULT_TRANSPORT) -> str:
    """Configured transport; an explicit MCP_TRANSPORT always wins over `default`."""
    value = os.environ.get(TRANSPORT_ENV) or default
    if value not in VALID_TRANSPORTS:
        raise ValueError(f"{TRANSPORT_ENV}={value!r} is not one of {', '.join(VALID_TRANSPORTS)}")
    return value


def host() -> str:
    """Bind address for the HTTP transports.

    Defaults to 0.0.0.0. Anything serving HTTP here is meant to be reached from
    outside its own process — an AgentCore Runtime behind the Gateway is the whole
    point of the HTTP path. Binding loopback instead makes the server start
    cleanly, log nothing, and time out on every connection, which is exactly what
    a deployed runtime did while `in_container()` returned False in the AgentCore
    sandbox. Set MCP_HOST to narrow it.
    """
    return os.environ.get(HOST_ENV) or "0.0.0.0"  # noqa: S104 - see docstring


def port() -> int:
    return int(os.environ.get(PORT_ENV) or DEFAULT_PORT)


def run_server(mcp: FastMCP, default: str = DEFAULT_TRANSPORT) -> None:
    """Run `mcp` on the configured transport.

    stdio ignores host/port; the HTTP transports bind them. The streamable-http
    endpoint is served at `mcp.settings.streamable_http_path` (default `/mcp`),
    which is the URL a gateway target points at.
    """
    selected = transport(default)
    if selected == "stdio":
        mcp.run()
        return

    mcp.settings.host = host()
    mcp.settings.port = port()
    # Per the AgentCore MCP contract: the platform load-balances and injects its own
    # Mcp-Session-Id, so the server must run stateless; json_response is what the
    # official samples mark "required for AgentCore Runtime compatibility".
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    # The MCP SDK's DNS-rebinding protection rejects any unrecognized Host header
    # with 421. That guards a *localhost dev server* against hostile web pages; a
    # deployed runtime is reached through AgentCore's authenticated front door,
    # whose forwarded AWS Host header the allowlist has never heard of — so with
    # protection on, every platform request 421s. (Reproduced locally: same POST
    # returns 200 with a localhost Host, 421 with an amazonaws.com Host.)
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
    mcp.run(transport=selected)
