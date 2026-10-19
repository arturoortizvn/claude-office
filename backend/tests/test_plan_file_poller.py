"""Tests for the plan file poller."""

import asyncio
import os
from pathlib import Path

import pytest

from app.core import plan_file_poller
from app.core.plan_file_poller import (
    PlanFilePoller,
    get_plan_file_poller,
    init_plan_file_poller,
)
from app.models.common import TodoItem, TodoStatus

INTERVAL = 0.05
WAIT = INTERVAL * 6

PLAN = (
    "# Demo Implementation Plan\n\n### Task 1: First\n- [{a}] a\n\n### Task 2: Second\n- [{b}] b\n"
)


def write_plan(path: Path, a: str = " ", b: str = " ", mtime: float | None = None) -> None:
    path.write_text(PLAN.format(a=a, b=b), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[TodoItem]]] = []

    async def __call__(self, session_id: str, todos: list[TodoItem]) -> None:
        self.calls.append((session_id, todos))


@pytest.fixture(autouse=True)
def fast_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_file_poller, "POLL_INTERVAL_SECONDS", INTERVAL)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.mark.asyncio
async def test_emits_todos_for_plan_file(tmp_path: Path, recorder: Recorder) -> None:
    plan = tmp_path / "plans" / "a.md"
    plan.parent.mkdir()
    write_plan(plan, a="x")
    poller = PlanFilePoller(recorder)
    await poller.start_polling("s1", str(plan))
    await asyncio.sleep(WAIT)
    await poller.stop_all()

    assert len(recorder.calls) == 1
    session_id, todos = recorder.calls[0]
    assert session_id == "s1"
    assert todos[0].status == TodoStatus.COMPLETED


@pytest.mark.asyncio
async def test_emits_again_when_file_changes(tmp_path: Path, recorder: Recorder) -> None:
    plan = tmp_path / "a.md"
    write_plan(plan, a="x", mtime=1000.0)
    poller = PlanFilePoller(recorder)
    await poller.start_polling("s1", str(plan))
    await asyncio.sleep(WAIT)
    write_plan(plan, a="x", b="x", mtime=2000.0)
    await asyncio.sleep(WAIT)
    await poller.stop_all()

    assert len(recorder.calls) == 2
    assert recorder.calls[0][1][1].status == TodoStatus.IN_PROGRESS
    assert recorder.calls[1][1][1].status == TodoStatus.COMPLETED


@pytest.mark.asyncio
async def test_no_emit_when_unchanged(tmp_path: Path, recorder: Recorder) -> None:
    plan = tmp_path / "a.md"
    write_plan(plan)
    poller = PlanFilePoller(recorder)
    await poller.start_polling("s1", str(plan))
    await asyncio.sleep(WAIT * 2)
    await poller.stop_all()

    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_missing_file_keeps_last_todos(tmp_path: Path, recorder: Recorder) -> None:
    plan = tmp_path / "a.md"
    write_plan(plan)
    poller = PlanFilePoller(recorder)
    await poller.start_polling("s1", str(plan))
    await asyncio.sleep(WAIT)
    plan.unlink()
    await asyncio.sleep(WAIT)

    assert len(recorder.calls) == 1
    assert await poller.is_polling("s1")
    await poller.stop_all()


@pytest.mark.asyncio
async def test_missing_file_at_start_emits_once_it_appears(
    tmp_path: Path, recorder: Recorder
) -> None:
    plan = tmp_path / "a.md"
    poller = PlanFilePoller(recorder)
    await poller.start_polling("s1", str(plan))
    await asyncio.sleep(WAIT)
    assert recorder.calls == []
    write_plan(plan)
    await asyncio.sleep(WAIT)
    await poller.stop_all()

    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_switches_to_new_plan_path(tmp_path: Path, recorder: Recorder) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    write_plan(a)
    b.write_text("# B\n\n### Task 1: Only B\n- [ ] x\n", encoding="utf-8")
    poller = PlanFilePoller(recorder)
    await poller.start_polling("s1", str(a))
    await asyncio.sleep(WAIT)
    await poller.start_polling("s1", str(b))
    assert await poller.current_path("s1") == str(b)
    await asyncio.sleep(WAIT)
    await poller.stop_all()

    assert [t.content for t in recorder.calls[-1][1]] == ["Only B"]


@pytest.mark.asyncio
async def test_same_path_is_noop(tmp_path: Path, recorder: Recorder) -> None:
    plan = tmp_path / "a.md"
    write_plan(plan)
    poller = PlanFilePoller(recorder)
    await poller.start_polling("s1", str(plan))
    await poller.start_polling("s1", str(plan))
    await asyncio.sleep(WAIT)
    await poller.stop_all()

    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_current_path_none_when_not_polling(recorder: Recorder) -> None:
    poller = PlanFilePoller(recorder)
    assert await poller.current_path("nope") is None


@pytest.mark.asyncio
async def test_callback_exception_is_swallowed(tmp_path: Path) -> None:
    plan = tmp_path / "a.md"
    write_plan(plan)

    async def boom(session_id: str, todos: list[TodoItem]) -> None:
        raise RuntimeError("boom")

    poller = PlanFilePoller(boom)
    await poller.start_polling("s1", str(plan))
    await asyncio.sleep(WAIT)
    assert await poller.is_polling("s1")
    await poller.stop_all()


@pytest.mark.asyncio
async def test_singleton_init_and_get(recorder: Recorder) -> None:
    poller = init_plan_file_poller(recorder)
    assert get_plan_file_poller() is poller
