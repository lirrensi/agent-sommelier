# Plan: Tasks CLI Module

_Create `src/agentcli_helpers/tasks.py` — a lightweight task management CLI tool using YAML files for storage, with nine commands._

---

# Checklist

- [x] Step 1: Add `pyyaml` dependency to pyproject.toml
- [x] Step 2: Install pyyaml into the project venv
- [x] Step 3: Create `src/agentcli_helpers/tasks.py` — imports, constants, path helpers, and file I/O functions
- [x] Step 4: Add data operation functions to tasks.py — find, filter, sort, add_task, update_task, complete_task
- [x] Step 5: Add CLI layer (Click group and all 10 subcommands) to tasks.py
- [x] Step 6: Add `tasks` entry point to pyproject.toml
- [x] Step 7: Verify bootstrap — `uv run tasks init` creates files correctly
- [x] Step 8: Verify write commands — add, update, done
- [x] Step 9: Verify read commands — list, show, next, status, history, inbox
- [x] Step 10: Verify idempotent init and edge cases

---

## Context

**Repository:** `C:\Users\rx\001_Code\100_M\AgentCLI_Helpers`

**Existing pattern:** Each tool is a single Python file in `src/agentcli_helpers/`. CLI uses Click groups (`@click.group()`), Rich for table output, plain functions with plain dicts for data. Entry points registered in `pyproject.toml` under `[project.scripts]`.

**Design doc:** `agent_chat/design_tasks_2026-05-10.md` — full design reference.

**Relevant existing files:**
- `pyproject.toml` — dependencies and entry points live here
- `src/agentcli_helpers/__init__.py` — package init, version string
- `src/agentcli_helpers/crony.py` — reference for Click group pattern with Rich tables
- `src/agentcli_helpers/notify.py` — reference for simpler single-command Click pattern

**New files to create:**
- `src/agentcli_helpers/tasks.py` — the entire module (~400–500 lines)

**Files created at runtime by `tasks init`:**
- `tasks/inbox.md` — empty file
- `tasks/tasks.yaml` — YAML with `meta: {counter: 0}` and `tasks: []`
- `tasks/done.yaml` — YAML with `[]`

**No other files are touched.** No `.tasks-counter`, no lockfile, no `.gitattributes` changes.

## Prerequisites

- Python >= 3.10 in the project venv at `.venv/`
- `uv` available on PATH
- `click` and `rich` already installed (core deps in pyproject.toml)
- Working directory: `C:\Users\rx\001_Code\100_M\AgentCLI_Helpers`

## Scope Boundaries

**IN SCOPE:**
- `pyproject.toml` — add `pyyaml` to dependencies, add `tasks` entry point
- `src/agentcli_helpers/tasks.py` — new file, full implementation

**OUT OF SCOPE — DO NOT TOUCH:**
- `docs/product.md`, `docs/arch.md` — canon docs, not updating yet
- `src/agentcli_helpers/__init__.py` — no changes needed
- Any existing module (crony.py, bg.py, notify.py, screenshot.py)
- `README.md`, `LICENSE`, `.gitignore`
- `agent_chat/design_tasks_2026-05-10.md` — design reference, read-only

---

## Steps

### Step 1: Add `pyyaml` to pyproject.toml dependencies

Open `pyproject.toml`. Find the `dependencies` list (line 11). The current list is:

```
dependencies = [
    "click>=8.1.0",
    "rich>=13.0.0",
    "pillow>=10.0.0",
    "psutil>=6.0.0",
]
```

Add `"pyyaml>=6.0"` to the end of this list, so it becomes:

```
dependencies = [
    "click>=8.1.0",
    "rich>=13.0.0",
    "pillow>=10.0.0",
    "psutil>=6.0.0",
    "pyyaml>=6.0",
]
```

Save the file.

✅ Success: `pyproject.toml` contains `"pyyaml>=6.0"` in the dependencies list.
❌ If failed: `pyproject.toml` is missing or unreadable — stop, report the error.

---

### Step 2: Install pyyaml into the project venv

Run from the project root:

```
uv sync
```

This installs the new `pyyaml` dependency into `.venv/`.

Verify by running:

```
uv run python -c "import yaml; print(yaml.__version__)"
```

✅ Success: Command prints a version string (e.g., `6.0.2`) and exits code 0.
❌ If failed: `uv sync` returned non-zero — stop, report the full error output.

---

### Step 3: Create tasks.py — imports, constants, path helpers, file I/O

