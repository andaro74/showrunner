// ShowRunner - argument-level bounds (Cedar)
//
// ASCII only: the CLI read a previous file as cp1252 and mojibaked its em-dashes
// into the manifest snapshot.
//
// policies/tools/*.cedar answer "may this caller run this tool at all". This rule
// narrows *how* an approved tool may be called, by testing call arguments exposed
// as `context.input.<argName>` - keys come from the tool's own input schema.
//
// `forbid` overrides `permit`, so this clips a permit without replacing it.
//
// NOTE on naming: in AgentCore, "guardrails" means the Bedrock content-filter
// construct (`when guardrails { BedrockGuardrails::... }`) for prompt-injection
// and sensitive-info screening. That is a separate feature; this is a plain
// argument condition.

// A huge radius turns one call into an expensive Overpass query, and the public
// OSM endpoints are rate-limited (CLAUDE.md hard rule #5). find_nearby defaults to
// 2000 m; 5000 m is a generous ceiling for planning a night out.
forbid(
  principal,
  action == AgentCore::Action::"PlacesMcpTarget___find_nearby",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:${AWS_REGION}:${AWS_ACCOUNT_ID}:gateway/${GATEWAY_ID}"
) when {
  context.input has radius && context.input.radius > 5000
};
