"""Standalone entry file for the tvmaze MCP server (AgentCore Runtime, BYO).

AgentCore's BYO deployment runs an entry *file* (`python <entrypoint>`), not a
module (`python -m ...`). Executing a file puts that file's directory on
`sys.path` rather than the repo root, so `import mcp_servers...` would fail —
hence the explicit path fix below.

Local dev and the tests keep using `python -m mcp_servers.tvmaze.server`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_servers.runtime import run_server  # noqa: E402
from mcp_servers.tvmaze.server import mcp  # noqa: E402

if __name__ == "__main__":
    run_server(mcp)
