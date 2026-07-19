"""Thin httpx client over Overpass — amenities within a radius of a lat/lon.

One responsibility: build the Overpass QL, POST it, parse `elements[]` into typed
`Place`s. Sends a descriptive User-Agent and caches responses (CLAUDE.md rule #4).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypedDict

import httpx

from mcp_servers.places.cache import ResponseCache, build_client, user_agent

BASE_URL = "https://overpass-api.de"

# Convenience aliases the server can expand into multiple OSM amenity tags.
AMENITY_ALIASES: dict[str, tuple[str, ...]] = {
    "food": ("restaurant", "fast_food"),
}

# opening_hours values that mean "not open" — filtered out when present.
_CLOSED_VALUES = {"closed", "off"}


class Place(TypedDict):
    name: str
    amenity: str
    lat: float | None
    lon: float | None
    opening_hours: str | None


def _coords(element: dict[str, Any]) -> tuple[float | None, float | None]:
    """Node coords are top-level; way/relation coords come from `out center`."""
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    center = element.get("center", {})
    return center.get("lat"), center.get("lon")


def _place(element: dict[str, Any]) -> Place:
    tags = element.get("tags", {})
    lat, lon = _coords(element)
    return Place(
        name=tags.get("name", ""),
        amenity=tags.get("amenity", ""),
        lat=lat,
        lon=lon,
        opening_hours=tags.get("opening_hours"),
    )


def _is_open(place: Place) -> bool:
    """Keep places without an opening_hours tag; drop only explicitly-closed ones.

    A full "open at showtime" check needs an opening_hours evaluator (deferred);
    this filters the unambiguous cases and always surfaces the raw value so the
    agent can reason about the rest.
    """
    hours = place["opening_hours"]
    if hours is None:
        return True
    return hours.strip().lower() not in _CLOSED_VALUES


class OverpassClient:
    """Synchronous client for the Overpass API (keyless)."""

    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self._client = build_client(base_url, client)
        self._cache = ResponseCache()

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _build_ql(lat: float, lon: float, amenities: Sequence[str], radius: int) -> str:
        clauses = "\n".join(
            f'  nwr["amenity"="{a}"](around:{radius},{lat},{lon});' for a in amenities
        )
        return f"[out:json][timeout:25];\n(\n{clauses}\n);\nout center tags;"

    def _post(self, ql: str) -> Any:
        response = self._client.post(
            "/api/interpreter",
            data={"data": ql},
            headers={"User-Agent": user_agent()},
        )
        response.raise_for_status()
        return response.json()

    def find_nearby(
        self, lat: float, lon: float, amenities: Sequence[str], radius: int = 2000
    ) -> list[Place]:
        """Amenities within `radius` metres of (lat, lon), minus explicitly-closed ones."""
        ql = self._build_ql(lat, lon, amenities, radius)
        data = self._cache.get_or_set(ql, lambda: self._post(ql))
        places = [_place(element) for element in data.get("elements", [])]
        return [place for place in places if _is_open(place)]