Create file `src/agentcli_helpers/tasks.py` with the following content. Write the entire block below as one file write operation.

```python
"""Task management CLI for AI agents.

Usage:
    tasks init              # Bootstrap task files
    tasks next              # Show highest-priority todo
    tasks list              # List active tasks
    tasks show TSK-0001     # Full detail of one task
    tasks history           # Recently completed tasks
    tasks status            # Session overview
    tasks add "Title"       # Create a task
    tasks update TSK-0001   # Modify a task
    tasks done TSK-0001     # Complete a task
    tasks inbox             # Print inbox contents
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TASKS_DIR_NAME = "tasks"
INBOX_FILE_NAME = "inbox.md"
TASKS_FILE_NAME = "tasks.yaml"
DONE_FILE_NAME = "done.yaml"

VALID_STATUSES = {"todo", "in-progress", "done"}
VALID_PRIORITIES = {"urgent", "high", "medium", "low"}
VALID_SOURCES = {"inbox", "audit", "test", "jira", "agent", "idea"}

PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_tasks_dir() -> Path:
    """Find or default to the tasks/ directory relative to cwd."""
    cwd = Path.cwd()
    tasks_dir = cwd / TASKS_DIR_NAME
    return tasks_dir


def _tasks_file() -> Path:
    return _resolve_tasks_dir() / TASKS_FILE_NAME


def _done_file() -> Path:
    return _resolve_tasks_dir() / DONE_FILE_NAME


def _inbox_file() -> Path:
    return _resolve_tasks_dir() / INBOX_FILE_NAME


# ---------------------------------------------------------------------------
# YAML file I/O
# ---------------------------------------------------------------------------


def load_tasks_yaml() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load tasks.yaml. Returns (meta, tasks_list).

    If file does not exist, raises FileNotFoundError.
    If YAML is malformed, raises yaml.YAMLError.
    If file exists but is empty or has unexpected structure, returns defaults.
    """
    path = _tasks_file()
    if not path.exists():
        raise FileNotFoundError(f"Tasks file not found: {path}\nRun 'tasks init' first.")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    meta = raw.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
    tasks = raw.get("tasks", [])
    if not isinstance(tasks, list):
        tasks = []
    return meta, tasks


def save_tasks_yaml(meta: dict[str, Any], tasks: list[dict[str, Any]]) -> None:
    """Write tasks.yaml with meta and tasks list.

    Tasks are stored newest-first (by 'created' descending).
    Missing optional fields are excluded from output.
    """
    path = _tasks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"meta": meta, "tasks": _strip_none_fields_from_list(tasks)}
    path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False,
                                    allow_unicode=True), encoding="utf-8")


def load_done_yaml() -> list[dict[str, Any]]:
    """Load done.yaml. Returns list of completed tasks.

    File is oldest-first (append-only). Returns [] if file doesn't exist.
    """
    path = _done_file()
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        return []
    return raw


def save_done_yaml(done_list: list[dict[str, Any]]) -> None:
    """Write done.yaml. Appends are handled by passing the full list.

    Done file is oldest-first (chronological order).
    """
    path = _done_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(_strip_none_fields_from_list(done_list),
                                    default_flow_style=False, sort_keys=False,
                                    allow_unicode=True), encoding="utf-8")


def load_inbox() -> str:
    """Read inbox.md contents. Returns empty string if file doesn't exist."""
    path = _inbox_file()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_none_fields(task: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with only non-None values."""
    return {k: v for k, v in task.items() if v is not None}


def _strip_none_fields_from_list(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_strip_none_fields(t) for t in tasks]


def _now_date() -> str:
    """Return today's date as YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    """Return current ISO 8601 timestamp."""
    return datetime.now().isoformat()


def _format_id(counter: int) -> str:
    """Format a counter integer into TSK-NNNN ID."""
    return f"TSK-{counter:04d}"


def _priority_sort_key(task: dict[str, Any]) -> int:
    """Return sort weight for priority (lower = higher priority)."""
    p = task.get("priority")
    return PRIORITY_ORDER.get(p, 99) if p else 99


def _find_task_by_id(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
    """Find a task by its 'id' field. Returns None if not found."""
    for t in tasks:
        if t.get("id") == task_id:
            return t
    return None


def _resolve_related(task_id: str, tasks_yaml: list[dict[str, Any]],
                     done_yaml: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Look up a task by ID in both active and done lists. Returns the task dict or None."""
    found = _find_task_by_id(tasks_yaml, task_id)
    if found:
        return found
    return _find_task_by_id(done_yaml, task_id)


def _collect_all_tags(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """Count tag frequency across all given tasks."""
    counts: dict[str, int] = {}
    for t in tasks:
        for tag in t.get("tags", []) or []:
            counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def _inbox_line_count() -> int:
    """Return number of non-empty lines in inbox.md."""
    content = load_inbox()
    return len([line for line in content.splitlines() if line.strip()])
```

