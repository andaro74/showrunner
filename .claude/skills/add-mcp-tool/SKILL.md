---
name: add-mcp-tool
description: Add a new tool to an existing FastMCP server in this repo (define client method, register tool, add pytest, update the server's SKILL.md).
---

# add-mcp-tool

Recipe for adding a tool to an existing FastMCP server under `mcp_servers/`.
Fleshed out in build-order step 8 (PROJECT.md Phase 9). Placeholder for now.

## Steps

1. Add the client method in the relevant `*_client.py` (thin: HTTP + typed parsing only).
2. Register the tool in that server's `server.py`.
3. Add a pytest under `tests/`.
4. Add a one-line entry to the server's `SKILL.md`.

## Gotchas

- Forgetting the descriptive **User-Agent** header on Nominatim/Overpass/OSRM calls.
- Not handling **empty Overpass results**.
- Leaking **Strands/LangChain imports** into `mcp_servers/` (breaks framework-agnosticism).
