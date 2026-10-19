"""Tests for the TrackView MCP client."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.trackview_client import TrackViewClient, TrackViewError

URL = "https://tv.example.test/api/mcp"


def _tool_result(payload: Any) -> dict[str, Any]:
    return {"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}


ToolHandler = Callable[[httpx.Request, dict[str, Any]], httpx.Response]


class Server:
    def __init__(self, tool_handler: ToolHandler) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self.tool_handler = tool_handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        method = body["method"]
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {}},
                headers={"Mcp-Session-Id": "s1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        return self.tool_handler(request, body)

    def methods(self) -> list[str]:
        return [b["method"] for b in self.bodies]

    def tool_calls(self) -> list[dict[str, Any]]:
        return [b for b in self.bodies if b["method"] == "tools/call"]


def _client(server: Server, token: str = "tok") -> TrackViewClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return TrackViewClient(URL, token, http=http)


async def test_initializes_then_calls_tool() -> None:
    server = Server(lambda r, b: httpx.Response(200, json=_tool_result({"userId": "u-1"})))
    client = _client(server)
    assert await client.whoami() == "u-1"
    assert server.methods() == ["initialize", "notifications/initialized", "tools/call"]
    assert server.bodies[0]["params"]["protocolVersion"] == "2025-03-26"
    assert "id" not in server.bodies[1]
    call = server.requests[2]
    assert call.headers["Mcp-Session-Id"] == "s1"
    assert call.headers["Authorization"] == "Bearer tok"
    assert call.headers["Accept"] == "application/json, text/event-stream"
    await client.aclose()


async def test_parses_event_stream_body() -> None:
    sse = "event: message\ndata: " + json.dumps(_tool_result({"userId": "u-1"})) + "\n\n"
    server = Server(
        lambda r, b: httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    )
    client = _client(server)
    assert await client.whoami() == "u-1"


async def test_list_tickets_follows_cursor() -> None:
    a = {"key": "XX-1", "title": "A"}
    b = {"key": "XX-2", "title": "B"}

    def handler(request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if "cursor" in body["params"]["arguments"]:
            return httpx.Response(200, json=_tool_result({"tickets": [b], "nextCursor": None}))
        return httpx.Response(200, json=_tool_result({"tickets": [a], "nextCursor": "c2"}))

    server = Server(handler)
    client = _client(server)
    assert await client.list_tickets("p-1", "u-1", "todo") == [a, b]
    calls = server.tool_calls()
    assert len(calls) == 2
    assert calls[0]["params"]["name"] == "list_tickets"
    args = calls[1]["params"]["arguments"]
    assert args["cursor"] == "c2"
    assert args["limit"] == 100
    assert args["projectId"] == "p-1"
    assert args["assigneeIds"] == ["u-1"]
    assert args["columnKind"] == "todo"
    assert "updatedSince" not in calls[0]["params"]["arguments"]


async def test_updated_since_passed_through() -> None:
    server = Server(
        lambda r, b: httpx.Response(200, json=_tool_result({"tickets": [], "nextCursor": None}))
    )
    client = _client(server)
    await client.list_tickets("p-1", "u-1", "done", "2026-10-12T00:00:00Z")
    args = server.tool_calls()[0]["params"]["arguments"]
    assert args["updatedSince"] == "2026-10-12T00:00:00Z"
    assert args["columnKind"] == "done"


async def test_list_projects_and_boards() -> None:
    def handler(request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        name = body["params"]["name"]
        payload = (
            [{"id": "p-1", "name": "Proj"}]
            if name == "list_projects"
            else [{"id": "b-1", "name": "Board"}]
        )
        return httpx.Response(200, json=_tool_result(payload))

    server = Server(handler)
    client = _client(server)
    assert await client.list_projects() == [{"id": "p-1", "name": "Proj"}]
    assert await client.list_boards("p-1") == [{"id": "b-1", "name": "Board"}]
    assert server.tool_calls()[1]["params"]["arguments"] == {"projectId": "p-1"}
    assert server.methods().count("initialize") == 1


async def test_reinitializes_on_expired_session() -> None:
    state = {"first": True}

    def handler(request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if state["first"]:
            state["first"] = False
            return httpx.Response(404)
        return httpx.Response(200, json=_tool_result({"userId": "u-1"}))

    server = Server(handler)
    client = _client(server)
    assert await client.whoami() == "u-1"
    assert server.methods().count("initialize") == 2
    assert len(server.tool_calls()) == 2


async def test_tool_error_raises_without_token() -> None:
    result = {"result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}}
    server = Server(lambda r, b: httpx.Response(200, json=result))
    client = _client(server)
    with pytest.raises(TrackViewError) as exc:
        await client.whoami()
    assert "tok" not in str(exc.value)


async def test_jsonrpc_error_raises() -> None:
    server = Server(
        lambda r, b: httpx.Response(200, json={"error": {"code": -32000, "message": "nope"}})
    )
    client = _client(server)
    with pytest.raises(TrackViewError):
        await client.whoami()


async def test_network_error_raises_trackview_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down for Bearer tok", request=request)

    client = TrackViewClient(
        URL, "tok", http=httpx.AsyncClient(transport=httpx.MockTransport(boom))
    )
    with pytest.raises(TrackViewError) as exc:
        await client.whoami()
    assert "tok" not in str(exc.value)


async def test_http_error_status_raises_without_token() -> None:
    server = Server(lambda r, b: httpx.Response(500, text="Bearer tok leaked"))
    client = _client(server)
    with pytest.raises(TrackViewError) as exc:
        await client.whoami()
    assert "tok" not in str(exc.value)


async def test_repr_hides_token() -> None:
    client = TrackViewClient(URL, "secret-token")
    assert "secret-token" not in repr(client)
    await client.aclose()
