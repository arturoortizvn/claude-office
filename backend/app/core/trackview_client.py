"""Read-only async client for TrackView's MCP JSON-RPC endpoint."""

import asyncio
import json
from itertools import count
from typing import Any, cast

import httpx

PROTOCOL_VERSION = "2025-03-26"
REQUEST_TIMEOUT = 10.0
PAGE_LIMIT = 100


class TrackViewError(Exception):
    """Raised for any TrackView failure; never carries the API token."""


class TrackViewClient:
    def __init__(self, url: str, token: str, http: httpx.AsyncClient | None = None) -> None:
        self._url = url
        self._token = token
        self._http = http or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        self._session_id: str | None = None
        self._ids = count(1)
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"TrackViewClient(url={self._url!r})"

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            return await self._http.post(
                self._url,
                content=json.dumps(payload),
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise TrackViewError(f"TrackView request failed: {type(exc).__name__}") from None

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise TrackViewError(f"TrackView HTTP {response.status_code}")
        text = response.text
        if "text/event-stream" in response.headers.get("content-type", ""):
            data_lines = [
                line[5:].strip() for line in text.splitlines() if line.startswith("data:")
            ]
            if not data_lines:
                raise TrackViewError("TrackView returned an empty event stream")
            text = data_lines[-1]
        try:
            decoded: Any = json.loads(text)
        except ValueError:
            raise TrackViewError("TrackView returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise TrackViewError("TrackView returned an unexpected payload")
        return cast(dict[str, Any], decoded)

    async def _initialize(self) -> None:
        self._session_id = None
        response = await self._post(
            {
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "claude-office", "version": "1"},
                },
            }
        )
        message = self._decode(response)
        if "error" in message:
            raise TrackViewError("TrackView initialize failed")
        self._session_id = response.headers.get("Mcp-Session-Id")
        notified = await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        if notified.status_code >= 400:
            raise TrackViewError(f"TrackView HTTP {notified.status_code}")

    async def _ensure_session(self) -> None:
        async with self._lock:
            if self._session_id is None:
                await self._initialize()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        await self._ensure_session()
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response = await self._post(payload)
        if response.status_code in (400, 404) and self._session_id:
            async with self._lock:
                await self._initialize()
            response = await self._post(payload)
        message = self._decode(response)
        if "error" in message:
            raise TrackViewError(f"TrackView error calling {name}")
        result: Any = message.get("result")
        if not isinstance(result, dict):
            raise TrackViewError(f"TrackView returned no result for {name}")
        result = cast(dict[str, Any], result)
        if result.get("isError"):
            raise TrackViewError(f"TrackView tool {name} reported an error")
        try:
            return json.loads(cast(str, result["content"][0]["text"]))
        except (KeyError, IndexError, TypeError, ValueError):
            raise TrackViewError(f"TrackView returned malformed {name} content") from None

    async def whoami(self) -> str:
        data = await self.call_tool("whoami", {})
        try:
            return str(data["userId"])
        except (KeyError, TypeError):
            raise TrackViewError("TrackView whoami returned no userId") from None

    async def list_projects(self) -> list[dict[str, Any]]:
        return await self.call_tool("list_projects", {})

    async def list_boards(self, project_id: str) -> list[dict[str, Any]]:
        return await self.call_tool("list_boards", {"projectId": project_id})

    async def list_tickets(
        self,
        project_id: str,
        assignee_id: str,
        column_kind: str,
        updated_since: str | None = None,
    ) -> list[dict[str, Any]]:
        args: dict[str, Any] = {
            "projectId": project_id,
            "assigneeIds": [assignee_id],
            "columnKind": column_kind,
            "limit": PAGE_LIMIT,
        }
        if updated_since:
            args["updatedSince"] = updated_since
        tickets: list[dict[str, Any]] = []
        while True:
            page = await self.call_tool("list_tickets", args)
            try:
                tickets.extend(page["tickets"])
                cursor = page.get("nextCursor")
            except (KeyError, TypeError, AttributeError):
                raise TrackViewError("TrackView list_tickets malformed page") from None
            if not cursor:
                return tickets
            args = {**args, "cursor": cursor}
