"""Tests for scripts/config.py — the identifier templating.

Synthetic manifests only: these must pass on a fresh clone, where
`agentcore/local-config.json` and the real manifest do not exist.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import config  # noqa: E402

ACCOUNT = "123456789012"
REGION = "us-west-2"
POOL = f"{REGION}_SlKdFD1Jn"
USER_CLIENT = "exampleuserclientid0000001"
M2M_CLIENT = "examplem2mclientid00000002"
GATEWAY = "myproject-my-gateway-example01"


def _authorizer(client):
    return {
        "authorizerConfiguration": {
            "customJwtAuthorizer": {
                "discoveryUrl": (
                    f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
                    "/.well-known/openid-configuration"
                ),
                "allowedClients": [client],
            }
        }
    }


def _manifest():
    return {
        "runtimes": [
            {"name": "TvmazeMcp", **_authorizer(M2M_CLIENT)},
            {"name": "ShowRunner", **_authorizer(USER_CLIENT)},
        ],
        "agentCoreGateways": [
            {
                "name": "showrunner-gateway",
                "targets": [
                    {
                        "endpoint": (
                            f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
                            f"arn%3Aaws%3Abedrock-agentcore%3A{REGION}%3A{ACCOUNT}%3Aruntime"
                        )
                    }
                ],
                **_authorizer(USER_CLIENT),
            }
        ],
        "policyEngines": [
            {
                "policies": [
                    {
                        "name": "AllowX",
                        "statement": (
                            "permit(resource == AgentCore::Gateway::"
                            f'"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/{GATEWAY}");'
                        ),
                    }
                ]
            }
        ],
    }


EXPECTED = {
    "AWS_ACCOUNT_ID": ACCOUNT,
    "AWS_REGION": REGION,
    "COGNITO_USER_POOL_ID": POOL,
    "COGNITO_USER_CLIENT_ID": USER_CLIENT,
    "COGNITO_M2M_CLIENT_ID": M2M_CLIENT,
    "GATEWAY_ID": GATEWAY,
}


# --- detection -------------------------------------------------------------


def test_detects_every_value():
    assert config.detect_values(_manifest()) == EXPECTED


def test_client_ids_are_told_apart_structurally():
    """Both ids are opaque 26-char strings; only position distinguishes them.

    The gateway's allowedClient is the user-facing app client; the other one is
    the M2M client the gateway uses to reach the MCP runtimes.
    """
    values = config.detect_values(_manifest())
    assert values["COGNITO_USER_CLIENT_ID"] != values["COGNITO_M2M_CLIENT_ID"]
    assert values["COGNITO_USER_CLIENT_ID"] == USER_CLIENT


def test_detects_runtime_ids_by_name():
    """The AWS-assigned suffix changes per deploy; the placeholder must not."""
    manifest = _manifest()
    manifest["agentCoreGateways"][0]["targets"][0]["endpoint"] = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
        f"arn%3Aaws%3Abedrock-agentcore%3A{REGION}%3A{ACCOUNT}%3Aruntime"
        "%2FmyProject_TvmazeMcp-EXAMPLE01/invocations?qualifier=DEFAULT"
    )
    values = config.detect_values(manifest)
    assert values["RUNTIME_ID_TVMAZEMCP"] == "myProject_TvmazeMcp-EXAMPLE01"

    templated = config.to_template(json.dumps(manifest), values)
    assert "IHSHV12CKi" not in templated
    assert "${RUNTIME_ID_TVMAZEMCP}" in templated


def test_ambiguous_account_is_refused():
    manifest = _manifest()
    manifest["runtimes"][0]["note"] = "arn:aws:iam::999999999999:role/Other"
    with pytest.raises(config.ConfigError, match="AWS account id"):
        config.detect_values(manifest)


def test_missing_gateway_is_refused():
    manifest = _manifest()
    manifest["agentCoreGateways"] = []
    with pytest.raises(config.ConfigError):
        config.detect_values(manifest)


# --- substitution ----------------------------------------------------------


def test_round_trips_exactly():
    raw = json.dumps(_manifest(), indent=2)
    templated = config.to_template(raw, EXPECTED)
    assert config.to_real(templated, EXPECTED) == raw


def test_no_real_value_survives_templating():
    templated = config.to_template(json.dumps(_manifest()), EXPECTED)
    for key, value in EXPECTED.items():
        assert value not in templated, f"{key} leaked into the template"


def test_region_inside_pool_id_is_not_corrupted():
    """`us-west-2` is a substring of `us-west-2_EXAMPLE01`.

    Substituting the region first would rewrite the pool id to
    `${AWS_REGION}_SlKdFD1Jn` and lose it — hence longest-value-first.
    """
    templated = config.to_template(json.dumps({"pool": POOL}), EXPECTED)
    assert "${COGNITO_USER_POOL_ID}" in templated
    assert "${AWS_REGION}_" not in templated


def test_placeholder_containing_a_digit_is_validated():
    """`COGNITO_M2M_CLIENT_ID` has a digit — a [A-Z_]+ class would skip it here
    and leave the placeholder literal in the rendered manifest."""
    with pytest.raises(config.ConfigError, match="COGNITO_M2M_CLIENT_ID"):
        config.to_real("client: ${COGNITO_M2M_CLIENT_ID}", {"AWS_REGION": REGION})


def test_unknown_placeholder_is_refused():
    with pytest.raises(config.ConfigError, match="NOPE"):
        config.to_real("${NOPE}", EXPECTED)


# --- the scrub/render cycle ------------------------------------------------


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Repoint the module at a throwaway tree so the cycle can run for real."""
    policies = tmp_path / "policies" / "tools"
    policies.mkdir(parents=True)
    permit = policies / "tvmaze_search_shows.cedar"
    permit.write_text(
        "permit(resource == AgentCore::Gateway::"
        f'"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/{GATEWAY}");\n',
        encoding="utf-8",
    )

    manifest = _manifest()
    manifest["policyEngines"][0]["policies"][0]["sourceFile"] = "policies/tools/" + permit.name
    (tmp_path / "agentcore").mkdir()
    (tmp_path / "agentcore" / "agentcore.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    for name, value in {
        "ROOT": tmp_path,
        "MANIFEST": tmp_path / "agentcore" / "agentcore.json",
        "TEMPLATE": tmp_path / "agentcore" / "agentcore.template.json",
        "LOCAL_CONFIG": tmp_path / "agentcore" / "local-config.json",
        "POLICIES_DIR": tmp_path / "policies",
        "RENDERED_DIR": tmp_path / "agentcore" / ".rendered" / "policies",
    }.items():
        monkeypatch.setattr(config, name, value)
    return tmp_path


