"""Shared User-Agent + response cache for the OSM clients.

Public OSM endpoints (Nominatim/Overpass/OSRM) are rate-limited and block
requests without a descriptive User-Agent (CLAUDE.md hard rule #4). Every
client sends `user_agent()` on each request and caches responses through a
`ResponseCache`, so repeated lookups don't re-hit the upstream service.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TypeVar

import httpx

# Overridable via OSM_USER_AGENT in .env (see .env.example).
DEFAULT_USER_AGENT = "showrunner/0.1 (+https://github.com/andaro74/showrunner)"

_TIMEOUT = httpx.Timeout(30.0)

T = TypeVar("T")


def user_agent() -> str:
    """Descriptive User-Agent for OSM requests; from OSM_USER_AGENT or a default."""
    return os.environ.get("OSM_USER_AGENT") or DEFAULT_USER_AGENT


def build_client(base_url: str, client: httpx.Client | None = None) -> httpx.Client:
    """Return the injected client (tests) or a new one with the User-Agent header set."""
    if client is not None:
        return client
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=_TIMEOUT,
        headers={"User-Agent": user_agent()},
    )


class ResponseCache:
    """Tiny in-memory cache keyed by an arbitrary request signature."""

    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    def get_or_set(self, key: str, producer: Callable[[], T]) -> T:
        if key in self._store:
            return self._store[key]  # type: ignore[return-value]
        value = producer()
        self._store[key] = value
        return value

    def clear(self) -> None:
        self._store.clear()
