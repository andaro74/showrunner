"""Shared MCP wiring helpers for the agents. Framework-free on purpose.

Two facts of deployed life live here:

- **The AgentCore CLI injects the gateway URL.** Every runtime deployed from the
  manifest receives `AGENTCORE_GATEWAY_<NAME>_URL` (observed:
  AGENTCORE_GATEWAY_SHOWRUNNER_GATEWAY_URL=https://.../mcp). Reading it means a
  deployed agent auto-wires to the Gateway with zero manual env config — and the
  stdio fallback (which would spawn duplicate in-container copies of the MCP
  servers and bypass Gateway/Cedar/Identity) only ever happens locally, where it
  is the correct behavior.

- **Gateway tool names are prefixed.** Through the Gateway a tool is
  `TvmazeMcpTarget___search_shows`; over stdio it is `search_shows`. Specialists
  filter the listed tools to the set they own by bare name, so pointing both
  specialists at the same gateway endpoint (which exposes ALL seven tools) cannot
  break the partition — without the filter, each specialist would register all
  seven and the show specialist would quietly gain places tools in production
  while every local test stays green.
"""

from __future__ import annotations

import os

GATEWAY_URL_ENV_PREFIX = "AGENTCORE_GATEWAY_"
GATEWAY_URL_ENV_SUFFIX = "_URL"


def injected_gateway_url() -> str | None:
    """The CLI-injected Gateway MCP URL, when running as a deployed runtime."""
    candidates = sorted(
        value
        for key, value in os.environ.items()
        if key.startswith(GATEWAY_URL_ENV_PREFIX) and key.endswith(GATEWAY_URL_ENV_SUFFIX) and value
    )
    return candidates[0] if candidates else None


def server_url(explicit_env: str) -> str | None:
    """Resolve a specialist's server URL: explicit env var first, else the
    injected gateway URL, else None (spawn the server on stdio — local dev)."""
    return os.environ.get(explicit_env) or injected_gateway_url()


def bare_tool_name(name: str) -> str:
    """Tool name without the gateway's `<TargetName>___` prefix."""
    return name.split("___")[-1]
