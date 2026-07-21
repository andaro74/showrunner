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
#   2. agentcore deploy
#   3. bash scripts/rotate_cognito_secrets.sh --verify    <- DO NOT SKIP
#      Confirms the gateway's stored m2m secret matches .env and that the
#      deployed runtimes trust the current client. A rotation that fails here
#      looks fine until an invoke returns "Authorization error when sending
#      message" from both specialists -- the gateway keeps the OLD secret while
#      the runtimes trust only the NEW client.
#   4. bash scripts/rotate_cognito_secrets.sh --retire --apply
#      Deletes every superseded client. Only now are the old secrets dead.
#
# Retirement is DISCOVERY-based: it asks Cognito which clients carry this
# project's base names and deletes everything that is not currently live. An
# earlier version tracked one COGNITO_*_CLIENT_ID_PREVIOUS key and overwrote it
# each run, so rotating twice orphaned the original pair -- still live, still
# holding the secrets the rotation was meant to kill, and invisible to --retire.
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
VERIFY=0
DO_USER=0
DO_M2M=0
REGION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)  APPLY=1 ;;
    --retire) RETIRE=1 ;;
    --verify) VERIFY=1 ;;
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

# --verify is read-only, so the dry-run banner would only confuse.
[ "$APPLY" -eq 1 ] || [ "$VERIFY" -eq 1 ] || echo "=== DRY RUN (pass --apply to execute) ==="
echo "region    : $REGION"
echo "user pool : $POOL_ID"
echo

# --- verify phase ----------------------------------------------------------
# The gateway->runtime hop is the one a rotation breaks silently. `add credential`
# stores the m2m secret in Secrets Manager; if that write does not land, the
# gateway keeps presenting the OLD secret while the redeployed runtimes trust
# only the NEW client. Nothing surfaces until an invoke fails with
# "McpException - MCP invocation failed: Authorization error when sending message".
# This compares what the gateway holds against .env, without printing either.

if [ "$VERIFY" -eq 1 ]; then
  echo "--- gateway -> runtime credential ---"
  CURRENT_M2M=$(read_env COGNITO_M2M_CLIENT_ID)
  SECRET_ARN=$(aws bedrock-agentcore-control get-oauth2-credential-provider \
    --name "$CREDENTIAL" --region "$REGION" \
    --query 'clientSecretArn.secretArn' --output text 2>/dev/null)

  if [ -z "$SECRET_ARN" ] || [ "$SECRET_ARN" = "None" ]; then
    echo "  credential '$CREDENTIAL' not found - the gateway has no outbound auth" >&2
    exit 1
  fi

  STORED=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" \
    --region "$REGION" --query SecretString --output text 2>/dev/null)

  STORED="$STORED" ENV_FILE="$ENV_FILE" uv run python - <<'PY' || exit 1
import hashlib, json, os, sys
stored = json.loads(os.environ["STORED"]).get("client_secret", "")
env = {}
for line in open(os.environ["ENV_FILE"], encoding="utf-8"):
    if "=" in line and not line.startswith("#"):
        k, v = line.rstrip("\n").split("=", 1)
        env[k] = v.strip('"')
current = env.get("COGNITO_M2M_CLIENT_SECRET", "")
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:16]
print(f"  gateway credential : {h(stored)}")
print(f"  .env m2m secret    : {h(current)}")
if stored == current:
    print("  MATCH - the gateway can authenticate to the runtimes")
    sys.exit(0)
sys.stdout.flush()  # else the hashes land AFTER the error, which reads backwards
print("  MISMATCH - the gateway holds a stale secret.", file=sys.stderr)
print("  Re-register it, then redeploy (add overwrites; do not `remove` first --", file=sys.stderr)
print("  remove fails once targets reference the credential and has no --force):", file=sys.stderr)
print("    agentcore add credential --name GatewayToRuntimes --type oauth \\", file=sys.stderr)
print('      --client-id "$COGNITO_M2M_CLIENT_ID" --client-secret "$COGNITO_M2M_CLIENT_SECRET" \\',
      file=sys.stderr)
print('      --discovery-url "https://cognito-idp.$AWS_REGION.amazonaws.com/'
      '$COGNITO_USER_POOL_ID/.well-known/openid-configuration" \\', file=sys.stderr)
print('      --scopes "$COGNITO_M2M_SCOPE"', file=sys.stderr)
print("    agentcore deploy", file=sys.stderr)
sys.exit(1)
PY

  echo
  echo "--- deployed runtimes trust the current m2m client? ---"
  for rt in TvmazeMcp PlacesMcp; do
    rid=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
      --query "agentRuntimes[?contains(agentRuntimeName,'$rt')].agentRuntimeId | [0]" --output text 2>/dev/null)
    ac=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$rid" \
      --region "$REGION" --query 'authorizerConfiguration.customJWTAuthorizer.allowedClients' \
      --output text 2>/dev/null)
    if [ "$ac" = "$CURRENT_M2M" ]; then
      printf '  %-11s OK\n' "$rt"
    else
      printf '  %-11s MISMATCH: trusts %s, .env has %s\n' "$rt" "$ac" "$CURRENT_M2M"
      echo "     -> run 'agentcore deploy' to push the current manifest" >&2
    fi
  done
  exit 0
