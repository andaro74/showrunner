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

## Files

- **`showrunner_tools.cedar`** — the allow-list. All seven MCP tools, permitted
  **one by one**. This is deliberate: using the target action group would mean any
  tool later added to a server inherits permission silently. Per-tool permits keep
  new tools denied until someone approves them here.
- **`argument_bounds.cedar`** — narrows an approved tool by its arguments (caps
  `find_nearby` radius, which protects the rate-limited Overpass endpoint).

## Applying them

Policies validate against a Cedar schema that the policy engine generates from the
**deployed** gateway's tool definitions — so the gateway and its targets must exist
first, and you need the real ARN.

```
1. agentcore add gateway --authorizer-type CUSTOM_JWT \
     --discovery-url <cognito discovery url> --allowed-audience <app client id>
2. agentcore add gateway-target ...        # tvmaze + places targets
3. agentcore deploy                        # gateway now has an ARN
4. sed -i "s|<GATEWAY_ARN>|$REAL_ARN|g" policies/*.cedar
5. agentcore add policy-engine --name ShowRunnerPolicies \
     --attach-to-gateways <gateway> --attach-mode LOG_ONLY
6. agentcore add policy --engine ShowRunnerPolicies --name AllowTools \
     --source policies/showrunner_tools.cedar
7. agentcore deploy
```

**Roll out in `LOG_ONLY` first.** Engine mode `LOG_ONLY` evaluates every call and
traces whether it *would* be allowed, without enforcing — so you can see what a
policy would break before it breaks it. Flip to `ENFORCE` once the traces are clean.
Engine mode wins over per-policy `enforcementMode`: a `LOG_ONLY` engine enforces
nothing, even for `ACTIVE` policies. Note the API default is `ENFORCE`, so
`LOG_ONLY` must be set explicitly.

`validationMode` is `FAIL_ON_ANY_FINDINGS` by default (schema **plus** semantic
checks). `IGNORE_ALL_FINDINGS` runs schema checks only — the documented escape
hatch while the tool schema is still moving.

## Unverified — confirm before relying on it

- **Target names.** `TvmazeTarget` / `PlacesTarget` are our intended names; the
  action strings must match the gateway targets actually created in step 2.
- **One statement per policy?** `Policy.statement` is a single string while these
  files hold several. Whether `--source` splits a multi-statement file or expects
  one statement per policy is untested — you may need one `add policy` per rule.
- **`create-policy` request shape.** AWS docs are internally inconsistent about the
  union member (`{"cedar":{"statement":…}}` vs `{"policy":{"statement":…}}`). Check
  `aws bedrock-agentcore-control create-policy help` for your SDK version.
- **`context.time.*`** appears in an AWS blog but not the conditions reference;
  don't depend on it.
