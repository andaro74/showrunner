"""FastMCP server for Places (OpenStreetMap).

Tools: geocode (Nominatim), find_nearby (Overpass), travel_time (OSRM).
Thin registration + light composition (geocoding a place name for routing);
all HTTP/parsing lives in the `*_client.py` modules.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_servers.places.nominatim_client import GeoPoint, NominatimClient
from mcp_servers.places.osrm_client import Coord, OSRMClient, Route
from mcp_servers.places.overpass_client import AMENITY_ALIASES, OverpassClient, Place

mcp = FastMCP("places")

_nominatim = NominatimClient()
_overpass = OverpassClient()
_osrm = OSRMClient()


def _resolve(value: str) -> Coord | None:
    """Accept a "lat,lon" string or free-text place name → (lat, lon)."""
    parts = value.split(",")
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    point = _nominatim.geocode(value)
    return (point["lat"], point["lon"]) if point else None


@mcp.tool()
def geocode(query: str) -> GeoPoint | None:
    """Resolve a place name or address to coordinates."""
    return _nominatim.geocode(query)


@mcp.tool()
def find_nearby(lat: float, lon: float, amenity: str = "cinema", radius: int = 2000) -> list[Place]:
    """Find amenities within `radius` metres of (lat, lon).

    `amenity` is an OSM value (e.g. "cinema", "restaurant", "fast_food") or the
    alias "food" (restaurant + fast_food). Places explicitly closed via their
    opening_hours tag are excluded.
    """
    amenities = AMENITY_ALIASES.get(amenity, (amenity,))
    return _overpass.find_nearby(lat, lon, amenities, radius)


@mcp.tool()
def travel_time(origin: str, destination: str) -> Route | None:
    """Driving time between two points, each a "lat,lon" string or a place name.

    (`from` is a Python keyword, so the parameters are named origin/destination.)
    """
    start = _resolve(origin)
    end = _resolve(destination)
    if start is None or end is None:
        return None
    return _osrm.travel_time(start, end)


if __name__ == "__main__":
    mcp.run()
