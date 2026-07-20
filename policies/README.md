# policies/ — Cedar authorization for the AgentCore Gateway

Identity proves *who* is calling. These policies decide *what that caller may do*:
the gateway executes only the tool actions approved here.

## Entity model

| Cedar part | Value for this project |
|---|---|
| `principal` | `AgentCore::OAuthUser` — created from the Cognito JWT `sub` claim. JWT claims are exposed as tags: `principal.getTag("username")`. |
| `action` | `AgentCore::Action::"<TargetName>___<toolName>"` — **triple** underscore. Cedar has **no wildcard actions**. A bare target name is an action *group*: `action in AgentCore::Action::"TvmazeTarget"`. |
| `resource` | `AgentCore::Gateway::"<gateway-arn>"` — the gateway, not the tool. Must be `==` with a real ARN when naming specific actions. |
| `context` | `context.input.<argName>` (tool call arguments), `context.output.*` (responses). Keys come from the tool's input schema. |

Evaluation is **default-deny**, and **`forbid` overrides `permit`**.

## Scope limit: these policies bind at the Gateway only

A tool call that does not go through the gateway is **not evaluated by Cedar at all** — not
denied, just never seen. Two paths skip it:

- **stdio** (local dev, every test) — the agent spawns the servers as subprocesses. No gateway,
  no policies. Expected, and why the tests don't exercise authorization.
- **Direct runtime invoke** — if `TvmazeMcp` / `PlacesMcp` accept `AWS_IAM`, anyone with
  `InvokeAgentRuntime` reaches the tools around the gateway. That one is a real hole, so both
  runtimes also require `CUSTOM_JWT` against the same Cognito pool (`agentcore.json`). Keep it
  that way: dropping the runtime authorizer silently disables every policy in this directory.

## Files

- **`tools/*.cedar`** — the allow-list, **one permit per file** because `CreatePolicy`
  accepts exactly one Cedar statement (a 7-permit file fails with *"Expected exactly
  one policy statement, but got 7"*). Per-tool permits are deliberate: using the
  target action group would mean any tool later added to a server inherits
  permission silently. New tools stay denied until someone approves them here.
- **`argument_bounds.cedar`** — narrows an approved tool by its arguments (caps
  `find_nearby` radius, which protects the rate-limited Overpass endpoint).

Keep these files ASCII-only: `add policy` snapshots the file into the manifest and
has read UTF-8 comments as cp1252, mojibaking them.

## Applying them (the sequence that actually deployed)

Policies validate against a Cedar schema that the policy engine generates from the
**deployed** gateway's tool definitions — so the gateway and its targets must exist
first, and you need the real ARN *and the real tool names*.

```
1. agentcore add gateway --protocol-type None --authorizer-type CUSTOM_JWT \
     --discovery-url <cognito discovery url> --allowed-clients <app client id>
   # --allowed-clients, NOT --allowed-audience: a Cognito ACCESS token has no `aud`
   # claim (it carries `client_id`), so --allowed-audience matches ID tokens only.
2. bash scripts/create_cognito_m2m.sh && bash scripts/wire_gateway_targets.sh
   # mcp-server targets, NOT http-runtime: an httpRuntime target is an opaque proxy,
   # so its Cedar schema has ONE action per target — the HTTP route "POST:/" — and
   # per-tool actions like TvmazeMcpTarget___search_shows simply don't exist.
3. agentcore deploy                        # gateway + targets live; tools enumerated
4. # tools/list through the gateway, and take the ACTION NAMES from its output —
   # they are <TargetName>___<tool>, so renaming a target renames every action.
   # Substitute the real gateway ARN into policies/**/*.cedar.
5. # engine attachment lives ON THE GATEWAY (add policy-engine's --attach-to-gateways
   # writes nothing): gateway.policyEngineConfiguration = {policyEngineName, mode}.
   agentcore add policy-engine --name ShowRunnerPolicies   # then set mode LOG_ONLY
6. for f in policies/tools/*.cedar: agentcore add policy --engine ShowRunnerPolicies \
     --name Allow<Tool> --source "$f"     # one add per file
7. agentcore deploy
```

**Roll out in `LOG_ONLY` first.** Engine mode `LOG_ONLY` evaluates every call and
traces whether it *would* be allowed, without enforcing — so you can see what a
policy would break before it breaks it. Flip to `ENFORCE` once the traces are clean.
Engine mode wins over per-policy `enforcementMode`: a `LOG_ONLY` engine enforces
nothing, even for `ACTIVE` policies. Note the API default is `ENFORCE`, so
`LOG_ONLY` must be set explicitly.

**`validationMode` must be `IGNORE_ALL_FINDINGS` for every policy here.** The
default `FAIL_ON_ANY_FINDINGS` includes semantic lint that has no passing shape for
this design: a per-tool allow-list is flagged **"Overly Permissive"** (it grants the
action to every authenticated `OAuthUser` — its purpose), and the radius guard is
flagged **"Overly Restrictive"** (the linter discounts the `when` clause). Schema
validation still runs under `IGNORE_ALL_FINDINGS`, so a wrong action name still
fails the deploy — which is the check that matters.

## Verified the hard way

- **Target/action names.** Actions are `<TargetName>___<tool>` and the CDK prefixes
  the gateway ARN internally (`<arn>___TvmazeMcpTarget___search_shows`). Current
  targets are `TvmazeMcpTarget`/`PlacesMcpTarget` — renamed from `TvmazeTarget`/
  `PlacesTarget` because a target's type cannot change in place ("Target
  configuration cannot be updated from runtime to mcpServer"); the rename forced
  replacement and **renamed every action**, which is why these files were rewritten
  from a live `tools/list` rather than edited.
- **One statement per policy: confirmed.** `--source` does not split a file.
- **`add policy` snapshots.** The manifest stores the statement text at `add` time;
  editing a `.cedar` file afterwards changes nothing until the policy is removed
  and re-added.
- **`context.time.*`** appears in an AWS blog but not the conditions reference;
  don't depend on it. (`context.input.radius` deployed fine; whether the gateway
  populates it for MCP calls is untested until ENFORCE + traces.)
