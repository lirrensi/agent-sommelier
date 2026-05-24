"""skill-store — CLI entry point for managing agent skills.

Click-based command line interface for the local skill registry.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich.syntax import Syntax

from agent_sommelier import __version__
from agent_sommelier.skill_store.core import (
    SKILLS_DIR_NAME,
    clean_desc,
    display_skills_list,
    e,
    ensure_store_initialized,
    find_skill,
    get_created_time,
    get_group,
    get_updated_time,
    git_auto_commit,
    group_exists,
    json_dumps,
    load_index,
    parse_skill_frontmatter,
    process_skill_file,
    resolve_store_path,
    rg_available,
    rg_search_json,
    save_index,
    validate_group_slug,
    validate_slug,
)

# ---------------------------------------------------------------------------
# Console (global for pretty output)
# ---------------------------------------------------------------------------

console = Console()


def _check_store(store: Path) -> None:
    """Ensure the store is initialized, with proper Click output."""
    try:
        ensure_store_initialized(store)
    except RuntimeError as exc:
        console.print(f"[red]{e('✗', 'x')}[/] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Tree builder (Rich version, for CLI display)
# ---------------------------------------------------------------------------


def build_rich_tree(path: Path) -> Tree:
    """Build a rich.Tree from a directory."""
    root = Tree(f"[bold]{path.name}[/]")
    _populate_rich_tree(path, root)
    return root


def _populate_rich_tree(path: Path, node: Tree) -> None:
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    for entry in entries:
        if entry.is_dir():
            branch = node.add(f"[cyan]{entry.name}/[/]")
            _populate_rich_tree(entry, branch)
        else:
            node.add(f"[white]{entry.name}[/]")


# ---------------------------------------------------------------------------
# COMMAND: init
# ---------------------------------------------------------------------------


@click.command()
@click.pass_context
def cmd_init(ctx: click.Context) -> None:
    """Scaffold the skill store directory and initialize git."""
    store: Path = ctx.obj["store"]

    if store.exists() and (store / "index.json").exists():
        console.print(f"[yellow]{e('⚠', '!')}[/] Skill store already initialized.")
        console.print(f"   Location: [bold]{store}[/]")
        return

    store.mkdir(parents=True, exist_ok=True)
    (store / SKILLS_DIR_NAME).mkdir(exist_ok=True)

    from agent_sommelier.skill_store.core import INDEX_VERSION

    index = {
        "version": INDEX_VERSION,
        "pinned": [],
        "skills": [],
        "groups": {},
        "stats": {"total": 0, "pinned": 0, "groups": 0, "organized": 0, "updated_at": ""},
    }
    save_index(store, index)

    from agent_sommelier.skill_store.core import git_run

    git_init_ok = git_run(store, "init")
    if git_init_ok:
        git_run(store, "checkout", "-b", "main")
        gitignore = store / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*.tmp\n__pycache__/\n.venv/\n")
        git_auto_commit(store, "chore: initialize skill store")
        console.print(f"[green]{e('✓', '+')}[/] Initialized skill store at [bold]{store}[/]")
        console.print(f"[green]{e('✓', '+')}[/] Git repository initialized")
    else:
        console.print(f"[green]{e('✓', '+')}[/] Initialized skill store at [bold]{store}[/]")
        console.print(f"[yellow]{e('⚠', '!')}[/] Git not found — skipping repository init")
        console.print("   Install git for automatic versioned backups.")


# ---------------------------------------------------------------------------
# COMMAND: sync
# ---------------------------------------------------------------------------


@click.command()
@click.pass_context
def cmd_sync(ctx: click.Context) -> None:
    """Scan skills, rebuild index, and create a git snapshot."""
    store: Path = ctx.obj["store"]
    _check_store(store)

    index = load_index(store)
    skills_dir = store / SKILLS_DIR_NAME

    # Process any .skill files before scanning directories
    if skills_dir.is_dir():
        skill_files = sorted(skills_dir.glob("*.skill"))
        for sf in skill_files:
            try:
                process_skill_file(sf, skills_dir)
            except RuntimeError as exc:
                console.print(f"[red]{e('✗', 'x')}[/] {exc}")

    pinned_slugs = index.get("pinned", [])

    scanned: list[dict[str, Any]] = []
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            slug = entry.name
            meta = parse_skill_frontmatter(entry)
            skill_entry = {
                "slug": slug,
                "name": meta.get("name", slug) if meta else slug,
                "version": meta.get("version", "1") if meta else "1",
                "description": meta.get("description", "") if meta else "",
                "path": f"{SKILLS_DIR_NAME}/{slug}",
                "created": get_created_time(entry),
                "updated": get_updated_time(entry),
            }
            scanned.append(skill_entry)

    updated_pinned = [s for s in pinned_slugs if any(skill["slug"] == s for skill in scanned)]
    pinned_set = set(updated_pinned)

    pinned_skills = [s for s in scanned if s["slug"] in pinned_set]
    pinned_skills.sort(key=lambda s: updated_pinned.index(s["slug"]) if s["slug"] in updated_pinned else 999)
    unpinned_skills = sorted(
        [s for s in scanned if s["slug"] not in pinned_set],
        key=lambda s: s["slug"],
    )

    # GC orphaned group slugs
    valid_slugs = {s["slug"] for s in scanned}
    groups = index.get("groups", {})
    for gslug, group in groups.items():
        before = len(group.get("skills", []))
        cleaned = [s for s in group.get("skills", []) if s in valid_slugs]
        if len(cleaned) != before:
            group["skills"] = cleaned

    organized_slugs: set[str] = set()
    for group in groups.values():
        organized_slugs.update(group.get("skills", []))

    from datetime import datetime, timezone

    index["skills"] = pinned_skills + unpinned_skills
    index["pinned"] = updated_pinned
    index["stats"] = {
        "total": len(scanned),
        "pinned": len(updated_pinned),
        "groups": len(groups),
        "organized": len(organized_slugs),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    save_index(store, index)
    git_auto_commit(store, f"sync: {len(scanned)} skills ({len(updated_pinned)} pinned, {len(groups)} groups)")

    console.print(f"[green]{e('✓', '+')}[/] Scanned [bold]{len(scanned)}[/] skills ([bold]{len(updated_pinned)}[/] pinned, [bold]{len(scanned) - len(updated_pinned)}[/] unpinned)")
    console.print(f"[green]{e('✓', '+')}[/] Index updated")
    if len(scanned) == 0:
        console.print("   [dim]No skills found. Add one with [bold]skill-store create-new[/].[/]")


# ---------------------------------------------------------------------------
# COMMAND: create-new
# ---------------------------------------------------------------------------


@click.command()
@click.pass_context
def cmd_create_new(ctx: click.Context) -> None:
    """Interactive wizard to scaffold a new skill."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)
    existing_slugs = {s["slug"] for s in index["skills"]}

    console.print("[bold]Creating a new skill[/]")
    console.print(e("─", "-") * 40)

    slug = ""
    while True:
        slug = click.prompt("  Slug", default="").strip()
        err = validate_slug(slug)
        if err:
            console.print(f"  [red]{e('✗', 'x')}[/] {err}")
            continue
        if slug in existing_slugs:
            console.print(f"  [red]{e('✗', 'x')}[/] Slug [bold]'{slug}'[/] already exists.")
            continue
        break

    name = click.prompt("  Name", default=slug.replace("-", " ").title()).strip()
    if not name:
        name = slug

    description = click.prompt("  Description").strip()
    while not description:
        console.print(f"  [yellow]{e('⚠', '!')}[/] Description is required.")
        description = click.prompt("  Description").strip()

    skill_dir = store / SKILLS_DIR_NAME / slug
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_md_content = f"""---
name: {name}
version: 1
description: {description}
---

## Overview

<!-- Describe what this skill enables the agent to do -->

## When to use

<!-- When should the agent trigger this skill -->

## Workflow

<!-- Step-by-step instructions -->
"""
    (skill_dir / "SKILL.md").write_text(skill_md_content.lstrip(), encoding="utf-8")

    console.print(f"\n[green]{e('✓', '+')}[/] Created skill at [bold]{skill_dir}[/]")
    ctx.invoke(cmd_sync)


