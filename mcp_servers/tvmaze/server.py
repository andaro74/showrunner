"""FastMCP server for TVmaze.

Tools: search_shows, get_schedule, get_episodes, get_cast.
Thin registration layer — all HTTP/parsing lives in `tvmaze_client.py`.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_servers.runtime import run_server
from mcp_servers.tvmaze.tvmaze_client import (
    CastMember,
    Episode,
    ScheduledEpisode,
    ShowSummary,
    TVmazeClient,
)

mcp = FastMCP("tvmaze")
_client = TVmazeClient()


@mcp.tool()
def search_shows(query: str) -> list[ShowSummary]:
    """Search TV shows by title. Returns matches ordered by relevance."""
    return _client.search_shows(query)


@mcp.tool()
def get_schedule(country: str = "US", date: str | None = None) -> list[ScheduledEpisode]:
    """List episodes airing on a given date (YYYY-MM-DD, defaults to today) in a country."""
    return _client.get_schedule(country=country, date=date)


@mcp.tool()
def get_episodes(show: str) -> list[Episode]:
    """List all episodes for the best-matching show (single embedded round-trip)."""
    return _client.get_episodes(show)


@mcp.tool()
def get_cast(show: str) -> list[CastMember]:
    """List the cast for the best-matching show (single embedded round-trip)."""
    return _client.get_cast(show)


if __name__ == "__main__":
    run_server(mcp)