✅ Success: File exists at `src/agentcli_helpers/tasks.py` and contains all functions above. Quick syntax check by running `uv run python -c "import ast; ast.parse(open('src/agentcli_helpers/tasks.py').read()); print('OK')"` — must print `OK`.
❌ If failed: File creation failed or syntax error — stop, report exact error.

---

### Step 4: Add data operation functions to tasks.py

Append the following functions to `src/agentcli_helpers/tasks.py`. These are the write and query operations that the CLI commands will call.

```python
# ---------------------------------------------------------------------------
# Data operations
# ---------------------------------------------------------------------------


def init_task_files() -> dict[str, str]:
    """Bootstrap the tasks/ directory and three files. Idempotent.

    Returns a dict mapping file path -> action taken ('created' | 'exists').
    """
    tasks_dir = _resolve_tasks_dir()
    results: dict[str, str] = {}

    tasks_dir.mkdir(parents=True, exist_ok=True)

    # inbox.md
    inbox = _inbox_file()
    if not inbox.exists():
        inbox.write_text("", encoding="utf-8")
        results[str(inbox)] = "created"
    else:
        results[str(inbox)] = "exists"

    # tasks.yaml
    tasks_file = _tasks_file()
    if not tasks_file.exists():
        data = {"meta": {"counter": 0}, "tasks": []}
        tasks_file.write_text(
            yaml.safe_dump(data, default_flow_style=False, sort_keys=False,
                           allow_unicode=True), encoding="utf-8")
        results[str(tasks_file)] = "created"
    else:
        # Validate existing file is parseable YAML
        try:
            load_tasks_yaml()
            results[str(tasks_file)] = "exists"
        except yaml.YAMLError:
            results[str(tasks_file)] = "invalid"
            return results

    # done.yaml
    done_file = _done_file()
    if not done_file.exists():
        done_file.write_text("[]\n", encoding="utf-8")
        results[str(done_file)] = "created"
    else:
        results[str(done_file)] = "exists"

    return results


def next_counter_and_id() -> tuple[int, str]:
    """Read meta.counter, increment it, save, and return (new_counter, formatted_id).

    Raises FileNotFoundError if tasks.yaml doesn't exist.
    """
    meta, tasks = load_tasks_yaml()
    counter = meta.get("counter", 0)
    if not isinstance(counter, int):
        counter = 0
    new_counter = counter + 1
    meta["counter"] = new_counter
    save_tasks_yaml(meta, tasks)
    return new_counter, _format_id(new_counter)


def add_task(title: str, priority: str | None = None, tags: list[str] | None = None,
             source: str | None = None, related: str | None = None,
             notes: str | None = None) -> dict[str, Any]:
    """Create a new task and prepend it to tasks.yaml.

    Returns the created task dict.
    """
    meta, tasks = load_tasks_yaml()
    counter = meta.get("counter", 0)
    if not isinstance(counter, int):
        counter = 0
    new_counter = counter + 1
    meta["counter"] = new_counter
    task_id = _format_id(new_counter)

    task: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "status": "todo",
        "created": _now_date(),
    }
    if priority:
        task["priority"] = priority
    if tags:
        task["tags"] = [t.lower().strip().replace(" ", "-") for t in tags]
    if source:
        task["source"] = source
    if related:
        task["related"] = related
    if notes:
        task["notes"] = notes

    # Prepend: newest at top
    tasks.insert(0, task)
    save_tasks_yaml(meta, tasks)
    return task


def update_task(task_id: str, status: str | None = None, priority: str | None = None,
                tags: list[str] | None = None, related: str | None = None,
                notes: str | None = None) -> dict[str, Any]:
    """Update fields on an existing task. Tags are appended, not replaced.

    Returns the updated task dict.
    Raises ValueError if task not found.
    """
    meta, tasks = load_tasks_yaml()
    task = _find_task_by_id(tasks, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")

    if status is not None:
        task["status"] = status
    if priority is not None:
        task["priority"] = priority
    if tags is not None:
        existing = task.get("tags", []) or []
        new_tags = [t.lower().strip().replace(" ", "-") for t in tags]
        for tag in new_tags:
            if tag not in existing:
                existing.append(tag)
        task["tags"] = existing
    if related is not None:
        task["related"] = related
    if notes is not None:
        task["notes"] = notes

    task["updated"] = _now_iso()
    save_tasks_yaml(meta, tasks)
    return task


def complete_task(task_id: str, note: str | None = None) -> dict[str, Any]:
    """Move a task from tasks.yaml to done.yaml.

    Sets status='done', adds 'completed' timestamp.
    Appends optional --note to the existing notes field.
    Returns the completed task dict.
    Raises ValueError if task not found or already done.
    """
    meta, tasks = load_tasks_yaml()
    task = _find_task_by_id(tasks, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    if task.get("status") == "done":
        raise ValueError(f"Task already done: {task_id}")

    # Remove from active list
    tasks.remove(task)

    # Update the task
    task["status"] = "done"
    task["completed"] = _now_iso()
    task["updated"] = _now_iso()
    if note:
        existing_notes = task.get("notes", "")
        task["notes"] = f"{existing_notes}\n{note}".strip() if existing_notes else note

    # Save active list
    save_tasks_yaml(meta, tasks)

    # Append to done.yaml (oldest-first: append to bottom)
    done_list = load_done_yaml()
    done_list.append(task)
    save_done_yaml(done_list)

    return task


def filter_tasks(tasks: list[dict[str, Any]], status: str | None = None,
                 tag: str | None = None, priority: str | None = None,
                 source: str | None = None) -> list[dict[str, Any]]:
    """Filter a task list by status, tag, priority, and/or source. All filters ANDed."""
    result = tasks
    if status:
        result = [t for t in result if t.get("status") == status]
    if priority:
        result = [t for t in result if t.get("priority") == priority]
    if source:
        result = [t for t in result if t.get("source") == source]
    if tag:
        result = [t for t in result if tag in (t.get("tags") or [])]
    return result
```

