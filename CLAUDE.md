# CLAUDE.md

ShowRunner — a movie-night agent: *what's on tonight, where can I watch it near me, and do I have time to grab food first.* Two keyless MCP servers, two agent frameworks that share them, on Amazon Bedrock AgentCore. Full spec: see `PROJECT.md`.

## Commands

```bash
uv run <script>               # run an agent / script in the project env
uv run pytest                 # run all tests
uv run pytest tests/test_x.py # run one test file
ruff check .                  # lint
uv run scripts/config.py render   # generate agentcore/agentcore.json (do this first)
uv run scripts/config.py scrub    # capture CLI edits back into the template (before commit)
agentcore dev                 # run an agent locally with a test endpoint
agentcore deploy              # deploy to AgentCore runtime (CDK)
```

Run tests after every change to `mcp_servers/` or `agents/`. A change isn't done until its test is green.

**`agentcore/agentcore.json` is generated — render before any `agentcore` command.** The tracked
source of truth is `agentcore/agentcore.template.json` (placeholders) plus `policies/**/*.cedar`;
the real account / pool / client / gateway / runtime ids live only in gitignored
`agentcore/local-config.json`. A fresh clone has no manifest until `render`, so every CLI command
fails with *"No agentcore project found."* until you run it.

**Cognito client secrets cannot be rotated in place** — there is no API. A replacement app client
gets a new id, which `allowedClients` references, so rotation is a config change plus a deploy:
`scripts/rotate_cognito_secrets.sh` (dry run by default, `--apply` to execute). It creates the
replacement, updates `.env` + `local-config.json`, refreshes the `GatewayToRuntimes` credential,
and re-renders. The old client stays live until a second explicit `--retire --apply`, so a failed
deploy never locks you out.

**The CLI writes to the generated file, so `scrub` before you commit.** `agentcore add …` mutates
`agentcore/agentcore.json`; without a `scrub` those edits never reach the template and are lost on
the next `render`. `scrub` refuses to write a template that would not render back byte-for-byte.
`render` is the only thing that may write real ids to disk, and everything it writes is gitignored.

`agentcore` is a separate Node CLI (not the `bedrock-agentcore` Python SDK). Local dev and the
tests don't need it — `BedrockAgentCoreApp` runs standalone, and the MCP servers run over stdio.

Every `agentcore` command except `create` needs a project manifest (`agentcore/agentcore.json`);
without one you get *"No agentcore project found."* That manifest lives at `agentcore/` (generated
— see above), so run `agentcore add …` / `validate` / `deploy` from the repo root.

Don't re-run `agentcore create` here — it scaffolds a **new child directory** with its own
`git init` rather than initializing in place (that's why `agentcore/` was generated elsewhere
and moved in). `import` doesn't bootstrap a repo either; it adopts resources already in AWS.
The CLI is happy with `agentcore/` in a directory it didn't scaffold.

`agentcore/cdk/node_modules/` is gitignored by the generated `cdk/.gitignore`; if it goes
missing, `npm install` inside `agentcore/cdk/`.

Add primitives with `agentcore add <memory|evaluator|online-eval|gateway|…>` — there is no
`add identity`; inbound Cognito JWT is the gateway's `CUSTOM_JWT` authorizer.

**Namespaces must match:** `add memory` writes default `namespaceTemplates` into the manifest.
Keep `agents/orchestrator/memory_config.py` pointed at those exact paths or recall silently
returns nothing.

**Observability needs both halves:** `instrumentation: {enableOtel: true}` per runtime (set it
explicitly — the CDK defaults a *missing* key to true, but `add agent` writes false on MCP
runtimes) AND `aws-opentelemetry-distro` in deps. The OTEL wrapper without the ADOT distro runs
clean and exports nothing — an empty `spans` log stream is the tell.

**Packaging ignores `.gitignore`:** every runtime is `codeLocation: "./"`, and `deploy`/`package`
copy the repo root *verbatim* into `agentcore/.cache/<Runtime>/staging/` — including `.env`. The
CLI has no exclusion flag and honours neither `.gitignore` nor `.dockerignore`, so whatever sits
in `.env` at deploy time ships inside the container image. Nothing in `agents/` or `mcp_servers/`
reads `.env` at runtime (config arrives as injected env vars), so keep deploy-time secrets out of
it, and treat any secret that was in `.env` during a past deploy as needing rotation.

## Architecture — three layers (see PROJECT.md for detail)