# ---------------------------------------------------------------------------
# COMMAND: load
# ---------------------------------------------------------------------------


def build_tree_lines(path: Path, prefix: str = "") -> list[str]:
    """Build a text-based directory tree (Unix tree style)."""
    lines: list[str] = []
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = e("└── ", "`-- ") if is_last else e("├── ", "|-- ")
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "    " if is_last else "│   "
            lines.extend(build_tree_lines(entry, prefix + extension))
    return lines


@click.command()
@click.argument("slug")
@click.option("--json/--no-json", "-j", default=False, help="Output in JSON format")
@click.pass_context
def cmd_load(ctx: click.Context, slug: str, json: bool) -> None:
    """Show the path, SKILL.md location, and folder tree for a skill."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    skill = find_skill(index, slug)
    if not skill:
        console.print(f"[red]{e('✗', 'x')}[/] Skill [bold]'{slug}'[/] not found.")
        suggestions = [s["slug"] for s in index["skills"] if slug in s["slug"]]
        if suggestions:
            console.print(f"   Did you mean: {', '.join(suggestions)}?")
        else:
            console.print(f"   Run [bold]skill-store list[/] to see available skills.")
        sys.exit(1)

    skill_path = store / skill["path"]

    if json:
        tree_lines = build_tree_lines(skill_path)
        result = {
            "slug": slug,
            "name": skill["name"],
            "version": skill.get("version", "1"),
            "description": skill.get("description", ""),
            "path": str(skill_path.resolve()),
            "skillmd": str((skill_path / "SKILL.md").resolve()),
            "tree": tree_lines,
        }
        click.echo(json_dumps(result))
    else:
        resolved = skill_path.resolve()
        console.print(f"[bold]Path:[/]     {resolved}")
        console.print(f"[bold]SKILL.md:[/] {resolved / 'SKILL.md'}")
        console.print()
        rich_tree = build_rich_tree(resolved)
        console.print(rich_tree)


# ---------------------------------------------------------------------------
# COMMAND: preview
# ---------------------------------------------------------------------------


@click.command()
@click.argument("slug")
@click.pass_context
def cmd_preview(ctx: click.Context, slug: str) -> None:
    """Print the first 100 lines of a skill's SKILL.md to stdout."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    skill = find_skill(index, slug)
    if not skill:
        console.print(f"[red]{e('✗', 'x')}[/] Skill [bold]'{slug}'[/] not found.")
        suggestions = [s["slug"] for s in index["skills"] if slug in s["slug"]]
        if suggestions:
            console.print(f"   Did you mean: {', '.join(suggestions)}?")
        else:
            console.print(f"   Run [bold]skill-store list[/] to see available skills.")
        sys.exit(1)

    skill_path = store / skill["path"]
    skill_md = skill_path / "SKILL.md"

    if not skill_md.exists():
        console.print(f"[red]{e('✗', 'x')}[/] No [bold]SKILL.md[/] found in [bold]{slug}[/].")
        sys.exit(1)

    lines = skill_md.read_text(encoding="utf-8").splitlines()
    preview = lines[:100]

    for line in preview:
        click.echo(line)

    if len(lines) > 100:
        click.echo(f"... (truncated, {len(lines)} total lines)", err=True)


