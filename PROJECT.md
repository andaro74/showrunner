# ShowRunner

A movie-night agent: it answers *what's on tonight, where can I watch it near me, and do I have time to grab food first.*

The project is deliberately two things at once:
1. A useful agent architecture (two frameworks sharing two keyless MCP servers, on AWS AgentCore).
2. A worked example of building with Claude Code (plan mode, lean CLAUDE.md, verified commits, subagents, skills, hooks).

## Architecture — three layers

**Layer 1 · MCP servers (keyless, framework-agnostic)**
- `tvmaze` — TV/show data over `https://api.tvmaze.com` (no key). Tools: `search_shows`, `get_schedule`, `get_episodes`, `get_cast`.
- `places` — location data over OpenStreetMap (no key). Tools: `geocode` (Nominatim), `find_nearby` (Overpass — cinemas + restaurants), `travel_time` (OSRM).

**Layer 2 · Agents (consume the SAME servers)**
- `strands` — primary agent, connects via Strands `MCPClient`, wrapped in `BedrockAgentCoreApp`.
- `langgraph` — variant, loads the identical servers via `langchain-mcp-adapters`. Exists to prove MCP portability: one server, two frameworks, no per-framework tool rewrites.

**Layer 3 · Amazon Bedrock AgentCore (production concerns)**
- **Runtime** — serverless host for each agent entrypoint.
- **Memory** — short-term (session) + long-term (genre preferences across sessions).
- **Identity** — inbound Cognito JWT; scopes memory per real user (anti-impersonation via the `sub` claim).
- **Gateway** — managed tool routing/auth; production alternative to self-hosting the MCP servers.
- **Evaluation** — LLM-as-a-judge; offline in CI + optional online on traces.
- **Observability** — built-in OTEL traces → CloudWatch.

## The user flow (one turn)

Identity validates the caller → Memory loads their history → the agent reasons (Claude on Bedrock) → tool calls resolve show + nearby cinema + food + travel time → new facts persist to Memory → the turn is traced.

## Build order (smallest-first, each independently testable)

1. Scaffold + CLAUDE.md
2. `tvmaze` MCP server + tests
3. `places` MCP server + tests (research Overpass QL in a subagent first)
4. `strands` agent
5. `langgraph` variant
6. `add-mcp-tool` skill + hooks (ruff/pytest, secret-blocking)
7. AgentCore memory → identity → evaluator (one commit each)
8. CI + docs, then push

## Non-goals / constraints

- **No API keys.** If a capability needs one, it doesn't belong in the core demo.
- MCP servers never import Strands or LangChain — they stay framework-agnostic.
- OpenStreetMap public endpoints are rate-limited: set a descriptive User-Agent and cache responses. Fine for a demo; self-host for production.
- TVmaze is free for non-commercial use only.

## Reference

- Free APIs: TVmaze (`api.tvmaze.com`), Overpass, Nominatim, OSRM.
- Adapters: `langchain-mcp-adapters` (bridges MCP into LangGraph, which has no native MCP support).
- Ordering rule: if Gateway is enabled, configure Identity first — Gateway relies on Identity's OAuth provider.
