// Permits PlacesMcpTarget -> find_nearby through the gateway.
// One statement per file: CreatePolicy accepts exactly one.

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"PlacesMcpTarget___find_nearby",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/myproject-my-gateway-example01"
);