# ---------------------------------------------------------------------------
# COMMAND: list
# ---------------------------------------------------------------------------


@click.command()
@click.option("--page", "-p", default=1, type=int, help="Page number (1-indexed)")
@click.option("--json/--no-json", "-j", default=False, help="Output in JSON format")
@click.pass_context
def cmd_list(ctx: click.Context, page: int, json: bool) -> None:
    """List skills with pagination (pinned first, then alphabetical)."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    from agent_sommelier.skill_store.core import PAGE_SIZE

    skills = index["skills"]
    total = len(skills)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    if page < 1 or page > total_pages:
        console.print(f"[red]{e('✗', 'x')}[/] Page [bold]{page}[/] out of range (1–{total_pages}).")
        sys.exit(1)

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_skills = skills[start:end]

    if total == 0:
        if json:
            click.echo(json_dumps({
                "page": page,
                "total_pages": total_pages,
                "total": 0,
                "pinned": [],
                "skills": [],
            }))
        else:
            console.print(f"[yellow]{e('⚠', '!')}[/] No skills in the store.")
            console.print("   Create one with [bold]skill-store create-new[/].")
        return

    pinned_slugs = set(index.get("pinned", []))

    if json:
        result = {
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "pinned": list(pinned_slugs),
            "skills": [
                {
                    "slug": s["slug"],
                    "name": s["name"],
                    "version": s.get("version", "1"),
                    "description": s["description"],
                    "pinned": s["slug"] in pinned_slugs,
                }
                for s in page_skills
            ],
        }
        click.echo(json_dumps(result))
    else:
        page_info = None
        if total_pages > 1:
            page_info = f"Page {page} / {total_pages}  —  use --page N to navigate"
        display_skills_list(
            f"Skills — Page {page}/{total_pages} ({total} total)",
            page_skills,
            pinned_slugs,
            page_info=page_info,
        )


# ---------------------------------------------------------------------------
# COMMAND: search
# ---------------------------------------------------------------------------


@click.command()
@click.argument("query")
@click.option("--json/--no-json", "-j", default=False, help="Output in JSON format")
@click.pass_context
def cmd_search(ctx: click.Context, query: str, json: bool) -> None:
    """Full-text search across skill metadata and file contents.

    Searches skill names and descriptions from the index. When ripgrep
    (rg) is installed, also searches the actual SKILL.md and other files
    inside each skill directory — giving you deeper, more accurate results.
    """
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    if not query.strip():
        if json:
            click.echo(json_dumps({"query": query, "results": 0, "skills": []}))
        else:
            console.print(f"[yellow]{e('⚠', '!')}[/] Search query cannot be empty.")
        return

    # 1 --- Index-based search (name + description) ---------------------------
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    index_results: list[dict[str, Any]] = []
    for skill in index["skills"]:
        name_match = bool(pattern.search(skill.get("name", "")))
        desc_match = bool(pattern.search(skill.get("description", "")))
        if name_match or desc_match:
            index_results.append({
                "slug": skill["slug"],
                "name": skill["name"],
                "version": skill.get("version", "1"),
                "description": skill.get("description", ""),
                "match_source": "name" if name_match else "description",
                "match_count": 0,
                "matches": [],
            })

    # 2 --- Content search via rg --json (when available) ---------------------
    rg_used = False
    rg_data: dict[str, dict[str, Any]] = {}
    skills_dir = store / SKILLS_DIR_NAME
    if rg_available() and skills_dir.is_dir():
        rg_data = rg_search_json(skills_dir, query)
        rg_used = bool(rg_data)

    # 3 --- Merge results ----------------------------------------------------
    index_slugs = {r["slug"] for r in index_results}

    for r in index_results:
        if r["slug"] in rg_data:
            r["matches"] = rg_data[r["slug"]]["matches"]
            r["match_count"] = len(r["matches"])

    for slug, data in rg_data.items():
        if slug not in index_slugs:
            name = slug
            desc = ""
            vers = "1"
            for s in index["skills"]:
                if s["slug"] == slug:
                    name = s["name"]
                    desc = s.get("description", "")
                    vers = s.get("version", "1")
                    break
            index_results.append({
                "slug": slug,
                "name": name,
                "version": vers,
                "description": desc,
                "match_source": "content",
                "match_count": len(data["matches"]),
                "matches": data["matches"],
            })

    _source_rank = {"name": 0, "description": 1, "content": 2}
    index_results.sort(key=lambda r: (_source_rank.get(r["match_source"], 99), r["slug"]))

    # 4 --- Output -----------------------------------------------------------
    if not index_results:
        if json:
            click.echo(json_dumps({
                "query": query, "results": 0, "rg_used": rg_used, "skills": [],
            }))
        else:
            msg = f"[yellow]{e('⚠', '!')}[/] No skills match [bold]'{query}'[/]."
            if not rg_used and rg_available():
                msg += " (searched metadata only, no content matches)"
            elif not rg_available():
                msg += " Install ripgrep (rg) for content-level search."
            console.print(msg)
        return

    if json:
        result_data: dict[str, Any] = {
            "query": query,
            "results": len(index_results),
            "rg_used": rg_used,
            "skills": [],
        }
        for r in index_results:
            entry: dict[str, Any] = {
                "slug": r["slug"],
                "name": r["name"],
                "version": r.get("version", "1"),
                "description": r["description"],
                "match_source": r["match_source"],
                "match_count": r["match_count"],
            }
            if r["matches"]:
                entry["matches"] = r["matches"]
            result_data["skills"].append(entry)
        click.echo(json_dumps(result_data))
    else:
        pinned_slugs = set(index.get("pinned", []))
        display_skills: list[dict[str, Any]] = []
        for r in index_results:
            source = r["match_source"]
            badge = source  # "name", "desc", or "content"
            cnt = f" ({r['match_count']})" if r["match_count"] else ""
            tag = f"[{badge}{cnt}]"
            desc = r.get("description", "") or ""
            if tag:
                desc = f"{tag} {desc}" if desc else tag
            display_skills.append({"slug": r["slug"], "description": desc})

        title = f"Search: '{query}' ({len(index_results)} found)"
        if rg_used:
            title += " · rg"

        display_skills_list(title, display_skills, pinned_slugs)

        if rg_used:
            click.echo("  ripgrep searched file contents -- use --json for full match details")
        elif not rg_available():
            click.echo("  Install ripgrep (rg) for deeper content-level search")


# ---------------------------------------------------------------------------
# COMMAND: pin / unpin
# ---------------------------------------------------------------------------


@click.command()
@click.argument("slug")
@click.pass_context
def cmd_pin(ctx: click.Context, slug: str) -> None:
    """Pin a skill to the top of the list."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    skill = find_skill(index, slug)
    if not skill:
        console.print(f"[red]{e('✗', 'x')}[/] Skill [bold]'{slug}'[/] not found.")
        sys.exit(1)

    pinned = index.get("pinned", [])
    if slug not in pinned:
        pinned.append(slug)
        index["pinned"] = pinned
        save_index(store, index)
        console.print(f"[green]{e('✓', '+')}[/] Pinned [bold]{slug}[/]")
        ctx.invoke(cmd_sync)
    else:
        console.print(f"[yellow]{e('⚠', '!')}[/] [bold]{slug}[/] is already pinned.")