fi

# --- retire phase ----------------------------------------------------------

if [ "$RETIRE" -eq 1 ]; then
  echo "Retiring superseded app clients. Do this ONLY after a successful"
  echo "'agentcore deploy' and a verified invoke on the rotated ids."
  echo
  # Retirement is DISCOVERY-based, not tracked. An earlier version of this
  # script recorded the outgoing id in COGNITO_*_CLIENT_ID_PREVIOUS and retired
  # that -- but it OVERWROTE the key on every run, so a second rotation orphaned
  # the first pair: untracked, still live, and never retired. Those originals
  # are precisely the credentials a rotation exists to kill.
  #
  # So: ask Cognito which clients carry this project's base names, subtract the
  # two that .env says are live, and everything left is superseded regardless of
  # how many rotations happened or what .env remembers.
  CURRENT_USER=$(read_env COGNITO_CLIENT_ID)
  CURRENT_M2M=$(read_env COGNITO_M2M_CLIENT_ID)
  for v in CURRENT_USER CURRENT_M2M; do
    [ -n "${!v}" ] || { echo "$v missing from $ENV_FILE - refusing to guess what is live" >&2; exit 1; }
  done
  echo "live (never deleted):"
  echo "  user client : $CURRENT_USER"
  echo "  m2m  client : $CURRENT_M2M"
  echo

  listing=$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
    --max-results 60 --region "$REGION" \
    --query 'UserPoolClients[].[ClientId,ClientName]' --output text)

  stale_ids=()
  stale_names=()
  while IFS=$'\t' read -r cid cname; do
    [ -n "$cid" ] || continue
    case "$cname" in
      "$USER_CLIENT_BASE"*|"$M2M_CLIENT_BASE"*) ;;
      *) continue ;;                       # not ours - never touch it
    esac
    [ "$cid" = "$CURRENT_USER" ] && continue
    [ "$cid" = "$CURRENT_M2M" ] && continue
    stale_ids+=("$cid")
    stale_names+=("$cname")
  done <<< "$listing"

  if [ ${#stale_ids[@]} -eq 0 ]; then
    echo "no superseded clients - nothing to retire."
    exit 0
  fi

  echo "superseded (${#stale_ids[@]}):"
  for i in "${!stale_ids[@]}"; do
    printf '  %-28s %s\n' "${stale_names[$i]}" "${stale_ids[$i]}"
  done
  echo

  for i in "${!stale_ids[@]}"; do
    cid="${stale_ids[$i]}"
    # Belt and braces: the loop already skipped these, but a delete is final.
    if [ "$cid" = "$CURRENT_USER" ] || [ "$cid" = "$CURRENT_M2M" ]; then
      echo "REFUSING to delete a live client ($cid)" >&2; exit 1
    fi
    if [ "$APPLY" -eq 1 ]; then
      aws cognito-idp delete-user-pool-client \
        --user-pool-id "$POOL_ID" --client-id "$cid" --region "$REGION"
      echo "  deleted ${stale_names[$i]} ($cid)"
    else
      echo "  [dry-run] delete ${stale_names[$i]} ($cid)"
    fi
  done

  if [ "$APPLY" -eq 1 ]; then
    # The _PREVIOUS keys are now meaningless; drop them so nothing reads them.
    drop_env COGNITO_CLIENT_ID_PREVIOUS
    drop_env COGNITO_M2M_CLIENT_ID_PREVIOUS
    echo
    echo "retired ${#stale_ids[@]} client(s). Those secrets are now dead."
    echo "Remaining manual step: reset the test user's password if it was exposed --"
    echo "  aws cognito-idp admin-set-user-password --user-pool-id $POOL_ID \\"
    echo "    --username \"\$(grep ^COGNITO_TEST_USERNAME $ENV_FILE | cut -d= -f2-)\" \\"
    echo "    --password '<new>' --permanent --region $REGION"
  else
    echo
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
      # No `remove` first: once gateway targets reference the credential, remove
      # fails with "referenced by gateway target(s) ... Use force to override" --
      # and `remove credential` has no --force flag, so that error is unfixable.
      # `add` overwrites an existing credential of the same name, which is all we
      # need. The old remove+add read as working because the remove's failure was
      # swallowed by `|| true` while the add quietly did the real work.
      agentcore add credential \
        --name "$CREDENTIAL" --type oauth \
        --client-id "$NEW_M2M_ID" --client-secret "$NEW_M2M_SECRET" \
        --discovery-url "$DISCOVERY_URL" --scopes "$SCOPE" >/dev/null
      echo "         (stored on 'agentcore deploy' -- run --verify afterwards)"
    else
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
