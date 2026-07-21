# ShowRunner 🎬

A movie-night agent: it figures out **what's on tonight**, **where you can watch it near you**, and **whether you have time to grab food first** — then plans the evening around it.

But the movie app is the vehicle, not the point. ShowRunner is a compact, runnable example of a **production-shaped agent architecture**: an orchestrator composing two framework specialists (Strands for shows, LangGraph for places), each owning one keyless MCP server, wired into Amazon Bedrock AgentCore for memory, identity, tool routing, authorization, and tracing.

Everything here is **free and keyless** — clone it and run it, no API signups, no billing.

![ShowRunner architecture](docs/architecture.png)

---

## Why this exists

Most "AI agent" demos stop at a single framework calling a single API. ShowRunner shows the two things that actually matter when you go past a demo:

- **MCP portability** — the same server code is consumed unchanged by two different frameworks: tvmaze through Strands, places through LangGraph. Which framework serves which server is interchangeable — the tools don't move. That's the whole promise of MCP, made concrete, and it's what makes the multi-agent split free.
- **Multi-agent composition** — an orchestrator routes each sub-question to the right specialist (agents-as-tools) and owns the user-facing concerns: entrypoint, memory, identity.
- **Production concerns** — memory across sessions, per-user identity, managed tool routing, default-deny tool authorization, and tracing, added one layer at a time instead of hand-rolled.

