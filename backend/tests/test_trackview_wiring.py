"""Tests for wiring TrackView tickets into the EventProcessor and the Kanban."""

import asyncio
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import event_processor
from app.core.event_processor import EventProcessor
from app.core.state_machine import StateMachine
from app.core.trackview_mapping import TrackViewRefs
from app.db.database import Base, override_engine
from app.models.events import (
    AnyEvent,
    EventType,
    SessionEvent,
    SessionEventData,
    ToolEvent,
    ToolEventData,
)
from app.models.sessions import KanbanTask

SESSION = "sess"


class _FakePoller:
    def __init__(self, resolve_delay: float = 0.0, gate: asyncio.Event | None = None) -> None:
        self.resolved: list[TrackViewRefs] = []
        self.subscribed: list[tuple[str, set[str]]] = []
        self.unsubscribed: list[str] = []
        self.resolve_delay = resolve_delay
        self.gate = gate

    async def resolve(self, refs: TrackViewRefs) -> set[str]:
        self.resolved.append(refs)
        if self.resolve_delay:
            await asyncio.sleep(self.resolve_delay)
        if self.gate is not None:
            await self.gate.wait()
        return {"proj-1"}

    async def subscribe(self, session_id: str, project_ids: set[str]) -> None:
        self.subscribed.append((session_id, project_ids))

    async def unsubscribe_session(self, session_id: str) -> None:
        self.unsubscribed.append(session_id)


@pytest.fixture(autouse=True, scope="module")
def ensure_test_db() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def _setup() -> None:
        import app.db.models  # noqa: F401  # pyright: ignore[reportUnusedImport]

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    override_engine(engine)


@pytest.fixture
def broadcasts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _broadcast(session_id: str, sm: StateMachine) -> None:
        calls.append(session_id)

    monkeypatch.setattr(event_processor, "broadcast_state", _broadcast)
    return calls


def _enable(monkeypatch: pytest.MonkeyPatch, fake: _FakePoller) -> None:
    def _on(_self: EventProcessor) -> bool:
        return True

    monkeypatch.setattr(EventProcessor, "_ensure_trackview_poller", _on)
    monkeypatch.setattr(event_processor, "get_trackview_poller", lambda: fake)


def _tool_event(tool_name: str, tool_input: dict[str, Any]) -> ToolEvent:
    return ToolEvent(
        event_type=EventType.PRE_TOOL_USE,
        session_id=SESSION,
        timestamp=datetime.now(UTC),
        data=ToolEventData(tool_name=tool_name, tool_input=tool_input),
    )


def _end_event() -> SessionEvent:
    return SessionEvent(
        event_type=EventType.SESSION_END,
        session_id=SESSION,
        timestamp=datetime.now(UTC),
        data=SessionEventData(),
    )


def _task(task_id: str) -> KanbanTask:
    return KanbanTask(task_id=task_id, subject=f"Subject {task_id}", status="pending")


async def _drain(processor: EventProcessor) -> None:
    pending = list(processor._trackview_tasks)  # pyright: ignore[reportPrivateUsage]
    if pending:
        await asyncio.gather(*pending)


async def _process(processor: EventProcessor, event: AnyEvent) -> None:
    await processor.process_event(event)


async def test_no_token_feature_off(monkeypatch: pytest.MonkeyPatch, broadcasts: list[str]) -> None:
    monkeypatch.setattr(
        event_processor, "get_settings", lambda: SimpleNamespace(TRACKVIEW_API_TOKEN="")
    )
    inits: list[object] = []

    def _init(*args: object, **kwargs: object) -> None:
        inits.append(args)

    monkeypatch.setattr(event_processor, "init_trackview_poller", _init)
    processor = EventProcessor()

    await _process(processor, _tool_event("mcp__trackview__get_ticket", {"key": "BSA-13"}))

    assert inits == []
    assert not processor._trackview_tasks  # pyright: ignore[reportPrivateUsage]


async def test_non_trackview_tool_ignored(
    monkeypatch: pytest.MonkeyPatch, broadcasts: list[str]
) -> None:
    fake = _FakePoller()
    _enable(monkeypatch, fake)
    processor = EventProcessor()

    await _process(processor, _tool_event("Read", {"file_path": "/r/a.py"}))
    await _drain(processor)

    assert fake.resolved == []
    assert fake.subscribed == []


async def test_trackview_tool_subscribes_session(
    monkeypatch: pytest.MonkeyPatch, broadcasts: list[str]
) -> None:
    fake = _FakePoller()
    _enable(monkeypatch, fake)
    processor = EventProcessor()

    await _process(processor, _tool_event("mcp__trackview__get_ticket", {"key": "BSA-13"}))
    await _drain(processor)

    assert [r.prefixes for r in fake.resolved] == [frozenset({"BSA"})]
    assert fake.subscribed == [(SESSION, {"proj-1"})]


async def test_trackview_detection_does_not_block_event(
    monkeypatch: pytest.MonkeyPatch, broadcasts: list[str]
) -> None:
    fake = _FakePoller(resolve_delay=1.0)
    _enable(monkeypatch, fake)
    processor = EventProcessor()

    start = time.monotonic()
    await _process(processor, _tool_event("mcp__trackview__get_ticket", {"key": "BSA-13"}))
    elapsed = time.monotonic() - start

    assert elapsed < 0.2
    await _drain(processor)


async def test_no_subscribe_after_session_end(
    monkeypatch: pytest.MonkeyPatch, broadcasts: list[str]
) -> None:
    gate = asyncio.Event()
    fake = _FakePoller(gate=gate)
    _enable(monkeypatch, fake)
    processor = EventProcessor()

    await _process(processor, _tool_event("mcp__trackview__get_ticket", {"key": "BSA-13"}))
    await asyncio.sleep(0)
    await _process(processor, _end_event())
    gate.set()
    await _drain(processor)

    assert fake.resolved
    assert fake.subscribed == []


async def test_update_sets_tasks_and_broadcasts(broadcasts: list[str]) -> None:
    processor = EventProcessor()
    sm = StateMachine()
    processor.sessions[SESSION] = sm
    tasks = [_task("A-1"), _task("A-2")]

    await processor._handle_trackview_update(SESSION, tasks)  # pyright: ignore[reportPrivateUsage]
    await processor._handle_trackview_update(  # pyright: ignore[reportPrivateUsage]
        SESSION, [_task("A-1"), _task("A-2")]
    )

    assert sm.trackview_tasks == tasks
    assert broadcasts == [SESSION]


async def test_update_unknown_session_is_ignored(broadcasts: list[str]) -> None:
    processor = EventProcessor()

    await processor._handle_trackview_update("ghost", [_task("A-1")])  # pyright: ignore[reportPrivateUsage]

    assert broadcasts == []


def test_kanban_view_prefers_trackview() -> None:
    sm = StateMachine()
    sm.kanban_tasks["t1"] = _task("t1")
    sm.trackview_tasks = [_task("BSA-1")]

    state = sm.to_game_state(SESSION)
    assert [t.task_id for t in state.whiteboard_data.kanban_tasks] == ["BSA-1"]

    sm.trackview_tasks = []
    state = sm.to_game_state(SESSION)
    assert [t.task_id for t in state.whiteboard_data.kanban_tasks] == ["t1"]


async def test_session_end_unsubscribes(
    monkeypatch: pytest.MonkeyPatch, broadcasts: list[str]
) -> None:
    fake = _FakePoller()
    _enable(monkeypatch, fake)

    await _process(EventProcessor(), _end_event())

    assert fake.unsubscribed == [SESSION]
