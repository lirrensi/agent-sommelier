# FILE: src/agent_sommelier/tasks/render.py
# PURPOSE: Hold task CLI rendering primitives shared across list-style, detail, and overview views.
# OWNS: Rich console/table handles and display formatting helpers for task priorities and overview sections.
# EXPORTS: console, Table, _format_priority, render_overview.
# DOCS: README.md, docs/arch.md, skills/task-system/SKILL.md

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

from .core import PRIORITY_LABELS, _normalize_priority

console = Console()


def _format_priority(priority: object) -> str:
    norm = _normalize_priority(priority)
    if norm is None:
        return "-"
    return PRIORITY_LABELS.get(norm, str(norm))


def _render_overview_section(title: str, tasks: list[dict[str, object]], empty_text: str, color: str) -> Panel:
    if tasks:
        rows = []
        for task in tasks:
            rows.append(Text.assemble(
                (f"{_format_priority(task.get('priority')):>3} ", "magenta"),
                (f"[{task.get('id', '?')}] ", "cyan"),
                (str(task.get("title") or "?"), "white"),
            ))
            rows.append(Text(f"  {task.get('hint', '-')}", style="dim"))
    else:
        rows = [Text(empty_text, style="dim")]
    return Panel(Group(*rows), title=f"[{color}]{title}[/{color}]", border_style=color, padding=(0, 1))


def render_overview(overview_data: dict[str, object]) -> None:
    counts = overview_data.get("counts", {}) if isinstance(overview_data.get("counts"), dict) else {}
    summary = Text.assemble(
        ("Active ", "bold white"), (str(counts.get("active", 0)), "bold cyan"),
        ("  •  Now ", "bold white"), (str(counts.get("now", 0)), "bold yellow"),
        ("  •  Ready ", "bold white"), (str(counts.get("ready", 0)), "bold green"),
        ("  •  Waiting ", "bold white"), (str(counts.get("waiting", 0)), "bold red"),
        ("  •  Parked ", "bold white"), (str(counts.get("parked", 0)), "bold blue"),
    )
    console.print(summary)
    console.print(_render_overview_section("NOW", overview_data.get("now", []), "No active tasks in progress.", "yellow"))
    console.print(_render_overview_section("READY", overview_data.get("ready", []), "No ready tasks.", "green"))
    console.print(_render_overview_section("WAITING", overview_data.get("waiting", []), "No waiting tasks.", "red"))
    console.print(_render_overview_section("PARKED", overview_data.get("parked", []), "No parked tasks.", "blue"))
    other_tasks = overview_data.get("other", [])
    if isinstance(other_tasks, list) and other_tasks:
        console.print(_render_overview_section("OTHER", other_tasks, "No other tasks.", "white"))
    console.print("[dim]Read-only view. Make changes with tasks add, tasks update, or tasks close.[/dim]")
