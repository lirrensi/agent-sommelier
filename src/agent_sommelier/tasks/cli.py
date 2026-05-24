# FILE: src/agent_sommelier/tasks/cli.py
# PURPOSE: Register the task-system Click commands and wire them to core storage logic.
# OWNS: The public CLI surface, option parsing, and command-to-render orchestration.
# EXPORTS: main (Click group).
# DOCS: README.md, docs/arch.md, skills/task-system/SKILL.md

from __future__ import annotations

import json
import socket
import sys
import webbrowser
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from .core import (
    _collect_all_tags,
    _ensure_config,
    _find_task_by_id,
    _format_id,
    _get_blockers,
    _inbox_line_count,
    _is_task_blocked,
    _normalize_evidence,
    _normalize_notes,
    _priority_sort_key,
    _resolve_related,
    _task_text,
    add_task,
    build_overview_data,
    close_task,
    filter_tasks,
    init_task_files,
    load_closed_yaml,
    load_inbox,
    load_tasks_yaml,
    migrate_to_perfile,
    next_counter_and_id,
    save_closed_yaml,
    save_tasks_yaml,
    search_tasks,
    update_task,
    VALID_SOURCES,
)
from .storage import STORAGE_VERSION, detect_storage_version
from .render import _format_priority, console, render_overview


@click.group()
def main():
    """Lightweight task management for AI agents."""
    pass


@main.command()
def init():
    """Bootstrap task files in the current directory. Safe to run repeatedly."""
    results = init_task_files()
    had_created = False
    for key, action in results.items():
        if action == "created":
            click.echo(f"  Created {key}")
            had_created = True
        elif action == "invalid":
            click.echo(f"  Error: {key} exists but is invalid YAML. Delete it and re-run.", err=True)
            sys.exit(1)
        elif action == "corrupt":
            click.echo(f"  Error: {key} is corrupt. Delete it and re-run.", err=True)
            sys.exit(1)
        elif "created" in action:
            had_created = True
        elif "corrupt" in action:
            click.echo(f"  Error: {key} is corrupt. Delete it and re-run.", err=True)
            sys.exit(1)

    all_exist = all(
        "exists" in v or v == "exists"
        for v in results.values()
    )
    if all_exist:
        click.echo("All files already exist. Nothing to do.")
    elif had_created:
        click.echo("Task system initialized.")


def _parse_dep_option(dep_str: str) -> dict[str, str]:
    if ":" in dep_str:
        parts = dep_str.split(":", 1)
        return {"id": parts[0], "type": parts[1]}
    return {"id": dep_str, "type": "relates"}


@main.command()
@click.argument("title")
@click.option("--tag", "-t", "tags", multiple=True, help="Tag(s) to apply (repeatable)")
@click.option("--priority", "-p", help="Priority: p0-p4, 0-4, or name (critical, urgent, high, medium, low, backlog)")
@click.option("--source", "-s", type=click.Choice(list(VALID_SOURCES)), default="agent", help="Source of the task")
@click.option("--claimed", help="Who is actively working this task (non-empty = locked from ready/next queues)")
@click.option("--created-by", "created_by", help="Who or what created this task (e.g. rx, agent, gmail-import)")
@click.option("--dep", "deps", multiple=True, help="Dependency: id:type (e.g. TSK-0042:blocks, TSK-0017:relates)")
@click.option("--related", "-r", help="Related task ID (shorthand for --dep id:relates)")
@click.option("--notes", "-n", help="Freeform notes")
@click.option("--evidence", "-e", help="Verification evidence / proof")
def add(title: str, tags: tuple[str, ...], priority: str | None, source: str,
        claimed: str | None, created_by: str | None,
        deps: tuple[str, ...], related: str | None, notes: str | None,
        evidence: str | None):
    """Create a new task. ID is auto-generated."""
    parsed_deps = [_parse_dep_option(d) for d in deps] if deps else None
    task = add_task(
        title=title,
        priority=priority,
        tags=list(tags) if tags else None,
        source=source,
        claimed=claimed,
        created_by=created_by,
        deps=parsed_deps,
        related=related,
        notes=notes,
        evidence=evidence,
    )
    click.echo(f"Created {task['id']}: {title}")


