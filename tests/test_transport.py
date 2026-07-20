"""Tests for the dual-transport wiring (Option B).

The servers speak stdio by default and streamable-http when deployed as their own
AgentCore Runtimes behind the Gateway. These tests pin the default (so the local
and test path can't silently change) and the per-server switch on both agents.
No network, no subprocesses.
"""

from pathlib import Path

import pytest

from agents.langgraph import agent as lg_agent
from agents.strands import agent as strands_agent
from mcp_servers import runtime


# --- server transport config ----------------------------------------------


def test_defaults_to_stdio(monkeypatch):
    monkeypatch.delenv(runtime.TRANSPORT_ENV, raising=False)
    assert runtime.transport() == "stdio"


def test_caller_can_default_to_http(monkeypatch):
    monkeypatch.delenv(runtime.TRANSPORT_ENV, raising=False)
    assert runtime.transport(runtime.DEPLOYED_TRANSPORT) == "streamable-http"


def test_env_overrides_caller_default(monkeypatch):
    monkeypatch.setenv(runtime.TRANSPORT_ENV, "stdio")
    assert runtime.transport(runtime.DEPLOYED_TRANSPORT) == "stdio"


def test_deploy_entrypoints_request_http():
    """The AgentCore entrypoints must not fall back to stdio: a deployed runtime
    has no stdio peer, so it would bind no port and the gateway target would fail
    with "Runtime initialization time exceeded"."""
    for name in ("serve_tvmaze.py", "serve_places.py"):
        source = (Path(__file__).resolve().parents[1] / "mcp_servers" / name).read_text()
        assert "default=DEPLOYED_TRANSPORT" in source, f"{name} must deploy over HTTP"


def test_transport_from_env(monkeypatch):
    monkeypatch.setenv(runtime.TRANSPORT_ENV, "streamable-http")
    assert runtime.transport() == "streamable-http"


def test_unknown_transport_is_rejected(monkeypatch):
    monkeypatch.setenv(runtime.TRANSPORT_ENV, "carrier-pigeon")
    with pytest.raises(ValueError, match="carrier-pigeon"):
        runtime.transport()


def test_port_matches_agentcore_mcp_contract(monkeypatch):
    """8000, NOT 8080. AgentCore's MCP protocol contract probes 0.0.0.0:8000/mcp;
    8080 is the HTTP protocol contract (BedrockAgentCoreApp). This test previously
    pinned 8080 — encoding the wrong contract — and the deployed runtimes timed out
    on every call while serving happily on a port nobody probes."""
    monkeypatch.delenv(runtime.PORT_ENV, raising=False)
    assert runtime.port() == 8000


def test_binds_all_interfaces_by_default(monkeypatch):
    """Must not depend on container detection.

    This previously asserted 127.0.0.1 unless `in_container()` was True, which is
    how a deployed runtime came to bind loopback: the AgentCore sandbox has no
    /.dockerenv, so the probe was False and nothing could reach the server. The
    test passed throughout, because it encoded the same wrong assumption as the code.
    """
    monkeypatch.delenv(runtime.HOST_ENV, raising=False)

    monkeypatch.setattr(runtime, "in_container", lambda: False)
    assert runtime.host() == "0.0.0.0"  # noqa: S104 - reachable is the point

    monkeypatch.setattr(runtime, "in_container", lambda: True)
    assert runtime.host() == "0.0.0.0"  # noqa: S104 - same either way


def test_host_env_overrides(monkeypatch):
    monkeypatch.setenv(runtime.HOST_ENV, "10.0.0.5")
    assert runtime.host() == "10.0.0.5"


# --- agent-side transport selection ---------------------------------------


def test_langgraph_uses_stdio_without_urls(monkeypatch):
    monkeypatch.delenv(lg_agent.PLACES_URL_ENV, raising=False)

    conn = lg_agent.connection_for(lg_agent.PLACES_SERVER, None)
    assert conn["transport"] == "stdio"
    assert conn["args"] == ["-m", lg_agent.PLACES_SERVER]


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


def test_strands_builds_one_client_for_its_server(monkeypatch):
    """The show specialist owns exactly one server — a second client would mean
    the specialist split leaked."""
    monkeypatch.delenv(strands_agent.TVMAZE_URL_ENV, raising=False)
    assert len(strands_agent.build_mcp_clients()) == 1


def test_strands_auth_headers(monkeypatch):
    monkeypatch.delenv(strands_agent.BEARER_TOKEN_ENV, raising=False)
    assert strands_agent._auth_headers() is None

    monkeypatch.setenv(strands_agent.BEARER_TOKEN_ENV, "tok-abc")
    assert strands_agent._auth_headers() == {"Authorization": "Bearer tok-abc"}


def test_http_transport_is_stateless(monkeypatch):
    """AgentCore Runtime spreads requests across instances, so MCP session state
    cannot be pinned to one process. Stateful FastMCP never reports ready and every
    call fails with "Runtime initialization time exceeded"."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("probe")
    assert mcp.settings.stateless_http is False  # FastMCP's default, which is wrong here

    monkeypatch.setattr(mcp, "run", lambda **kwargs: None)
    runtime.run_server(mcp, default=runtime.DEPLOYED_TRANSPORT)

    assert mcp.settings.stateless_http is True
    assert mcp.settings.json_response is True
    assert mcp.settings.host == "0.0.0.0"  # noqa: S104 - must be reachable
    assert mcp.settings.port == 8000  # MCP contract port, not HTTP's 8080


def test_http_transport_accepts_foreign_host_headers(monkeypatch):
    """The MCP SDK's DNS-rebinding protection 421s any Host it doesn't recognize.
    AgentCore forwards an amazonaws.com Host, so protection must be off when
    deployed: with it on, the platform reaches the server and every request is
    rejected as a misdirected request."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("probe")
    monkeypatch.setattr(mcp, "run", lambda **kwargs: None)
    runtime.run_server(mcp, default=runtime.DEPLOYED_TRANSPORT)

    security = mcp.settings.transport_security
    assert security is not None
    assert security.enable_dns_rebinding_protection is False