@click.command()
@click.argument("slug")
@click.pass_context
def cmd_unpin(ctx: click.Context, slug: str) -> None:
    """Unpin a skill (remove from top)."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    skill = find_skill(index, slug)
    if not skill:
        console.print(f"[red]{e('✗', 'x')}[/] Skill [bold]'{slug}'[/] not found.")
        sys.exit(1)

    pinned = index.get("pinned", [])
    if slug in pinned:
        pinned.remove(slug)
        index["pinned"] = pinned
        save_index(store, index)
        console.print(f"[green]{e('✓', '+')}[/] Unpinned [bold]{slug}[/]")
        ctx.invoke(cmd_sync)
    else:
        console.print(f"[yellow]{e('⚠', '!')}[/] [bold]{slug}[/] is not pinned.")


# ---------------------------------------------------------------------------
# COMMAND: status
# ---------------------------------------------------------------------------


@click.command()
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Show store health: skills, groups, and organization overview."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    total = len(index.get("skills", []))
    pinned = len(index.get("pinned", []))
    groups = index.get("groups", {})
    group_count = len(groups)
    organized = len({s for g in groups.values() for s in g.get("skills", [])})
    unorganized = total - organized

    if total == 0:
        health = "[dim]Empty[/]"
        health_icon = e("⚪", "o")
    elif group_count == 0:
        health = "[yellow]Unorganized[/]"
        health_icon = e("🔴", "!")
    elif organized == total:
        health = "[green]Fully Organized[/]"
        health_icon = e("🟢", "+")
    elif organized > total * 0.5:
        health = "[cyan]Mostly Organized[/]"
        health_icon = e("🔵", "~")
    else:
        health = "[yellow]Semi-organized[/]"
        health_icon = e("🟡", "~")

    console.print(f"[bold]Store:[/]       {store.resolve()}")
    console.print(f"[bold]Skills:[/]      {total} total · {pinned} pinned")
    console.print(f"[bold]Groups:[/]      {group_count} groups · {organized} skills organized ({(organized / total * 100 if total else 0):.0f}%) · {unorganized} ungrouped")
    console.print(f"[bold]Health:[/]      {health_icon} {health}")

    if groups:
        console.print("")
        console.print("[bold]Top groups:[/]")
        sorted_groups = sorted(
            groups.items(),
            key=lambda item: (-len(item[1].get("skills", [])), item[0]),
        )
        for gslug, group in sorted_groups[:10]:
            skill_count = len(group.get("skills", []))
            preview = ", ".join(group.get("skills", [])[:5])
            if len(group.get("skills", [])) > 5:
                preview += ", ..."
            console.print(f"  [cyan]{gslug}[/]  ({skill_count} skills)")
            if preview:
                console.print(f"    [dim]{preview}[/]")

    console.print("")
    if group_count == 0 and total > 10:
        console.print("[dim]Tip: You have many ungrouped skills. Try [bold]skill-store groups create <slug> <name> <description>[/] to start organizing.[/]")
    elif unorganized > 0:
        console.print(f"[dim]Tip: {unorganized} skills are not in any group. Use [bold]skill-store groups add <group> <skill>[/] to organize.[/]")


# ---------------------------------------------------------------------------
# COMMAND GROUP: groups
# ---------------------------------------------------------------------------


@click.group("groups")
@click.pass_context
def cmd_groups(ctx: click.Context) -> None:
    """Manage skill groups (tags/collections)."""
    pass


@click.command("create")
@click.argument("slug")
@click.argument("name")
@click.argument("description")
@click.pass_context
def cmd_groups_create(ctx: click.Context, slug: str, name: str, description: str) -> None:
    """Create a new group."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    err = validate_group_slug(slug)
    if err:
        console.print(f"[red]{e('✗', 'x')}[/] {err}")
        sys.exit(1)

    if group_exists(index, slug):
        console.print(f"[red]{e('✗', 'x')}[/] Group [bold]'{slug}'[/] already exists.")
        sys.exit(1)

    if not name.strip():
        console.print(f"[red]{e('✗', 'x')}[/] Group name cannot be empty.")
        sys.exit(1)
    if not description.strip():
        console.print(f"[red]{e('✗', 'x')}[/] Group description cannot be empty.")
        sys.exit(1)

    index["groups"][slug] = {
        "name": name.strip(),
        "description": description.strip(),
        "skills": [],
    }
    organized = len({s for g in index["groups"].values() for s in g.get("skills", [])})
    index["stats"]["groups"] = len(index["groups"])
    index["stats"]["organized"] = organized

    save_index(store, index)
    git_auto_commit(store, f"groups: create '{slug}'")
    console.print(f"[green]{e('✓', '+')}[/] Created group [bold]{slug}[/] — {name.strip()}")


