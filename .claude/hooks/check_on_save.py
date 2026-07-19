"""Claude Code PostToolUse hook: lint + test after edits under mcp_servers/ or agents/.

Claude Code has no literal "file save" event; the equivalent is PostToolUse after
a Write/Edit/MultiEdit. This reads the tool event on stdin, and when the edited
file is a .py under a watched directory, runs `ruff check` and `uv run pytest -q`.
Exit 2 surfaces any failure back to Claude (CLAUDE.md hard rule: tests stay green).
"""

from __future__ import annotations

import json
import subprocess
import sys

WATCHED_DIRS = ("mcp_servers", "agents")

CHECKS = (
    ["uv", "run", "ruff", "check", "."],
    ["uv", "run", "pytest", "-q"],
)


def _edited_path(event: dict) -> str:
    return event.get("tool_input", {}).get("file_path", "") or ""


def _is_watched(file_path: str) -> bool:
    """True for a .py file with a watched directory as one of its path segments.

    Matches on path segments (not substrings) and normalizes separators, so it
    works for native Windows or POSIX paths, absolute or relative.
    """
    if not file_path.endswith(".py"):
        return False
    segments = file_path.replace("\\", "/").split("/")
    return any(watched in segments for watched in WATCHED_DIRS)


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if not _is_watched(_edited_path(event)):
        return 0

    failures: list[str] = []
    for cmd in CHECKS:
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
        if proc.returncode != 0:
            failures.append(f"$ {' '.join(cmd)}\n{proc.stdout}{proc.stderr}".rstrip())

    if failures:
        print("\n\n".join(failures), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
