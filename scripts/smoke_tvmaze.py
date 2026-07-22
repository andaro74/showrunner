"""Smoke-test a locally running tvmaze MCP server over streamable-http.

Start the server first (separate terminal):

    uv run mcp_servers/serve_tvmaze.py            # binds 0.0.0.0:8000, path /mcp

Then run this:

    uv run scripts/smoke_tvmaze.py                        # default query on :8000
    uv run scripts/smoke_tvmaze.py "the wire"             # custom query on :8000
    uv run scripts/smoke_tvmaze.py http://127.0.0.1:9000/mcp "the wire"   # custom URL + query

Args are order-independent: anything starting with http(s):// is the URL, everything else is the
query. It lists the tools, then calls search_shows(query) and prints the first hit.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_URL = "http://localhost:8000/mcp"


async def main(url: str, query: str) -> None:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])

            result = await session.call_tool("search_shows", {"query": query})
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    print(f"\nsearch_shows({query!r}) ->\n{text[:600]}")
                    break


if __name__ == "__main__":
    url = DEFAULT_URL
    query = "breaking bad"
    for arg in sys.argv[1:]:
        if arg.startswith(("http://", "https://")):
            url = arg
        else:
            query = arg
    try:
        asyncio.run(main(url, query))
    except* httpx.ConnectError:
        sys.exit(
            f"error: could not connect to {url}\n"
            "  is the server running on that host/port?\n"
            "  start it with:  uv run mcp_servers/serve_tvmaze.py   (binds :8000)\n"
            "  for another port:  MCP_PORT=9000 uv run mcp_servers/serve_tvmaze.py"
        )