@click.command("list")
@click.pass_context
def cmd_groups_list(ctx: click.Context) -> None:
    """List all groups with skill previews."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)
    groups = index.get("groups", {})

    if not groups:
        console.print(f"[yellow]{e('⚠', '!')}[/] No groups yet.")
        console.print("   Create one with [bold]skill-store groups create <slug> <name> <description>[/]")
        return

    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Group", style="cyan", no_wrap=True)
    table.add_column("Skills", style="green", justify="right")
    table.add_column("Description", style="white")
    table.add_column("Preview", style="dim")

    for gslug in sorted(groups.keys()):
        group = groups[gslug]
        skill_list = group.get("skills", [])
        preview = ", ".join(skill_list[:10])
        if len(skill_list) > 10:
            preview += f", ... ({len(skill_list) - 10} more)"
        table.add_row(
            gslug,
            str(len(skill_list)),
            group.get("description", ""),
            preview or "<empty>",
        )

    console.print(table)


@click.command("delete")
@click.argument("slug")
@click.confirmation_option(prompt="Are you sure you want to delete this group?")
@click.pass_context
def cmd_groups_delete(ctx: click.Context, slug: str) -> None:
    """Delete a group (skills are not deleted)."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    if not group_exists(index, slug):
        console.print(f"[red]{e('✗', 'x')}[/] Group [bold]'{slug}'[/] not found.")
        sys.exit(1)

    del index["groups"][slug]
    organized = len({s for g in index["groups"].values() for s in g.get("skills", [])})
    index["stats"]["groups"] = len(index["groups"])
    index["stats"]["organized"] = organized

    save_index(store, index)
    git_auto_commit(store, f"groups: delete '{slug}'")
    console.print(f"[green]{e('✓', '+')}[/] Deleted group [bold]{slug}[/]")


