"""Tests for wiring plan progress into the EventProcessor."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import event_processor
from app.core.event_processor import EventProcessor
from app.core.state_machine import StateMachine
from app.db.database import Base, override_engine
from app.models.common import TodoItem, TodoStatus
from app.models.events import (
    AnyEvent,
    EventType,
    SessionEvent,
    SessionEventData,
    ToolEvent,
    ToolEventData,
)

SESSION = "sess"
PLAN = "/r/docs/superpowers/plans/p.md"


class _FakePlanPoller:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.stopped: list[str] = []

    async def start_polling(self, session_id: str, plan_path: str) -> None:
        self.started.append((session_id, plan_path))

    async def stop_polling(self, session_id: str) -> None:
        self.stopped.append(session_id)


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
def poller(monkeypatch: pytest.MonkeyPatch) -> _FakePlanPoller:
    fake = _FakePlanPoller()
    monkeypatch.setattr(event_processor, "get_plan_file_poller", lambda: fake)

    def _init(_cb: object) -> _FakePlanPoller:
        return fake

    monkeypatch.setattr(event_processor, "init_plan_file_poller", _init)
    return fake


@pytest.fixture
def broadcasts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _save(session_id: str, todos: list[TodoItem]) -> None:
        return None

    async def _broadcast(session_id: str, sm: StateMachine) -> None:
        calls.append(session_id)

    monkeypatch.setattr(event_processor, "save_tasks", _save)
    monkeypatch.setattr(event_processor, "broadcast_state", _broadcast)
    return calls


def _plan_todo(content: str) -> TodoItem:
    return TodoItem(
        task_id=content,
        content=content,
        status=TodoStatus.PENDING,
        metadata={"source": "plan"},
    )


def _processor_with_sm(todos: list[TodoItem]) -> tuple[EventProcessor, StateMachine]:
    processor = EventProcessor()
    sm = StateMachine()
    sm.todos = todos
    processor.sessions[SESSION] = sm
    return processor, sm


def _tool_event(file_path: str, tool_name: str = "Read") -> ToolEvent:
    return ToolEvent(
        event_type=EventType.PRE_TOOL_USE,
        session_id=SESSION,
        timestamp=datetime.now(UTC),
        data=ToolEventData(tool_name=tool_name, tool_input={"file_path": file_path}),
    )


async def _process(processor: EventProcessor, event: AnyEvent) -> None:
    await processor.process_event(event)


async def test_read_of_plan_starts_polling(poller: _FakePlanPoller, broadcasts: list[str]) -> None:
    await _process(EventProcessor(), _tool_event(PLAN))

    assert poller.started == [(SESSION, PLAN)]


async def test_read_of_other_file_does_not_poll(
    poller: _FakePlanPoller, broadcasts: list[str]
) -> None:
    await _process(EventProcessor(), _tool_event("/r/src/a.py"))

    assert poller.started == []


async def test_plan_update_sets_todos_when_empty(
    poller: _FakePlanPoller, broadcasts: list[str]
) -> None:
    processor, sm = _processor_with_sm([])
    todos = [_plan_todo("Task 1"), _plan_todo("Task 2")]

    await processor._handle_plan_update(  # pyright: ignore[reportPrivateUsage]
        SESSION, todos
    )

    assert sm.todos == todos
    assert broadcasts == [SESSION]


async def test_plan_update_replaces_older_plan_todos(
    poller: _FakePlanPoller, broadcasts: list[str]
) -> None:
    processor, sm = _processor_with_sm([_plan_todo("Old")])
    todos = [_plan_todo("New")]

    await processor._handle_plan_update(  # pyright: ignore[reportPrivateUsage]
        SESSION, todos
    )

    assert sm.todos == todos
    assert broadcasts == [SESSION]


async def test_real_todos_are_not_overwritten(
    poller: _FakePlanPoller, broadcasts: list[str]
) -> None:
    real = [TodoItem(content="Real", status=TodoStatus.PENDING)]
    processor, sm = _processor_with_sm(real)

    await processor._handle_plan_update(  # pyright: ignore[reportPrivateUsage]
        SESSION, [_plan_todo("Task 1")]
    )

    assert sm.todos == real
    assert broadcasts == []


async def test_empty_plan_parse_keeps_current_todos(
    poller: _FakePlanPoller, broadcasts: list[str]
) -> None:
    current = [_plan_todo("Task 1")]
    processor, sm = _processor_with_sm(current)

    await processor._handle_plan_update(  # pyright: ignore[reportPrivateUsage]
        SESSION, []
    )

    assert sm.todos == current
    assert broadcasts == []


async def test_todowrite_then_plan_update_is_ignored(
    poller: _FakePlanPoller, broadcasts: list[str]
) -> None:
    processor, sm = _processor_with_sm([_plan_todo("Task 1")])
    event = ToolEvent(
        event_type=EventType.PRE_TOOL_USE,
        session_id=SESSION,
        timestamp=datetime.now(UTC),
        data=ToolEventData(
            tool_name="TodoWrite",
            tool_input={"todos": [{"content": "Real", "status": "pending"}]},
        ),
    )

    await _process(processor, event)
    written = list(sm.todos)
    broadcasts.clear()

    assert [t.content for t in written] == ["Real"]

    await processor._handle_plan_update(  # pyright: ignore[reportPrivateUsage]
        SESSION, [_plan_todo("Task 9")]
    )

    assert sm.todos == written
    assert broadcasts == []


async def test_session_end_stops_plan_polling(
    poller: _FakePlanPoller, broadcasts: list[str]
) -> None:
    event = SessionEvent(
        event_type=EventType.SESSION_END,
        session_id=SESSION,
        timestamp=datetime.now(UTC),
        data=SessionEventData(),
    )
    processor, _ = _processor_with_sm([])

    await _process(processor, event)

    assert poller.stopped == [SESSION]
