"""Tests for the dual-transport wiring (Option B).

The servers speak stdio by default and streamable-http when deployed as their own
AgentCore Runtimes behind the Gateway. These tests pin the default (so the local
and test path can't silently change) and the per-server switch on both agents.
No network, no subprocesses.
"""

import pytest

from agents.langgraph import agent as lg_agent
from agents.strands import agent as strands_agent
from mcp_servers import runtime


# --- server transport config ----------------------------------------------


def test_defaults_to_stdio(monkeypatch):
    monkeypatch.delenv(runtime.TRANSPORT_ENV, raising=False)
    assert runtime.transport() == "stdio"


def test_transport_from_env(monkeypatch):
    monkeypatch.setenv(runtime.TRANSPORT_ENV, "streamable-http")
    assert runtime.transport() == "streamable-http"


def test_unknown_transport_is_rejected(monkeypatch):
    monkeypatch.setenv(runtime.TRANSPORT_ENV, "carrier-pigeon")
    with pytest.raises(ValueError, match="carrier-pigeon"):
        runtime.transport()


def test_port_matches_agentcore_runtime_contract(monkeypatch):
    monkeypatch.delenv(runtime.PORT_ENV, raising=False)
    assert runtime.port() == 8080  # same port BedrockAgentCoreApp.run() serves on


def test_binds_all_interfaces_only_in_container(monkeypatch):
    monkeypatch.delenv(runtime.HOST_ENV, raising=False)

    monkeypatch.setattr(runtime, "in_container", lambda: True)
    assert runtime.host() == "0.0.0.0"  # noqa: S104 - asserting container behaviour

    monkeypatch.setattr(runtime, "in_container", lambda: False)
    assert runtime.host() == "127.0.0.1"


def test_host_env_overrides(monkeypatch):
    monkeypatch.setenv(runtime.HOST_ENV, "10.0.0.5")
    assert runtime.host() == "10.0.0.5"


# --- agent-side transport selection ---------------------------------------


def test_langgraph_uses_stdio_without_urls(monkeypatch):
    monkeypatch.delenv(lg_agent.TVMAZE_URL_ENV, raising=False)
    monkeypatch.delenv(lg_agent.PLACES_URL_ENV, raising=False)

    conn = lg_agent.connection_for(lg_agent.TVMAZE_SERVER, None)
    assert conn["transport"] == "stdio"
    assert conn["args"] == ["-m", lg_agent.TVMAZE_SERVER]


def test_langgraph_uses_http_with_url(monkeypatch):
    monkeypatch.delenv(lg_agent.BEARER_TOKEN_ENV, raising=False)
    conn = lg_agent.connection_for(lg_agent.PLACES_SERVER, "https://gw.example/mcp")

    assert conn["transport"] == "streamable_http"
    assert conn["url"] == "https://gw.example/mcp"
    assert conn["headers"] is None


def test_langgraph_sends_bearer_token(monkeypatch):
    monkeypatch.setenv(lg_agent.BEARER_TOKEN_ENV, "tok-123")
    conn = lg_agent.connection_for(lg_agent.PLACES_SERVER, "https://gw.example/mcp")
    assert conn["headers"] == {"Authorization": "Bearer tok-123"}


def test_strands_builds_a_client_per_server(monkeypatch):
    monkeypatch.delenv(strands_agent.TVMAZE_URL_ENV, raising=False)
    monkeypatch.delenv(strands_agent.PLACES_URL_ENV, raising=False)
    assert len(strands_agent.build_mcp_clients()) == 2


def test_strands_auth_headers(monkeypatch):
    monkeypatch.delenv(strands_agent.BEARER_TOKEN_ENV, raising=False)
    assert strands_agent._auth_headers() is None

    monkeypatch.setenv(strands_agent.BEARER_TOKEN_ENV, "tok-abc")
    assert strands_agent._auth_headers() == {"Authorization": "Bearer tok-abc"}
