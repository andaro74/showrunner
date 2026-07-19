# tvmaze MCP server

Keyless TV/show data over `https://api.tvmaze.com`.

## Tools

<!-- One line per tool. Add an entry here whenever a tool is added (CLAUDE.md hard rule #4). -->

- `search_shows(query)` — full-text show search, ordered by relevance.
- `get_schedule(country="US", date=None)` — episodes airing on a date (YYYY-MM-DD, defaults to today).
- `get_episodes(show)` — all episodes for the best-matching show (one `?embed=episodes` round-trip).
- `get_cast(show)` — cast for the best-matching show (one `?embed=cast` round-trip).