@main.command("list")
@click.option("--status", type=str, default=None, help="Filter by status (must be defined in meta.config.statuses)")
@click.option("--tag", "tags", multiple=True, help="Filter by tag (repeatable, AND logic)")
@click.option("--tag-any", "tags_any", multiple=True, help="Filter by tag (OR logic, repeatable)")
@click.option("--priority", help="Filter by priority (p0-p4, 0-4, or name like urgent, high)")
@click.option("--source", type=click.Choice(list(VALID_SOURCES)), help="Filter by source")
@click.option("--related", "-r", help="Filter by related/dep task ID")
@click.option("--text", "-t", help="Full-text search (combined with other filters)")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def list_cmd(status: str | None, tags: tuple[str, ...], tags_any: tuple[str, ...],
             priority: str | None,
             source: str | None, related: str | None, text: str | None,
             json_output: bool):
    try:
        meta, tasks = load_tasks_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    if status:
        config = _ensure_config(meta)
        if status not in config["statuses"]:
            click.echo(f"Invalid status '{status}'. Valid statuses: {', '.join(config['statuses'])}", err=True)
            sys.exit(1)

    tasks = [t for t in tasks if not t.get("closed", False)]
    tasks = filter_tasks(tasks, status=status,
                         tags=list(tags) if tags else None,
                         tags_any=list(tags_any) if tags_any else None,
                         priority=priority, source=source, related=related)

    if text:
        tasks = search_tasks(tasks, text)

    if json_output:
        click.echo(json.dumps(tasks, indent=2))
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
            _format_priority(t.get("priority")),
            t.get("title", "?"),
            ", ".join(t.get("tags", []) or []),
        )

    console.print(table)


