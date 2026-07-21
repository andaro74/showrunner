"""Templating for deployment-instance identifiers — keeps them out of git.

`agentcore/agentcore.json` and the Cedar permits carry values specific to one
deployed stack: the AWS account, the Cognito pool and app clients, the gateway
id. None are secrets, but together they inventory live infrastructure, so the
tracked copies hold `${PLACEHOLDER}`s and the real manifest is generated.

    uv run scripts/config.py scrub    # real manifest -> template + local-config
    uv run scripts/config.py render   # template + local-config -> real manifest

**Tracked** (safe to publish): `agentcore/agentcore.template.json`, and
`policies/**/*.cedar` in placeholder form — the permits stay readable, which is
the point of publishing them.

**Generated / local-only** (gitignored): `agentcore/agentcore.json`, the
substituted permits under `agentcore/.rendered/`, and `agentcore/local-config.json`
holding the real values.

The CLI owns `agentcore.json` — `agentcore add …` writes to it. So the loop is
render → work → `scrub` before committing, or CLI edits are lost on next render.
`scrub` verifies it round-trips and refuses to write a template that would not
render back to the exact bytes it read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MANIFEST = ROOT / "agentcore" / "agentcore.json"
TEMPLATE = ROOT / "agentcore" / "agentcore.template.json"
LOCAL_CONFIG = ROOT / "agentcore" / "local-config.json"
POLICIES_DIR = ROOT / "policies"
RENDERED_DIR = ROOT / "agentcore" / ".rendered" / "policies"

# Required in every local-config.json. `scrub` also emits one `RUNTIME_ID_<NAME>`
# per deployed runtime; those are discovered, so they are not listed here.
KEYS = (
    "AWS_ACCOUNT_ID",
    "AWS_REGION",
    "COGNITO_USER_POOL_ID",
    "COGNITO_USER_CLIENT_ID",
    "COGNITO_M2M_CLIENT_ID",
    "GATEWAY_ID",
)

_ACCOUNT_RE = re.compile(r"\b(\d{12})\b")
_POOL_RE = re.compile(r"\b([a-z]{2}-[a-z]+-\d_[A-Za-z0-9]+)\b")
_GATEWAY_RE = re.compile(r"gateway/([A-Za-z0-9][A-Za-z0-9-]*)")
# Runtime ids appear inside the gateway targets' URL-encoded ARNs (`runtime%2F…`).
# Each carries an AWS-assigned random suffix, so they are per-deployment values.
_RUNTIME_RE = re.compile(r"runtime(?:%2F|/)([A-Za-z0-9]+_[A-Za-z0-9]+-[A-Za-z0-9]+)")


class ConfigError(RuntimeError):
    """A detection or substitution step could not complete safely."""


# --- value detection -------------------------------------------------------


def detect_values(manifest: dict) -> dict[str, str]:
    """Pull the instance-specific values out of a real manifest.

    Structural where it matters: the gateway's `allowedClients` is the
    user-facing app client, and any *other* client id is the machine-to-machine
    one the gateway uses to reach the MCP runtimes. Pattern-matching alone
    cannot tell those two apart — they are both opaque 26-char ids.
    """
    blob = json.dumps(manifest)

    account = _sole_match(_ACCOUNT_RE, blob, "AWS account id")
    pool = _sole_match(_POOL_RE, blob, "Cognito user pool id")
    gateway = _sole_match(_GATEWAY_RE, blob, "gateway id")

    gateways = manifest.get("agentCoreGateways") or []
    if not gateways:
        raise ConfigError("no agentCoreGateways in manifest — cannot identify the user client")
    user_client = _allowed_clients(gateways[0])
    if len(user_client) != 1:
        raise ConfigError(f"expected exactly 1 gateway allowedClient, found {len(user_client)}")
    user_client = user_client[0]

    others = {
        c
        for runtime in manifest.get("runtimes") or []
        for c in _allowed_clients(runtime)
        if c != user_client
    }
    if len(others) != 1:
        raise ConfigError(f"expected exactly 1 non-gateway (M2M) client id, found {sorted(others)}")

    values = {
        "AWS_ACCOUNT_ID": account,
        # Region is derived from the pool id, which always carries its region
        # prefix — more reliable than scraping one of several ARN shapes.
        "AWS_REGION": pool.split("_", 1)[0],
        "COGNITO_USER_POOL_ID": pool,
        "COGNITO_USER_CLIENT_ID": user_client,
        "COGNITO_M2M_CLIENT_ID": others.pop(),
        "GATEWAY_ID": gateway,
    }
    values.update(_runtime_ids(blob))
    return values


def _runtime_ids(blob: str) -> dict[str, str]:
    """Map each runtime id to a `RUNTIME_ID_<NAME>` placeholder.

    `myProject_TvmazeMcp-A1B2C3D4E5` -> `RUNTIME_ID_TVMAZEMCP`. Keyed
    on the runtime's own name so the placeholder stays stable across redeploys,
    which change only the AWS-assigned suffix.
    """
    found: dict[str, str] = {}
    for runtime_id in sorted(set(_RUNTIME_RE.findall(blob))):
        name = runtime_id.split("_", 1)[1].split("-", 1)[0].upper()
        key = f"RUNTIME_ID_{name}"
        if found.get(key, runtime_id) != runtime_id:
            raise ConfigError(f"two runtimes collapse to {key}: {found[key]} and {runtime_id}")
        found[key] = runtime_id
    return found


def _allowed_clients(node: dict) -> list[str]:
    cfg = (node.get("authorizerConfiguration") or {}).get("customJwtAuthorizer") or {}
    return list(cfg.get("allowedClients") or [])


def _sole_match(pattern: re.Pattern[str], blob: str, label: str) -> str:
    found = set(pattern.findall(blob))
    if len(found) != 1:
        raise ConfigError(f"expected exactly 1 {label}, found {sorted(found) or 'none'}")
    return found.pop()


# --- substitution ----------------------------------------------------------


def to_template(text: str, values: dict[str, str]) -> str:
    """Real values -> `${PLACEHOLDER}`s.

    Longest value first: the region (`us-west-2`) is a substring of the pool id
    (`us-west-2_AbC…`), so replacing it first would corrupt the pool id.
    """
    for key in sorted(values, key=lambda k: len(values[k]), reverse=True):
        text = text.replace(values[key], "${" + key + "}")
    return text


def to_real(text: str, values: dict[str, str]) -> str:
    """`${PLACEHOLDER}`s -> real values. Unknown placeholders are an error.

    The character class must include digits: `COGNITO_M2M_CLIENT_ID` has one,
    and a name-only class would skip it here and leave it literal in the output.
    """
    missing = {m for m in re.findall(r"\$\{([A-Z0-9_]+)\}", text) if m not in values}
    if missing:
        raise ConfigError(f"no value for placeholder(s): {sorted(missing)}")
    for key, value in values.items():
        text = text.replace("${" + key + "}", value)
    return text


def _policy_files() -> list[Path]:
    return sorted(POLICIES_DIR.rglob("*.cedar"))


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"missing {path.relative_to(ROOT)} — see scripts/config.py docstring")
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# --- commands --------------------------------------------------------------


def scrub() -> None:
    """Real manifest + permits -> tracked template, local-only values."""
    raw = MANIFEST.read_text(encoding="utf-8")

    # `render` repoints each sourceFile into the gitignored .rendered/ tree.
    # Normalise it back to the tracked path first, or the template would carry a
    # generated path that the next `render` cannot resolve.
    rendered_prefix = RENDERED_DIR.relative_to(ROOT).as_posix() + "/"
    canonical_prefix = POLICIES_DIR.relative_to(ROOT).as_posix() + "/"
    raw = raw.replace(rendered_prefix, canonical_prefix)

    values = detect_values(json.loads(raw))

    templated = to_template(raw, values)
    if to_real(templated, values) != raw:
        raise ConfigError("scrub does not round-trip — refusing to write a lossy template")

    _write(TEMPLATE, templated)
    _write(LOCAL_CONFIG, json.dumps(values, indent=2) + "\n")
    for policy in _policy_files():
        _write(policy, to_template(policy.read_text(encoding="utf-8"), values))

    print(f"scrubbed -> {TEMPLATE.relative_to(ROOT)}")
    print(f"values   -> {LOCAL_CONFIG.relative_to(ROOT)} (gitignored)")
    print(f"permits  -> {len(_policy_files())} .cedar file(s) in placeholder form")


def render() -> None:
    """Tracked template + local values -> real manifest the CLI can deploy."""
    values = _read_json(LOCAL_CONFIG)
    if missing := [k for k in KEYS if not values.get(k)]:
        raise ConfigError(f"local-config.json is missing: {missing}")

    if not TEMPLATE.exists():
        raise ConfigError(f"missing {TEMPLATE.relative_to(ROOT)} — run `scrub` first")
    manifest = json.loads(to_real(TEMPLATE.read_text(encoding="utf-8"), values))

    # The .cedar files are the source of truth for policy text: substitute each
    # one, write it where the deployed manifest can point at it, and re-sync the
    # inline `statement` so an edited permit cannot silently disagree with the
    # copy that actually ships.
    by_source = {}
    for policy in _policy_files():
        rendered = to_real(policy.read_text(encoding="utf-8"), values)
        out = RENDERED_DIR / policy.relative_to(POLICIES_DIR)
        _write(out, rendered)
        by_source[policy.relative_to(ROOT).as_posix()] = (rendered, out)

    for engine in manifest.get("policyEngines") or []:
        for policy in engine.get("policies") or []:
            source = policy.get("sourceFile")
            if source not in by_source:
                raise ConfigError(f"policy {policy.get('name')!r} has unknown sourceFile {source!r}")
            rendered, out = by_source[source]
            policy["statement"] = rendered.strip()
            policy["sourceFile"] = out.relative_to(ROOT).as_posix()

    _write(MANIFEST, json.dumps(manifest, indent=2) + "\n")
    print(f"rendered -> {MANIFEST.relative_to(ROOT)}")
    print(f"permits  -> {len(by_source)} file(s) under {RENDERED_DIR.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("render", "scrub"))
    args = parser.parse_args(argv)
    try:
        {"render": render, "scrub": scrub}[args.command]()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
