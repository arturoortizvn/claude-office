from app.core.plan_progress import (
    PLAN_SOURCE,
    extract_plan_path,
    is_plan_todos,
    parse_plan,
)
from app.models.common import TodoItem, TodoStatus

PLAN_PATH = "/r/docs/superpowers/plans/x.md"

PLAN = """# Stream Auth Implementation Plan

## Global Constraints
- [ ] not a task step

### Task 1: Token helpers
- [x] **Step 1: Write tests**
- [x] **Step 2: Implement**

### Task 2: Require access
- [x] **Step 1: Write tests**
- [ ] **Step 2: Implement**

### Task 3: Docs
- [ ] **Step 1: Edit docs**

## Pre-deploy handoff
- [ ] not a task step either
"""


def _statuses(todos: list[TodoItem]) -> list[TodoStatus]:
    return [t.status for t in todos]


def test_parses_tasks_and_status() -> None:
    todos = parse_plan(PLAN, PLAN_PATH)
    assert [(t.task_id, t.content, t.status) for t in todos] == [
        ("1", "Token helpers", TodoStatus.COMPLETED),
        ("2", "Require access", TodoStatus.IN_PROGRESS),
        ("3", "Docs", TodoStatus.PENDING),
    ]
    assert todos[0].metadata == {
        "source": "plan",
        "plan_title": "Stream Auth",
        "plan_path": PLAN_PATH,
    }


def test_ignores_checkboxes_outside_tasks() -> None:
    ticked = PLAN.replace("- [ ] **Step", "- [x] **Step")
    ticked = ticked.replace("- [ ] **Step 2", "- [x] **Step 2")
    todos = parse_plan(ticked, PLAN_PATH)
    assert _statuses(todos) == [TodoStatus.COMPLETED] * 3


def test_next_pending_after_completed_is_in_progress() -> None:
    md = "# P\n\n### Task 1: A\n- [x] a\n\n### Task 2: B\n- [ ] b\n\n### Task 3: C\n- [ ] c\n"
    todos = parse_plan(md, PLAN_PATH)
    assert _statuses(todos) == [
        TodoStatus.COMPLETED,
        TodoStatus.IN_PROGRESS,
        TodoStatus.PENDING,
    ]


def test_nothing_ticked_is_all_pending() -> None:
    md = "# P\n\n### Task 1: A\n- [ ] a\n\n### Task 2: B\n- [ ] b\n"
    assert _statuses(parse_plan(md, PLAN_PATH)) == [TodoStatus.PENDING] * 2


def test_parses_h2_task_headings() -> None:
    todos = parse_plan("# P\n\n## Task 1: A\n- [ ] a\n", PLAN_PATH)
    assert [(t.task_id, t.content) for t in todos] == [("1", "A")]


def test_crlf_line_endings() -> None:
    todos = parse_plan(PLAN.replace("\n", "\r\n"), PLAN_PATH)
    assert _statuses(todos) == [
        TodoStatus.COMPLETED,
        TodoStatus.IN_PROGRESS,
        TodoStatus.PENDING,
    ]
    assert [t.content for t in todos] == ["Token helpers", "Require access", "Docs"]


def test_no_tasks_returns_empty_list() -> None:
    assert parse_plan("# X\n\nno tasks", PLAN_PATH) == []


def test_title_falls_back_to_file_stem() -> None:
    todos = parse_plan("### Task 1: A\n- [ ] a\n", "/r/docs/plans/my-plan.md")
    assert todos[0].metadata is not None
    assert todos[0].metadata["plan_title"] == "my-plan"


def test_extract_plan_path() -> None:
    p = "/r/docs/superpowers/plans/a.md"
    assert extract_plan_path("Read", {"file_path": p}) == p
    assert extract_plan_path("Edit", {"file_path": "/r/docs/plans/a.md"}) == "/r/docs/plans/a.md"
    assert extract_plan_path("Read", {"file_path": "/r/docs/specs/a.md"}) is None
    assert extract_plan_path("Read", {"file_path": "/r/docs/plans/a.txt"}) is None
    assert extract_plan_path("Bash", {"command": "cat /r/docs/plans/a.md"}) is None
    assert extract_plan_path("Read", None) is None


def test_is_plan_todos() -> None:
    plan_item = TodoItem(content="a", status=TodoStatus.PENDING, metadata={"source": PLAN_SOURCE})
    other = TodoItem(content="b", status=TodoStatus.PENDING)
    assert is_plan_todos([]) is False
    assert is_plan_todos([plan_item, plan_item]) is True
    assert is_plan_todos([plan_item, other]) is False


def test_fenced_comment_does_not_end_task_section() -> None:
    md = (
        "# P\n\n### Task 1: A\n- [x] a\n\n```bash\n# Expected: FAIL\n"
        "- [ ] fake\n### Task 9: fake\n```\n\n- [x] b\n\n### Task 2: B\n- [ ] c\n"
    )
    todos = parse_plan(md, PLAN_PATH)
    assert [(t.task_id, t.status) for t in todos] == [
        ("1", TodoStatus.COMPLETED),
        ("2", TodoStatus.IN_PROGRESS),
    ]


def test_title_ignores_fenced_heading() -> None:
    md = "```bash\n# not a title\n```\n\n# Real Implementation Plan\n\n### Task 1: A\n- [ ] a\n"
    todos = parse_plan(md, "/r/docs/plans/my-plan.md")
    assert todos[0].metadata is not None
    assert todos[0].metadata["plan_title"] == "Real"
    md2 = "```bash\n# not a title\n```\n\n### Task 1: A\n- [ ] a\n"
    todos2 = parse_plan(md2, "/r/docs/plans/my-plan.md")
    assert todos2[0].metadata is not None
    assert todos2[0].metadata["plan_title"] == "my-plan"