@main.command()
@click.argument("task_id")
@click.option("--claimed", default="agent", help="Who is claiming this task (default: agent). Empty string clears the claim.")
def take(task_id: str, claimed: str):
    """Mark a task as in-progress and claim it. Idempotent shorthand.

    Defaults --claimed to "agent" so the task is always locked after taking.
    Use `tasks update TSK-NNNN --claimed ""` to release a claim.
    Safe to re-run on tasks already in-progress (does nothing but still succeeds).
    """
    try:
        meta, _ = load_tasks_yaml()
        config = _ensure_config(meta)
        active_status = config.get("active_status", "in-progress")
        task = update_task(
            task_id=task_id,
            status=active_status,
            claimed=claimed,
        )
        click.echo(f"Task {task['id']} is now {active_status} (claimed: {task.get('claimed', 'unset')}): {task['title']}")
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command()
@click.argument("task_id")
@click.option("--claimed", default="agent", help="Who is claiming this task (default: agent). Empty string clears the claim.")
def claim(task_id: str, claimed: str):
    """Alias for 'take' -- mark a task as in-progress and claim it.

    Defaults --claimed to "agent" so the task is always locked.
    """
    try:
        meta, _ = load_tasks_yaml()
        config = _ensure_config(meta)
        active_status = config.get("active_status", "in-progress")
        task = update_task(
            task_id=task_id,
            status=active_status,
            claimed=claimed,
        )
        click.echo(f"Task {task['id']} is now {active_status} (claimed: {task.get('claimed', 'unset')}): {task['title']}")
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command()
@click.argument("task_id")
def show(task_id: str):
    try:
        _, tasks = load_tasks_yaml()
        closed_list = load_closed_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    task = _find_task_by_id(tasks, task_id)
    if task is None:
        task = _find_task_by_id(closed_list, task_id)
    if task is None:
        click.echo(f"Task not found: {task_id}", err=True)
        sys.exit(1)

    click.echo(f"\n  [{task.get('id')}] {task.get('status')}  priority: {_format_priority(task.get('priority'))}")
    click.echo(f"  {task.get('title')}")

    tags = task.get("tags")
    if tags:
        click.echo(f"  tags: {', '.join(tags)}")

    if task.get("source"):
        src = task["source"]
        if task.get("source_ref"):
            src += f" ({task['source_ref']})"
        click.echo(f"  source: {src}")

    if task.get("claimed"):
        click.echo(f"  claimed: {task['claimed']}")
    if task.get("createdBy"):
        click.echo(f"  createdBy: {task['createdBy']}")

    deps = task.get("deps")
    if deps:
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            dep_id = dep.get("id", "")
            dep_type = dep.get("type", "relates")
            target = _resolve_related(dep_id, tasks, closed_list)
            if target:
                click.echo(f"  dep ({dep_type}): {dep_id} ({target.get('status')}) — \"{target.get('title')}\"")
            else:
                click.echo(f"  dep ({dep_type}): {dep_id} (not found)")
    elif task.get("related"):
        related_id = task["related"]
        target = _resolve_related(related_id, tasks, closed_list)
        if target:
            click.echo(f"  related: {related_id} ({target.get('status')}) — \"{target.get('title')}\"")
        else:
            click.echo(f"  related: {related_id} (not found)")

    click.echo(f"  created: {task.get('created')}")
    if task.get("updated"):
        click.echo(f"  updated: {task.get('updated')}")
    if task.get("closed_at"):
        click.echo(f"  closed_at: {task.get('closed_at')}")
    elif task.get("completed"):
        click.echo(f"  completed: {task.get('completed')}")
    notes_val = task.get("notes")
    if notes_val:
        notes_list = _normalize_notes(notes_val)
        if len(notes_list) == 1:
            click.echo(f"  notes: {notes_list[0]}")
        else:
            click.echo("  notes:")
            for i, note in enumerate(notes_list, 1):
                click.echo(f"    {i}. {note}")
    evidence_val = task.get("evidence")
    if evidence_val:
        evidence_list = _normalize_evidence(evidence_val)
        if len(evidence_list) == 1:
            click.echo(f"  evidence: {evidence_list[0]}")
        else:
            click.echo("  evidence:")
            for i, item in enumerate(evidence_list, 1):
                click.echo(f"    {i}. {item}")
    click.echo("")


