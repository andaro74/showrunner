"""Thin httpx client over Nominatim — geocode / reverse-geocode (no key).

One responsibility: HTTP + parsing. Sends a descriptive User-Agent on every
request and caches responses (Nominatim's usage policy is max 1 req/sec).
"""

from __future__ import annotations

from typing import Any, TypedDict

import httpx

from mcp_servers.places.cache import ResponseCache, build_client, user_agent

BASE_URL = "https://nominatim.openstreetmap.org"


class GeoPoint(TypedDict):
    lat: float
    lon: float
    display_name: str


class Address(GeoPoint):
    address: dict[str, Any]


class NominatimClient:
    """Synchronous client for the Nominatim geocoding API (keyless)."""

    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self._client = build_client(base_url, client)
        self._cache = ResponseCache()

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = self._client.get(path, params=params, headers={"User-Agent": user_agent()})
        response.raise_for_status()
        return response.json()

    def geocode(self, query: str) -> GeoPoint | None:
        """Free-text search → best-match coordinates, or None if nothing matches."""
        results = self._cache.get_or_set(
            f"search:{query}",
            lambda: self._get("/search", {"q": query, "format": "jsonv2", "limit": 1}),
        )
        if not results:
            return None
        top = results[0]
        return GeoPoint(
            lat=float(top["lat"]),
            lon=float(top["lon"]),
            display_name=top.get("display_name", ""),
        )

    def reverse(self, lat: float, lon: float) -> Address | None:
        """Coordinates → nearest address, or None if nothing matches."""
        data = self._cache.get_or_set(
            f"reverse:{lat},{lon}",
            lambda: self._get("/reverse", {"lat": lat, "lon": lon, "format": "jsonv2"}),
        )
        if not data or "lat" not in data:
            return None
        return Address(
            lat=float(data["lat"]),
            lon=float(data["lon"]),
            display_name=data.get("display_name", ""),
            address=data.get("address", {}),
        )
