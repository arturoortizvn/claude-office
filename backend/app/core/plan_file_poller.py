"""Poll a session's active plan file and emit its tasks as todos."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.base_poller import BasePoller
from app.core.plan_progress import parse_plan
from app.models.common import TodoItem

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.0


@dataclass
class PlanFileState:
    session_id: str
    plan_path: Path
    last_mtime: float | None = None


def _read_plan_sync(path: Path) -> tuple[float, str] | None:
    try:
        mtime = path.stat().st_mtime
        return mtime, path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _stat_mtime_sync(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


class PlanFilePoller(BasePoller[PlanFileState]):
    def _get_poll_interval(self) -> float:
        return POLL_INTERVAL_SECONDS

    def __init__(
        self,
        todo_callback: Callable[[str, list[TodoItem]], Coroutine[Any, Any, None]],
    ) -> None:
        super().__init__()
        self._todo_callback = todo_callback

    async def current_path(self, session_id: str) -> str | None:
        async with self._lock:
            state = self._sessions.get(session_id)
        return str(state.plan_path) if state else None

    async def start_polling(self, session_id: str, plan_path: str) -> None:
        if await self.current_path(session_id) == plan_path:
            return
        await self.stop_polling(session_id)
        state = PlanFileState(session_id=session_id, plan_path=Path(plan_path))
        await self._register_polling(session_id, state, task_name=f"plan_poll_{session_id}")
        logger.info(f"Started plan file polling for session {session_id} at {plan_path}")

    async def _check(self, key: str, state: PlanFileState) -> None:
        mtime = await asyncio.to_thread(_stat_mtime_sync, state.plan_path)
        if mtime is None or mtime == state.last_mtime:
            return

        result = await asyncio.to_thread(_read_plan_sync, state.plan_path)
        if result is None:
            return
        state.last_mtime, markdown = result

        todos = parse_plan(markdown, str(state.plan_path))
        try:
            await self._todo_callback(key, todos)
        except Exception as e:
            logger.warning(f"Error in plan callback for {key}: {e}")


_plan_file_poller: PlanFilePoller | None = None


def get_plan_file_poller() -> PlanFilePoller | None:
    return _plan_file_poller


def init_plan_file_poller(
    todo_callback: Callable[[str, list[TodoItem]], Coroutine[Any, Any, None]],
) -> PlanFilePoller:
    global _plan_file_poller
    _plan_file_poller = PlanFilePoller(todo_callback)
    return _plan_file_poller
