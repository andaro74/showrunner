# identity/

AgentCore **Identity** config — inbound Cognito JWT that scopes memory per real
user (anti-impersonation via the `sub` claim).

Configure Identity **before** Gateway — Gateway relies on Identity's OAuth
provider (CLAUDE.md hard rule #2).

Config lands in build-order step 9 (PROJECT.md Phase 11). Placeholder for now.
