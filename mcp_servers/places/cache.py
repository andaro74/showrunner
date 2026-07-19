"""Shared response cache + User-Agent helper for OSM calls.

Public OSM endpoints are rate-limited: every request carries a descriptive
User-Agent and responses are cached (CLAUDE.md hard rule #4).
Implemented in build-order step 4.
"""
