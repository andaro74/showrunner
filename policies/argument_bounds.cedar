// ShowRunner — argument-level bounds (Cedar)
//
// Tool-name allow-listing (showrunner_tools.cedar) answers "may this caller run
// this tool at all". These rules narrow *how* an approved tool may be called, by
// testing the call arguments exposed as `context.input.<argName>` — the keys come
// from the tool's own input schema.
//
// `forbid` overrides `permit`, so these clip the permits without replacing them.
//
// NOTE on naming: in AgentCore, "guardrails" means the Bedrock content-filter
// construct (`when guardrails { BedrockGuardrails::... }`) for prompt-injection and
// sensitive-info screening. That is a separate feature; these are plain argument
// conditions.

// A huge radius turns one call into an expensive Overpass query, and the public
// OSM endpoints are rate-limited (CLAUDE.md hard rule #4). find_nearby defaults to
// 2000 m; 5000 m is a generous ceiling for planning a night out.
forbid(
  principal,
  action == AgentCore::Action::"PlacesTarget___find_nearby",
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
) when {
  context.input has radius && context.input.radius > 5000
};
