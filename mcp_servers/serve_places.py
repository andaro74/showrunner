"""Standalone entry file for the places MCP server (AgentCore Runtime, BYO).

See `serve_tvmaze.py` — AgentCore runs an entry *file*, so the repo root has to
be put on `sys.path` before the package imports resolve.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_servers.places.server import mcp  # noqa: E402
from mcp_servers.runtime import run_server  # noqa: E402

if __name__ == "__main__":
    run_server(mcp)
