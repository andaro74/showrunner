"""Smoke-test a locally running places MCP server over streamable-http.

Start the server first (separate terminal):

    uv run mcp_servers/serve_places.py           # binds 0.0.0.0:8000, path /mcp

Then run this:

    uv run scripts/smoke_places.py                       # default location on :8000
    uv run scripts/smoke_places.py "Union Square, San Francisco"   # custom location
    uv run scripts/smoke_places.py http://127.0.0.1:9000/mcp "Soho, London"   # custom URL + location

Args are order-independent: anything starting with http(s):// is the URL, everything else is the
location. It exercises all three tools end to end: geocode the location, find_nearby cinemas at
those coordinates, then travel_time from the location to a second point.

OpenStreetMap's public endpoints are rate-limited (Nominatim ~1 req/sec), so a run makes a few
seconds of upstream calls — that is expected, not a hang.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_URL = "http://localhost:8000/mcp"
DEFAULT_LOCATION = "Times Square, New York"
DEFAULT_DESTINATION = "Central Park, New York"


async def call(session: ClientSession, name: str, args: dict) -> str:
    """Call a tool and return the first text block (JSON), or "" if none."""
    result = await session.call_tool(name, args)
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            return text
    return ""


async def main(url: str, location: str) -> None:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])

            geo = await call(session, "geocode", {"query": location})
            print(f"\ngeocode({location!r}) ->\n{geo[:400]}")
            point = json.loads(geo) if geo else None
            if not point:
                sys.exit(f"\nno coordinates for {location!r} — try another location")

            lat, lon = point["lat"], point["lon"]
            nearby = await call(
                session, "find_nearby", {"lat": lat, "lon": lon, "amenity": "cinema"}
            )
            print(f"\nfind_nearby(cinema near {lat},{lon}) ->\n{nearby[:500]}")

            route = await call(
                session, "travel_time", {"origin": location, "destination": DEFAULT_DESTINATION}
            )
            print(f"\ntravel_time({location!r} -> {DEFAULT_DESTINATION!r}) ->\n{route[:400]}")


if __name__ == "__main__":
    url = DEFAULT_URL
    location = DEFAULT_LOCATION
    for arg in sys.argv[1:]:
        if arg.startswith(("http://", "https://")):
            url = arg
        else:
            location = arg
    try:
        asyncio.run(main(url, location))
    except* httpx.ConnectError:
        sys.exit(
            f"error: could not connect to {url}\n"
            "  is the server running on that host/port?\n"
            "  start it with:  uv run mcp_servers/serve_places.py   (binds :8000)\n"
            "  for another port:  MCP_PORT=9000 uv run mcp_servers/serve_places.py"
        )
