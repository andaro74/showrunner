"""Standalone entry file for the ShowRunner orchestrator (AgentCore Runtime, BYO).

This file MUST live at the repo root — not in agents/ like the first attempt.
`python <entrypoint>` puts the entry file's DIRECTORY on sys.path, and
`agents/` contains a package named `langgraph`. The real langgraph library is a
PEP 420 namespace package (no __init__.py), and Python resolves a regular
package found anywhere on sys.path ahead of a namespace package everywhere —
so with agents/ on the path, `import langgraph.types` resolved to OUR
agents/langgraph and the deployed container died at import with
`ModuleNotFoundError: No module named 'langgraph.types'`. At the root, the
script directory IS the repo root: `import agents...` works with no sys.path
surgery, and nothing shadows the vendored libraries.

Unlike the MCP servers (MCP contract: 0.0.0.0:8000/mcp), the orchestrator is an
agent on the HTTP protocol contract — `BedrockAgentCoreApp.run()` natively
serves :8080 with /invocations and /ping, so no port or transport overrides.

Local dev keeps using `python -m agents.orchestrator.agent`.
"""

from agents.orchestrator.agent import app

if __name__ == "__main__":
    app.run()
