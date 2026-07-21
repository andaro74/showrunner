// Permits TvmazeMcpTarget -> search_shows through the gateway.
// One statement per file: CreatePolicy accepts exactly one.

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"TvmazeMcpTarget___search_shows",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:${AWS_REGION}:${AWS_ACCOUNT_ID}:gateway/${GATEWAY_ID}"
);
