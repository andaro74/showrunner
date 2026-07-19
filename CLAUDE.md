# CLAUDE.md

ShowRunner — a movie-night agent: *what's on tonight, where can I watch it near me, and do I have time to grab food first.* Two keyless MCP servers, two agent frameworks that share them, on Amazon Bedrock AgentCore. Full spec: see `PROJECT.md`.

## Commands

```bash
uv run <script>               # run an agent / script in the project env
uv run pytest                 # run all tests
uv run pytest tests/test_x.py # run one test file
ruff check .                  # lint
agentcore dev                 # run an agent locally with a test endpoint
agentcore deploy              # deploy to AgentCore runtime
```

Run tests after every change to `mcp_servers/` or `agents/`. A change isn't done until its test is green.

## Architecture — three layers (see PROJECT.md for detail)

**Layer 1 · MCP servers (keyless, framework-agnostic).** FastMCP servers under `mcp_servers/`.
- `tvmaze` — TV/show data over `api.tvmaze.com`. Tools: `search_shows`, `get_schedule`, `get_episodes`, `get_cast`.
- `places` — OpenStreetMap data. Tools: `geocode` (Nominatim), `find_nearby` (Overpass), `travel_time` (OSRM).

**Layer 2 · Agents (consume the SAME servers).** Under `agents/`.
- `strands` — primary agent; Strands `MCPClient` wrapped in `BedrockAgentCoreApp`.
- `langgraph` — variant; loads the identical servers via `langchain-mcp-adapters`. Proves MCP portability: one server, two frameworks, no per-framework rewrites.

**Layer 3 · Amazon Bedrock AgentCore (production concerns).** Runtime, Memory (short-term session + long-term genre prefs), Identity (Cognito JWT), Gateway (managed tool routing), Evaluation, Observability. Added incrementally under `gateway/`, `identity/`, `evals/`.

## Hard rules

1. **MCP servers stay framework-agnostic.** No Strands or LangChain imports inside `mcp_servers/` — both frameworks must consume them unchanged.
2. **Identity before Gateway.** If enabling Gateway, configure Identity first; Gateway relies on Identity's OAuth provider.
3. **Every new tool ships with** a pytest test and a one-line entry in the relevant SKILL.md.
4. **Set a descriptive User-Agent** on every Nominatim/Overpass/OSRM request, and cache responses — public instances are rate-limited.

## Conventions

- Keep each MCP client (`*_client.py`) thin: one responsibility, no agent logic.
- Tools return typed, JSON-serializable results — no raw upstream payloads.
- Use plan mode before non-trivial edits; build smallest-first, one module per commit.
- For noisy research (e.g. Overpass QL), use a subagent so it doesn't flood context.