@main.command()
@click.argument("task_id")
@click.option("--status", type=str, default=None, help="Change status (must be defined in meta.config.statuses)")
@click.option("--tag", "-t", "tags", multiple=True, help="Tag(s) to append (repeatable)")
@click.option("--priority", "-p", help="Change priority (p0-p4, 0-4, or name like urgent, high)")
@click.option("--claimed", help="Set or clear claim (empty string clears). Claimed tasks are locked from ready/next queues.")
@click.option("--created-by", "created_by", help="Set or clear createdBy metadata (empty string clears)")
@click.option("--dep", "deps", multiple=True, help="Append dependency: id:type (repeatable)")
@click.option("--related", "-r", help="Set related task ID (shorthand for --dep id:relates)")
@click.option("--notes", "-n", help="Append a note (use --replace-notes to overwrite)")
@click.option("--replace-notes", is_flag=True, help="Replace notes instead of appending")
@click.option("--evidence", "-e", help="Append evidence (use --replace-evidence to overwrite)")
@click.option("--replace-evidence", is_flag=True, help="Replace evidence instead of appending")
@click.option("--closed", "-c", is_flag=True, help="Close the task (move to closed.yaml)")
def update(task_id: str, status: str | None, tags: tuple[str, ...], priority: str | None,
           claimed: str | None, created_by: str | None,
           deps: tuple[str, ...], related: str | None, notes: str | None,
           replace_notes: bool, evidence: str | None,
           replace_evidence: bool, closed: bool):
    parsed_deps = [_parse_dep_option(d) for d in deps] if deps else None
    try:
        task = update_task(
            task_id=task_id,
            status=status,
            priority=priority,
            tags=list(tags) if tags else None,
            claimed=claimed,
            created_by=created_by,
            deps=parsed_deps,
            related=related,
            notes=notes,
            replace_notes=replace_notes,
            evidence=evidence,
            replace_evidence=replace_evidence,
            closed=closed if closed else None,
        )
        click.echo(f"Updated {task['id']}: {task['title']}")
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command()
@click.argument("task_id")
@click.option("--note", "-n", help="Closing note to append")
@click.option("--evidence", "-e", help="Closing evidence to append")
def close(task_id: str, note: str | None, evidence: str | None):
    try:
        task = close_task(task_id, note=note, evidence=evidence)
        click.echo(f"Closed {task['id']}: {task['title']} (status: {task.get('status', '?')})")
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command()
@click.option("--take", default="1", help="Number of tasks, or 'all'")
@click.option("--tag", help="Filter by tag")
@click.option("--priority", help="Filter by priority (0-4 or name)")
@click.option("--skip-related", is_flag=True, help="Exclude tasks whose deps (type=blocks) are not resolved")
@click.option("--skip-blocks", is_flag=True, help="Alias for --skip-related")
def next_cmd(take: str, tag: str | None, priority: str | None,
             skip_related: bool, skip_blocks: bool):
    try:
        meta, tasks = load_tasks_yaml()
        closed_list = load_closed_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    config = _ensure_config(meta)
    ready_status = config.get("ready_status", "todo")
    close_status = config.get("close_status", "done")
    candidates = [t for t in tasks if t.get("status") == ready_status]
    candidates = [t for t in candidates if not t.get("closed", False)]
    # Exclude claimed tasks (claimed + in-progress or just claimed)
    candidates = [t for t in candidates if not t.get("claimed")]
    candidates = filter_tasks(candidates, tags=[tag] if tag else None, priority=priority)

    skip = skip_related or skip_blocks
    if skip:
        candidates = [t for t in candidates if not _is_task_blocked(t, tasks, closed_list, close_status=close_status)]

    candidates.sort(key=lambda t: t.get("created", ""), reverse=True)
    candidates.sort(key=_priority_sort_key)

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
        click.echo(f"  [{t.get('id')}] {_format_priority(t.get('priority')):4s}  {t.get('title')}  tags: {tags_str}")


@main.command()
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def status(json_output: bool):
    try:
        meta, tasks = load_tasks_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    active = [t for t in tasks if not t.get("closed", False)]
    config = _ensure_config(meta)
    active_status = config.get("active_status", "in-progress")
    ready_status = config.get("ready_status", "todo")
    in_column = [t for t in active if t.get("status") == active_status]
    ready_column = [t for t in active if t.get("status") == ready_status]

    top_ready_column = sorted(ready_column, key=lambda t: t.get("created", ""), reverse=True)
    top_ready_column.sort(key=_priority_sort_key)
    top_ready_column = top_ready_column[:5]

    tag_counts = _collect_all_tags(active)
    inbox_count = _inbox_line_count()

    if json_output:
        output = {
            "counts": {ready_status: len(ready_column), active_status: len(in_column)},
            active_status: in_column,
            "top_priority": top_ready_column,
            "tags": tag_counts,
            "inbox_entries": inbox_count,
        }
        click.echo(json.dumps(output, indent=2))
        return

    click.echo(f"\nTasks: {len(in_column)} {active_status}, {len(ready_column)} {ready_status}")

    if in_column:
        click.echo(f"\n{active_status.upper()}")
        for t in in_column:
            tags_str = ", ".join(t.get("tags", []) or [])
            click.echo(f"  [{t.get('id')}] {_format_priority(t.get('priority')):4s}  {t.get('title')}  tags: {tags_str}")

    if top_ready_column:
        click.echo("\nTOP PRIORITY")
        for t in top_ready_column:
            tags_str = ", ".join(t.get("tags", []) or [])
            click.echo(f"  [{t.get('id')}] {_format_priority(t.get('priority')):4s}  {t.get('title')}  tags: {tags_str}")
    else:
        click.echo("\nNo pending tasks.")

    if tag_counts:
        tags_line = ", ".join(f"{k}({v})" for k, v in list(tag_counts.items())[:10])
        click.echo(f"\nTAGS: {tags_line}")

    if inbox_count:
        click.echo(f"\nInbox has {inbox_count} unprocessed entries. Run `tasks inbox` to view.")
    click.echo("")


