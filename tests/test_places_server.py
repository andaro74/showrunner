"""Tests for places/server.py — build-order step 5.

Drives the FastMCP server's find_nearby against a mocked Overpass response:
verifies tool registration, node/way coordinate parsing, the opening_hours
filter, the User-Agent header, and response caching. No network.
"""

from urllib.parse import parse_qs

import httpx
import pytest

from mcp_servers.places import server
from mcp_servers.places.cache import user_agent
from mcp_servers.places.overpass_client import OverpassClient

EXPECTED_TOOLS = {"geocode", "find_nearby", "travel_time"}

# Overpass returns a node (top-level lat/lon), a way (coords via `center`), and
# an explicitly-closed place that find_nearby must drop.
CANNED_OVERPASS = {
    "elements": [
        {
            "type": "node",
            "lat": 40.7128,
            "lon": -74.0060,
            "tags": {"amenity": "cinema", "name": "Open Cinema", "opening_hours": "24/7"},
        },
        {
            "type": "way",
            "center": {"lat": 40.7130, "lon": -74.0070},
            "tags": {"amenity": "cinema", "name": "Center Cinema"},
        },
        {
            "type": "node",
            "lat": 40.7100,
            "lon": -74.0050,
            "tags": {"amenity": "cinema", "name": "Closed Cinema", "opening_hours": "closed"},
        },
    ]
}


@pytest.fixture
def mock_overpass(monkeypatch):
    """Swap the server's Overpass client for one backed by a MockTransport."""
    calls = {"count": 0, "user_agent": None, "ql": None}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        calls["user_agent"] = request.headers.get("user-agent")
        # Overpass QL is form-encoded as `data=<QL>`; decode it back.
        calls["ql"] = parse_qs(request.content.decode())["data"][0]
        return httpx.Response(200, json=CANNED_OVERPASS)

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://overpass-api.de"
    )
    monkeypatch.setattr(server, "_overpass", OverpassClient(client=client))
    return calls


def _payload(result):
    structured = result[1] if isinstance(result, tuple) else result
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


async def test_registers_three_tools():
    tools = await server.mcp.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_find_nearby_parses_and_filters(mock_overpass):
    result = await server.mcp.call_tool(
        "find_nearby", {"lat": 40.7128, "lon": -74.0060, "amenity": "cinema", "radius": 1500}
    )
    places = _payload(result)

    names = [p["name"] for p in places]
    assert names == ["Open Cinema", "Center Cinema"]  # "Closed Cinema" filtered out

    # node coords are top-level; way coords come from `center`.
    assert (places[0]["lat"], places[0]["lon"]) == (40.7128, -74.0060)
    assert (places[1]["lat"], places[1]["lon"]) == (40.7130, -74.0070)

    # QL was built with the amenity + around clause.
    assert 'nwr["amenity"="cinema"](around:1500,40.7128,-74.006)' in mock_overpass["ql"]
    # User-Agent sent per CLAUDE.md hard rule #5.
    assert mock_overpass["user_agent"] == user_agent()


async def test_find_nearby_caches(mock_overpass):
    args = {"lat": 40.7128, "lon": -74.0060, "amenity": "cinema", "radius": 1500}
    await server.mcp.call_tool("find_nearby", args)
    await server.mcp.call_tool("find_nearby", args)
    assert mock_overpass["count"] == 1  # second identical call served from cache
