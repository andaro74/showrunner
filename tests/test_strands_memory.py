"""Tests for the AgentCore Memory wiring — PROJECT.md Phase 11.

Pure unit tests against a fake session manager: no AWS, no network. Covers
per-actor namespace scoping, the enable/disable switch, actor resolution from
Identity's JWT `sub`, and that memory failures never break a turn.
"""

import base64
import json

from agents.strands import memory_config
from agents.strands.agent import recall, remember, resolve_actor_id


def _jwt(claims: dict) -> str:
    """Minimal unsigned JWT — only the payload segment matters here."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class FakeManager:
    """Stands in for MemorySessionManager, recording calls."""

    def __init__(self, turns=None, records=None, raises=False):
        self._turns = turns or []
        self._records = records if records is not None else []
        self._raises = raises
        self.searched_namespaces = []
        self.added = None

    def get_last_k_turns(self, actor_id, session_id, k):
        if self._raises:
            raise RuntimeError("memory unavailable")
        return self._turns

    def search_long_term_memories(self, query, namespace_prefix, top_k):
        if self._raises:
            raise RuntimeError("memory unavailable")
        self.searched_namespaces.append(namespace_prefix)
        return self._records

    def add_turns(self, actor_id, session_id, messages):
        if self._raises:
            raise RuntimeError("memory unavailable")
        self.added = (actor_id, session_id, messages)


# --- namespaces -----------------------------------------------------------


def test_namespaces_are_scoped_per_actor():
    prefs_a = memory_config.namespace_for(memory_config.GENRE_PREFERENCES, "user-a")
    prefs_b = memory_config.namespace_for(memory_config.GENRE_PREFERENCES, "user-b")
    picks_a = memory_config.namespace_for(memory_config.REMEMBERED_PICKS, "user-a")

    # Paths must match the manifest's namespaceTemplates (see memory_config).
    assert prefs_a == "/users/user-a/preferences"
    assert picks_a == "/users/user-a/facts"
    assert prefs_a != prefs_b  # one user can never read another's records


def test_every_namespace_has_a_strategy():
    assert set(memory_config.NAMESPACE_STRATEGIES) == {
        memory_config.GENRE_PREFERENCES,
        memory_config.REMEMBERED_PICKS,
    }


# --- enable / disable -----------------------------------------------------


def test_memory_disabled_without_env(monkeypatch):
    monkeypatch.delenv(memory_config.MEMORY_ID_ENV, raising=False)
    assert memory_config.is_enabled() is False
    assert memory_config.build_session_manager() is None


def test_memory_enabled_with_env(monkeypatch):
    monkeypatch.setenv(memory_config.MEMORY_ID_ENV, "mem-123")
    built = {}

    def fake_manager(memory_id, region_name):
        built["args"] = (memory_id, region_name)
        return "manager"

    monkeypatch.setattr(memory_config, "MemorySessionManager", fake_manager)
    monkeypatch.setenv(memory_config.REGION_ENV, "eu-west-1")

    assert memory_config.is_enabled() is True
    assert memory_config.build_session_manager() == "manager"
    assert built["args"] == ("mem-123", "eu-west-1")


# --- actor resolution (Identity) ------------------------------------------


def test_actor_id_prefers_jwt_subject():
    context = type(
        "Ctx", (), {"request_headers": {"Authorization": f"Bearer {_jwt({'sub': 'u-42'})}"}}
    )
    assert resolve_actor_id({"actor_id": "ignored"}, context) == "u-42"


def test_actor_id_falls_back_to_payload_then_default():
    assert resolve_actor_id({"actor_id": "explicit"}, None) == "explicit"
    assert resolve_actor_id({}, None) == memory_config.DEFAULT_ACTOR_ID


def test_malformed_token_falls_back():
    context = type("Ctx", (), {"request_headers": {"Authorization": "Bearer not-a-jwt"}})
    assert resolve_actor_id({}, context) == memory_config.DEFAULT_ACTOR_ID


# --- recall / remember ----------------------------------------------------


def test_recall_builds_context_from_both_tiers():
    manager = FakeManager(
        turns=[[{"content": {"text": "earlier question"}}]],
        records=[{"content": {"text": "likes sci-fi"}}],
    )
    block = recall(manager, "u-1", "s-1", "what's on tonight?")

    assert "earlier question" in block
    assert "likes sci-fi" in block
    assert manager.searched_namespaces == [
        "/users/u-1/preferences",
        "/users/u-1/facts",
    ]


def test_recall_is_empty_when_memory_fails():
    assert recall(FakeManager(raises=True), "u-1", "s-1", "prompt") == ""


def test_remember_persists_user_and_assistant_turn():
    manager = FakeManager()
    remember(manager, "u-1", "s-1", "the question", "the answer")

    actor_id, session_id, messages = manager.added
    assert (actor_id, session_id) == ("u-1", "s-1")
    assert [m.text for m in messages] == ["the question", "the answer"]


def test_remember_swallows_failures():
    remember(FakeManager(raises=True), "u-1", "s-1", "q", "a")  # must not raise
