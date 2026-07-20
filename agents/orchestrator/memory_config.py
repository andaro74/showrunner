"""AgentCore Memory configuration for the Strands agent.

Two tiers:
- **Short-term** — the active session's conversation turns, keyed by
  (`actor_id`, `session_id`). Replayed into the next turn.
- **Long-term** — durable records under the *named namespaces* below: the user's
  genre preferences and the picks they've been shown before.

Every namespace is scoped by `{actor_id}`, so one user can never read another's
memory. The actor id comes from Identity's inbound JWT `sub` claim — see the
Memory + Identity note in the README.

Memory is optional: with no `AGENTCORE_MEMORY_ID` set (local dev, tests) the
agent runs statelessly.
"""

from __future__ import annotations

import os

from bedrock_agentcore.memory import MemorySessionManager
from bedrock_agentcore.memory.constants import StrategyType

MEMORY_ID_ENV = "AGENTCORE_MEMORY_ID"
REGION_ENV = "AWS_REGION"
DEFAULT_REGION = "us-west-2"

# Used only when no authenticated actor is available (local dev).
DEFAULT_ACTOR_ID = "anonymous"

# How many recent turns to replay as short-term context.
SHORT_TERM_TURNS = 5
# How many long-term records to pull per namespace.
LONG_TERM_TOP_K = 3

# --- Named long-term namespaces -------------------------------------------
# `{actor_id}` is filled per request via `namespace_for()`.
#
# These MUST match the `namespaceTemplates` that `agentcore add memory` wrote
# into the deploy project's manifest (../showrunnerAgentcore/agentcore/
# agentcore.json) — the CLI has no flag to override them, and if they drift,
# recall silently returns nothing. The manifest spells the placeholder
# `{actorId}`; only the substituted path has to match.
GENRE_PREFERENCES = "/users/{actor_id}/preferences"
REMEMBERED_PICKS = "/users/{actor_id}/facts"

# Namespace -> the strategy that populates it, mirroring the manifest.
NAMESPACE_STRATEGIES: dict[str, StrategyType] = {
    GENRE_PREFERENCES: StrategyType.USER_PREFERENCE,
    REMEMBERED_PICKS: StrategyType.SEMANTIC,
}


def namespace_for(template: str, actor_id: str) -> str:
    """Fill a namespace template for one actor, e.g. /showrunner/actors/u123/picks."""
    return template.format(actor_id=actor_id)


def memory_id() -> str | None:
    """The configured memory id: explicit env first, else the CLI-injected one.

    A deployed runtime does not get AGENTCORE_MEMORY_ID — the AgentCore CLI
    injects `MEMORY_<NAME>_ID` (observed: MEMORY_SHOWRUNNERMEMORY_ID) for every
    memory in the manifest. Without this fallback, memory silently disables on
    deploy: same failure class as the namespace mismatch, invisible until a
    user notices nothing is remembered.
    """
    explicit = os.environ.get(MEMORY_ID_ENV)
    if explicit:
        return explicit
    injected = sorted(
        value
        for key, value in os.environ.items()
        if key.startswith("MEMORY_") and key.endswith("_ID") and value
    )
    return injected[0] if injected else None


def region() -> str:
    return os.environ.get(REGION_ENV) or DEFAULT_REGION


def is_enabled() -> bool:
    """True when an AgentCore Memory resource is configured."""
    return memory_id() is not None


def build_session_manager() -> MemorySessionManager | None:
    """MemorySessionManager for the configured memory, or None when disabled."""
    configured = memory_id()
    if not configured:
        return None
    return MemorySessionManager(memory_id=configured, region_name=region())
