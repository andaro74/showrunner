#!/usr/bin/env bash
# Pre-publication hardening for the Cognito pool: close self-signup, remove demo users.
#
# WHY: the pool ships with AdminCreateUserConfig.AllowAdminCreateUserOnly=false, so
# `SignUp` is open to the internet. The only thing stopping a stranger from
# registering is that SignUp requires a SECRET_HASH derived from the app client
# secret -- a single control carrying the whole perimeter. Nothing in this project
# uses SignUp (BUILD.md creates users with admin-create-user), so closing it costs
# nothing and removes the dependency on that one secret.
#
# THE update-user-pool FOOTGUN: the API is a REPLACE, not a merge. Any field you
# omit is reset to its default -- silently. Passing only --admin-create-user-config
# would wipe Policies, MfaConfiguration, AccountRecoverySetting, LambdaConfig, tags,
# and the verification templates. So this script does a read-modify-write: it
# describes the pool, keeps every field update-user-pool actually accepts, flips the
# one flag, and sends the whole thing back.
#
# The accepted-field list is derived at runtime from
# `aws cognito-idp update-user-pool --generate-cli-skeleton`, not hardcoded, so a
# field AWS adds later is preserved instead of quietly dropped. Fields describe
# returns but update rejects (Arn, Id, Domain, SchemaAttributes, CreationDate,
# LastModifiedDate, EstimatedNumberOfUsers) are create-only or read-only and are
# not settable by any means -- dropping them changes nothing.
#
# Runs as a DRY RUN unless you pass --apply. In dry run it prints the exact JSON
# payload it would send, so you can diff it against the live pool yourself.
#
# Usage:
#   bash scripts/harden_cognito.sh                          # show the plan
#   bash scripts/harden_cognito.sh --apply                  # close self-signup
#   bash scripts/harden_cognito.sh --delete-user NAME --apply
#   bash scripts/harden_cognito.sh --list-users

set -euo pipefail

# Repo root: .env lives there, and a relative path would otherwise resolve against
# the caller's cwd and read (or create) the wrong file.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

APPLY=0
LIST_ONLY=0
SKIP_SIGNUP=0
DELETE_USERS=()
REGION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)        APPLY=1 ;;
    --list-users)   LIST_ONLY=1 ;;
    --keep-signup)  SKIP_SIGNUP=1 ;;
    --delete-user)  shift; [ $# -gt 0 ] || { echo "--delete-user needs a username" >&2; exit 1; }
                    DELETE_USERS+=("$1") ;;
    -h|--help)      sed -n '2,32p' "$0"; exit 0 ;;
    -*)             echo "unknown flag: $1" >&2; exit 1 ;;
    *)              REGION="$1" ;;
  esac
  shift
done

ENV_FILE=".env"
command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found" >&2; exit 1; }

