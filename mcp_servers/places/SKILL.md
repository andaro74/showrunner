# places MCP server

Keyless location data over OpenStreetMap (Nominatim/Overpass/OSRM).

## Tools

<!-- One line per tool. Add an entry here whenever a tool is added (CLAUDE.md hard rule #3). -->

- `geocode(query)` — resolve a place name/address to coordinates (Nominatim).
- `find_nearby(lat, lon, amenity="cinema", radius=2000)` — amenities within radius metres (Overpass); `amenity` accepts the alias `"food"` (restaurant + fast_food); explicitly-closed places are filtered out.
- `travel_time(origin, destination)` — driving time/distance between two points, each a `"lat,lon"` string or a place name (OSRM; geocodes names via Nominatim).
