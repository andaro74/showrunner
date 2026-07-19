"""Scaffold sanity: every package imports cleanly.

Replaced/augmented by real module tests in build-order steps 2-7. For now it
keeps the suite green (exit 0) and proves the tree is import-clean.
"""

import importlib

import pytest

PACKAGES = [
    "mcp_servers",
    "mcp_servers.tvmaze",
    "mcp_servers.tvmaze.tvmaze_client",
    "mcp_servers.tvmaze.server",
    "mcp_servers.places",
    "mcp_servers.places.nominatim_client",
    "mcp_servers.places.overpass_client",
    "mcp_servers.places.osrm_client",
    "mcp_servers.places.cache",
    "mcp_servers.places.server",
    "agents",
    "agents.strands",
    "agents.langgraph",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name):
    assert importlib.import_module(name) is not None
