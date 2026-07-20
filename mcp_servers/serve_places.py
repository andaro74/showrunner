"""Standalone entry file for the places MCP server (AgentCore Runtime, BYO).

See `serve_tvmaze.py` — AgentCore runs an entry *file*, so the repo root has to
be put on `sys.path` before the package imports resolve.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_servers.places.server import mcp  # noqa: E402
from mcp_servers.runtime import DEPLOYED_TRANSPORT, run_server  # noqa: E402

if __name__ == "__main__":
    # This file is the AgentCore Runtime entrypoint, which is reached over HTTP.
    # stdio here would bind no port and the gateway target would fail to stabilize.
    run_server(mcp, default=DEPLOYED_TRANSPORT)
