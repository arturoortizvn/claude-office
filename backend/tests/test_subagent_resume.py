"""Tests for how a resumed subagent (native SubagentStart after it already left) is shown."""

from datetime import UTC, datetime
from typing import Literal

import pytest

from app.core.handlers import agent_handler
from app.core.handlers.agent_handler import handle_subagent_info
from app.core.state_machine import StateMachine
from app.models.events import AgentEvent, AgentEventData, EventType, ToolEvent, ToolEventData

SESSION = "session-resume"
NATIVE = "a32782ed9d6a87a01"
AgentEventType = Literal[EventType.SUBAGENT_START, EventType.SUBAGENT_INFO, EventType.SUBAGENT_STOP]

TRANSCRIPT = "/nonexistent/subagents/agent-a32782ed9d6a87a01.jsonl"


class _FakePoller:
    def __init__(self) -> None:
        self.polling: set[str] = set()

    async def is_polling(self, agent_id: str) -> bool:
        return agent_id in self.polling

    async def start_polling(self, agent_id: str, session_id: str, path: str) -> None:
        self.polling.add(agent_id)


@pytest.fixture(autouse=True)
def fake_poller(monkeypatch: pytest.MonkeyPatch) -> _FakePoller:
    poller = _FakePoller()
    monkeypatch.setattr(agent_handler, "get_transcript_poller", lambda: poller)
    return poller


def _agent_event(event_type: AgentEventType, data: AgentEventData) -> AgentEvent:
    return AgentEvent(
        event_type=event_type,
        session_id=SESSION,
        timestamp=datetime.now(UTC),
        data=data,
    )


def _start_event(agent_id: str) -> AgentEvent:
    return _agent_event(EventType.SUBAGENT_START, AgentEventData(agent_id=agent_id))


def _stop_event() -> AgentEvent:
    return _agent_event(EventType.SUBAGENT_STOP, AgentEventData(native_agent_id=NATIVE))


def _info_event() -> AgentEvent:
    return _agent_event(
        EventType.SUBAGENT_INFO,
        AgentEventData(
            native_agent_id=NATIVE,
            agent_transcript_path=TRANSCRIPT,
            agent_type="general-purpose",
        ),
    )


def _send_message_event(to: str) -> ToolEvent:
    return ToolEvent(
        event_type=EventType.PRE_TOOL_USE,
        session_id=SESSION,
        timestamp=datetime.now(UTC),
        data=ToolEventData(
            agent_id="main",
            tool_name="SendMessage",
            tool_input={"to": to, "message": "fix round 1"},
        ),
    )


async def _run_agent_to_completion(sm: StateMachine) -> None:
    """Spawn an agent via the Agent tool, link it, and let it finish."""
    sm.transition(_start_event("subagent_toolu_1"))
    await handle_subagent_info(sm, _info_event(), lambda: None)
    assert sm.agents["subagent_toolu_1"].native_id == NATIVE
    sm.transition(_stop_event())
    sm.remove_agent("subagent_toolu_1")
    assert sm.agents == {}


async def test_automatic_resume_of_departed_agent_does_not_spawn_it_again() -> None:
    sm = StateMachine()
    await _run_agent_to_completion(sm)

    await handle_subagent_info(sm, _info_event(), lambda: None)

    assert sm.agents == {}


async def test_resume_requested_by_send_message_brings_the_agent_back() -> None:
    sm = StateMachine()
    await _run_agent_to_completion(sm)

    sm.transition(_send_message_event(NATIVE))
    await handle_subagent_info(sm, _info_event(), lambda: None)

    assert list(sm.agents) == [f"subagent_{NATIVE}"]


async def test_send_message_request_is_consumed_by_one_resume() -> None:
    sm = StateMachine()
    await _run_agent_to_completion(sm)
    sm.transition(_send_message_event(NATIVE))
    await handle_subagent_info(sm, _info_event(), lambda: None)
    sm.transition(_stop_event())
    sm.remove_agent(f"subagent_{NATIVE}")

    await handle_subagent_info(sm, _info_event(), lambda: None)

    assert sm.agents == {}


async def test_ignored_resume_does_not_link_to_an_unrelated_unlinked_agent() -> None:
    sm = StateMachine()
    await _run_agent_to_completion(sm)
    sm.transition(_start_event("subagent_toolu_2"))

    await handle_subagent_info(sm, _info_event(), lambda: None)

    assert sm.agents["subagent_toolu_2"].native_id is None


async def test_unknown_native_agent_is_still_synthesized_after_backend_restart() -> None:
    sm = StateMachine()

    await handle_subagent_info(sm, _info_event(), lambda: None)

    assert list(sm.agents) == [f"subagent_{NATIVE}"]
