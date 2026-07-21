"""ShowRunner orchestrator — the central agent point.

Composes the two domain specialists as tools (agents-as-tools): the Strands show
specialist owns the tvmaze MCP server, the LangGraph places specialist owns the
places MCP server, and this agent routes each sub-question to the right one and
assembles the movie-night plan.

The user-facing concerns live here, not in the specialists: the
`BedrockAgentCoreApp` entrypoint, AgentCore Memory (session recall + long-term
preferences), and identity (actor scoping from the inbound JWT `sub`). The
specialists stay plain domain agents that can be tested — and swapped between
frameworks — in isolation.

The places delegate is `async def` on purpose: Strands awaits coroutine tools on
its own event loop (strands.tools.decorator handles `iscoroutinefunction`), which
lets the LangGraph specialist's async `invoke` compose without a nested
`asyncio.run`.
"""

from __future__ import annotations

import base64
import contextvars
import json
import os

from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool

from agents.langgraph import agent as places_specialist
from agents.orchestrator import memory_config
from agents.orchestrator.prompts import SYSTEM_PROMPT
from agents.strands import agent as show_specialist

app = BedrockAgentCoreApp()

# The caller's bearer token for the CURRENT request, set by the entrypoint and
# read by the delegates, which forward it to the specialists so the Gateway's
# Cedar policies evaluate the real user — not a shared service identity. A
# contextvar (not a global) so concurrent requests can't read each other's
# token; it propagates into Strands' worker thread and event loop because
# Python 3.14's ThreadPoolExecutor runs tasks under the submitting context.
_caller_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "showrunner_caller_token", default=None
)


# --- specialist delegates (agents-as-tools) --------------------------------


def ask_show_expert(question: str) -> str:
    """Ask the TV-show specialist about shows: what's airing tonight, episode
    lists, or who's in the cast. Phrase a complete, standalone question."""
    return show_specialist.answer(question, bearer_token=_caller_token.get())


async def ask_places_expert(question: str) -> str:
    """Ask the places specialist about locations: nearby cinemas, food stops,
    or travel times. Include the user's location in the question."""
    return await places_specialist.answer(question, bearer_token=_caller_token.get())


def build_agent() -> Agent:
    """Assemble the orchestrator over the two specialist delegates.

    Model id comes from BEDROCK_MODEL_ID when set, else Strands' Bedrock default.
    """
    return Agent(
        model=os.environ.get("BEDROCK_MODEL_ID"),
        system_prompt=SYSTEM_PROMPT,
        tools=[tool(ask_show_expert), tool(ask_places_expert)],
        # No streaming printer: the reply is returned from the entrypoint, and the
        # default PrintingCallbackHandler crashes on emoji under cp1252 consoles
        # (and interleaves garbage when both agents stream to one stdout).
        callback_handler=None,
    )


# --- identity (inbound JWT -> actor) ---------------------------------------


def resolve_actor_id(payload: dict, context: object | None = None) -> str:
    """Identify the caller, preferring Identity's inbound JWT `sub` claim.

    The JWT is *verified upstream* by the gateway's CUSTOM_JWT authorizer, so we
    only decode the claims here — never trust this token without that authorizer.

    Identity comes from the token *alone* whenever one is present: a token that
    carries no usable `sub` falls back to the anonymous default, never to the
    payload's actor_id. Otherwise a caller could send a parseable token without a
    `sub` plus someone else's id in the body and read their memory namespace.
    The payload actor_id is honoured only for tokenless local dev.
    """
    headers = getattr(context, "request_headers", None) or {}
    token = _bearer_token(headers)
    if token:
        return _jwt_subject(token) or memory_config.DEFAULT_ACTOR_ID
    return payload.get("actor_id") or memory_config.DEFAULT_ACTOR_ID


def _bearer_token(headers: dict) -> str | None:
    for key, value in headers.items():
        if key.lower() == "authorization" and str(value).lower().startswith("bearer "):
            return str(value).split(" ", 1)[1].strip()
    return None


def _jwt_subject(token: str) -> str | None:
    """Read the `sub` claim from a JWT payload segment (no signature check)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    segment = parts[1]
    padded = segment + "=" * (-len(segment) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError, json.JSONDecodeError:
        return None
    return claims.get("sub")


# --- memory (best-effort: never breaks a turn) -----------------------------


def _record_text(record: object) -> str:
    """Pull display text out of a dict-like MemoryRecord/EventMessage."""
    getter = getattr(record, "get", None)
    if getter is None:
        return str(record)
    content = getter("content")
    if isinstance(content, dict):
        return str(content.get("text", "")).strip()
    return str(content or "").strip()


def recall(manager, actor_id: str, session_id: str, prompt: str) -> str:
    """Short-term turns + long-term preferences/picks, as a context block.

    Memory must never break a turn, so retrieval failures degrade to no context.
    """
    sections: list[str] = []

    try:
        turns = manager.get_last_k_turns(
            actor_id=actor_id, session_id=session_id, k=memory_config.SHORT_TERM_TURNS
        )
    except Exception:  # noqa: BLE001 - memory is best-effort
        turns = []
    recent = [_record_text(m) for turn in turns for m in turn]
    if any(recent):
        sections.append("Earlier in this session:\n" + "\n".join(t for t in recent if t))

    for label, template in (
        ("Known preferences", memory_config.GENRE_PREFERENCES),
        ("Previously suggested", memory_config.REMEMBERED_PICKS),
    ):
        try:
            records = manager.search_long_term_memories(
                query=prompt,
                namespace_prefix=memory_config.namespace_for(template, actor_id),
                top_k=memory_config.LONG_TERM_TOP_K,
            )
        except Exception:  # noqa: BLE001 - memory is best-effort
            continue
        texts = [t for t in (_record_text(r) for r in records) if t]
        if texts:
            sections.append(f"{label}:\n" + "\n".join(texts))

    return "\n\n".join(sections)


def remember(manager, actor_id: str, session_id: str, prompt: str, reply: str) -> None:
    """Persist this turn as short-term memory; strategies distil it long-term."""
    try:
        manager.add_turns(
            actor_id=actor_id,
            session_id=session_id,
            messages=[
                ConversationalMessage(prompt, MessageRole.USER),
                ConversationalMessage(reply, MessageRole.ASSISTANT),
            ],
        )
    except Exception:  # noqa: BLE001 - memory is best-effort
        return


# --- runtime entrypoint ----------------------------------------------------


@app.entrypoint
def invoke(payload: dict, context: object | None = None) -> dict:
    """Runtime entrypoint: plan a movie night for the prompt in `payload`.

    When AgentCore Memory is configured, the caller's prior session turns and
    long-term preferences are recalled first and the turn is persisted after.
    The specialists open their own MCP connections per delegation, so there is
    no client context to hold here.
    """
    prompt = payload.get("prompt", "")
    actor_id = resolve_actor_id(payload, context)
    session_id = getattr(context, "session_id", None) or payload.get("session_id") or actor_id

    # Make the caller's token available to the delegates for this request.
    headers = getattr(context, "request_headers", None) or {}
    _caller_token.set(_bearer_token(headers))

    manager = memory_config.build_session_manager()
    remembered = recall(manager, actor_id, session_id, prompt) if manager else ""
    turn_input = f"{remembered}\n\n{prompt}".strip() if remembered else prompt

    agent = build_agent()
    result = agent(turn_input)

    reply = str(result)
    if manager:
        remember(manager, actor_id, session_id, prompt, reply)
    return {"result": reply, "actor_id": actor_id, "session_id": session_id}


if __name__ == "__main__":
    app.run()
