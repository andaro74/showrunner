#!/usr/bin/env bash
# Provision the Cognito user pool + app client that back AgentCore Identity.
#
# Cognito is an *external prerequisite*, not part of the AgentCore CDK stack:
# the gateway's CUSTOM_JWT authorizer references it by OIDC discovery URL. This
# script is the reproducible record of how it was created (the AgentCore CDK
# project is generated, so hand-edits there aren't safe).
#
# Idempotent: re-running reuses an existing pool/client of the same name.
# The client secret is written straight to .env (gitignored) and never printed.
#
# Usage:  bash scripts/create_cognito.sh [region]

set -euo pipefail

# Always operate on the repo root's .env. Without this, running the script from
# scripts/ writes a second .env there (upsert_env touches it) holding a real
# client secret, while the real .env goes unchanged.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

REGION="${1:-${AWS_REGION:-us-west-2}}"
POOL_NAME="showrunner-users"
CLIENT_NAME="showrunner-agent"
ENV_FILE=".env"

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }

# Upsert KEY=VALUE in .env without disturbing other lines.
upsert_env() {
  local key="$1" value="$2"
  touch "$ENV_FILE"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    grep -v "^${key}=" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

echo "region: $REGION"

# --- user pool -------------------------------------------------------------
POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" \
  --query "UserPools[?Name=='${POOL_NAME}'].Id | [0]" --output text)

if [ "$POOL_ID" = "None" ] || [ -z "$POOL_ID" ]; then
  POOL_ID=$(aws cognito-idp create-user-pool \
    --pool-name "$POOL_NAME" \
    --auto-verified-attributes email \
    --region "$REGION" \
    --query 'UserPool.Id' --output text)
  echo "created user pool : $POOL_ID"
else
  echo "reusing user pool : $POOL_ID"
fi

# --- app client ------------------------------------------------------------
CLIENT_ID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
  --max-results 60 --region "$REGION" \
  --query "UserPoolClients[?ClientName=='${CLIENT_NAME}'].ClientId | [0]" --output text)

if [ "$CLIENT_ID" = "None" ] || [ -z "$CLIENT_ID" ]; then
  CLIENT_ID=$(aws cognito-idp create-user-pool-client \
    --user-pool-id "$POOL_ID" \
    --client-name "$CLIENT_NAME" \
    --generate-secret \
    --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --region "$REGION" \
    --query 'UserPoolClient.ClientId' --output text)
  echo "created app client: $CLIENT_ID"
else
  echo "reusing app client: $CLIENT_ID"
fi

CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" --region "$REGION" \
  --query 'UserPoolClient.ClientSecret' --output text)

ISSUER="https://cognito-idp.${REGION}.amazonaws.com/${POOL_ID}"

# --- write .env (gitignored); secret is never echoed ------------------------
upsert_env AWS_REGION "$REGION"
upsert_env COGNITO_USER_POOL_ID "$POOL_ID"
upsert_env COGNITO_CLIENT_ID "$CLIENT_ID"
upsert_env COGNITO_CLIENT_SECRET "$CLIENT_SECRET"
upsert_env COGNITO_ISSUER "$ISSUER"

echo "wrote 5 values to $ENV_FILE (client secret redacted: ${#CLIENT_SECRET} chars)"
echo
echo "Gateway authorizer settings:"
echo "  --authorizer-type  CUSTOM_JWT"
echo "  --discovery-url    ${ISSUER}/.well-known/openid-configuration"
echo "  --allowed-audience ${CLIENT_ID}"