# `|| true`: under `set -o pipefail` a missing key would fail the pipeline and
# `set -e` would abort before the friendly message below.
read_env() { grep "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true; }

REGION="${REGION:-$(read_env AWS_REGION)}"
REGION="${REGION:-us-west-2}"
POOL_ID=$(read_env COGNITO_USER_POOL_ID)
[ -n "$POOL_ID" ] || { echo "COGNITO_USER_POOL_ID missing from $ENV_FILE" >&2; exit 1; }

[ "$APPLY" -eq 1 ] || echo "=== DRY RUN (pass --apply to execute) ==="
echo "region    : $REGION"
echo "user pool : $POOL_ID"
echo

# --- users -----------------------------------------------------------------

echo "--- users currently in the pool ---"
aws cognito-idp list-users --user-pool-id "$POOL_ID" --region "$REGION" \
  --query 'Users[].{User:Username,Status:UserStatus,Created:UserCreateDate}' \
  --output table

if [ "$LIST_ONLY" -eq 1 ]; then exit 0; fi
echo

# --- 1. close self-signup --------------------------------------------------

if [ "$SKIP_SIGNUP" -eq 1 ]; then
  echo "--- self-signup: skipped (--keep-signup) ---"
else
  echo "--- self-signup ---"
  CURRENT=$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" --region "$REGION" \
    --query 'UserPool.AdminCreateUserConfig.AllowAdminCreateUserOnly' --output text)

  if [ "$CURRENT" = "True" ]; then
    echo "already closed (AllowAdminCreateUserOnly=true) - nothing to do"
  else
    PAYLOAD=$(mktemp)
    SKELETON=$(mktemp)
    DESCRIBED=$(mktemp)
    trap 'rm -f "$PAYLOAD" "$SKELETON" "$DESCRIBED"' EXIT

    aws cognito-idp update-user-pool --generate-cli-skeleton > "$SKELETON"
    aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" --region "$REGION" > "$DESCRIBED"

    POOL_ID="$POOL_ID" SKELETON="$SKELETON" DESCRIBED="$DESCRIBED" PAYLOAD="$PAYLOAD" \
    uv run python - <<'PY'
import json, os

skeleton = json.load(open(os.environ["SKELETON"]))
pool = json.load(open(os.environ["DESCRIBED"]))["UserPool"]

# Keep exactly what update-user-pool accepts. Everything else describe returns is
# create-only or read-only, so dropping it is not a loss of settable state.
payload = {k: v for k, v in pool.items() if k in skeleton}
payload["UserPoolId"] = os.environ["POOL_ID"]

# describe calls it Name; update calls it PoolName. Omitting it would rename the pool.
if "Name" in pool:
    payload["PoolName"] = pool["Name"]

# Flip the one flag, preserving siblings (InviteMessageTemplate, etc).
acu = dict(payload.get("AdminCreateUserConfig") or {})
acu["AllowAdminCreateUserOnly"] = True
payload["AdminCreateUserConfig"] = acu

json.dump(payload, open(os.environ["PAYLOAD"], "w"), indent=2, default=str)

kept = sorted(k for k in payload if k not in ("UserPoolId",))
dropped = sorted(set(pool) - set(skeleton) - {"Name"})
print(f"  preserving {len(kept)} field(s): {', '.join(kept)}")
print(f"  dropping (not settable): {', '.join(dropped)}")
PY

    echo "  setting  : AllowAdminCreateUserOnly false -> true"
    if [ "$APPLY" -eq 1 ]; then
      aws cognito-idp update-user-pool --region "$REGION" --cli-input-json "file://$PAYLOAD" >/dev/null
      VERIFY=$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" --region "$REGION" \
        --query 'UserPool.AdminCreateUserConfig.AllowAdminCreateUserOnly' --output text)
      echo "  verified : AllowAdminCreateUserOnly=$VERIFY"
      [ "$VERIFY" = "True" ] || { echo "  UPDATE DID NOT TAKE EFFECT" >&2; exit 1; }
    else
      echo "  [dry-run] aws cognito-idp update-user-pool --cli-input-json file://<payload>"
      echo "  [dry-run] payload written to: $PAYLOAD"
      echo "            review it, then re-run with --apply"
      trap - EXIT   # keep the payload around so it can actually be reviewed
      rm -f "$SKELETON" "$DESCRIBED"
    fi
  fi
fi
echo

# --- 2. delete demo users --------------------------------------------------

echo "--- demo users ---"
if [ ${#DELETE_USERS[@]} -eq 0 ]; then
  echo "no --delete-user given; nothing will be deleted."
  echo "Deletion is irreversible, so each username must be named explicitly:"
  echo "  bash scripts/harden_cognito.sh --delete-user showrunner-tester --apply"
else
  for user in "${DELETE_USERS[@]}"; do
    if ! aws cognito-idp admin-get-user --user-pool-id "$POOL_ID" --username "$user" \
         --region "$REGION" >/dev/null 2>&1; then
      echo "  $user: not found - skipping"
      continue
    fi
    if [ "$APPLY" -eq 1 ]; then
      aws cognito-idp admin-delete-user --user-pool-id "$POOL_ID" --username "$user" --region "$REGION"
      echo "  $user: DELETED"
    else
      echo "  [dry-run] aws cognito-idp admin-delete-user --username $user"
    fi
  done
fi

echo
if [ "$APPLY" -eq 1 ]; then
  echo "Done. Self-signup now requires an administrator, so the app client secret is"
  echo "no longer the only thing standing between the internet and your user pool."
else
  echo "Dry run complete. Nothing changed. Re-run with --apply to execute."
fi