**Layer 1 · MCP servers (keyless, framework-agnostic).** FastMCP servers under `mcp_servers/`.
Two transports, one codebase (`mcp_servers/runtime.py`): **stdio** by default — the agent spawns
them as private subprocesses, used by local dev and every test — or **streamable-http**
(`MCP_TRANSPORT`), where each server runs as its own AgentCore Runtime behind the Gateway. Only
the HTTP path gets Identity and the Cedar policies, since those apply at the Gateway.
Deploy uses the standalone entry files (`serve_*.py`); AgentCore runs an entry *file*, not a module.
- `tvmaze` — TV/show data over `api.tvmaze.com`. Tools: `search_shows`, `get_schedule`, `get_episodes`, `get_cast`.
- `places` — OpenStreetMap data. Tools: `geocode` (Nominatim), `find_nearby` (Overpass), `travel_time` (OSRM).

**Layer 2 · Agents (orchestrator + two framework specialists).** Under `agents/`.
- `orchestrator` — the central agent point (Strands). Composes the specialists as tools (`ask_show_expert`, `ask_places_expert`) and owns every user-facing concern: `BedrockAgentCoreApp` entrypoint, Memory, identity (JWT `sub` → actor).
- `strands` — show specialist; owns ONLY the tvmaze server via `MCPClient`.
- `langgraph` — places specialist; owns ONLY the places server via `langchain-mcp-adapters`.
The specialists **partition** the seven tools (no overlap — tested). MCP portability: each server is consumed by a *different* framework with no per-framework rewrites, so which framework serves which server is interchangeable.
**Deployed shape:** the orchestrator is the ONLY agent runtime — specialists run in-process inside its bundle; do not give them runtimes. Its entry file `serve_orchestrator.py` **must stay at the repo root**: an entry file under `agents/` puts `agents/` on `sys.path`, and our `agents/langgraph` (regular package) then shadows the langgraph library (a namespace package) — the container dies at import, and it cannot reproduce locally. Deployed specialists auto-wire to the CLI-injected `AGENTCORE_GATEWAY_*_URL` and filter gateway tools to the set they own (`owned_tools`); memory reads the injected `MEMORY_<NAME>_ID`.

**Layer 3 · Amazon Bedrock AgentCore (production concerns).** Runtime, Memory (short-term session + long-term genre prefs), Identity (Cognito JWT via a gateway's `CUSTOM_JWT` authorizer), Gateway (managed tool routing), Evaluation, Observability. Configured as a **flat resource model** — top-level arrays in `agentcore/agentcore.json`, deployed by `agentcore/cdk/`. There are no per-primitive directories; `evals/` holds our own eval harness.

## Hard rules

1. **MCP servers stay framework-agnostic.** No Strands or LangChain imports inside `mcp_servers/` — both frameworks must consume them unchanged.
2. **Identity → Gateway → Policy Engine → Policies.** Gateway relies on Identity's OAuth provider; the policy engine needs an existing gateway; Cedar policies validate against a schema generated from the *deployed* gateway's tools (so they need its real ARN). Roll policies out `LOG_ONLY`, then `ENFORCE`. Inbound JWT uses `allowedClients` (Cognito access tokens carry `client_id`, not `aud`), and the MCP runtimes keep their own `CUSTOM_JWT` — on `AWS_IAM` a direct runtime invoke bypasses the gateway and every Cedar policy.
3. **A new gateway tool ships with a Cedar permit.** `policies/tools/` holds one permit per file — `CreatePolicy` accepts exactly one Cedar statement — and Cedar is default-deny, so an unlisted tool is refused. Action names are `<TargetName>___<tool>` from the *deployed* gateway (currently `TvmazeMcpTarget`/`PlacesMcpTarget`); read them from a live `tools/list`, never guess. Permits need `validationMode: IGNORE_ALL_FINDINGS` (the semantic linter flags every intentional allow-list as "Overly Permissive").
4. **Every new tool ships with** a pytest test and a one-line entry in the relevant SKILL.md.
5. **Set a descriptive User-Agent** on every Nominatim/Overpass/OSRM request, and cache responses — public instances are rate-limited.

## Conventions

- Keep each MCP client (`*_client.py`) thin: one responsibility, no agent logic.
- Tools return typed, JSON-serializable results — no raw upstream payloads.
- Use plan mode before non-trivial edits; build smallest-first, one module per commit.
- For noisy research (e.g. Overpass QL), use a subagent so it doesn't flood context.

## Hooks

Configured in `.claude/settings.json`, backed by scripts in `.claude/hooks/`:

- **PostToolUse** (`check_on_save.py`) — after a Write/Edit under `mcp_servers/` or
  `agents/`, runs `ruff check .` and `uv run pytest -q`; failures are reported back.
- **PreToolUse** (`block_secrets.py`) — blocks a `git commit` whose staged changes
  contain a `.env` file, an API-key-shaped string, or a real value from local `.env`
  (no secrets in git). Only guards commits Claude runs; install the same scan as
  a native git pre-commit hook to cover manual commits too.
