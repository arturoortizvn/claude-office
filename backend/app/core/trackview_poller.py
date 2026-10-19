"""Poll TrackView once per subscribed project and fan tickets out to sessions."""

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.base_poller import BasePoller
from app.core.trackview_client import TrackViewClient, TrackViewError
from app.core.trackview_mapping import TrackViewRefs, map_ticket
from app.models.sessions import KanbanTask

logger = logging.getLogger(__name__)

DONE_WINDOW = timedelta(days=7)
QUERY_KINDS = ("todo", "in_progress", "done")


@dataclass
class ProjectPollState:
    project_id: str
    tickets: list[KanbanTask] | None = None
    in_error: bool = False


class TrackViewPoller(BasePoller[ProjectPollState]):
    def __init__(
        self,
        client: TrackViewClient,
        tasks_callback: Callable[[str, list[KanbanTask]], Coroutine[Any, Any, None]],
        interval: float,
    ) -> None:
        super().__init__()
        self._client = client
        self._tasks_callback = tasks_callback
        self._interval = interval
        self._user_id: str | None = None
        self._prefix_to_project: dict[str, str] | None = None
        self._board_to_project: dict[str, str] | None = None
        self._subscriptions: dict[str, set[str]] = {}

    def _get_poll_interval(self) -> float:
        return self._interval

    async def resolve(self, refs: TrackViewRefs) -> set[str]:
        resolved: set[str] = set(refs.project_ids)
        try:
            if refs.prefixes:
                if self._prefix_to_project is None:
                    projects = await self._client.list_projects()
                    self._prefix_to_project = {
                        p["keyPrefix"]: p["id"] for p in projects if "keyPrefix" in p and "id" in p
                    }
                for prefix in refs.prefixes:
                    project_id = self._prefix_to_project.get(prefix)
                    if project_id:
                        resolved.add(project_id)
            if refs.board_ids:
                known = self._board_to_project or {}
                if not refs.board_ids <= known.keys():
                    known = await self._build_board_map()
                    self._board_to_project = known
                for board_id in refs.board_ids:
                    project_id = known.get(board_id)
                    if project_id:
                        resolved.add(project_id)
        except TrackViewError as e:
            logger.warning(f"TrackView project resolution failed: {e}")
        except (KeyError, TypeError):
            logger.warning("TrackView project resolution got malformed data")
        return resolved

    async def _build_board_map(self) -> dict[str, str]:
        projects = await self._client.list_projects()
        mapping: dict[str, str] = {}
        for project in projects:
            for board in await self._client.list_boards(project["id"]):
                mapping[board["id"]] = project["id"]
        return mapping

    async def subscribe(self, session_id: str, project_ids: set[str]) -> None:
        current = self._subscriptions.setdefault(session_id, set())
        new = project_ids - current
        if not new:
            return
        current |= new
        for project_id in new:
            if await self.is_polling(project_id):
                async with self._lock:
                    state = self._sessions.get(project_id)
                if state is not None:
                    await self._check(project_id, state, only_session=session_id)
            else:
                await self._register_polling(
                    project_id,
                    ProjectPollState(project_id),
                    task_name=f"trackview_poll_{project_id}",
                )
        await self._emit(session_id)

    async def unsubscribe_session(self, session_id: str) -> None:
        projects = self._subscriptions.pop(session_id, set())
        for project_id in projects:
            if not any(project_id in s for s in self._subscriptions.values()):
                await self.stop_polling(project_id)

    def _combined(self, session_id: str) -> list[KanbanTask]:
        combined: list[KanbanTask] = []
        for project_id in sorted(self._subscriptions.get(session_id, ())):
            state = self._sessions.get(project_id)
            if state and state.tickets:
                combined.extend(state.tickets)
        return combined

    async def _emit(self, session_id: str) -> None:
        try:
            await self._tasks_callback(session_id, self._combined(session_id))
        except Exception as e:
            logger.warning(f"Error in TrackView callback for {session_id}: {e}")

    async def _fetch(self, project_id: str) -> list[KanbanTask]:
        if self._user_id is None:
            self._user_id = await self._client.whoami()
        done_since = (datetime.now(UTC) - DONE_WINDOW).isoformat()
        tasks: list[KanbanTask] = []
        seen: set[str] = set()
        for kind in QUERY_KINDS:
            rows = await self._client.list_tickets(
                project_id, self._user_id, kind, done_since if kind == "done" else None
            )
            for item in rows:
                try:
                    task = map_ticket(item, kind)
                except Exception:
                    logger.debug("Skipping unmappable TrackView row in project %s", project_id)
                    continue
                if task and task.task_id not in seen:
                    seen.add(task.task_id)
                    tasks.append(task)
        return tasks

    async def _check(
        self, key: str, state: ProjectPollState, only_session: str | None = None
    ) -> None:
        try:
            tasks = await self._fetch(key)
        except TrackViewError as e:
            if not state.in_error:
                logger.warning(f"TrackView fetch failed for project {key}: {e}")
            state.in_error = True
            return
        state.in_error = False
        if tasks == state.tickets:
            return
        state.tickets = tasks
        subscribers = [s for s, projects in self._subscriptions.items() if key in projects]
        for session_id in subscribers:
            if session_id != only_session:
                await self._emit(session_id)


_trackview_poller: TrackViewPoller | None = None


def get_trackview_poller() -> TrackViewPoller | None:
    return _trackview_poller


def init_trackview_poller(
    client: TrackViewClient,
    tasks_callback: Callable[[str, list[KanbanTask]], Coroutine[Any, Any, None]],
    interval: float,
) -> TrackViewPoller:
    global _trackview_poller
    _trackview_poller = TrackViewPoller(client, tasks_callback, interval)
    return _trackview_poller
