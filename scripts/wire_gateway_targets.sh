#!/usr/bin/env bash
# Point the gateway at the MCP runtimes as `mcp-server` targets.
#
# WHY NOT httpRuntime: an httpRuntime target is an opaque HTTP proxy -- the gateway
# forwards bytes and never learns what tools the server exposes. The Cedar schema it
# generates therefore contains ONE action per target, the HTTP route:
#
#     AgentCore::Action::"<gatewayArn>___TvmazeTarget___POST:/"
#
# so per-tool policies cannot be written at all. Our first attempt failed with
# `unrecognized action ...___TvmazeTarget___search_shows`. An `mcp-server` target
# makes the gateway speak MCP to the backend, enumerate its tools, and generate
# per-tool actions -- which is what policies/tools/*.cedar require.
#
# WHY OUTBOUND OAUTH: mcp-server targets require outbound credentials (the CLI
# rejects even `--outbound-auth none` without one). The gateway authenticates to the
# runtimes with the client_credentials client from create_cognito_m2m.sh, and the
# runtimes' CUSTOM_JWT authorizer lists that client in allowedClients.
#
# The client secret is read from .env and handed to the CLI, which sends it to AWS.
# It is NOT written to agentcore.json (verified: the manifest keeps only
# discoveryUrl/scopes/vendor), so the manifest stays safe to commit.
#
# Run scripts/create_cognito_m2m.sh first.
#
# Usage:  bash scripts/wire_gateway_targets.sh

set -euo pipefail

# Repo root: .env lives there, and every `agentcore` command needs the manifest
# at agentcore/agentcore.json or it reports "No agentcore project found."
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

REGION="${AWS_REGION:-us-west-2}"
GATEWAY="showrunner-gateway"
CREDENTIAL="GatewayToRuntimes"
ENV_FILE=".env"

command -v agentcore >/dev/null || { echo "agentcore CLI not found" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found" >&2; exit 1; }

# `|| true` keeps a missing key from aborting the script: under `set -o pipefail`
# grep's exit 1 fails the pipeline and `set -e` kills us before the check below
# can report which value is missing.
read_env() { grep "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true; }

M2M_ID=$(read_env COGNITO_M2M_CLIENT_ID)
M2M_SECRET=$(read_env COGNITO_M2M_CLIENT_SECRET)
SCOPE=$(read_env COGNITO_M2M_SCOPE)
POOL_ID=$(read_env COGNITO_USER_POOL_ID)

for v in M2M_ID M2M_SECRET SCOPE POOL_ID; do
  [ -n "${!v}" ] || { echo "$v missing from $ENV_FILE - run scripts/create_cognito_m2m.sh" >&2; exit 1; }
done

DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${POOL_ID}/.well-known/openid-configuration"

# Runtime invocation endpoints. The ARN is percent-encoded into the path, and
# ?qualifier=DEFAULT selects the live version -- the same URL shape `agentcore status`
# reports and the MCP client uses over streamable-http.
runtime_url() {
  local arn="$1"
  local encoded
  encoded=$(printf '%s' "$arn" | sed 's|:|%3A|g; s|/|%2F|g')
  printf 'https://bedrock-agentcore.%s.amazonaws.com/runtimes/%s/invocations?qualifier=DEFAULT' \
    "$REGION" "$encoded"
}

TV_ARN=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
  --query "agentRuntimes[?contains(agentRuntimeName,'TvmazeMcp')].agentRuntimeArn | [0]" --output text)
PL_ARN=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
  --query "agentRuntimes[?contains(agentRuntimeName,'PlacesMcp')].agentRuntimeArn | [0]" --output text)

for pair in "TvmazeMcp:$TV_ARN" "PlacesMcp:$PL_ARN"; do
  name="${pair%%:*}"; arn="${pair#*:}"
  [ "$arn" != "None" ] && [ -n "$arn" ] || {
    echo "runtime $name not deployed - run 'agentcore deploy' first" >&2; exit 1; }
done

echo "tvmaze runtime : $TV_ARN"
echo "places runtime : $PL_ARN"
echo

# Remove the httpRuntime targets (idempotent: ignore "not found").
for t in TvmazeTarget PlacesTarget; do
  agentcore remove gateway-target --name "$t" -y >/dev/null 2>&1 || true
done

# --- outbound credential ---------------------------------------------------
# The --oauth-client-id/-secret/-discovery-url flags on `add gateway-target` are
# documented as "creates credential inline", but they do NOT populate
# outboundAuth.credentialName, and validation then fails with:
#     outboundAuth.credentialName: OAUTH outbound auth requires a credentialName
# The credential must be a separate resource, referenced by name.
agentcore remove credential --name "$CREDENTIAL" -y >/dev/null 2>&1 || true
agentcore add credential \
  --name "$CREDENTIAL" \
  --type oauth \
  --client-id "$M2M_ID" \
  --client-secret "$M2M_SECRET" \
  --discovery-url "$DISCOVERY_URL" \
  --scopes "$SCOPE" >/dev/null
echo "added credential: $CREDENTIAL (secret sent to AWS, not written to agentcore.json)"

add_target() {
  local name="$1" url="$2"
  agentcore add gateway-target \
    --gateway "$GATEWAY" \
    --name "$name" \
    --type mcp-server \
    --endpoint "$url" \
    --outbound-auth oauth \
    --credential-name "$CREDENTIAL" >/dev/null
  echo "added mcp-server target: $name"
}

add_target TvmazeTarget "$(runtime_url "$TV_ARN")"
add_target PlacesTarget "$(runtime_url "$PL_ARN")"

echo
agentcore validate
echo
echo "Next: agentcore deploy"
echo "Then read the REAL action names out of the deployed schema before writing"
echo "policies -- policies/tools/*.cedar still assume the per-tool names that the"
echo "httpRuntime schema rejected."
