"""Tests for tvmaze_client mappings.

Pure unit tests over the payload->TypedDict mappers, using the REAL response
shapes from api.tvmaze.com. The schedule mapper earned this file: it read the
show from `_embedded.show` while /schedule nests it at top level, so every
deployed schedule entry had an empty show name — and only a live agent noticed.
"""

from mcp_servers.tvmaze.tvmaze_client import _scheduled_episode

# The actual /schedule shape: show nested at TOP LEVEL, not under _embedded.
REAL_SCHEDULE_ENTRY = {
    "id": 3457933,
    "name": "Episode 139",
    "season": 2026,
    "number": 139,
    "airtime": "05:00",
    "show": {
        "id": 24605,
        "name": "Way Too Early",
        "network": {"name": "MSNBC"},
    },
}

# The shape some other endpoints use — kept working as a fallback.
EMBEDDED_ENTRY = {
    "name": "Pilot",
    "season": 1,
    "number": 1,
    "airtime": "21:00",
    "_embedded": {"show": {"name": "Breaking Bad", "network": {"name": "AMC"}}},
}


def test_schedule_reads_show_from_top_level():
    ep = _scheduled_episode(REAL_SCHEDULE_ENTRY)
    assert ep["show"] == "Way Too Early"
    assert ep["network"] == "MSNBC"
    assert ep["episode"] == "Episode 139"
    assert ep["airtime"] == "05:00"


def test_schedule_falls_back_to_embedded_show():
    ep = _scheduled_episode(EMBEDDED_ENTRY)
    assert ep["show"] == "Breaking Bad"
    assert ep["network"] == "AMC"


def test_schedule_tolerates_missing_show():
    ep = _scheduled_episode({"name": "Orphan", "airtime": "20:00"})
    assert ep["show"] == ""
    assert ep["network"] is None
