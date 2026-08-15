from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class EventHub:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients[channel].add(websocket)

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        self._clients[channel].discard(websocket)

    async def broadcast(self, channel: str, message: dict[str, Any]) -> None:
        clients = tuple(self._clients[channel])
        if not clients:
            return
        results = await asyncio.gather(
            *(client.send_json(message) for client in clients), return_exceptions=True
        )
        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                self.disconnect(channel, client)

    async def broadcast_bytes(self, channel: str, payload: bytes) -> None:
        clients = tuple(self._clients[channel])
        if not clients:
            return
        results = await asyncio.gather(
            *(client.send_bytes(payload) for client in clients), return_exceptions=True
        )
        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                self.disconnect(channel, client)
