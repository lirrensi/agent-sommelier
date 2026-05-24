# FILE: src/agent_sommelier/web/app.py
# PURPOSE: FastAPI application with WebSocket endpoint for real-time task dashboard.
# OWNS: HTTP static file serving, WebSocket message handling, and lifecycle management.
# EXPORTS: app (FastAPI instance)
# DOCS: .agents/reports/plan_web_ui_2026-05-24.md

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from agent_sommelier.tasks import (
    _ensure_config,
    _resolve_tasks_dir,
    add_task,
    build_overview_data,
    close_task,
    load_closed_yaml,
    load_tasks_yaml,
    update_task,
)

from .file_watcher import FileWatcher
from .ws_manager import ConnectionManager

manager = ConnectionManager()
watcher: FileWatcher | None = None


def _build_overview() -> dict[str, Any]:
    """Build the full overview dict from task data."""
    meta, tasks = load_tasks_yaml()
    closed_list = load_closed_yaml()
    config = _ensure_config(meta)
    ready_status = config.get("ready_status", "todo")
    close_status = config.get("close_status", "done")
    overview_data = build_overview_data(
        tasks, closed_list,
        ready_status=ready_status,
        close_status=close_status,
    )
    return overview_data


def _get_task_dict(task_id: str) -> dict[str, Any] | None:
    """Search active tasks then closed tasks for the given ID."""
    _, tasks = load_tasks_yaml()
    closed_list = load_closed_yaml()
    for t in tasks:
        if t.get("id") == task_id:
            return dict(t)
    for t in closed_list:
        if t.get("id") == task_id:
            return dict(t)
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start file watcher on startup, cancel on shutdown."""
    global watcher
    tasks_dir = _resolve_tasks_dir()
    if tasks_dir.exists():
        watcher = FileWatcher(tasks_dir, manager)
        watch_task = asyncio.create_task(watcher.start())
        try:
            yield
        finally:
            watch_task.cancel()
            try:
                await watch_task
            except asyncio.CancelledError:
                pass
    else:
        yield


app = FastAPI(lifespan=lifespan)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    client_id = await manager.connect(websocket)
    try:
        # Send initial overview and meta on connect
        overview = _build_overview()
        await manager.send_personal({"type": "overview", "data": overview}, websocket)

        # Message loop
        while True:
            raw = await websocket.receive_text()
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_personal(
                    {"type": "error", "message": "Invalid JSON"},
                    websocket,
                )
                continue

            msg_type = msg.get("type", "")

            try:
                if msg_type == "add_task":
                    task = add_task(
                        title=msg["title"],
                        priority=msg.get("priority"),
                        tags=msg.get("tags"),
                        source=msg.get("source", "web"),
                    )
                    await manager.broadcast({
                        "type": "task_created",
                        "task": dict(task),
                    })

                elif msg_type == "update_task":
                    task_id = msg["id"]
                    kwargs: dict[str, Any] = {}
                    if "status" in msg:
                        kwargs["status"] = msg["status"]
                    if "priority" in msg:
                        kwargs["priority"] = msg["priority"]
                    if "claimed" in msg:
                        kwargs["claimed"] = msg["claimed"]
                    task = update_task(task_id, **kwargs)
                    await manager.broadcast({
                        "type": "task_updated",
                        "task": dict(task),
                    })

                elif msg_type == "close_task":
                    task = close_task(task_id=msg["id"])
                    await manager.broadcast({
                        "type": "task_updated",
                        "task": dict(task),
                    })

                elif msg_type == "take_task":
                    meta, _ = load_tasks_yaml()
                    config = _ensure_config(meta)
                    active_status = config.get("active_status", "in-progress")
                    task = update_task(
                        task_id=msg["id"],
                        status=active_status,
                        claimed=msg.get("claimed", "agent"),
                    )
                    await manager.broadcast({
                        "type": "task_updated",
                        "task": dict(task),
                    })

                elif msg_type == "request_overview":
                    overview = _build_overview()
                    await manager.send_personal({
                        "type": "overview",
                        "data": overview,
                    }, websocket)

                elif msg_type == "ping":
                    await manager.send_personal({"type": "pong"}, websocket)

                else:
                    await manager.send_personal({
                        "type": "error",
                        "message": f"Unknown message type: {msg_type}",
                    }, websocket)

            except ValueError as e:
                await manager.send_personal({
                    "type": "error",
                    "message": str(e),
                }, websocket)
            except KeyError as e:
                await manager.send_personal({
                    "type": "error",
                    "message": f"Missing required field: {e}",
                }, websocket)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.disconnect(websocket)


# Mount static files if the dist/ directory exists
# NOTE: must be mounted after WebSocket route to avoid intercepting /ws
_dist_dir = Path(__file__).resolve().parent / "dist"
if _dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="static")