✅ Success: Appended all functions to `src/agentcli_helpers/tasks.py`. Run `uv run python -c "import ast; ast.parse(open('src/agentcli_helpers/tasks.py').read()); print('OK')"` — must print `OK`.
❌ If failed: Syntax error in the appended code — stop, report the exact error line.

---

### Step 5: Add CLI layer (Click group and all 10 subcommands) to tasks.py

Append the following to `src/agentcli_helpers/tasks.py`. This is the Click CLI layer — one group with 10 subcommands. Write all of it in a single append operation.

```python
# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

console = Console()


@click.group()
def main():
    """Lightweight task management for AI agents."""
    pass


# ---- init ----

@main.command()
def init():
    """Bootstrap task files in the current directory. Safe to run repeatedly."""
    results = init_task_files()
    for path, action in results.items():
        if action == "created":
            click.echo(f"  Created {path}")
        elif action == "invalid":
            click.echo(f"  Error: {path} exists but is invalid YAML. Delete it and re-run.", err=True)
            sys.exit(1)

    if all(v == "exists" for v in results.values()):
        click.echo("All files already exist. Nothing to do.")
    elif any(v == "created" for v in results.values()):
        click.echo("Task system initialized.")


# ---- add ----

@main.command()
@click.argument("title")
@click.option("--tag", "-t", "tags", multiple=True, help="Tag(s) to apply (repeatable)")
@click.option("--priority", "-p", type=click.Choice(list(VALID_PRIORITIES)), help="Priority level")
@click.option("--source", "-s", type=click.Choice(list(VALID_SOURCES)), default="agent", help="Source of the task")
@click.option("--related", "-r", help="Related task ID (e.g. TSK-0042)")
@click.option("--notes", "-n", help="Freeform notes")
def add(title: str, tags: tuple[str, ...], priority: str | None, source: str,
        related: str | None, notes: str | None):
    """Create a new task. ID is auto-generated."""
    task = add_task(
        title=title,
        priority=priority,
        tags=list(tags) if tags else None,
        source=source,
        related=related,
        notes=notes,
    )
    click.echo(f"Created {task['id']}: {title}")


# ---- list ----

@main.command("list")
@click.option("--status", type=click.Choice(list(VALID_STATUSES)), help="Filter by status")
@click.option("--tag", help="Filter by tag")
@click.option("--priority", type=click.Choice(list(VALID_PRIORITIES)), help="Filter by priority")
@click.option("--source", type=click.Choice(list(VALID_SOURCES)), help="Filter by source")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def list_cmd(status: str | None, tag: str | None, priority: str | None,
             source: str | None, json_output: bool):
    """List active tasks (not done). Newest first."""
    try:
        _, tasks = load_tasks_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    tasks = [t for t in tasks if t.get("status") != "done"]
    tasks = filter_tasks(tasks, status=status, tag=tag, priority=priority, source=source)

    if json_output:
        click.echo(__import__("json").dumps(tasks, indent=2))
        return

    if not tasks:
        click.echo("No tasks found.")
        return

    table = Table(title="Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Status", style="yellow")
    table.add_column("Priority", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("Tags", style="green")

    for t in tasks:
        table.add_row(
            t.get("id", "?"),
            t.get("status", "?"),
            t.get("priority", "-"),
            t.get("title", "?"),
            ", ".join(t.get("tags", []) or []),
        )

    console.print(table)


# ---- show ----

@main.command()
@click.argument("task_id")
def show(task_id: str):
    """Show full detail of one task. Resolves related task inline."""
    try:
        _, tasks = load_tasks_yaml()
        done_list = load_done_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    task = _find_task_by_id(tasks, task_id)
    if task is None:
        task = _find_task_by_id(done_list, task_id)
    if task is None:
        click.echo(f"Task not found: {task_id}", err=True)
        sys.exit(1)

    click.echo(f"\n  [{task.get('id')}] {task.get('status')}  priority: {task.get('priority', '-')}")
    click.echo(f"  {task.get('title')}")

    tags = task.get("tags")
    if tags:
        click.echo(f"  tags: {', '.join(tags)}")

    if task.get("source"):
        src = task["source"]
        if task.get("source_ref"):
            src += f" ({task['source_ref']})"
        click.echo(f"  source: {src}")

    # Resolve related inline
    related_id = task.get("related")
    if related_id:
        target = _resolve_related(related_id, tasks, done_list)
        if target:
            click.echo(f"  related: {related_id} ({target.get('status')}) — \"{target.get('title')}\"")
        else:
            click.echo(f"  related: {related_id} (not found)")

    click.echo(f"  created: {task.get('created')}")
    if task.get("updated"):
        click.echo(f"  updated: {task.get('updated')}")
    if task.get("completed"):
        click.echo(f"  completed: {task.get('completed')}")
    if task.get("notes"):
        click.echo(f"  notes: >\n    {task['notes']}")
    click.echo("")


# ---- update ----

@main.command()
@click.argument("task_id")
@click.option("--status", type=click.Choice(list(VALID_STATUSES)), help="Change status")
@click.option("--tag", "-t", "tags", multiple=True, help="Tag(s) to append (repeatable)")
@click.option("--priority", "-p", type=click.Choice(list(VALID_PRIORITIES)), help="Change priority")
@click.option("--related", "-r", help="Set related task ID")
@click.option("--notes", "-n", help="Set notes (replaces existing)")
def update(task_id: str, status: str | None, tags: tuple[str, ...], priority: str | None,
           related: str | None, notes: str | None):
    """Update fields on a task. Tags are appended to existing."""
    try:
        task = update_task(
            task_id=task_id,
            status=status,
            priority=priority,
            tags=list(tags) if tags else None,
            related=related,
            notes=notes,
        )
        click.echo(f"Updated {task['id']}: {task['title']}")
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


# ---- done ----

@main.command()
@click.argument("task_id")
@click.option("--note", "-n", help="Closing note to append")
def done(task_id: str, note: str | None):
    """Mark a task as done. Moves it to done.yaml."""
    try:
        task = complete_task(task_id, note=note)
        click.echo(f"Completed {task['id']}: {task['title']}")
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


# ---- next ----

@main.command()
@click.option("--take", default="1", help="Number of tasks, or 'all'")
@click.option("--tag", help="Filter by tag")
@click.option("--priority", type=click.Choice(list(VALID_PRIORITIES)), help="Filter by priority")
@click.option("--skip-related", is_flag=True, help="Exclude tasks whose related target is not done")
def next_cmd(take: str, tag: str | None, priority: str | None, skip_related: bool):
    """Show highest-priority todo task(s)."""
    try:
        _, tasks = load_tasks_yaml()
        done_list = load_done_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    # Filter to todo only
    candidates = [t for t in tasks if t.get("status") == "todo"]
    candidates = filter_tasks(candidates, tag=tag, priority=priority)

    # --skip-related: exclude tasks with unresolved related
    if skip_related:
        filtered: list[dict[str, Any]] = []
        for t in candidates:
            rid = t.get("related")
            if rid:
                target = _resolve_related(rid, tasks, done_list)
                if target is None or target.get("status") != "done":
                    continue  # skip: related target not done or not found
            filtered.append(t)
        candidates = filtered

    # Sort by priority (highest first), then newest first
    candidates.sort(key=lambda t: (_priority_sort_key(t), t.get("created", "")), reverse=False)
    # For newest-first when same priority: reverse created sort
    # Actually: sort by priority ascending, then created descending
    # Let's do a two-pass: group same priority, then sort each group by created desc
    candidates.sort(key=lambda t: t.get("created", ""), reverse=True)  # newest first
    candidates.sort(key=_priority_sort_key)  # stable sort by priority (lower=higher)

    # Take
    if take == "all":
        count = len(candidates)
    else:
        try:
            count = int(take)
        except ValueError:
            click.echo(f"Invalid --take value: {take}. Use a number or 'all'.", err=True)
            sys.exit(1)

    results = candidates[:count]

    if not results:
        click.echo("No tasks found.")
        return

    for t in results:
        tags_str = ", ".join(t.get("tags", []) or [])
        click.echo(f"  [{t.get('id')}] {t.get('priority', '-'):6s}  {t.get('title')}  tags: {tags_str}")


# ---- status ----

@main.command()
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def status(json_output: bool):
    """Show session overview: in-progress, top priorities, tag stats, inbox status."""
    try:
        _, tasks = load_tasks_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    active = [t for t in tasks if t.get("status") != "done"]
    in_progress = [t for t in active if t.get("status") == "in-progress"]
    todo = [t for t in active if t.get("status") == "todo"]

    # Top priority: sort by priority then created desc
    top_todo = sorted(todo, key=lambda t: t.get("created", ""), reverse=True)
    top_todo.sort(key=_priority_sort_key)
    top_todo = top_todo[:5]

    tag_counts = _collect_all_tags(active)
    inbox_count = _inbox_line_count()

    if json_output:
        import json as _json
        output = {
            "counts": {
                "todo": len(todo),
                "in_progress": len(in_progress),
            },
            "in_progress": in_progress,
            "top_priority": top_todo,
            "tags": tag_counts,
            "inbox_entries": inbox_count,
        }
        click.echo(_json.dumps(output, indent=2))
        return

    click.echo(f"\nTasks: {len(in_progress)} in-progress, {len(todo)} todo")

    if in_progress:
        click.echo("\nIN PROGRESS")
        for t in in_progress:
            tags_str = ", ".join(t.get("tags", []) or [])
            click.echo(f"  [{t.get('id')}] {t.get('priority', '-'):6s}  {t.get('title')}  tags: {tags_str}")

    if top_todo:
        click.echo("\nTOP PRIORITY")
        for t in top_todo:
            tags_str = ", ".join(t.get("tags", []) or [])
            click.echo(f"  [{t.get('id')}] {t.get('priority', '-'):6s}  {t.get('title')}  tags: {tags_str}")
    else:
        click.echo("\nNo pending tasks.")

    if tag_counts:
        tags_line = ", ".join(f"{k}({v})" for k, v in list(tag_counts.items())[:10])
        click.echo(f"\nTAGS: {tags_line}")

    if inbox_count:
        click.echo(f"\nInbox has {inbox_count} unprocessed entries. Run `tasks inbox` to view.")
    click.echo("")


# ---- history ----

@main.command()
@click.option("--tag", help="Filter by tag")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def history(tag: str | None, json_output: bool):
    """Show recently completed tasks (newest first)."""
    done_list = load_done_yaml()

    if tag:
        done_list = [t for t in done_list if tag in (t.get("tags") or [])]

    # done.yaml is oldest-first; reverse for display (newest first)
    done_list = list(reversed(done_list))

    if json_output:
        click.echo(__import__("json").dumps(done_list, indent=2))
        return

    if not done_list:
        click.echo("No completed tasks.")
        return

    table = Table(title="Completed Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Priority", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("Completed", style="green")
    table.add_column("Tags", style="dim")

    for t in done_list:
        table.add_row(
            t.get("id", "?"),
            t.get("priority", "-"),
            t.get("title", "?"),
            t.get("completed", "?")[:10],
            ", ".join(t.get("tags", []) or []),
        )

    console.print(table)


# ---- inbox ----

@main.command()
def inbox():
    """Print inbox.md contents."""
    content = load_inbox()
    if content.strip():
        click.echo(content)
    else:
        click.echo("Inbox is empty.")


if __name__ == "__main__":
    main()
```

