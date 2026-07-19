"""Thin httpx client over OSRM — driving route → travel time (no key).

One responsibility: HTTP + parsing. Callers pass (lat, lon) pairs; this client
encapsulates OSRM's lon,lat coordinate ordering so that footgun stays in one place.
"""

from __future__ import annotations

from typing import Any, TypedDict

import httpx

from mcp_servers.places.cache import ResponseCache, build_client, user_agent

BASE_URL = "https://router.project-osrm.org"

Coord = tuple[float, float]  # (lat, lon)


class Route(TypedDict):
    duration_seconds: float
    duration_minutes: float
    distance_meters: float


class OSRMClient:
    """Synchronous client for the OSRM routing API (keyless demo server)."""

    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self._client = build_client(base_url, client)
        self._cache = ResponseCache()

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = self._client.get(path, params=params, headers={"User-Agent": user_agent()})
        response.raise_for_status()
        return response.json()

    def travel_time(self, origin: Coord, destination: Coord) -> Route | None:
        """Driving time/distance between two (lat, lon) points, or None if no route."""
        (o_lat, o_lon), (d_lat, d_lon) = origin, destination
        # OSRM wants lon,lat — swap here so callers never have to think about it.
        path = f"/route/v1/driving/{o_lon},{o_lat};{d_lon},{d_lat}"
        data = self._cache.get_or_set(path, lambda: self._get(path, {"overview": "false"}))
        routes = data.get("routes") or []
        if not routes:
            return None
        route = routes[0]
        duration = float(route["duration"])
        return Route(
            duration_seconds=duration,
            duration_minutes=round(duration / 60, 1),
            distance_meters=float(route["distance"]),
        )
