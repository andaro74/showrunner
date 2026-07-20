#!/usr/bin/env bash
# Provision the Cognito machine-to-machine credentials the Gateway uses to call
# the MCP runtimes.
#
# Why this exists: a gateway `mcp-server` target REQUIRES outbound auth — the CLI
# rejects even `--outbound-auth none` without a credential. The gateway therefore
# needs its own OAuth client_credentials token to reach the runtimes, which is a
# different grant from the user-facing client created by create_cognito.sh:
#
#   create_cognito.sh      user  -> Gateway     (USER_PASSWORD_AUTH, inbound)
#   create_cognito_m2m.sh  Gateway -> Runtimes  (CLIENT_CREDENTIALS, outbound)
#
# client_credentials needs three things a plain user pool does not have: a hosted
# domain (to expose /oauth2/token), a resource server (to own a custom scope),
# and a client with the client_credentials grant enabled.
#
# Idempotent: re-running reuses existing resources. Secrets go to .env, never stdout.
#
# Usage:  bash scripts/create_cognito_m2m.sh [region]

set -euo pipefail

REGION="${1:-${AWS_REGION:-us-west-2}}"
POOL_NAME="showrunner-users"
M2M_CLIENT_NAME="showrunner-gateway-m2m"
RESOURCE_SERVER_ID="showrunner"
SCOPE_NAME="invoke"
ENV_FILE=".env"

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }

upsert_env() {
  local key="$1" value="$2"
  touch "$ENV_FILE"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    grep -v "^${key}=" "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

echo "region: $REGION"

POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" \
  --query "UserPools[?Name=='${POOL_NAME}'].Id | [0]" --output text)
[ "$POOL_ID" != "None" ] && [ -n "$POOL_ID" ] || {
  echo "user pool '${POOL_NAME}' not found - run scripts/create_cognito.sh first" >&2; exit 1; }
echo "user pool        : $POOL_ID"

# --- hosted domain ---------------------------------------------------------
# Must be globally unique across all AWS accounts, so it is suffixed with the
# account id. This is what serves https://<domain>.auth.<region>.amazoncognito.com/oauth2/token
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
DOMAIN="showrunner-${ACCOUNT_ID}"

EXISTING_DOMAIN=$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" \
  --region "$REGION" --query 'UserPool.Domain' --output text)

if [ "$EXISTING_DOMAIN" = "None" ] || [ -z "$EXISTING_DOMAIN" ]; then
  aws cognito-idp create-user-pool-domain \
    --domain "$DOMAIN" --user-pool-id "$POOL_ID" --region "$REGION" >/dev/null
  echo "created domain   : $DOMAIN"
else
  DOMAIN="$EXISTING_DOMAIN"
  echo "reusing domain   : $DOMAIN"
fi

# --- resource server (owns the custom scope) -------------------------------
# The scope is what the runtime's CUSTOM_JWT authorizer checks via --allowed-scopes.
# Cognito's built-in aws.cognito.signin.user.admin is a user scope and cannot be
# used here; client_credentials tokens only ever carry custom scopes.
if ! aws cognito-idp describe-resource-server --user-pool-id "$POOL_ID" \
      --identifier "$RESOURCE_SERVER_ID" --region "$REGION" >/dev/null 2>&1; then
  aws cognito-idp create-resource-server \
    --user-pool-id "$POOL_ID" \
    --identifier "$RESOURCE_SERVER_ID" \
    --name "ShowRunner MCP runtimes" \
    --scopes "ScopeName=${SCOPE_NAME},ScopeDescription=Invoke the ShowRunner MCP runtimes" \
    --region "$REGION" >/dev/null
  echo "created resource server: ${RESOURCE_SERVER_ID}"
else
  echo "reusing resource server: ${RESOURCE_SERVER_ID}"
fi

FULL_SCOPE="${RESOURCE_SERVER_ID}/${SCOPE_NAME}"

# --- machine client (client_credentials) -----------------------------------
M2M_CLIENT_ID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
  --max-results 60 --region "$REGION" \
  --query "UserPoolClients[?ClientName=='${M2M_CLIENT_NAME}'].ClientId | [0]" --output text)

if [ "$M2M_CLIENT_ID" = "None" ] || [ -z "$M2M_CLIENT_ID" ]; then
  M2M_CLIENT_ID=$(aws cognito-idp create-user-pool-client \
    --user-pool-id "$POOL_ID" \
    --client-name "$M2M_CLIENT_NAME" \
    --generate-secret \
    --allowed-o-auth-flows client_credentials \
    --allowed-o-auth-scopes "$FULL_SCOPE" \
    --allowed-o-auth-flows-user-pool-client \
    --region "$REGION" \
    --query 'UserPoolClient.ClientId' --output text)
  echo "created m2m client: $M2M_CLIENT_ID"
else
  echo "reusing m2m client: $M2M_CLIENT_ID"
fi

M2M_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "$POOL_ID" --client-id "$M2M_CLIENT_ID" --region "$REGION" \
  --query 'UserPoolClient.ClientSecret' --output text)

ISSUER="https://cognito-idp.${REGION}.amazonaws.com/${POOL_ID}"
TOKEN_URL="https://${DOMAIN}.auth.${REGION}.amazoncognito.com/oauth2/token"

upsert_env COGNITO_DOMAIN "$DOMAIN"
upsert_env COGNITO_M2M_CLIENT_ID "$M2M_CLIENT_ID"
upsert_env COGNITO_M2M_CLIENT_SECRET "$M2M_CLIENT_SECRET"
upsert_env COGNITO_M2M_SCOPE "$FULL_SCOPE"
upsert_env COGNITO_TOKEN_URL "$TOKEN_URL"

echo "wrote 5 values to $ENV_FILE (m2m secret redacted: ${#M2M_CLIENT_SECRET} chars)"
echo
echo "Gateway target outbound auth:"
echo "  --outbound-auth        oauth"
echo "  --oauth-client-id      ${M2M_CLIENT_ID}"
echo "  --oauth-discovery-url  ${ISSUER}/.well-known/openid-configuration"
echo "  --oauth-scopes         ${FULL_SCOPE}"
echo
echo "Runtime inbound authorizer must accept this client:"
echo "  --allowed-clients      ${M2M_CLIENT_ID}"
echo "  --allowed-scopes       ${FULL_SCOPE}"
