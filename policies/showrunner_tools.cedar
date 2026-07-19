// ShowRunner — gateway tool allow-list (Cedar)
//
// Cedar is default-deny: any action not permitted below is refused. Each of the
// seven MCP tools is permitted EXPLICITLY rather than via the target action
// group (`action in AgentCore::Action::"TvmazeTarget"`), so that adding a new
// tool to an MCP server does not silently inherit permission — a new tool stays
// denied until someone adds it here on purpose.
//
// Entity model:
//   principal  AgentCore::OAuthUser          <- from the Cognito JWT `sub` claim
//   action     AgentCore::Action::"<Target>___<tool>"   (triple underscore)
//   resource   AgentCore::Gateway::"<arn>"   <- the gateway, not the tool
//
// Replace <GATEWAY_ARN> with the real ARN after `agentcore deploy` (policies are
// validated against the schema generated from the deployed gateway's tools).

// ─── tvmaze target: TV/show lookups (read-only) ───────────────────────────

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"TvmazeTarget___search_shows",
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
);

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"TvmazeTarget___get_schedule",
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
);

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"TvmazeTarget___get_episodes",
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
);

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"TvmazeTarget___get_cast",
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
);

// ─── places target: OpenStreetMap lookups (read-only) ─────────────────────

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"PlacesTarget___geocode",
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
);

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"PlacesTarget___find_nearby",
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
);

permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"PlacesTarget___travel_time",
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
);