✅ Success: All CLI commands appended to `src/agentcli_helpers/tasks.py`. Run `uv run python -c "import ast; ast.parse(open('src/agentcli_helpers/tasks.py').read()); print('OK')"` — must print `OK`.
❌ If failed: Syntax error — stop, report exact error.

---

### Step 6: Add `tasks` entry point to pyproject.toml

Open `pyproject.toml`. Find the `[project.scripts]` section (line 32). The current content is:

```
[project.scripts]
crony = "agentcli_helpers.crony:main"
notify = "agentcli_helpers.notify:main"
bg = "agentcli_helpers.bg:main"
screenshot = "agentcli_helpers.screenshot:main"
```

Add a new line after `screenshot = "agentcli_helpers.screenshot:main"`:

```
screenshot = "agentcli_helpers.screenshot:main"
tasks = "agentcli_helpers.tasks:main"
```

Save the file.

✅ Success: `pyproject.toml` contains `tasks = "agentcli_helpers.tasks:main"` in the `[project.scripts]` section.
❌ If failed: File not found or section not found — stop, report error.

---

### Step 7: Verify bootstrap — `uv run tasks init`

Run each command from the project root. Check exact output.

**Command 1: Fresh init**

First, ensure there's no `tasks/` directory in the project root. If it exists from prior testing, delete it: `Remove-Item -Recurse -Force tasks` (use bash `rm -rf tasks` on Unix).

