#!/usr/bin/env bash
# Rotate the Cognito app client secrets.
#
# Cognito CANNOT rotate a client secret in place -- there is no API for it. The
# only way to retire a secret is to create a replacement app client, which issues
# a NEW client id. That id is referenced by `allowedClients` on the runtimes and
# the gateway, so a rotation is a config change plus a deploy, not a one-liner.
#
# Two secrets, both created with --generate-secret:
#
#   COGNITO_CLIENT_SECRET      user -> gateway    (create_cognito.sh)
#   COGNITO_M2M_CLIENT_SECRET  gateway -> runtimes (create_cognito_m2m.sh), also
#                              stored in Secrets Manager as the AgentCore identity
#                              credential `GatewayToRuntimes`
#
# TWO PHASES, on purpose. The old client keeps working until you retire it, so a
# failed deploy never locks you out:
#
#   1. bash scripts/rotate_cognito_secrets.sh --apply
#      Creates replacement clients, updates .env + agentcore/local-config.json,
#      refreshes the GatewayToRuntimes credential, re-renders the manifest.
#   2. agentcore deploy    <- then verify you can still get a token and invoke
#   3. bash scripts/rotate_cognito_secrets.sh --retire --apply
#      Deletes the old clients. Only now is the old secret actually dead.
#
# Runs as a DRY RUN unless you pass --apply: every AWS call is printed, nothing
# executes. Read the plan first.
#
# Replacement clients are named <base>-rN (the base name stays taken until you
# retire). Note that create_cognito.sh looks clients up by the BASE name, so do
# not re-run it after a rotation -- it would provision a third, parallel client.
#
# Usage:
#   bash scripts/rotate_cognito_secrets.sh [--apply] [--user|--m2m] [region]
#   bash scripts/rotate_cognito_secrets.sh --retire [--apply] [region]

set -euo pipefail

APPLY=0
RETIRE=0
DO_USER=0
DO_M2M=0
REGION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)  APPLY=1 ;;
    --retire) RETIRE=1 ;;
    --user)   DO_USER=1 ;;
    --m2m)    DO_M2M=1 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    -*) echo "unknown flag: $1" >&2; exit 1 ;;
    *)  REGION="$1" ;;
  esac
  shift
done

# Neither selected means both.
if [ "$DO_USER" -eq 0 ] && [ "$DO_M2M" -eq 0 ]; then DO_USER=1; DO_M2M=1; fi

# Run from the repo root regardless of where the caller invoked us. Without this,
# `.env` resolves against the caller's cwd: running from scripts/ silently creates
# a SECOND .env there (upsert_env touches it), writes real secrets into it, and
# leaves the real one untouched.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ENV_FILE=".env"
LOCAL_CONFIG="agentcore/local-config.json"
CREDENTIAL="GatewayToRuntimes"
USER_CLIENT_BASE="showrunner-agent"
M2M_CLIENT_BASE="showrunner-gateway-m2m"

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found - run scripts/create_cognito.sh first" >&2; exit 1; }

