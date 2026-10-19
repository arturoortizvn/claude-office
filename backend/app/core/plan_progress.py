"""Parse superpowers plan files into whiteboard todos."""

import re
from pathlib import PurePosixPath
from typing import Any

from app.models.common import TodoItem, TodoStatus

PLAN_SOURCE = "plan"

_PLAN_TOOLS = frozenset({"Read", "Write", "Edit", "MultiEdit"})
_PLAN_PATH_RE = re.compile(r".*/plans/[^/]+\.md$")
_TITLE_RE = re.compile(r"^# (.+?)\s*$")
_TASK_RE = re.compile(r"^#{2,3} Task (\d+):\s*(.+?)\s*$")
_SECTION_END_RE = re.compile(r"^#{1,3}(?:\s|$)")
_CHECKBOX_RE = re.compile(r"^\s*[-*] \[( |x|X)\]")
_TITLE_SUFFIX = " Implementation Plan"


def extract_plan_path(tool_name: str | None, tool_input: dict[str, Any] | None) -> str | None:
    if tool_name not in _PLAN_TOOLS or not tool_input:
        return None
    path = tool_input.get("file_path")
    if isinstance(path, str) and _PLAN_PATH_RE.match(path):
        return path
    return None


def is_plan_todos(todos: list[TodoItem]) -> bool:
    return bool(todos) and all((t.metadata or {}).get("source") == PLAN_SOURCE for t in todos)


def _plan_title(lines: list[str], plan_path: str) -> str:
    for line in lines:
        match = _TITLE_RE.match(line)
        if match:
            title = match.group(1)
            return title.removesuffix(_TITLE_SUFFIX).strip() or title
    return PurePosixPath(plan_path).stem


def parse_plan(markdown: str, plan_path: str) -> list[TodoItem]:
    lines = markdown.splitlines()
    title = _plan_title(lines, plan_path)

    tasks: list[tuple[str, str]] = []
    counts: list[list[int]] = []
    current: list[int] | None = None
    for line in lines:
        task = _TASK_RE.match(line)
        if task:
            tasks.append((task.group(1), task.group(2)))
            current = [0, 0]
            counts.append(current)
        elif _SECTION_END_RE.match(line):
            current = None
        elif current is not None:
            box = _CHECKBOX_RE.match(line)
            if box:
                current[1] += 1
                if box.group(1) != " ":
                    current[0] += 1

    statuses: list[TodoStatus] = []
    for checked, total in counts:
        if total and checked == total:
            statuses.append(TodoStatus.COMPLETED)
        elif checked:
            statuses.append(TodoStatus.IN_PROGRESS)
        else:
            statuses.append(TodoStatus.PENDING)

    if TodoStatus.IN_PROGRESS not in statuses:
        seen_completed = False
        for i, status in enumerate(statuses):
            if status == TodoStatus.COMPLETED:
                seen_completed = True
            elif seen_completed and status == TodoStatus.PENDING:
                statuses[i] = TodoStatus.IN_PROGRESS
                break

    metadata = {"source": PLAN_SOURCE, "plan_title": title, "plan_path": plan_path}
    return [
        TodoItem(task_id=task_id, content=text, status=status, metadata=dict(metadata))
        for (task_id, text), status in zip(tasks, statuses, strict=True)
    ]