@click.command("add")
@click.argument("group_slug")
@click.argument("skill_slugs", nargs=-1, required=True)
@click.pass_context
def cmd_groups_add(ctx: click.Context, group_slug: str, skill_slugs: tuple[str, ...]) -> None:
    """Add one or more skills to a group."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    group = get_group(index, group_slug)
    if not group:
        console.print(f"[red]{e('✗', 'x')}[/] Group [bold]'{group_slug}'[/] not found.")
        sys.exit(1)

    existing_skills = {s["slug"] for s in index.get("skills", [])}
    added: list[str] = []
    skipped: list[str] = []

    for skill_slug in skill_slugs:
        if skill_slug not in existing_skills:
            console.print(f"[yellow]{e('⚠', '!')}[/] Skill [bold]'{skill_slug}'[/] does not exist — skipping.")
            skipped.append(skill_slug)
            continue
        if skill_slug in group.get("skills", []):
            console.print(f"[yellow]{e('⚠', '!')}[/] Skill [bold]'{skill_slug}'[/] already in group — skipping.")
            skipped.append(skill_slug)
            continue
        group.setdefault("skills", []).append(skill_slug)
        added.append(skill_slug)

    if added:
        organized = len({s for g in index["groups"].values() for s in g.get("skills", [])})
        index["stats"]["organized"] = organized
        save_index(store, index)
        git_auto_commit(store, f"groups: add {len(added)} skill(s) to '{group_slug}'")
        console.print(f"[green]{e('✓', '+')}[/] Added [bold]{', '.join(added)}[/] to [bold]{group_slug}[/]")
    elif not skipped:
        console.print(f"[yellow]{e('⚠', '!')}[/] Nothing to add.")


@click.command("rm")
@click.argument("group_slug")
@click.argument("skill_slugs", nargs=-1, required=True)
@click.pass_context
def cmd_groups_rm(ctx: click.Context, group_slug: str, skill_slugs: tuple[str, ...]) -> None:
    """Remove one or more skills from a group."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    group = get_group(index, group_slug)
    if not group:
        console.print(f"[red]{e('✗', 'x')}[/] Group [bold]'{group_slug}'[/] not found.")
        sys.exit(1)

    removed: list[str] = []
    for skill_slug in skill_slugs:
        if skill_slug in group.get("skills", []):
            group["skills"].remove(skill_slug)
            removed.append(skill_slug)

    if removed:
        organized = len({s for g in index["groups"].values() for s in g.get("skills", [])})
        index["stats"]["organized"] = organized
        save_index(store, index)
        git_auto_commit(store, f"groups: remove {len(removed)} skill(s) from '{group_slug}'")
        console.print(f"[green]{e('✓', '+')}[/] Removed [bold]{', '.join(removed)}[/] from [bold]{group_slug}[/]")
    else:
        console.print(f"[yellow]{e('⚠', '!')}[/] None of the specified skills were in the group.")


