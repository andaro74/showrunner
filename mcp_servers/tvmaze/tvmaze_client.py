"""Thin httpx client over https://api.tvmaze.com (no key).

One responsibility: HTTP + parsing upstream JSON into typed, slim results.
No MCP or agent logic. Uses `?embed=` to fetch a show plus its episodes/cast
in a single round-trip.
"""

from __future__ import annotations

from typing import Any, TypedDict

import httpx

BASE_URL = "https://api.tvmaze.com"
_TIMEOUT = httpx.Timeout(10.0)


class ShowSummary(TypedDict):
    id: int
    name: str
    premiered: str | None
    genres: list[str]
    network: str | None
    summary: str | None


class Episode(TypedDict):
    season: int | None
    number: int | None
    name: str
    airdate: str | None
    runtime: int | None
    summary: str | None


class ScheduledEpisode(TypedDict):
    show: str
    network: str | None
    episode: str
    season: int | None
    number: int | None
    airtime: str | None


class CastMember(TypedDict):
    person: str
    character: str


def _network_name(show: dict[str, Any]) -> str | None:
    """TVmaze shows air on a `network` or a `webChannel`; pick whichever is set."""
    channel = show.get("network") or show.get("webChannel")
    return channel.get("name") if channel else None


def _show_summary(show: dict[str, Any]) -> ShowSummary:
    return ShowSummary(
        id=show["id"],
        name=show["name"],
        premiered=show.get("premiered"),
        genres=show.get("genres") or [],
        network=_network_name(show),
        summary=show.get("summary"),
    )


def _episode(ep: dict[str, Any]) -> Episode:
    return Episode(
        season=ep.get("season"),
        number=ep.get("number"),
        name=ep.get("name", ""),
        airdate=ep.get("airdate"),
        runtime=ep.get("runtime"),
        summary=ep.get("summary"),
    )


def _scheduled_episode(ep: dict[str, Any]) -> ScheduledEpisode:
    # /schedule nests the show at TOP LEVEL (`ep["show"]`); the `_embedded.show`
    # shape belongs to other endpoints. Reading only `_embedded` here shipped a
    # schedule where every entry had an empty show name — invisible to mocked
    # tests, caught when a live agent kept (correctly) refusing to name shows.
    show = ep.get("show") or ep.get("_embedded", {}).get("show") or {}
    return ScheduledEpisode(
        show=show.get("name", ""),
        network=_network_name(show),
        episode=ep.get("name", ""),
        season=ep.get("season"),
        number=ep.get("number"),
        airtime=ep.get("airtime"),
    )


def _cast_member(member: dict[str, Any]) -> CastMember:
    return CastMember(
        person=member.get("person", {}).get("name", ""),
        character=member.get("character", {}).get("name", ""),
    )


class TVmazeClient:
    """Synchronous client for the TVmaze REST API (keyless)."""

    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=_TIMEOUT)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TVmazeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def _singlesearch(self, query: str, embed: str | None = None) -> dict[str, Any] | None:
        """Best-match show for `query`, optionally embedding episodes/cast.

        Returns None when TVmaze has no match (it answers 404 with an empty body).
        """
        params: dict[str, Any] = {"q": query}
        if embed:
            params["embed"] = embed
        try:
            return self._get("/singlesearch/shows", params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == httpx.codes.NOT_FOUND:
                return None
            raise

    def search_shows(self, query: str) -> list[ShowSummary]:
        """Full-text show search; ordered by relevance."""
        results = self._get("/search/shows", {"q": query})
        return [_show_summary(item["show"]) for item in results]

    def get_schedule(self, country: str = "US", date: str | None = None) -> list[ScheduledEpisode]:
        """Episodes airing on `date` (YYYY-MM-DD; defaults to today) in `country`."""
        params: dict[str, Any] = {"country": country}
        if date:
            params["date"] = date
        return [_scheduled_episode(ep) for ep in self._get("/schedule", params)]

    def get_episodes(self, show: str) -> list[Episode]:
        """Episode list for the best match of `show`.

        One round-trip: resolves the show and embeds its episodes via `?embed=episodes`.
        """
        data = self._singlesearch(show, embed="episodes")
        if not data:
            return []
        return [_episode(ep) for ep in data.get("_embedded", {}).get("episodes", [])]

    def get_cast(self, show: str) -> list[CastMember]:
        """Cast for the best match of `show`.

        One round-trip: resolves the show and embeds its cast via `?embed=cast`.
        """
        data = self._singlesearch(show, embed="cast")
        if not data:
            return []
        return [_cast_member(m) for m in data.get("_embedded", {}).get("cast", [])]