def test_scrub_then_render_is_idempotent(sandbox):
    """render repoints sourceFile into .rendered/; scrub must normalise it back.

    Without that, the second render fails with "unknown sourceFile" — the
    template would carry a generated path that no longer resolves.
    """
    config.scrub()
    config.render()
    first = config.MANIFEST.read_text(encoding="utf-8")

    config.scrub()
    config.render()
    assert config.MANIFEST.read_text(encoding="utf-8") == first


def test_scrubbed_permit_and_template_hold_no_real_values(sandbox):
    config.scrub()
    for path in (config.TEMPLATE, config.POLICIES_DIR / "tools" / "tvmaze_search_shows.cedar"):
        text = path.read_text(encoding="utf-8")
        for value in (ACCOUNT, POOL, USER_CLIENT, M2M_CLIENT, GATEWAY):
            assert value not in text


def test_render_without_local_config_fails_loudly(sandbox):
    config.scrub()
    config.LOCAL_CONFIG.unlink()
    with pytest.raises(config.ConfigError, match="local-config"):
        config.render()


# --- the tracked artifacts stay clean --------------------------------------


def test_no_tracked_file_contains_a_real_identifier():
    """The guarantee this whole module exists for: nothing git would publish
    carries a deployment identifier.

    Scans every tracked file, not just the ones templating touches — a stray
    paste into a doc or a test fixture is exactly the leak worth catching.
    AWS_REGION is exempt: `us-west-2` is a public region name and a legitimate
    default throughout the source.

    Skips on a fresh clone, where local-config.json (the only place the real
    values live) is absent by design.
    """
    if not config.LOCAL_CONFIG.exists():
        pytest.skip("no local-config.json — nothing to check against")
    values = json.loads(config.LOCAL_CONFIG.read_text(encoding="utf-8"))
    values.pop("AWS_REGION", None)

    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=config.ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")

    leaks = []
    for name in filter(None, tracked):
        path = config.ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        leaks += [f"{name} <- {key}" for key, value in values.items() if value and value in text]

    assert not leaks, "deployment identifiers in tracked files:\n  " + "\n  ".join(leaks)
