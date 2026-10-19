"""Pure helpers: TrackView refs from tool inputs, ticket rows to KanbanTask."""

import re
from dataclasses import dataclass
from typing import Any, cast

from app.models.sessions import KanbanTask

TRACKVIEW_TOOL_PREFIX = "mcp__trackview__"

_KEY_RE = re.compile(r"^([A-Z][A-Z0-9]*)-\d+$")
_SINGLE_KEY_FIELDS = ("key", "ticketKey", "parentKey")

_COLUMN_STATUS = {
    "Backlog": "pending",
    "Ready to Start": "pending",
    "Stuck": "pending",
    "In Progress": "in_progress",
    "QA": "in_progress",
    "Done": "completed",
}
_KIND_STATUS = {"todo": "pending", "in_progress": "in_progress", "done": "completed"}


@dataclass(frozen=True)
class TrackViewRefs:
    prefixes: frozenset[str]
    project_ids: frozenset[str]
    board_ids: frozenset[str]

    @property
    def is_empty(self) -> bool:
        return not (self.prefixes or self.project_ids or self.board_ids)


def _prefix_of(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _KEY_RE.match(value)
    return match.group(1) if match else None


def extract_trackview_refs(
    tool_name: str | None, tool_input: dict[str, Any] | None
) -> TrackViewRefs:
    prefixes: set[str] = set()
    project_ids: set[str] = set()
    board_ids: set[str] = set()
    if tool_name and tool_name.startswith(TRACKVIEW_TOOL_PREFIX) and tool_input:
        candidates: list[Any] = [tool_input.get(f) for f in _SINGLE_KEY_FIELDS]
        keys = tool_input.get("keys")
        if isinstance(keys, list):
            candidates.extend(cast(list[Any], keys))
        for candidate in candidates:
            prefix = _prefix_of(candidate)
            if prefix:
                prefixes.add(prefix)
        project_id = tool_input.get("projectId")
        if isinstance(project_id, str) and project_id:
            project_ids.add(project_id)
        board_id = tool_input.get("boardId")
        if isinstance(board_id, str) and board_id:
            board_ids.add(board_id)
    return TrackViewRefs(frozenset(prefixes), frozenset(project_ids), frozenset(board_ids))


def map_ticket(row: dict[str, Any], query_kind: str) -> KanbanTask | None:
    key = row.get("key")
    title = row.get("title")
    if not key or not title:
        return None
    column = row.get("column")
    status = _COLUMN_STATUS.get(column) if isinstance(column, str) else None
    status = status or _KIND_STATUS.get(query_kind, "pending")
    return KanbanTask(task_id=key, subject=title, status=status, assignee=None, linear_id=key)
