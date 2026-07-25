"""WebSocket broadcast hub for scan and OCR status updates."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class EventHub:
    """Fan-out of JSON messages to subscribers of a topic."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, topic: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers[topic].add(websocket)

    async def unsubscribe(self, topic: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers[topic].discard(websocket)
            if not self._subscribers[topic]:
                self._subscribers.pop(topic, None)

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._subscribers.get(topic, ()))
        dead: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
        if dead:
            async with self._lock:
                for websocket in dead:
                    self._subscribers.get(topic, set()).discard(websocket)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, ()))


hub = EventHub()


def session_topic(session_id: int) -> str:
    return f"session:{session_id}"


OCR_TOPIC = "ocr"
