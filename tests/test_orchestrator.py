"""Tests for the orchestrator — the central agent point.

Pure unit tests: the specialists are monkeypatched, so no MCP subprocesses, no
Bedrock, no network. What matters here is the wiring — the orchestrator exposes
exactly the two delegate tools, and each delegate reaches the right specialist.
"""

from agents.orchestrator import agent as orchestrator

DELEGATE_TOOLS = {"ask_show_expert", "ask_places_expert"}


def test_orchestrator_registers_exactly_the_delegate_tools():
    agent = orchestrator.build_agent()
    assert set(agent.tool_names) == DELEGATE_TOOLS


def test_show_delegate_reaches_the_strands_specialist(monkeypatch):
    asked = []

    def fake_answer(question: str, bearer_token: str | None = None) -> str:
        asked.append(question)
        return "Breaking Bad airs tonight"

    monkeypatch.setattr(orchestrator.show_specialist, "answer", fake_answer)
    reply = orchestrator.ask_show_expert("what's on tonight?")

    assert asked == ["what's on tonight?"]
    assert reply == "Breaking Bad airs tonight"


async def test_places_delegate_reaches_the_langgraph_specialist(monkeypatch):
    asked = []

    async def fake_answer(question: str, bearer_token: str | None = None) -> str:
        asked.append(question)
        return "Cinema at 47.6,-122.3"

    monkeypatch.setattr(orchestrator.places_specialist, "answer", fake_answer)
    reply = await orchestrator.ask_places_expert("cinema near Seattle?")

    assert asked == ["cinema near Seattle?"]
    assert reply == "Cinema at 47.6,-122.3"


def test_entrypoint_returns_reply_and_actor(monkeypatch):
    """The entrypoint flow without memory or a model: agent stubbed, no AWS."""

    class FakeResult:
        def __str__(self) -> str:
            return "the plan"

    class FakeAgent:
        def __call__(self, prompt: str) -> FakeResult:
            self.prompt = prompt
            return FakeResult()

    monkeypatch.setattr(orchestrator, "build_agent", FakeAgent)
    monkeypatch.setattr(orchestrator.memory_config, "build_session_manager", lambda: None)

    out = orchestrator.invoke({"prompt": "plan my night", "actor_id": "user-a"})

    assert out == {"result": "the plan", "actor_id": "user-a", "session_id": "user-a"}
