"""Tests for TrackView ref extraction and ticket mapping."""

import pytest

from app.core.trackview_mapping import (
    TrackViewRefs,
    extract_trackview_refs,
    map_ticket,
)

COLUMNS = {
    "Backlog": "pending",
    "Ready to Start": "pending",
    "Stuck": "pending",
    "In Progress": "in_progress",
    "QA": "in_progress",
    "Done": "completed",
}


def test_extracts_prefix_from_key() -> None:
    refs = extract_trackview_refs("mcp__trackview__get_ticket", {"key": "BSA-13"})
    assert refs.prefixes == frozenset({"BSA"})
    assert not refs.is_empty


def test_extracts_from_keys_list_and_ids() -> None:
    refs = extract_trackview_refs("mcp__trackview__move_tickets", {"keys": ["AN-1", "PAE-2"]})
    assert refs.prefixes == frozenset({"AN", "PAE"})
    refs = extract_trackview_refs(
        "mcp__trackview__list_tickets", {"projectId": "p1", "boardId": "b1"}
    )
    assert refs.project_ids == frozenset({"p1"})
    assert refs.board_ids == frozenset({"b1"})


def test_extracts_ticket_and_parent_keys() -> None:
    refs = extract_trackview_refs(
        "mcp__trackview__create_ticket", {"ticketKey": "AB-1", "parentKey": "CD-2"}
    )
    assert refs.prefixes == frozenset({"AB", "CD"})


def test_ignores_non_trackview_tools() -> None:
    assert extract_trackview_refs("Read", {"key": "BSA-13"}).is_empty
    assert extract_trackview_refs(None, {"key": "BSA-13"}).is_empty


@pytest.mark.parametrize("value", ["bsa-13", "BSA", 13])
def test_ignores_malformed_keys(value: object) -> None:
    assert extract_trackview_refs("mcp__trackview__get_ticket", {"key": value}).is_empty


def test_none_input() -> None:
    assert extract_trackview_refs("mcp__trackview__whoami", None).is_empty


def test_empty_refs_value() -> None:
    assert TrackViewRefs(frozenset(), frozenset(), frozenset()).is_empty


@pytest.mark.parametrize("kind", ["todo", "in_progress", "done"])
def test_maps_named_columns(kind: str) -> None:
    for column, status in COLUMNS.items():
        task = map_ticket({"key": "XX-1", "title": "t", "column": column}, kind)
        assert task is not None
        assert task.status == status


@pytest.mark.parametrize(
    ("kind", "status"),
    [("done", "completed"), ("todo", "pending"), ("in_progress", "in_progress")],
)
def test_unknown_column_falls_back_to_query_kind(kind: str, status: str) -> None:
    task = map_ticket({"key": "XX-1", "title": "t", "column": "Retired"}, kind)
    assert task is not None
    assert task.status == status


def test_task_fields() -> None:
    task = map_ticket(
        {"key": "XX-1", "title": "Fix it", "column": "Done", "assignees": ["Ana"]},
        "done",
    )
    assert task is not None
    assert task.task_id == task.linear_id == "XX-1"
    assert task.subject == "Fix it"
    assert task.assignee is None


def test_missing_key_or_title_returns_none() -> None:
    assert map_ticket({"title": "t"}, "todo") is None
    assert map_ticket({"key": "XX-1"}, "todo") is None