@click.command("organize-helper")
@click.pass_context
def cmd_groups_organize_helper(ctx: click.Context) -> None:
    """List all skills that are not in any group (the todo list)."""
    store: Path = ctx.obj["store"]
    _check_store(store)
    index = load_index(store)

    organized_slugs = {s for g in index.get("groups", {}).values() for s in g.get("skills", [])}
    all_skills = index.get("skills", [])
    orphans = [s for s in all_skills if s["slug"] not in organized_slugs]

    if not orphans:
        console.print(f"[green]{e('✓', '+')}[/] All [bold]{len(all_skills)}[/] skills are organized into groups. Good job!")
        return

    pinned_slugs = set(index.get("pinned", []))
    console.print(f"[bold]Ungrouped skills[/] ({len(orphans)} of {len(all_skills)}):")
    console.print("")

    for i, skill in enumerate(orphans):
        slug = skill["slug"]
        desc = clean_desc(skill.get("description", ""))
        prefix = e("⭐ ", "* ") if slug in pinned_slugs else "+ "
        click.echo(f"{prefix}{slug}")
        if desc:
            click.echo("  " + desc)
        else:
            click.echo("  <no description>")
        if i < len(orphans) - 1:
            click.echo(e("───", "---"))

    console.print("")
    console.print("[dim]Tip: Add a skill to a group with [bold]skill-store groups add <group> <skill>[/][/]")


