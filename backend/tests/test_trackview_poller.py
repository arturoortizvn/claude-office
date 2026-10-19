"""Tests for the project-keyed TrackView poller."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core import trackview_poller
from app.core.trackview_client import TrackViewError
from app.core.trackview_mapping import TrackViewRefs
from app.core.trackview_poller import TrackViewPoller, get_trackview_poller, init_trackview_poller
from app.models.sessions import KanbanTask

INTERVAL = 0.05
WAIT = INTERVAL * 6


def row(key: str, title: str = "T", column: str = "Backlog") -> dict[str, Any]:
    return {"key": key, "title": title, "column": column, "updatedAt": "2026-01-01T00:00:00Z"}


class FakeClient:
    def __init__(self) -> None:
        self.whoami_calls = 0
        self.projects = [
            {"id": "p1", "name": "One", "keyPrefix": "ONE"},
            {"id": "p2", "name": "Two", "keyPrefix": "TWO"},
        ]
        self.boards = {"p1": [{"id": "b1", "name": "B1"}], "p2": [{"id": "b2", "name": "B2"}]}
        self.tickets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.ticket_calls: list[tuple[str, str, str | None]] = []
        self.fail = False

    async def whoami(self) -> str:
        self.whoami_calls += 1
        return "user-1"

    async def list_projects(self) -> list[dict[str, Any]]:
        return self.projects

    async def list_boards(self, project_id: str) -> list[dict[str, Any]]:
        return self.boards[project_id]

    async def list_tickets(
        self,
        project_id: str,
        assignee_id: str,
        column_kind: str,
        updated_since: str | None = None,
    ) -> list[dict[str, Any]]:
        self.ticket_calls.append((project_id, column_kind, updated_since))
        if self.fail:
            raise TrackViewError("boom")
        return self.tickets.get((project_id, column_kind), [])


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[KanbanTask]]] = []

    async def __call__(self, session_id: str, tasks: list[KanbanTask]) -> None:
        self.calls.append((session_id, tasks))

    def last(self, session_id: str) -> list[KanbanTask]:
        return [t for s, t in self.calls if s == session_id][-1]


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
async def poller(client: FakeClient, recorder: Recorder):
    p = TrackViewPoller(client, recorder, INTERVAL)  # type: ignore[arg-type]
    yield p
    await p.stop_all()


def refs(
    prefixes: frozenset[str] = frozenset(),
    pids: frozenset[str] = frozenset(),
    bids: frozenset[str] = frozenset(),
) -> TrackViewRefs:
    return TrackViewRefs(frozenset(prefixes), frozenset(pids), frozenset(bids))


async def test_resolves_prefix_project_and_board(poller: TrackViewPoller) -> None:
    assert await poller.resolve(refs(frozenset({"ONE"}), frozenset({"pX"}), frozenset({"b2"}))) == {
        "p1",
        "pX",
        "p2",
    }


async def test_unknown_prefix_resolves_to_nothing(poller: TrackViewPoller) -> None:
    assert await poller.resolve(refs(frozenset({"NOPE"}), bids=frozenset({"nope"}))) == set()


async def test_resolve_returns_partial_on_client_error(
    poller: TrackViewPoller, client: FakeClient
) -> None:
    async def boom() -> list[dict[str, Any]]:
        raise TrackViewError("x")

    client.list_projects = boom  # type: ignore[method-assign]
    assert await poller.resolve(refs(frozenset({"ONE"}), frozenset({"pX"}))) == {"pX"}


async def test_subscribe_emits_mapped_tasks(
    poller: TrackViewPoller, client: FakeClient, recorder: Recorder
) -> None:
    client.tickets[("p1", "todo")] = [row("ONE-1"), row("ONE-2")]
    client.tickets[("p1", "in_progress")] = [row("ONE-3", column="In Progress")]
    client.tickets[("p1", "done")] = [row("ONE-4", column="Done")]
    await poller.subscribe("s1", {"p1"})
    await asyncio.sleep(WAIT)
    tasks = recorder.last("s1")
    assert [(t.task_id, t.status) for t in tasks] == [
        ("ONE-1", "pending"),
        ("ONE-2", "pending"),
        ("ONE-3", "in_progress"),
        ("ONE-4", "completed"),
    ]


async def test_done_query_uses_seven_day_window(
    poller: TrackViewPoller, client: FakeClient
) -> None:
    await poller.subscribe("s1", {"p1"})
    await asyncio.sleep(WAIT)
    sinces = {k: s for _, k, s in client.ticket_calls}
    assert sinces["todo"] is None and sinces["in_progress"] is None
    parsed = datetime.fromisoformat(sinces["done"] or "")
    assert abs(parsed - (datetime.now(UTC) - timedelta(days=7))) < timedelta(minutes=1)


async def test_shared_project_fetched_once(
    poller: TrackViewPoller, client: FakeClient, recorder: Recorder
) -> None:
    client.tickets[("p1", "todo")] = [row("ONE-1")]
    await poller.subscribe("s1", {"p1"})
    await asyncio.sleep(INTERVAL * 2)
    await poller.subscribe("s2", {"p1"})
    await poller.stop_all()
    kinds = [k for _, k, _ in client.ticket_calls]
    assert kinds.count("todo") == kinds.count("done") == kinds.count("in_progress")
    assert [t.task_id for t in recorder.last("s1")] == ["ONE-1"]
    assert [t.task_id for t in recorder.last("s2")] == ["ONE-1"]


async def test_two_sessions_one_fetch_per_interval(
    poller: TrackViewPoller, client: FakeClient
) -> None:
    await poller.subscribe("s1", {"p1"})
    await poller.subscribe("s2", {"p1"})
    await asyncio.sleep(INTERVAL * 0.5)
    await poller.stop_all()
    kinds = [k for _, k, _ in client.ticket_calls]
    assert kinds.count("todo") <= 2


async def test_session_gets_union_of_its_projects(
    poller: TrackViewPoller, client: FakeClient, recorder: Recorder
) -> None:
    client.tickets[("p1", "todo")] = [row("ONE-1")]
    client.tickets[("p2", "todo")] = [row("TWO-1")]
    await poller.subscribe("s1", {"p1", "p2"})
    await asyncio.sleep(WAIT)
    assert [t.task_id for t in recorder.last("s1")] == ["ONE-1", "TWO-1"]


async def test_fetch_error_keeps_last_tickets(
    poller: TrackViewPoller, client: FakeClient, recorder: Recorder
) -> None:
    client.tickets[("p1", "todo")] = [row("ONE-1")]
    await poller.subscribe("s1", {"p1"})
    await asyncio.sleep(INTERVAL * 2)
    before = len(recorder.calls)
    client.fail = True
    await asyncio.sleep(WAIT)
    assert len(recorder.calls) == before
    assert [t.task_id for t in recorder.last("s1")] == ["ONE-1"]


async def test_bad_row_is_skipped(
    poller: TrackViewPoller, client: FakeClient, recorder: Recorder
) -> None:
    client.tickets[("p1", "todo")] = [row("ONE-1"), {"key": 5, "title": ["x"]}, row("ONE-2")]
    await poller.subscribe("s1", {"p1"})
    await asyncio.sleep(WAIT)
    assert [t.task_id for t in recorder.last("s1")] == ["ONE-1", "ONE-2"]


async def test_unsubscribe_stops_unused_project(poller: TrackViewPoller) -> None:
    await poller.subscribe("s1", {"p1"})
    await poller.subscribe("s2", {"p1"})
    await poller.unsubscribe_session("s1")
    assert await poller.is_polling("p1")
    await poller.unsubscribe_session("s2")
    assert not await poller.is_polling("p1")


async def test_whoami_called_once(poller: TrackViewPoller, client: FakeClient) -> None:
    await poller.subscribe("s1", {"p1", "p2"})
    await asyncio.sleep(WAIT)
    assert client.whoami_calls == 1


async def test_singleton(
    client: FakeClient, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trackview_poller, "_trackview_poller", None)
    p = init_trackview_poller(client, recorder, INTERVAL)  # type: ignore[arg-type]
    assert get_trackview_poller() is p