```
uv run tasks init
```

✅ Success: Output must contain:
```
  Created tasks\inbox.md
  Created tasks\tasks.yaml
  Created tasks\done.yaml
Task system initialized.
```
Verify files exist:
- `tasks/inbox.md` exists and is empty
- `tasks/tasks.yaml` exists and contains valid YAML with `meta.counter: 0` and `tasks: []`
- `tasks/done.yaml` exists and contains `[]`

❌ If failed: Any error — stop, report the full output.

**Command 2: Idempotent re-init**

```
uv run tasks init
```

✅ Success: Output must be:
```
All files already exist. Nothing to do.
```
❌ If failed: Any error or different message — stop, report full output.

---

### Step 8: Verify write commands

Run each command sequentially.

**Command 1: Add a task**

```
uv run tasks add "Fix login button alignment" --tag ui --tag mobile --priority low --source inbox --notes "Check CSS for iPhone SE"
```

✅ Success: Output like `Created TSK-0001: Fix login button alignment`. Verify `tasks/tasks.yaml` contains the task with all fields.

**Command 2: Add a second task**

```
uv run tasks add "SQL injection risk in user search" --tag security --priority high --source audit --related TSK-0001
```

✅ Success: Output like `Created TSK-0002: SQL injection risk in user search`. Verify TSK-0002 appears at the top of tasks.yaml (newest first).

