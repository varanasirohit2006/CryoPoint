"""Bounded, redacted Chromium network telemetry attributed to browser actions."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    sensitive = ("password", "username", "email", "token", "secret", "auth", "key", "session", "credential")
    query = [(key, "[redacted]" if any(term in key.casefold() for term in sensitive) else value) for key, value in parse_qsl(parts.query, keep_blank_values=True)]
    host = parts.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    if parts.port:
        host += ":" + str(parts.port)
    return urlunsplit((parts.scheme, host, parts.path, urlencode(query), ""))


class NetworkTracer:
    def __init__(self, cdp: Any) -> None:
        self.cdp = cdp
        self.records: list[dict[str, Any]] = []
        self._requests: dict[str, dict[str, Any]] = {}
        self._change = asyncio.Event()

    async def start(self) -> None:
        await self.cdp.send("Network.enable")
        self.cdp.on("Network.requestWillBeSent", self._request)
        self.cdp.on("Network.responseReceived", self._response)
        self.cdp.on("Network.loadingFailed", self._failed)

    def begin_action(self) -> int:
        return len(self.records)

    def finish_action(self, cursor: int) -> list[dict[str, Any]]:
        result = [dict(record) for record in self.records[cursor:cursor + 40]]
        if len(self.records) > 500:
            self.records = self.records[-100:]
            self._requests.clear()
        return result

    async def settle(self, cursor: int, timeout: float = 2.0) -> None:
        """Wait for requests caused by this action, avoiding global network-idle."""
        await asyncio.sleep(0.05)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._change.clear()
            pending = any(str(record.get("request_id")) in self._requests for record in self.records[cursor:])
            if not pending:
                return
            try:
                await asyncio.wait_for(self._change.wait(), timeout=max(0.001, deadline - time.monotonic()))
            except TimeoutError:
                return

    def _request(self, event: dict[str, Any]) -> None:
        request = event.get("request", {})
        if not request.get("url", "").startswith(("http://", "https://")):
            return
        record = {"request_id": event.get("requestId", ""), "method": request.get("method", "GET"), "url": redact_url(request.get("url", "")), "resource_type": event.get("type", ""), "timestamp": event.get("timestamp", 0)}
        self.records.append(record)
        self._requests[str(event.get("requestId", ""))] = record
        self._change.set()

    def _response(self, event: dict[str, Any]) -> None:
        record = self._requests.pop(str(event.get("requestId", "")), None)
        if record is not None:
            response = event.get("response", {})
            record.update({"status": response.get("status"), "mime_type": response.get("mimeType", "")})
            self._change.set()

    def _failed(self, event: dict[str, Any]) -> None:
        record = self._requests.pop(str(event.get("requestId", "")), None)
        if record is not None:
            record["error"] = event.get("errorText", "Network request failed")
            self._change.set()
