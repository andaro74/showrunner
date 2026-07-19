"""Claude Code PreToolUse hook: block `git commit` that stages a secret.

Fires before the Bash tool runs. If the command is a `git commit`, it scans the
staged changes for (a) a staged `.env`/`.env.*` file, (b) API-key-shaped strings,
and (c) any real value pulled from the local `.env`. Exit 2 blocks the commit and
reports why (CLAUDE.md hard rule #3: no keys or secrets in git).

This guards commits Claude runs. For commits made directly in a terminal, install
the same scan as a native git pre-commit hook.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"aws_secret_access_key\s*=\s*\S+", re.IGNORECASE),
    re.compile(
        r"(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9/+=_-]{16,}",
        re.IGNORECASE,
    ),
)

# Values in .env that are obviously placeholders, not real secrets.
_PLACEHOLDER = re.compile(r"(x{4,}|example|placeholder|your|changeme|<|>)", re.IGNORECASE)


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout  # noqa: S603


def _staged_added_lines() -> str:
    diff = _run(["git", "diff", "--cached", "--unified=0"])
    return "\n".join(
        line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )


def _staged_names() -> list[str]:
    return _run(["git", "diff", "--cached", "--name-only"]).split()


def _env_values() -> list[str]:
    env = Path(".env")
    if not env.exists():
        return []
    values: list[str] = []
    for raw in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        value = line.partition("=")[2].strip().strip("\"'")
        if len(value) >= 8 and not _PLACEHOLDER.search(value):
            values.append(value)
    return values


def _problems() -> list[str]:
    found: list[str] = []

    for name in _staged_names():
        base = name.rsplit("/", 1)[-1]
        if (base == ".env" or base.startswith(".env.")) and base != ".env.example":
            found.append(f"staged env file: {name}")

    added = _staged_added_lines()
    for pattern in SECRET_PATTERNS:
        if pattern.search(added):
            found.append(f"secret-like value matching /{pattern.pattern}/")

    if any(value in added for value in _env_values()):
        found.append("a real value from your local .env appears in the staged diff")

    return found


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    command = event.get("tool_input", {}).get("command", "")
    if "git commit" not in command:
        return 0

    problems = _problems()
    if problems:
        print(
            "BLOCKED: this commit appears to contain secrets:\n- " + "\n- ".join(problems),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