# Register group subcommands
cmd_groups.add_command(cmd_groups_create, "create")
cmd_groups.add_command(cmd_groups_list, "list")
cmd_groups.add_command(cmd_groups_delete, "delete")
cmd_groups.add_command(cmd_groups_add, "add")
cmd_groups.add_command(cmd_groups_rm, "rm")
cmd_groups.add_command(cmd_groups_organize_helper, "organize-helper")


# ---------------------------------------------------------------------------
# COMMAND: version
# ---------------------------------------------------------------------------


@click.command()
@click.pass_context
def cmd_version(ctx: click.Context) -> None:
    """Show the skill-store version."""
    click.echo(f"skill-store v{__version__}")


# ---------------------------------------------------------------------------
# COMMAND: help
# ---------------------------------------------------------------------------


@click.command()
@click.argument("command", required=False)
@click.pass_context
def cmd_help(ctx: click.Context, command: str | None) -> None:
    """Show help for a command, or general help."""
    if command:
        cmd = cli.get_command(ctx, command)
        if cmd is None:
            console.print(f"[red]{e('✗', 'x')}[/] Unknown command: [bold]{command}[/]")
            suggestions = [c for c in cli.list_commands(ctx) if command in c]
            if suggestions:
                console.print(f"   Did you mean: {', '.join(suggestions)}?")
            else:
                console.print("   Run [bold]skill-store --help[/] to see available commands.")
            sys.exit(1)
        with click.Context(cmd, info_name=command, parent=ctx.parent) as cmd_ctx:
            click.echo(cmd.get_help(cmd_ctx))
    else:
        if ctx.parent is not None:
            click.echo(cli.get_help(ctx.parent))
        else:
            click.echo(cli.get_help(ctx))


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


@click.group(no_args_is_help=True)
@click.version_option(
    version=__version__,
    prog_name="skill-store",
    message="%(prog)s v%(version)s",
)
@click.option(
    "--store",
    "-s",
    default=None,
    envvar="SKILL_STORE_PATH",
    type=click.Path(path_type=Path),
    help="Path to the skill store (default: ~/.skill-store)",
)
@click.pass_context
def cli(ctx: click.Context, store: Path | None) -> None:
    """skill-store — A CLI for managing agent skills."""
    ctx.ensure_object(dict)
    ctx.obj["store"] = store if store else resolve_store_path()


# Register commands
cli.add_command(cmd_init, "init")
cli.add_command(cmd_sync, "sync")
cli.add_command(cmd_create_new, "create-new")
cli.add_command(cmd_load, "load")
cli.add_command(cmd_preview, "preview")
cli.add_command(cmd_list, "list")
cli.add_command(cmd_search, "search")
cli.add_command(cmd_pin, "pin")
cli.add_command(cmd_unpin, "unpin")
cli.add_command(cmd_groups, "groups")
cli.add_command(cmd_status, "status")
cli.add_command(cmd_version, "version")
cli.add_command(cmd_help, "help")


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