@main.command()
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def overview(json_output: bool):
    try:
        meta, tasks = load_tasks_yaml()
        closed_list = load_closed_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    config = _ensure_config(meta)
    ready_status = config.get("ready_status", "todo")
    close_status = config.get("close_status", "done")
    overview_data = build_overview_data(tasks, closed_list, ready_status=ready_status, close_status=close_status)

    if json_output:
        click.echo(json.dumps(overview_data, indent=2))
        return

    render_overview(overview_data)


@main.command()
@click.option("--tag", "tags", multiple=True, help="Filter by tag (repeatable, AND logic)")
@click.option("--tag-any", "tags_any", multiple=True, help="Filter by tag (OR logic, repeatable)")
@click.option("--text", help="Full-text search within closed tasks")
@click.option("--related", "-r", help="Filter by related task ID")
@click.option("--limit", default="30", help="Number of tasks to show, or 'all' (default: 30)")
@click.option("--offset", default=0, type=int, help="Skip N entries from the newest (default: 0)")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def history(tags: tuple[str, ...], tags_any: tuple[str, ...], text: str | None,
            related: str | None,
            limit: str, offset: int, json_output: bool):
    closed_list = load_closed_yaml()

    closed_list = filter_tasks(closed_list,
                               tags=list(tags) if tags else None,
                               tags_any=list(tags_any) if tags_any else None,
                               related=related)

    if text:
        closed_list = search_tasks(closed_list, text)

    total = len(closed_list)
    closed_list = list(reversed(closed_list))
    closed_list = closed_list[offset:]

    if limit == "all":
        count = len(closed_list)
    else:
        try:
            count = int(limit)
        except ValueError:
            click.echo(f"Invalid --limit value: {limit}. Use a number or 'all'.", err=True)
            sys.exit(1)
        count = min(count, len(closed_list))

    display = closed_list[:count]

    if json_output:
        click.echo(json.dumps(display, indent=2))
        return

    if not display:
        click.echo("No completed tasks.")
        return

    table = Table(title="Completed Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Priority", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("Completed", style="green")
    table.add_column("Tags", style="dim")

    for t in display:
        completed_val = t.get("closed_at") or t.get("completed") or ""
        completed_display = completed_val[:10] if completed_val else "?"
        table.add_row(
            t.get("id", "?"),
            _format_priority(t.get("priority")),
            t.get("title", "?"),
            completed_display,
            ", ".join(t.get("tags", []) or []),
        )

    console.print(table)

    remaining = total - offset - len(display)
    if remaining > 0:
        shown = offset + len(display)
        console.print(f"\n[dim]Showing {shown} of {total}. Use --offset {shown} to see next page, --limit all to see everything.[/dim]")


@main.command()
def inbox():
    content = load_inbox()
    if content.strip():
        click.echo(content)
    else:
        click.echo("Inbox is empty.")


@main.command()
@click.argument("text")
@click.option("--in", "search_field", help="Scope search to a specific field (title, notes, evidence, id, status, priority, tags, source)")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def search(text: str, search_field: str | None, json_output: bool):
    try:
        _, tasks = load_tasks_yaml()
        closed_list = load_closed_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    all_tasks: list[dict] = list(tasks)
    active_ids = {t.get("id") for t in all_tasks}
    for ct in closed_list:
        if ct.get("id") not in active_ids:
            all_tasks.append(ct)

    if search_field:
        needle = text.lower()
        results = []
        for t in all_tasks:
            val = t.get(search_field)
            if val is None:
                continue
            if isinstance(val, list):
                val_str = " ".join(str(v) for v in val).lower()
            else:
                val_str = str(val).lower()
            if needle in val_str:
                results.append(t)
    else:
        results = search_tasks(all_tasks, text)

    if json_output:
        click.echo(json.dumps(results, indent=2))
        return

    if not results:
        click.echo("No results found.")
        return

    table = Table(title=f"Search: {text}")
    table.add_column("ID", style="cyan")
    table.add_column("Status", style="yellow")
    table.add_column("Priority", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("Tags", style="green")
    table.add_column("Source", style="dim")

    for t in results:
        table.add_row(
            t.get("id", "?"),
            t.get("status", "?"),
            _format_priority(t.get("priority")),
            t.get("title", "?"),
            ", ".join(t.get("tags", []) or []),
            t.get("source", "-"),
        )

    console.print(table)


@main.command()
@click.argument("task_id")
def deps(task_id: str):
    try:
        _, tasks = load_tasks_yaml()
        closed_list = load_closed_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    task = _find_task_by_id(tasks, task_id)
    if task is None:
        task = _find_task_by_id(closed_list, task_id)
    if task is None:
        click.echo(f"Task not found: {task_id}", err=True)
        sys.exit(1)

    click.echo(f"\n  [{task.get('id')}] {task.get('title')}")

    out_deps = task.get("deps") or []
    if out_deps:
        click.echo("\n  DEPENDS ON:")
        for dep in out_deps:
            if not isinstance(dep, dict):
                continue
            dep_id = dep.get("id", "")
            dep_type = dep.get("type", "relates")
            target = _resolve_related(dep_id, tasks, closed_list)
            if target:
                status_str = target.get("status", "?")
                click.echo(f"    {dep_id} ({dep_type}) [{status_str}] — {target.get('title')}")
            else:
                click.echo(f"    {dep_id} ({dep_type}) [not found]")

    incoming: list[tuple[str, str, str]] = []
    for t in tasks + closed_list:
        for dep in (t.get("deps") or []):
            if isinstance(dep, dict) and dep.get("id") == task_id:
                incoming.append((t.get("id", "?"), dep.get("type", "?"), t.get("title", "?")))
                break
    if incoming:
        click.echo("\n  DEPENDED BY:")
        for dep_id, dep_type, title in incoming:
            click.echo(f"    {dep_id} ({dep_type}) — {title}")

    if not out_deps and not incoming:
        click.echo("  No dependencies.")
    click.echo("")


@main.command()
@click.option("--take", default="5", help="Number of tasks, or 'all'")
@click.option("--tag", help="Filter by tag")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def ready(take: str, tag: str | None, json_output: bool):
    try:
        meta, tasks = load_tasks_yaml()
        closed_list = load_closed_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    config = _ensure_config(meta)
    ready_status = config.get("ready_status", "todo")
    close_status = config.get("close_status", "done")
    candidates = [t for t in tasks if t.get("status") == ready_status and not t.get("closed", False) and not _is_task_blocked(t, tasks, closed_list, close_status=close_status) and not t.get("claimed")]

    if tag:
        candidates = [t for t in candidates if tag in (t.get("tags") or [])]

    candidates.sort(key=lambda t: t.get("created", ""), reverse=True)
    candidates.sort(key=_priority_sort_key)

    if take == "all":
        count = len(candidates)
    else:
        try:
            count = int(take)
        except ValueError:
            click.echo(f"Invalid --take value: {take}. Use a number or 'all'.", err=True)
            sys.exit(1)

    results = candidates[:count]

    if json_output:
        click.echo(json.dumps(results, indent=2))
        return

    if not results:
        click.echo("No ready tasks. Check 'tasks blocked' or 'tasks next' for the full queue.")
        return

    click.echo(f"\nREADY ({len(results)}):")
    for t in results:
        tags_str = ", ".join(t.get("tags", []) or [])
        click.echo(f"  [{t.get('id')}] {_format_priority(t.get('priority')):4s}  {t.get('title')}  tags: {tags_str}")
    click.echo("")


@main.command()
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def blocked(json_output: bool):
    try:
        meta, tasks = load_tasks_yaml()
        closed_list = load_closed_yaml()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    config = _ensure_config(meta)
    close_status = config.get("close_status", "done")
    candidates = [t for t in tasks if not t.get("closed", False) and _is_task_blocked(t, tasks, closed_list, close_status=close_status)]

    if json_output:
        output = []
        for t in candidates:
            blockers = _get_blockers(t, tasks, closed_list, close_status=close_status)
            entry = dict(t)
            entry["blockers"] = [
                {"id": b["id"], "status": b["task"].get("status") if b["task"] else "not_found",
                 "title": b["task"].get("title") if b["task"] else None}
                for b in blockers
            ]
            output.append(entry)
        click.echo(json.dumps(output, indent=2))
        return

    if not candidates:
        click.echo("\nNo blocked tasks. Everything's ready to go! 🎉\n")
        return

    click.echo(f"\nBLOCKED ({len(candidates)}):")
    for t in candidates:
        blockers = _get_blockers(t, tasks, closed_list, close_status=close_status)
        tags_str = ", ".join(t.get("tags", []) or [])
        click.echo(f"\n  [{t.get('id')}] {_format_priority(t.get('priority')):4s} {t.get('title')}  tags: {tags_str}")
        for b in blockers:
            b_task = b["task"]
            if b_task:
                click.echo(f"    ⛔ blocked by {b['id']} ({b_task.get('status')}) — {b_task.get('title')}")
            else:
                click.echo(f"    ⛔ blocked by {b['id']} (not found)")
    click.echo("")


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would be migrated without writing")
@click.pass_context
def migrate(ctx: click.Context, dry_run: bool) -> None:
    """Migrate storage from monolithic YAML to per-task YAML files.

    After migration, each task gets its own TSK-xxxx.yaml file.
    The old tasks.yaml and closed.yaml are backed up with .bak extension.
    """
    tasks_dir = Path.cwd() / ".agents" / "tasks"
    version = detect_storage_version(tasks_dir)

    if version == STORAGE_VERSION:
        click.echo(f"Storage is already at version {STORAGE_VERSION} (per-file). Nothing to migrate.")
        return

    if dry_run:
        try:
            meta, tasks = load_tasks_yaml()
            closed = load_closed_yaml()
        except FileNotFoundError:
            click.echo("No tasks found. Nothing to migrate.")
            return
        click.echo("Detected: monolithic storage (version 1)")
        click.echo(f"  Active tasks: {len(tasks)}")
        click.echo(f"  Closed tasks: {len(closed)}")
        click.echo(f"Would migrate to: per-file storage (version {STORAGE_VERSION})")
        return

    result = migrate_to_perfile()
    click.echo(f"Migrated {result['migrated']} tasks ({result['active']} active, {result['closed']} closed)")
    click.echo(f"Backend switched to: {result['to']}")


@main.command()
@click.option("--port", type=int, default=0, help="Port to bind (0 = random free port)")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
def serve(port: int, no_browser: bool) -> None:
    """Start the web UI dashboard for real-time task management.

    Launches a local web server on a random port (or the specified port).
    Opens the browser automatically. Press Ctrl+C to stop.

    The dashboard shows overview sections (Now, Ready, Waiting, Parked)
    and supports adding, taking, updating, and closing tasks in real time.
    Multiple instances can run in different directories simultaneously.
    """
    import uvicorn

    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            port = s.getsockname()[1]

    url = f"http://localhost:{port}"
    click.echo(f"📋 Task dashboard starting at {url}")
    click.echo("Press Ctrl+C to stop.")

    if not no_browser:
        webbrowser.open(url)

    from agent_sommelier.tasks.web.app import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