# `|| true` matters: under `set -o pipefail` a missing key makes grep exit 1, the
# whole pipeline exit 1, and `set -e` kill the script -- so a legitimately absent
# key (no rotation yet, nothing to retire) would abort instead of reporting.
read_env() { grep "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true; }

upsert_env() {
  local key="$1" value="$2"
  touch "$ENV_FILE"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    grep -v "^${key}=" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

drop_env() {
  grep -v "^$1=" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
}

# Execute, or print the call in dry-run.
run() {
  if [ "$APPLY" -eq 1 ]; then "$@"; else printf '  [dry-run] %s\n' "$*" >&2; fi
}

# Like run(), but the caller wants stdout. Dry-run yields a visible placeholder
# so downstream steps still print something legible.
capture() {
  if [ "$APPLY" -eq 1 ]; then
    "$@"
  else
    printf '  [dry-run] %s\n' "$*" >&2
    printf 'WOULD-BE-NEW-ID'
  fi
}

REGION="${REGION:-$(read_env AWS_REGION)}"
REGION="${REGION:-us-west-2}"
POOL_ID=$(read_env COGNITO_USER_POOL_ID)
[ -n "$POOL_ID" ] || { echo "COGNITO_USER_POOL_ID missing from $ENV_FILE" >&2; exit 1; }

[ "$APPLY" -eq 1 ] || echo "=== DRY RUN (pass --apply to execute) ==="
echo "region    : $REGION"
echo "user pool : $POOL_ID"
echo

# --- retire phase ----------------------------------------------------------

if [ "$RETIRE" -eq 1 ]; then
  echo "Retiring the previous app clients. Do this ONLY after a successful"
  echo "'agentcore deploy' on the rotated ids."
  echo

  retired=0
  for key in COGNITO_CLIENT_ID_PREVIOUS COGNITO_M2M_CLIENT_ID_PREVIOUS; do
    old=$(read_env "$key")
    if [ -z "$old" ]; then
      echo "no $key in $ENV_FILE - nothing to retire for this client"
      continue
    fi
    echo "deleting old client: $old  ($key)"
    run aws cognito-idp delete-user-pool-client \
      --user-pool-id "$POOL_ID" --client-id "$old" --region "$REGION"
    [ "$APPLY" -eq 1 ] && drop_env "$key"
    retired=$((retired + 1))
  done

  echo
  if [ "$APPLY" -eq 1 ]; then
    echo "retired $retired client(s). The old secrets are now dead."
    echo "Remaining manual step: reset the test user's password if it was exposed --"
    echo "  aws cognito-idp admin-set-user-password --user-pool-id $POOL_ID \\"
    echo "    --username \"\$(grep ^COGNITO_TEST_USERNAME $ENV_FILE | cut -d= -f2-)\" \\"
    echo "    --password '<new>' --permanent --region $REGION"
    echo "  then update COGNITO_TEST_PASSWORD in $ENV_FILE"
  else
    echo "dry run: nothing deleted."
  fi
  exit 0
fi

# --- rotate phase ----------------------------------------------------------

# Next free -rN suffix, so a replacement never collides with the base name or an
# earlier rotation.
next_name() {
  local base="$1" n=1
  while aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
          --max-results 60 --region "$REGION" \
          --query "UserPoolClients[?ClientName=='${base}-r${n}'].ClientId | [0]" \
          --output text 2>/dev/null | grep -qv '^None$'; do
    n=$((n + 1))
  done
  printf '%s-r%s' "$base" "$n"
}

secret_of() {
  aws cognito-idp describe-user-pool-client \
    --user-pool-id "$POOL_ID" --client-id "$1" --region "$REGION" \
    --query 'UserPoolClient.ClientSecret' --output text
}

if [ "$DO_USER" -eq 1 ]; then
  OLD_USER_ID=$(read_env COGNITO_CLIENT_ID)
  NEW_USER_NAME=$(next_name "$USER_CLIENT_BASE")
  echo "--- user client (user -> gateway) ---"
  echo "current : $OLD_USER_ID"
  echo "creating: $NEW_USER_NAME"

  NEW_USER_ID=$(capture aws cognito-idp create-user-pool-client \
    --user-pool-id "$POOL_ID" \
    --client-name "$NEW_USER_NAME" \
    --generate-secret \
    --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --region "$REGION" \
    --query 'UserPoolClient.ClientId' --output text)

  if [ "$APPLY" -eq 1 ]; then
    NEW_USER_SECRET=$(secret_of "$NEW_USER_ID")
    upsert_env COGNITO_CLIENT_ID_PREVIOUS "$OLD_USER_ID"
    upsert_env COGNITO_CLIENT_ID "$NEW_USER_ID"
    upsert_env COGNITO_CLIENT_SECRET "$NEW_USER_SECRET"
    echo "created : $NEW_USER_ID (secret redacted: ${#NEW_USER_SECRET} chars)"
  fi
  echo
fi

if [ "$DO_M2M" -eq 1 ]; then
  OLD_M2M_ID=$(read_env COGNITO_M2M_CLIENT_ID)
  SCOPE=$(read_env COGNITO_M2M_SCOPE)
  [ -n "$SCOPE" ] || { echo "COGNITO_M2M_SCOPE missing from $ENV_FILE" >&2; exit 1; }
  NEW_M2M_NAME=$(next_name "$M2M_CLIENT_BASE")
  echo "--- m2m client (gateway -> runtimes) ---"
  echo "current : $OLD_M2M_ID"
  echo "creating: $NEW_M2M_NAME"

  NEW_M2M_ID=$(capture aws cognito-idp create-user-pool-client \
    --user-pool-id "$POOL_ID" \
    --client-name "$NEW_M2M_NAME" \
    --generate-secret \
    --allowed-o-auth-flows client_credentials \
    --allowed-o-auth-scopes "$SCOPE" \
    --allowed-o-auth-flows-user-pool-client \
    --region "$REGION" \
    --query 'UserPoolClient.ClientId' --output text)

  if [ "$APPLY" -eq 1 ]; then
    NEW_M2M_SECRET=$(secret_of "$NEW_M2M_ID")
    upsert_env COGNITO_M2M_CLIENT_ID_PREVIOUS "$OLD_M2M_ID"
    upsert_env COGNITO_M2M_CLIENT_ID "$NEW_M2M_ID"
    upsert_env COGNITO_M2M_CLIENT_SECRET "$NEW_M2M_SECRET"
    echo "created : $NEW_M2M_ID (secret redacted: ${#NEW_M2M_SECRET} chars)"
  fi

  # The gateway's outbound credential holds the OLD secret; replace it. Same
  # remove+add shape as wire_gateway_targets.sh -- `add credential` has no update.
  DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${POOL_ID}/.well-known/openid-configuration"
  if command -v agentcore >/dev/null; then
    echo "refreshing credential: $CREDENTIAL"
    if [ "$APPLY" -eq 1 ]; then
      agentcore remove credential --name "$CREDENTIAL" -y >/dev/null 2>&1 || true
      agentcore add credential \
        --name "$CREDENTIAL" --type oauth \
        --client-id "$NEW_M2M_ID" --client-secret "$NEW_M2M_SECRET" \
        --discovery-url "$DISCOVERY_URL" --scopes "$SCOPE" >/dev/null
      echo "         (secret sent to AWS, not written to the manifest)"
    else
      printf '  [dry-run] agentcore remove credential --name %s -y\n' "$CREDENTIAL" >&2
      printf '  [dry-run] agentcore add credential --name %s --type oauth --client-id <new> ...\n' \
        "$CREDENTIAL" >&2
    fi
  else
    echo "agentcore CLI not found - refresh $CREDENTIAL manually before deploying" >&2
  fi
  echo
fi

# --- config + manifest -----------------------------------------------------
# The client ids are templated, so the only file to touch is local-config.json.

if [ "$APPLY" -eq 1 ]; then
  [ -f "$LOCAL_CONFIG" ] || { echo "$LOCAL_CONFIG not found - run scripts/config.py scrub" >&2; exit 1; }
  NEW_USER_ID="${NEW_USER_ID:-}" NEW_M2M_ID="${NEW_M2M_ID:-}" LOCAL_CONFIG="$LOCAL_CONFIG" \
  uv run python - <<'PY'
import json, os, pathlib
path = pathlib.Path(os.environ["LOCAL_CONFIG"])
cfg = json.loads(path.read_text(encoding="utf-8"))
for key, env in (("COGNITO_USER_CLIENT_ID", "NEW_USER_ID"),
                 ("COGNITO_M2M_CLIENT_ID", "NEW_M2M_ID")):
    if value := os.environ.get(env):
        cfg[key] = value
        print(f"  {key} -> {value}")
path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
PY
  echo "updated $LOCAL_CONFIG"
  uv run python scripts/config.py render
else
  keys=""
  [ "$DO_USER" -eq 1 ] && keys="COGNITO_USER_CLIENT_ID"
  [ "$DO_M2M" -eq 1 ] && keys="${keys:+$keys, }COGNITO_M2M_CLIENT_ID"
  echo "  [dry-run] update $keys in $LOCAL_CONFIG"
  echo "  [dry-run] uv run python scripts/config.py render"
fi

echo
if [ "$APPLY" -eq 1 ]; then
  cat <<'NEXT'
Rotated. The OLD clients still exist and still work -- nothing is retired yet.

Next:
  1. agentcore deploy
  2. Verify: mint a user token and invoke the agent (see BUILD.md), and confirm
     the gateway can still reach the MCP runtimes.
  3. bash scripts/rotate_cognito_secrets.sh --retire --apply

Until step 3 the old secrets remain valid. If step 2 fails, restore the
*_PREVIOUS ids in .env and agentcore/local-config.json, render, and redeploy.
NEXT
else
  echo "Dry run complete. Nothing changed. Re-run with --apply to execute."
fi
