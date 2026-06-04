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
from starlette.responses import FileResponse

from agent_sommelier.tasks import (
    _ensure_config,
    _resolve_tasks_dir,
    add_task,
    build_overview_data,
    close_task,
    delete_task,
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
    config = _ensure_config(meta)
    ready_status = config.get("ready_status", "todo")
    close_status = config.get("close_status", "done")
    # All tasks live in one unified list; closed_list is empty (deprecated)
    overview_data = build_overview_data(
        tasks, [],
        ready_status=ready_status,
        close_status=close_status,
    )
    # Add done/completed section for the web dashboard
    terminal_statuses = {"done", "cancelled", "abandoned"}
    done_tasks = [dict(t) for t in tasks if t.get("status") in terminal_statuses]
    for t in done_tasks:
        t["hint"] = None
    done_tasks.sort(key=lambda t: t.get("updated", t.get("created", "")), reverse=True)
    overview_data["done"] = done_tasks
    overview_data["counts"]["done"] = len(done_tasks)
    # Include status config so frontend knows available columns
    overview_data["statuses"] = config.get("statuses", [])
    return overview_data


def _get_task_dict(task_id: str) -> dict[str, Any] | None:
    """Search all tasks for the given ID."""
    _, tasks = load_tasks_yaml()
    for t in tasks:
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
    print(f"[WS] Client {client_id} connected — sending overview...")
    try:
        # Send initial overview and meta on connect
        overview = _build_overview()
        await manager.send_personal({"type": "overview", "data": overview}, websocket)
        print(f"[WS] Overview sent to {client_id}")

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
                        claimed=msg.get("claimed"),
                        created_by=msg.get("createdBy"),
                        order=msg.get("order", 0),
                        status=msg.get("status"),
                    )
                    await manager.broadcast({
                        "type": "task_created",
                        "task": dict(task),
                    })

                elif msg_type == "update_task":
                    task_id = msg["id"]
                    kwargs: dict[str, Any] = {}
                    if "title" in msg:
                        kwargs["title"] = msg["title"]
                    if "status" in msg:
                        kwargs["status"] = msg["status"]
                    if "priority" in msg:
                        kwargs["priority"] = msg["priority"]
                    if "tags" in msg:
                        kwargs["tags"] = msg["tags"]
                        kwargs["replace_tags"] = msg.get("replace_tags", True)
                    if "claimed" in msg:
                        kwargs["claimed"] = msg["claimed"]
                    if "created_by" in msg:
                        kwargs["created_by"] = msg["created_by"]
                    if "notes" in msg:
                        kwargs["notes"] = msg["notes"]
                        kwargs["replace_notes"] = msg.get("replace_notes", True)
                    if "order" in msg:
                        kwargs["order"] = msg["order"]
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

                elif msg_type == "delete_task":
                    delete_task(task_id=msg["id"])
                    await manager.broadcast({
                        "type": "task_deleted",
                        "task_id": msg["id"],
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
        print(f"[WS] Client {client_id} disconnected")
    except Exception as e:
        print(f"[WS] Client {client_id} error: {e}")
    finally:
        manager.disconnect(websocket)


# Serve static SPA files — catch-all HTTP route (does NOT intercept WebSocket scopes)
# NOTE: using api_route() instead of mount() so WebSocket scopes are never intercepted
_dist_dir = Path(__file__).resolve().parent / "dist"
if _dist_dir.is_dir():
    @app.api_route("/", methods=["GET"])
    async def serve_root():
        return FileResponse(str(_dist_dir / "index.html"))

    @app.api_route("/{path:path}", methods=["GET"])
    async def serve_spa(path: str):
        """Serve static files or fall back to index.html."""
        file_path = _dist_dir / path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_dist_dir / "index.html"))
