# FILE: src/agent_sommelier/web/ws_manager.py
# PURPOSE: Manage WebSocket connections and provide broadcast utilities.
# OWNS: Connection lifecycle tracking and message fan-out to all connected clients.
# EXPORTS: ConnectionManager
# DOCS: .agents/reports/plan_web_ui_2026-05-24.md

from __future__ import annotations

import time
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    """Track active WebSocket connections and broadcast messages."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._last_broadcast: dict[str, float] = {}

    async def connect(self, websocket: WebSocket) -> str:
        """Accept and register a new WebSocket connection. Returns a client_id string."""
        await websocket.accept()
        self._connections.append(websocket)
        return f"client_{id(websocket)}"

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from the connection list."""
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients. Removes dead connections."""
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)

    async def send_personal(self, message: dict[str, Any], websocket: WebSocket) -> None:
        """Send a JSON message to a single client."""
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(websocket)

    @property
    def active_count(self) -> int:
        """Return the number of active connections."""
        return len(self._connections)
