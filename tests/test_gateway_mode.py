"""Tests for gateway mode — how the agents behave when deployed.

Deployed specialists reach their MCP server through the Gateway, which exposes
ALL seven tools at one endpoint with prefixed names. These tests pin the three
things that keep that safe, with no network:

- ownership filters keep the specialist partition intact behind the gateway
- the CLI-injected gateway URL auto-wires a deployed runtime (stdio only local)
- the caller's bearer token flows entrypoint -> delegate -> specialist
"""

from types import SimpleNamespace

from agents import mcp_env
from agents.langgraph import agent as lg_agent
from agents.orchestrator import agent as orchestrator
from agents.orchestrator import memory_config
from agents.strands import agent as strands_agent

GATEWAY_TOOLS = [
    "TvmazeMcpTarget___search_shows",
    "TvmazeMcpTarget___get_schedule",
    "TvmazeMcpTarget___get_episodes",
    "TvmazeMcpTarget___get_cast",
    "PlacesMcpTarget___geocode",
    "PlacesMcpTarget___find_nearby",
    "PlacesMcpTarget___travel_time",
]


# --- ownership filters ------------------------------------------------------


def test_strands_filter_keeps_only_tvmaze_tools():
    tools = [SimpleNamespace(tool_name=name) for name in GATEWAY_TOOLS]
    owned = {t.tool_name for t in strands_agent.owned_tools(tools)}
    assert owned == {n for n in GATEWAY_TOOLS if n.startswith("TvmazeMcpTarget")}


def test_langgraph_filter_keeps_only_places_tools():
    tools = [SimpleNamespace(name=name) for name in GATEWAY_TOOLS]
    owned = {t.name for t in lg_agent.owned_tools(tools)}
    assert owned == {n for n in GATEWAY_TOOLS if n.startswith("PlacesMcpTarget")}


def test_filters_pass_bare_stdio_names_through():
    assert len(strands_agent.owned_tools([SimpleNamespace(tool_name="search_shows")])) == 1
    assert len(lg_agent.owned_tools([SimpleNamespace(name="geocode")])) == 1


def test_filters_partition_the_gateway_toolset():
    """Both specialists pointed at the SAME gateway endpoint must still share
    nothing and cover everything — the deployed form of the partition test."""
    strands_owned = {
        t.tool_name
        for t in strands_agent.owned_tools([SimpleNamespace(tool_name=n) for n in GATEWAY_TOOLS])
    }
    lg_owned = {
        t.name for t in lg_agent.owned_tools([SimpleNamespace(name=n) for n in GATEWAY_TOOLS])
    }
    assert not (strands_owned & lg_owned)
    assert strands_owned | lg_owned == set(GATEWAY_TOOLS)


# --- deployed env auto-wiring ------------------------------------------------


def test_injected_gateway_url_is_detected(monkeypatch):
    monkeypatch.setenv("AGENTCORE_GATEWAY_SHOWRUNNER_GATEWAY_URL", "https://gw.example/mcp")
    assert mcp_env.injected_gateway_url() == "https://gw.example/mcp"
    assert mcp_env.server_url("TVMAZE_MCP_URL") == "https://gw.example/mcp"


def test_explicit_url_beats_injected(monkeypatch):
    monkeypatch.setenv("AGENTCORE_GATEWAY_SHOWRUNNER_GATEWAY_URL", "https://gw.example/mcp")
    monkeypatch.setenv("TVMAZE_MCP_URL", "https://explicit.example/mcp")
    assert mcp_env.server_url("TVMAZE_MCP_URL") == "https://explicit.example/mcp"


def test_no_urls_means_stdio(monkeypatch):
    for key in list(mcp_env.os.environ):
        if key.startswith("AGENTCORE_GATEWAY_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("TVMAZE_MCP_URL", raising=False)
    assert mcp_env.server_url("TVMAZE_MCP_URL") is None


def test_injected_memory_id_is_detected(monkeypatch):
    """The CLI injects MEMORY_<NAME>_ID, not AGENTCORE_MEMORY_ID — without the
    fallback, memory silently disables on every deployed runtime."""
    monkeypatch.delenv(memory_config.MEMORY_ID_ENV, raising=False)
    monkeypatch.setenv("MEMORY_SHOWRUNNERMEMORY_ID", "mem-injected-123")
    assert memory_config.memory_id() == "mem-injected-123"

    monkeypatch.setenv(memory_config.MEMORY_ID_ENV, "mem-explicit-456")
    assert memory_config.memory_id() == "mem-explicit-456"


# --- caller-token pass-through ----------------------------------------------


def test_token_flows_from_entrypoint_to_both_delegates(monkeypatch):
    """entrypoint sets the contextvar from the request's Authorization header;
    each delegate forwards it to its specialist as bearer_token."""
    seen: dict[str, str | None] = {}

    def fake_show_answer(question, bearer_token=None):
        seen["show"] = bearer_token
        return "a show"

    async def fake_places_answer(question, bearer_token=None):
        seen["places"] = bearer_token
        return "a place"

    monkeypatch.setattr(orchestrator.show_specialist, "answer", fake_show_answer)
    monkeypatch.setattr(orchestrator.places_specialist, "answer", fake_places_answer)

    class DelegatingFakeAgent:
        def __call__(self, prompt):
            import asyncio

            orchestrator.ask_show_expert("q1")
            asyncio.run(orchestrator.ask_places_expert("q2"))
            return "done"

    monkeypatch.setattr(orchestrator, "build_agent", DelegatingFakeAgent)
    monkeypatch.setattr(orchestrator.memory_config, "build_session_manager", lambda: None)

    context = SimpleNamespace(
        request_headers={"Authorization": "Bearer tok-e2e"}, session_id="s1"
    )
    out = orchestrator.invoke({"prompt": "plan"}, context)

    assert out["result"] == "done"
    assert seen == {"show": "tok-e2e", "places": "tok-e2e"}


def test_no_header_means_no_token(monkeypatch):
    monkeypatch.setattr(
        orchestrator.show_specialist, "answer", lambda q, bearer_token=None: str(bearer_token)
    )

    class DelegatingFakeAgent:
        def __call__(self, prompt):
            return orchestrator.ask_show_expert("q")

    monkeypatch.setattr(orchestrator, "build_agent", DelegatingFakeAgent)
    monkeypatch.setattr(orchestrator.memory_config, "build_session_manager", lambda: None)

    out = orchestrator.invoke({"prompt": "plan", "actor_id": "u1"})
    assert out["result"] == "None"