**Command 3: Update a task**

```
uv run tasks update TSK-0001 --status in-progress --tag bug
```

✅ Success: Output like `Updated TSK-0001: Fix login button alignment`. Verify tasks.yaml shows `status: in-progress` and `tags: [ui, mobile, bug]` (ui and mobile from creation, bug appended).

**Command 4: Complete a task**

```
uv run tasks done TSK-0002 --note "Fixed with parameterized queries"
```

✅ Success: Output like `Completed TSK-0002: SQL injection risk in user search`. Verify:
- TSK-0002 is no longer in `tasks/tasks.yaml`
- TSK-0002 appears in `tasks/done.yaml` with `status: done` and a `completed` timestamp

❌ If any command fails: Stop, report the full error output and which command failed.

---

### Step 9: Verify read commands

Run each command sequentially.

**Command 1: tasks list**

```
uv run tasks list
```

✅ Success: Rich table showing TSK-0001 with status `in-progress`. No TSK-0002 (it's done).

**Command 2: tasks list --json**

```
uv run tasks list --json
```

✅ Success: JSON array output with TSK-0001's data.

**Command 3: tasks show (with related resolution)**

```
uv run tasks show TSK-0002
```

✅ Success: Must show TSK-0002 from done.yaml. The `related` line shows `TSK-0001 (in-progress) — "Fix login button alignment"`.

**Command 4: tasks show (stale related)**

First create a task with a bogus related: `uv run tasks add "Test stale related" --related TSK-9999`. Then:

```
uv run tasks show TSK-0003
```

✅ Success: The `related` line shows `TSK-9999 (not found)`.

**Command 5: tasks next**

```
uv run tasks next
```

✅ Success: Shows TSK-0001 at minimum. Capture the output.

**Command 6: tasks next --skip-related**

Create a task with related pointing to an in-progress (not done) task — already have TSK-0003 with stale related. Create TSK-0004 with no related:

```
uv run tasks add "No dependencies task" --priority high
uv run tasks next --skip-related
```

✅ Success: TSK-0004 must appear. TSK-0003 must NOT appear (its related TSK-9999 is not found/done). TSK-0001 should also appear (no related field).

**Command 7: tasks status**

```
uv run tasks status
```

✅ Success: Shows counts, in-progress section, top priority section, tags, inbox status.

**Command 8: tasks status --json**

```
uv run tasks status --json
```

✅ Success: Valid JSON with keys `counts`, `in_progress`, `top_priority`, `tags`, `inbox_entries`.

**Command 9: tasks history**

```
uv run tasks history
```

✅ Success: Rich table showing TSK-0002 as completed.

**Command 10: tasks inbox**

```
uv run tasks inbox
```

✅ Success: `Inbox is empty.` (since we haven't written to it).

❌ If any command fails: Stop, report which command and the full error output.

---

### Step 10: Verify idempotent init and edge cases

**Command 1: Init after data exists**

```
uv run tasks init
```

✅ Success: `All files already exist. Nothing to do.` Must NOT overwrite existing data.

**Command 2: Verify data preserved after re-init**

```
uv run tasks list
```

✅ Success: TSK-0001 still shows with its tags and status. No data lost.

**Command 3: Update non-existent task**

```
uv run tasks update TSK-9999 --status done
```

✅ Success: Error message, exit code 1. Must NOT crash, must NOT create any file.

**Command 4: Done on already-done task**

```
uv run tasks done TSK-0002
```

✅ Success: Error message `Task already done: TSK-0002`, exit code 1.

**Command 5: Show non-existent task**

```
uv run tasks show TSK-9999
```

✅ Success: `Task not found: TSK-9999`, exit code 1.

**Command 6: Add task with no optional flags**

```
uv run tasks add "Minimal task"
```

✅ Success: Creates TSK-0005 with only `id`, `title`, `status: todo`, `source: agent`, `created`. No priority, no tags, no notes in YAML.

**Command 7: Verify --take on next**

```
uv run tasks next --take all
```

✅ Success: Lists all todo tasks (not in-progress, not done).

**Command 8: Verify list filtering**

```
uv run tasks list --tag bug
```

✅ Success: Only TSK-0001 shown (has bug tag). Other tasks without bug tag excluded.

❌ If any command fails: Stop, report which command and full error output.

---

## Verification

When ALL steps pass, the following must be true:

1. `uv run tasks init` is idempotent — safe to run any number of times
2. `uv run tasks add "test"` creates a task with auto-generated TSK-NNNN ID
3. `uv run tasks list` shows active tasks in a Rich table
4. `uv run tasks list --json` outputs valid JSON
5. `uv run tasks show TSK-0001` resolves `related` inline
6. `uv run tasks next` shows highest-priority todo
7. `uv run tasks next --skip-related` excludes tasks with unresolved related
8. `uv run tasks update TSK-0001 --status in-progress --tag bug` appends tags
9. `uv run tasks done TSK-0001` moves task to done.yaml with completed timestamp
10. `uv run tasks status` shows overview with counts, in-progress, top priority, tags, inbox
11. `uv run tasks status --json` outputs valid JSON
12. `uv run tasks history` shows completed tasks (newest first)
13. `uv run tasks inbox` prints inbox.md contents
14. All error cases (missing files, bad IDs, already-done) produce clear messages and exit code 1
15. Missing optional fields are absent from YAML (no nulls, no placeholders)
16. `tasks.yaml` is sorted newest-first, `done.yaml` is sorted oldest-first (append to bottom)

## Rollback

If a critical step fails and cannot be recovered:

1. Delete `src/agentcli_helpers/tasks.py` if it was created
2. Remove `"pyyaml>=6.0"` from `pyproject.toml` dependencies
3. Remove `tasks = "agentcli_helpers.tasks:main"` from `pyproject.toml` `[project.scripts]`
4. Run `uv sync` to clean up the venv
5. Delete `tasks/` directory if it was created during testing
