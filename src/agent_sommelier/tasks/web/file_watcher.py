# FILE: src/agent_sommelier/web/file_watcher.py
# PURPOSE: Watch the tasks directory for YAML file changes and broadcast updates via WebSocket.
# OWNS: File-system change detection, filtering task-related YAML, and forwarding to ConnectionManager.
# EXPORTS: FileWatcher
# DOCS: .agents/reports/plan_web_ui_2026-05-24.md

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import yaml
from watchfiles import Change, awatch

from .ws_manager import ConnectionManager


class FileWatcher:
    """Watch a directory for task YAML changes and broadcast them."""

    def __init__(self, tasks_dir: Path, manager: ConnectionManager) -> None:
        self._tasks_dir = tasks_dir
        self._manager = manager
        self._last_broadcast: dict[str, float] = {}
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Run the watch loop forever, yielding to cancellation."""
        async for changes in awatch(str(self._tasks_dir)):
            await self._handle_changes(changes)

    async def _handle_changes(self, changes: set[tuple[Change, str]]) -> None:
        """Process a batch of file change events."""
        for change_type, file_path_str in changes:
            path = Path(file_path_str)

            # Skip non-YAML files
            if path.suffix.lower() not in (".yaml", ".yml"):
                continue

            # Skip files whose stem doesn't look like a task ID
            if not path.stem.startswith("TSK-"):
                continue

            task_id = path.stem

            # Echo suppression: skip if we broadcast this task_id within 0.1s
            now = time.time()
            last = self._last_broadcast.get(task_id, 0.0)
            if now - last < 0.1:
                continue
            self._last_broadcast[task_id] = now

            if change_type == Change.deleted:
                await self._manager.broadcast({
                    "type": "task_deleted",
                    "task_id": task_id,
                })
            elif change_type in (Change.added, Change.modified):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        task_dict: dict[str, Any] | None = yaml.safe_load(f)
                    if task_dict and "id" in task_dict:
                        await self._manager.broadcast({
                            "type": "task_updated",
                            "task": task_dict,
                        })
                except (yaml.YAMLError, OSError):
                    # Skip files we can't read or parse
                    pass