It was also built entirely through [Claude Code](https://www.claude.com/product/claude-code)'s own workflow — plan mode, a lean `CLAUDE.md`, verified commits, subagents, and hooks — so the repo doubles as a worked example of *how* to build something like this. See [`BUILD.md`](BUILD.md).

## Architecture at a glance

| Layer | What | Keyless? |
|-------|------|----------|
| **MCP servers** | `tvmaze` (what's on) · `places` (cinemas, restaurants, travel time via OpenStreetMap) | ✅ |
| **Agents** | `orchestrator` (central point) · `strands` (show specialist) · `langgraph` (places specialist) | — |
| **AgentCore** | runtime · memory · identity · gateway · authorization (Cedar) · observability | — |

Full spec: [`PROJECT.md`](PROJECT.md).

## Quickstart

Requires [uv](https://github.com/astral-sh/uv) (it manages Python and dependencies). AWS/Bedrock is only needed for the AgentCore deploy steps — the MCP servers and agents run locally without it.

```bash
git clone https://github.com/andaro74/showrunner.git
cd showrunner

uv sync                                   # rebuild the exact environment from the lockfile

uv run pytest                             # everything green?

uv run python mcp_servers/tvmaze/server.py   # run a server standalone
uv run python -m agents.orchestrator.agent   # serve the orchestrator locally (:8080)
```

Copy `.env.example` to `.env` if you're wiring up the AgentCore layer; the core demo needs no secrets.

**Calling the deployed agent** is a different path: the runtime accepts only a Cognito access
token, so you create a user in the pool, mint a token, and pass it explicitly — a bare
`agentcore invoke` is signed with IAM and gets rejected.

```bash
agentcore invoke --runtime ShowRunner --bearer-token "$TOKEN" --prompt "What should I watch tonight?"
```

The full recipe — creating the user, computing `SECRET_HASH`, minting the token — is in
[`BUILD.md`](BUILD.md#invoking-the-deployed-showrunner-agent).

## The two MCP servers

**`tvmaze`** — over `https://api.tvmaze.com` (no key, non-commercial use).
`search_shows` · `get_schedule` · `get_episodes` · `get_cast`

**`places`** — over OpenStreetMap (no key).
`geocode` (Nominatim) · `find_nearby` (Overpass — cinemas + restaurants) · `travel_time` (OSRM)

Both are framework-agnostic FastMCP servers — they contain zero Strands or LangChain code, which is exactly why each can be served by a different framework (and swapped) without touching the server.

## The three agents

- **`orchestrator`** ([`agents/orchestrator/`](agents/orchestrator/)) — the central agent point, in Strands. Its only tools are the two delegates `ask_show_expert` and `ask_places_expert` (agents-as-tools); it routes each sub-question, assembles the movie-night plan, and owns the `BedrockAgentCoreApp` entrypoint, Memory, and identity.
- **`strands`** — show specialist. Owns *only* the tvmaze server via Strands `MCPClient`.
- **`langgraph`** — places specialist. Owns *only* the places server via `langchain-mcp-adapters`.

The specialists **partition** the seven MCP tools — a test asserts they share none and cover all seven. The specialists never see each other or the user session; the orchestrator phrases each delegated question so it stands alone.

**Deployed shape: three runtimes, not five.** The orchestrator is the only *agent* runtime
([`serve_orchestrator.py`](serve_orchestrator.py), HTTP contract); the specialists ship inside
its bundle and run in-process — they're stateless, have exactly one caller, and a runtime
boundary would only add a network hop and a second auth surface. The two MCP servers run as
their own runtimes (MCP contract) behind the Gateway. When deployed, the specialists auto-wire
to the Gateway via the CLI-injected env var and filter the gateway's seven tools down to the
set each owns — so local stdio and deployed gateway modes keep the same partition. The caller's
JWT is forwarded per request all the way to the Gateway, so Cedar authorizes the *real* user
and memory scopes to their `sub` claim.

## Memory, and why Identity is what makes it safe

The orchestrator uses AgentCore Memory in two tiers ([`agents/orchestrator/memory_config.py`](agents/orchestrator/memory_config.py)):

- **Short-term** — the active session's turns, keyed by `(actor_id, session_id)` and replayed into the next turn.
- **Long-term** — durable records under two named namespaces, both scoped by actor:
  - `/users/{actor_id}/preferences` — genre preferences (user-preference strategy)
  - `/users/{actor_id}/facts` — what's already been suggested (semantic strategy)

  These paths mirror the `namespaceTemplates` that `agentcore add memory` provisions; if code
  and manifest drift apart, recall silently returns nothing.

**`{actor_id}` is the load-bearing part.** It comes from the `sub` claim of Identity's inbound
Cognito JWT. Without that, "who is this user?" would be a value the *caller* supplies — so anyone
could pass someone else's id and read their memory. The JWT is verified upstream by the gateway's
`CUSTOM_JWT` authorizer; the agent only decodes the already-verified claims. That's the
anti-impersonation story, and it's why the ordering rule is **Identity before Gateway**.

Note the asymmetry worth calling out: TVmaze and OpenStreetMap are *keyless* — the APIs need no
auth at all. Identity isn't here to reach the upstream data. It's here purely to keep one user's
remembered preferences from leaking into another user's movie night.

Memory is optional: with no `AGENTCORE_MEMORY_ID` set, the agent runs statelessly (that's how the
tests run — no AWS required).

## Authorization: what a verified user may actually do

Identity answers *who is calling*; Cedar answers *what they may do*. The Gateway runs a policy
engine that is **default-deny**, so each of the seven tools needs its own permit in
[`policies/tools/`](policies/tools/) — one Cedar statement per file, named for the deployed
gateway's real action (`TvmazeMcpTarget___search_shows` and friends). A tool added without a
permit is simply refused, which is the point: new capability doesn't become reachable by
accident. [`policies/argument_bounds.cedar`](policies/argument_bounds.cedar) goes further and
forbids calls whose *arguments* are out of bounds (a `find_nearby` radius over 5 km).

Because the caller's JWT rides along per request, these policies evaluate against the real
`OAuthUser` — not a service identity. The engine currently runs in `LOG_ONLY`: decisions are
traced, not enforced, which is the recommended way to roll policies out before flipping to
`ENFORCE`. Details and the verified-the-hard-way notes are in [`policies/README.md`](policies/README.md).

## Observability

All three runtimes are OTEL-instrumented: the CDK wraps each entrypoint in
`opentelemetry-instrument`, and `aws-opentelemetry-distro` routes the spans to CloudWatch's
GenAI Observability (per-runtime `spans` log streams, `aws.service.type: gen_ai_agent`,
transaction search for cross-runtime traces). One turn produces a connected trace: orchestrator
→ specialist delegation → Gateway → MCP runtime. Two gotchas doing this yourself: the flag and
the dependency are both required — the OTEL wrapper without the ADOT distro exports *nothing*,
silently — and the tooling's defaults disagree (missing `instrumentation` key means **on** to
the CDK, while `agentcore add agent` writes `false` for MCP runtimes). Details in
[`BUILD.md`](BUILD.md).

## How it was built (build in public)

The repo grows one verified, single-purpose commit at a time — so `git log` *is* the tutorial:

1. Repo skeleton — `PROJECT.md`, `CLAUDE.md`, `.gitignore`, `pyproject.toml`
2. TVmaze MCP server + tests
3. Places MCP server + tests (Overpass QL researched in a subagent)
4. Strands agent — first end-to-end "plan my night"
5. LangGraph variant — same servers, second framework, no rewrites
6. Skill + hooks — guardrails become automatic
7. AgentCore memory → identity → gateway → Cedar policies → observability (one commit each)
8. Specialist split + orchestrator — agents-as-tools; entrypoint/memory/identity move to the center
9. Deploy: three runtimes behind the Gateway, invoked with a real user's token

The step-by-step method, with the exact prompts used at each stage, is in [`BUILD.md`](BUILD.md).

## Project structure

```
showrunner/
├── PROJECT.md · CLAUDE.md · BUILD.md    # spec, agent memory, build guide
├── serve_orchestrator.py                # AgentCore entrypoint (root on purpose — see file)
├── mcp_servers/tvmaze · places          # keyless, framework-agnostic (+ serve_*.py entry files)
├── agents/orchestrator · strands · langgraph   # central point + two framework specialists
├── agentcore/                           # AgentCore manifest + CDK (flat resource model)
├── policies/                            # Cedar permits — one file per gateway tool
├── scripts/                             # Cognito pool + M2M client + gateway wiring (deploy prereqs)
├── evals/                               # LLM-as-judge harness — scaffolded, cases not written yet
├── tests/                               # a test per tool
├── docs/                                # architecture diagram
└── .claude/                             # skills, hooks (how it's built)
```

## Caveats (the honest bits)

- **OpenStreetMap public endpoints are rate-limited.** Fine for a demo — set a descriptive User-Agent and cache responses. Self-host Overpass/Nominatim/OSRM for anything real.
- **TVmaze is free for non-commercial use only.**
- **Identity's role here is memory-scoping, not API-key protection.** Because the APIs are keyless, the inbound Cognito JWT exists so long-term memory is tied to a real user (anti-impersonation via the `sub` claim), not to guard a secret.
- **LangChain doesn't speak MCP natively** — the LangGraph agent bridges via `langchain-mcp-adapters`.
- **Cedar runs in `LOG_ONLY`.** Policy decisions are traced, not enforced — so an unpermitted tool is still executed while being logged. Denial isn't real until the engine flips to `ENFORCE`.
- **The eval harness is scaffolding.** `evals/` has the structure and the planned cases as comments; validating the deployed stack is still the manual sequence in [`BUILD.md`](BUILD.md).
- **No CI yet.** `uv run pytest` and `ruff check .` run locally (and on every edit via the hooks in `.claude/`), but nothing enforces them on push.
- **Long turns outlive the sync invocation window (~100s).** A cold full movie-night plan can complete server-side after the client has already disconnected; streaming or async invocation is the fix.

## License

MIT — see [LICENSE](LICENSE).

---

*Built with Claude Code. Contributions and new MCP servers welcome — what would you add as the third?*
