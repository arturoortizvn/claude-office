"""Tests for linking SubagentInfo events to the agent that spawned them."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.event_processor import EventProcessor
from app.core.handlers import agent_handler
from app.core.handlers.agent_handler import handle_subagent_info
from app.core.state_machine import StateMachine
from app.db.database import AsyncSessionLocal, Base, override_engine
from app.db.models import EventRecord, SessionRecord
from app.models.events import AgentEvent, AgentEventData, EventType

SESSION = "session-linking"


class _FakePoller:
    def __init__(self) -> None:
        self.paths: dict[str, str] = {}

    async def is_polling(self, agent_id: str) -> bool:
        return agent_id in self.paths

    async def start_polling(self, agent_id: str, session_id: str, path: str) -> None:
        self.paths[agent_id] = path


@pytest.fixture(autouse=True, scope="module")
def ensure_test_db() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def _setup() -> None:
        import app.db.models  # noqa: F401  # pyright: ignore[reportUnusedImport]

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    override_engine(engine)


@pytest.fixture(autouse=True)
def fake_poller(monkeypatch: pytest.MonkeyPatch) -> _FakePoller:
    poller = _FakePoller()
    monkeypatch.setattr(agent_handler, "get_transcript_poller", lambda: poller)
    return poller


def _start(sm: StateMachine, tool_use_id: str) -> None:
    sm.transition(
        AgentEvent(
            event_type=EventType.SUBAGENT_START,
            session_id=SESSION,
            timestamp=datetime.now(UTC),
            data=AgentEventData(agent_id=f"subagent_{tool_use_id}"),
        )
    )


def _transcript(directory: Path, native_id: str, tool_use_id: str | None) -> str:
    transcript = directory / f"agent-{native_id}.jsonl"
    transcript.write_text("")
    if tool_use_id:
        meta = directory / f"agent-{native_id}.meta.json"
        meta.write_text(json.dumps({"toolUseId": tool_use_id}))
    return str(transcript)


async def _info(sm: StateMachine, native_id: str, transcript_path: str) -> None:
    event = AgentEvent(
        event_type=EventType.SUBAGENT_INFO,
        session_id=SESSION,
        timestamp=datetime.now(UTC),
        data=AgentEventData(native_agent_id=native_id, agent_transcript_path=transcript_path),
    )
    await handle_subagent_info(sm, event, lambda: None)


async def test_parallel_agents_link_to_the_tool_use_that_spawned_them(tmp_path: Path) -> None:
    sm = StateMachine()
    for tool_use_id in ("toolu_a", "toolu_b", "toolu_c"):
        _start(sm, tool_use_id)

    await _info(sm, "native_c", _transcript(tmp_path, "native_c", "toolu_c"))
    await _info(sm, "native_b", _transcript(tmp_path, "native_b", "toolu_b"))
    await _info(sm, "native_a", _transcript(tmp_path, "native_a", "toolu_a"))

    assert {aid: a.native_id for aid, a in sm.agents.items()} == {
        "subagent_toolu_a": "native_a",
        "subagent_toolu_b": "native_b",
        "subagent_toolu_c": "native_c",
    }


async def test_parallel_info_waits_for_the_meta_written_after_the_hook(tmp_path: Path) -> None:
    sm = StateMachine()
    _start(sm, "toolu_a")
    _start(sm, "toolu_b")
    path_b = _transcript(tmp_path, "native_b", None)

    async def write_meta_late() -> None:
        await asyncio.sleep(0.12)
        (tmp_path / "agent-native_b.meta.json").write_text(json.dumps({"toolUseId": "toolu_b"}))

    writer = asyncio.create_task(write_meta_late())
    await _info(sm, "native_b", path_b)
    await writer

    assert sm.agents["subagent_toolu_b"].native_id == "native_b"
    assert sm.agents["subagent_toolu_a"].native_id is None


async def test_each_agent_polls_its_own_transcript(
    tmp_path: Path, fake_poller: _FakePoller
) -> None:
    sm = StateMachine()
    _start(sm, "toolu_a")
    _start(sm, "toolu_b")
    path_b = _transcript(tmp_path, "native_b", "toolu_b")

    await _info(sm, "native_b", path_b)

    assert fake_poller.paths == {"subagent_toolu_b": path_b}


async def test_info_without_meta_still_links_an_unlinked_agent(tmp_path: Path) -> None:
    sm = StateMachine()
    _start(sm, "toolu_a")

    await _info(sm, "native_a", _transcript(tmp_path, "native_a", None))

    assert sm.agents["subagent_toolu_a"].native_id == "native_a"


async def test_info_for_an_unseen_tool_use_does_not_hijack_another_agent(
    tmp_path: Path,
) -> None:
    sm = StateMachine()
    _start(sm, "toolu_a")

    await _info(sm, "native_x", _transcript(tmp_path, "native_x", "toolu_x"))

    assert sm.agents["subagent_toolu_a"].native_id is None
    assert sm.agents["subagent_native_x"].native_id == "native_x"


async def test_restored_session_links_parallel_agents_to_their_own_tool_use(
    tmp_path: Path,
) -> None:
    session_id = "restore-parallel-linking"
    start = datetime.now(UTC)
    rows: list[tuple[EventType, dict[str, str]]] = [
        (EventType.SESSION_START, {}),
        (EventType.SUBAGENT_START, {"agent_id": "subagent_toolu_a"}),
        (EventType.SUBAGENT_START, {"agent_id": "subagent_toolu_b"}),
        (
            EventType.SUBAGENT_INFO,
            {
                "native_agent_id": "native_b",
                "agent_transcript_path": _transcript(tmp_path, "native_b", "toolu_b"),
            },
        ),
        (
            EventType.SUBAGENT_INFO,
            {
                "native_agent_id": "native_a",
                "agent_transcript_path": _transcript(tmp_path, "native_a", "toolu_a"),
            },
        ),
    ]
    async with AsyncSessionLocal() as db:
        db.add(SessionRecord(id=session_id, project_name="app"))
        for offset, (event_type, data) in enumerate(rows):
            db.add(
                EventRecord(
                    session_id=session_id,
                    timestamp=start + timedelta(seconds=offset),
                    event_type=event_type.value,
                    data=data,
                )
            )
        await db.commit()

    ep = EventProcessor()
    await ep._restore_session(session_id)  # pyright: ignore[reportPrivateUsage]

    agents = ep.sessions[session_id].agents
    assert agents["subagent_toolu_a"].native_id == "native_a"
    assert agents["subagent_toolu_b"].native_id == "native_b"
