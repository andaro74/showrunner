---
name: add-mcp-tool
description: Add a new tool to an existing FastMCP server in this repo — define the client method, register the tool, add a pytest, and update the server's SKILL.md.
---

# add-mcp-tool

How to add a tool to an existing FastMCP server under `mcp_servers/` (tvmaze or
places), following the patterns already in the repo. Keep the split: `*_client.py`
does HTTP + typed parsing, `server.py` just registers thin tool wrappers.

Run `uv run pytest` after each step — a tool isn't done until its test is green
(CLAUDE.md hard rule #4).

## Steps

### 1. Define the client method (`*_client.py`)

Add a method to the relevant client (e.g. `mcp_servers/tvmaze/tvmaze_client.py`,
or a `*_client.py` under `mcp_servers/places/`). It owns HTTP + parsing only — no
MCP, no agent logic.

- Return a **typed, JSON-serializable** result — a `TypedDict` (see `ShowSummary`,
  `Place`, `Route`) or a list of them. Never return the raw upstream payload;
  project the fields you need in a small `_helper(raw) -> TypedDict`.
- For OSM clients, go through `cache.py`: build the client with `build_client()`,
  send `user_agent()` on every request, and wrap the call in
  `self._cache.get_or_set(key, lambda: self._get(...))` so repeats don't re-hit
  the upstream (rate-limited) service.
- Handle "nothing found" explicitly: return `[]` or `None` rather than raising
  (see `TVmazeClient._singlesearch` swallowing a 404, `OverpassClient.find_nearby`
  reading `data.get("elements", [])`).

### 2. Register the tool (`server.py`)

Add a thin `@mcp.tool()` function that calls the client method. Keep server logic
minimal (light composition like geocoding a name is fine — see
`places/server.py:travel_time`).

```python
@mcp.tool()
def my_tool(arg: str) -> list[MyResult]:
    """One-line description the model sees — say what it returns."""
    return _client.my_method(arg)
```

- The docstring is the tool description shown to the agent — make it concrete.
- Annotate params and the return type; FastMCP builds the schema from them.
- Remember Python keywords can't be parameter names (`from` → use `origin`).

### 3. Add a pytest (`tests/test_<server>_server.py`)

Drive the tool through the server, not just the client:

```python
result = await mcp.call_tool("my_tool", {"arg": "value"})
payload = _payload(result)   # unwrap (content, structured); lists arrive as {"result": [...]}
```

- `call_tool` returns `(content_blocks, structured_content)`; list results are
  wrapped as `{"result": [...]}`. Copy the `_payload` helper from an existing
  server test.
- Prefer **mocked HTTP** via `httpx.MockTransport` + client injection (see
  `test_places_server.py`) so tests are offline and deterministic. Inject with
  `OverpassClient(client=mock_client)` and `monkeypatch.setattr(server, "_overpass", ...)`.
- Mark live-network tests `@pytest.mark.network`; mark subprocess/agent tests
  `@pytest.mark.integration` (both registered in `pyproject.toml`).
- Assert the User-Agent header and a cache hit where relevant.

### 4. Update the server's SKILL.md

Add exactly one line under `## Tools` in that server's `SKILL.md`
(`mcp_servers/<server>/SKILL.md`), matching the existing `name(args) — summary`
style. This keeps the tool list discoverable (CLAUDE.md hard rule #4).

### 5. Add a Cedar permit (`policies/showrunner_tools.cedar`)

Only if the tool is exposed through the AgentCore Gateway. Cedar is **default-deny**
and this repo permits each tool individually, so a tool with no permit is refused —
it will work fine over stdio locally and then silently fail through the gateway.

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"<Target>___my_tool",   // triple underscore
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
);
```

Add an argument bound in `policies/argument_bounds.cedar` if the tool takes anything
worth capping (`forbid` overrides `permit`). See `policies/README.md`.

## Gotchas

- **Forgetting the User-Agent header.** Every Nominatim/Overpass/OSRM request must
  send `user_agent()` (CLAUDE.md hard rule #5) — public instances block
  unidentified requests. Set it per request, not just on the client, so injected
  test clients carry it too.
- **Not handling empty Overpass results.** Overpass returns `{"elements": []}` for
  no matches — read `data.get("elements", [])`, never index `[0]`. Ways/relations
  have coords under `element.center`, nodes at top-level `element.lat/lon`; a
  missing tag (e.g. `name`, `opening_hours`) means `.get(...)`, not `[...]`.
- **Leaking framework imports into `mcp_servers/`.** No `strands` or `langchain*`
  imports anywhere under `mcp_servers/` (CLAUDE.md hard rule #1) — both agent
  frameworks must consume the servers unchanged. Framework code lives only in
  `agents/`.
