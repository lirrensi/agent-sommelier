"""skill-store — A CLI for managing agent skills.

Manage a folder-based registry of agent skills at ~/.skill-store/.
Load, list, pin, create, and sync skills with automatic git backups.

Usage:
    skill-store                   Show help (no args = help)
    skill-store --version         Show version
    skill-store --help            Show this help
    skill-store init              Scaffold store + git init
    skill-store sync              Scan skills, rebuild index, git commit
    skill-store create-new        Interactive skill scaffold
    skill-store load <slug>       Show path + tree for a skill
    skill-store list [--page N]   Paginated listing (pinned first)
    skill-store pin <slug>        Pin a skill to top of list
    skill-store unpin <slug>      Unpin a skill
    skill-store version           Show version
    skill-store help [command]    Show help for a command
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich.syntax import Syntax

from agentcli_helpers import __version__

# ---------------------------------------------------------------------------
# JSON helper
# ---------------------------------------------------------------------------


def _json_dumps(obj: Any) -> str:
    """Serialize to JSON with consistent formatting."""
    return json.dumps(obj, indent=2, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_STORE_DIR = Path.home() / ".skill-store"
SKILLS_DIR_NAME = "skills"
INDEX_FILE_NAME = "index.json"
INDEX_VERSION = 1
PAGE_SIZE = 20

# ---------------------------------------------------------------------------
# Console (global for pretty output)
# ---------------------------------------------------------------------------

console = Console()

# ---------------------------------------------------------------------------
# Store path resolution
# ---------------------------------------------------------------------------


def resolve_store_path() -> Path:
    """Resolve the store path: env var > default."""
    override = os.environ.get("SKILL_STORE_PATH")
    return Path(override) if override else DEFAULT_STORE_DIR


def ensure_store_initialized(store: Path) -> None:
    """Raise if the store hasn't been initialized."""
    if not (store / INDEX_FILE_NAME).exists():
        console.print(
            f"[red]✗[/] Store not initialized at [bold]{store}[/]\n"
            f"  Run [bold]skill-store init[/] first."
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------


def load_index(store: Path) -> dict[str, Any]:
    """Load the index.json from the store."""
    idx_path = store / INDEX_FILE_NAME
    if not idx_path.exists():
        return {"version": INDEX_VERSION, "pinned": [], "skills": [], "stats": {"total": 0, "pinned": 0, "updated_at": ""}}
    with open(idx_path, encoding="utf-8") as f:
        return json.load(f)


def save_index(store: Path, index: dict[str, Any]) -> None:
    """Write index.json atomically."""
    idx_path = store / INDEX_FILE_NAME
    tmp = idx_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    tmp.replace(idx_path)


def find_skill(index: dict[str, Any], slug: str) -> dict[str, Any] | None:
    """Look up a skill by slug in the index."""
    for s in index["skills"]:
        if s["slug"] == slug:
            return s
    return None


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_skill_frontmatter(skill_dir: Path) -> dict[str, str] | None:
    """Parse YAML frontmatter from SKILL.md. Returns None if missing/invalid."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None

    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    try:
        meta = yaml.safe_load(parts[1])
        if isinstance(meta, dict):
            return {k: str(v) for k, v in meta.items()}
        return None
    except yaml.YAMLError:
        return None


# ---------------------------------------------------------------------------
# Tree builder for load command
# ---------------------------------------------------------------------------


def build_tree_lines(path: Path, prefix: str = "") -> list[str]:
    """Build a text-based directory tree (Unix tree style)."""
    lines: list[str] = []
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")

        if entry.is_dir():
            extension = "    " if is_last else "│   "
            lines.extend(build_tree_lines(entry, prefix + extension))

    return lines


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
# Git helpers
# ---------------------------------------------------------------------------


def git_run(store: Path, *args: str) -> bool:
    """Run a git command inside the store. Returns True on success."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=store,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def git_auto_commit(store: Path, message: str) -> None:
    """Stage all changes and commit in the store repo. Silent if git unavailable."""
    if not git_run(store, "add", "-A"):
        return
    # Only commit if there's something to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=store,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        git_run(store, "commit", "-m", message, "--allow-empty")


# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------


def validate_slug(slug: str) -> str | None:
    """Validate a skill slug. Returns an error message or None."""
    if not slug:
        return "Slug cannot be empty."
    if not slug.isascii() or not slug.replace("-", "").isalnum():
        return "Slug must be kebab-case: only lowercase letters, numbers, and hyphens."
    if slug != slug.lower():
        return "Slug must be lowercase."
    if slug.startswith("-") or slug.endswith("-"):
        return "Slug cannot start or end with a hyphen."
    if "--" in slug:
        return "Slug cannot have consecutive hyphens."
    return None


# ---------------------------------------------------------------------------
# COMMAND: init
# ---------------------------------------------------------------------------


@click.command()
@click.pass_context
def cmd_init(ctx: click.Context) -> None:
    """Scaffold the skill store directory and initialize git."""
    store = ctx.obj["store"]

    if store.exists() and (store / INDEX_FILE_NAME).exists():
        console.print("[yellow]⚠[/] Skill store already initialized.")
        console.print(f"   Location: [bold]{store}[/]")
        return

    store.mkdir(parents=True, exist_ok=True)
    (store / SKILLS_DIR_NAME).mkdir(exist_ok=True)

    index = {
        "version": INDEX_VERSION,
        "pinned": [],
        "skills": [],
        "stats": {"total": 0, "pinned": 0, "updated_at": ""},
    }
    save_index(store, index)

    # Git init
    git_init_ok = git_run(store, "init")
    if git_init_ok:
        # Set a descriptive default branch name (doesn't override existing)
        git_run(store, "checkout", "-b", "main")
        # Write .gitignore
        gitignore = store / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*.tmp\n__pycache__/\n.venv/\n")
        git_auto_commit(store, "chore: initialize skill store")
        console.print(f"[green]✓[/] Initialized skill store at [bold]{store}[/]")
        console.print(f"[green]✓[/] Git repository initialized")
    else:
        console.print(f"[green]✓[/] Initialized skill store at [bold]{store}[/]")
        console.print("[yellow]⚠[/] Git not found — skipping repository init")
        console.print("   Install git for automatic versioned backups.")


# ---------------------------------------------------------------------------
# .skill file extraction
# ---------------------------------------------------------------------------


def process_skill_file(skill_file: Path, skills_dir: Path) -> bool:
    """Extract a .skill zip into skills_dir. Returns True if processed.

    Handles collisions: in TTY mode prompts for overwrite/skip/rename;
    in non-TTY mode errors out. Deletes the .skill file on success.
    """
    original_slug = skill_file.stem
    target_slug = original_slug
    target_dir = skills_dir / target_slug

    # --- Collision check ---
    if target_dir.exists():
        if not sys.stdin.isatty():
            console.print(
                f"[red]✗[/] Collision: [bold]'{original_slug}'[/] already exists."
            )
            console.print(
                "   Delete or rename the existing folder, or run in a terminal."
            )
            return False

        console.print(
            f"[yellow]⚠[/] Skill [bold]'{original_slug}'[/] already exists."
        )
        action = click.prompt(
            "  [O]verwrite, [S]kip, [R]ename", default="s"
        ).strip().lower()

        if action.startswith("s"):
            return False
        elif action.startswith("o"):
            shutil.rmtree(target_dir)
        elif action.startswith("r"):
            while True:
                new_slug = click.prompt("  New slug").strip()
                err = validate_slug(new_slug)
                if err:
                    console.print(f"  [red]✗[/] {err}")
                    continue
                if (skills_dir / new_slug).exists():
                    console.print(
                        f"  [red]✗[/] [bold]'{new_slug}'[/] already exists."
                    )
                    continue
                break
            target_slug = new_slug
            target_dir = skills_dir / target_slug
        else:
            return False

    # --- Extract to temp dir, then move to final location ---
    try:
        with zipfile.ZipFile(skill_file, "r") as zf:
            # Quick validation: check it's a real zip
            bad = zf.testzip()
            if bad is not None:
                console.print(f"  [red]✗[/] Corrupt zip: {bad}")
                return False

            with tempfile.TemporaryDirectory(prefix="skill-extract-") as tmp:
                tmp_path = Path(tmp)
                zf.extractall(tmp_path)

                # Determine the source: if zip had a single root dir, descend into it
                entries = list(tmp_path.iterdir())
                if len(entries) == 1 and entries[0].is_dir():
                    source = entries[0]
                else:
                    source = tmp_path

                # Move contents to target
                target_dir.mkdir(parents=True, exist_ok=True)
                items = list(source.iterdir())
                for item in items:
                    dest = target_dir / item.name
                    if dest.exists():
                        if dest.is_dir():
                            shutil.rmtree(dest)
                        else:
                            dest.unlink()
                    shutil.move(str(item), str(dest))

    except zipfile.BadZipFile:
        console.print(f"  [red]✗[/] [bold]{skill_file.name}[/] is not a valid zip.")
        return False
    except Exception as exc:
        console.print(f"  [red]✗[/] Failed to extract [bold]{skill_file.name}[/]: {exc}")
        return False

    # Remove the .skill file now that extraction is complete
    skill_file.unlink()
    console.print(
        f"  [green]✓[/] Extracted [bold]{skill_file.name}[/] → [bold]{target_slug}[/]"
    )
    return True


# ---------------------------------------------------------------------------
# COMMAND: sync
# ---------------------------------------------------------------------------


@click.command()
@click.pass_context
def cmd_sync(ctx: click.Context) -> None:
    """Scan skills, rebuild index, and create a git snapshot."""
    store = ctx.obj["store"]
    ensure_store_initialized(store)

    index = load_index(store)
    skills_dir = store / SKILLS_DIR_NAME

    # --- Process any .skill files before scanning directories ---
    if skills_dir.is_dir():
        skill_files = sorted(skills_dir.glob("*.skill"))
        for sf in skill_files:
            process_skill_file(sf, skills_dir)

    # Preserve current pinned slugs and their order
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
                "created": _get_created_time(entry),
                "updated": _get_updated_time(entry),
            }
            scanned.append(skill_entry)

    # Merge pinned: preserve order, remove stale pins
    updated_pinned = [s for s in pinned_slugs if any(skill["slug"] == s for skill in scanned)]
    pinned_set = set(updated_pinned)

    # Build final skills list: pinned first (in order), then alphabetical
    pinned_skills = [s for s in scanned if s["slug"] in pinned_set]
    pinned_skills.sort(key=lambda s: updated_pinned.index(s["slug"]) if s["slug"] in updated_pinned else 999)
    unpinned_skills = sorted(
        [s for s in scanned if s["slug"] not in pinned_set],
        key=lambda s: s["slug"],
    )

    index["skills"] = pinned_skills + unpinned_skills
    index["pinned"] = updated_pinned
    index["stats"] = {
        "total": len(scanned),
        "pinned": len(updated_pinned),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    save_index(store, index)

    # Git snapshot
    git_auto_commit(store, f"sync: {len(scanned)} skills ({len(updated_pinned)} pinned)")

    console.print(f"[green]✓[/] Scanned [bold]{len(scanned)}[/] skills ([bold]{len(updated_pinned)}[/] pinned, [bold]{len(scanned) - len(updated_pinned)}[/] unpinned)")
    console.print(f"[green]✓[/] Index updated")
    if len(scanned) == 0:
        console.print("   [dim]No skills found. Add one with [bold]skill-store create-new[/].[/]")


def _get_created_time(path: Path) -> str:
    """Get a reasonable 'created' timestamp for a skill folder."""
    # Use the SKILL.md modification time as proxy
    skill_md = path / "SKILL.md"
    if skill_md.exists():
        ts = skill_md.stat().st_mtime
    else:
        ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _get_updated_time(path: Path) -> str:
    """Get the latest modification time from any file in the skill folder."""
    latest = 0.0
    for f in path.rglob("*"):
        if f.is_file():
            latest = max(latest, f.stat().st_mtime)
    if latest == 0.0:
        latest = path.stat().st_mtime
    return datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# COMMAND: create-new
# ---------------------------------------------------------------------------


@click.command()
@click.pass_context
def cmd_create_new(ctx: click.Context) -> None:
    """Interactive wizard to scaffold a new skill."""
    store = ctx.obj["store"]
    ensure_store_initialized(store)
    index = load_index(store)
    existing_slugs = {s["slug"] for s in index["skills"]}

    console.print("[bold]Creating a new skill[/]")
    console.print("─" * 40)

    # --- Slug ---
    slug = ""
    while True:
        slug = click.prompt("  Slug", default="").strip()
        err = validate_slug(slug)
        if err:
            console.print(f"  [red]✗[/] {err}")
            continue
        if slug in existing_slugs:
            console.print(f"  [red]✗[/] Slug [bold]'{slug}'[/] already exists.")
            continue
        break

    # --- Name ---
    name = click.prompt("  Name", default=slug.replace("-", " ").title()).strip()
    if not name:
        name = slug

    # --- Description ---
    description = click.prompt("  Description").strip()
    while not description:
        console.print("  [yellow]⚠[/] Description is required.")
        description = click.prompt("  Description").strip()

    # --- Create folder ---
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

    console.print(f"\n[green]✓[/] Created skill at [bold]{skill_dir}[/]")

    # --- Run sync to update index ---
    ctx.invoke(cmd_sync)


# ---------------------------------------------------------------------------
# COMMAND: load
# ---------------------------------------------------------------------------


@click.command()
@click.argument("slug")
@click.option("--json/--no-json", "-j", default=False, help="Output in JSON format")
@click.pass_context
def cmd_load(ctx: click.Context, slug: str, json: bool) -> None:
    """Show the path, SKILL.md location, and folder tree for a skill."""
    store = ctx.obj["store"]
    ensure_store_initialized(store)
    index = load_index(store)

    skill = find_skill(index, slug)
    if not skill:
        console.print(f"[red]✗[/] Skill [bold]'{slug}'[/] not found.")
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
        click.echo(_json_dumps(result))
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
    store = ctx.obj["store"]
    ensure_store_initialized(store)
    index = load_index(store)

    skill = find_skill(index, slug)
    if not skill:
        console.print(f"[red]✗[/] Skill [bold]'{slug}'[/] not found.")
        suggestions = [s["slug"] for s in index["skills"] if slug in s["slug"]]
        if suggestions:
            console.print(f"   Did you mean: {', '.join(suggestions)}?")
        else:
            console.print(f"   Run [bold]skill-store list[/] to see available skills.")
        sys.exit(1)

    skill_path = store / skill["path"]
    skill_md = skill_path / "SKILL.md"

    if not skill_md.exists():
        console.print(f"[red]✗[/] No [bold]SKILL.md[/] found in [bold]{slug}[/].")
        sys.exit(1)

    lines = skill_md.read_text(encoding="utf-8").splitlines()
    preview = lines[:100]

    for line in preview:
        click.echo(line)

    if len(lines) > 100:
        click.echo(f"... (truncated, {len(lines)} total lines)", err=True)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _clean_desc(text: str) -> str:
    """Normalize a description: collapse newlines and excessive whitespace."""
    if not text:
        return ""
    return " ".join(text.split())


def _display_skills_list(
    title: str,
    skills: list[dict[str, Any]],
    pinned_slugs: set[str],
    *,
    page_info: str | None = None,
) -> None:
    """Print a clean, lightweight skill listing.

    Format::

        ⭐ slug
          Normalized description...
        ───
          slug
          Normalized description...
        ───
    """
    max_width = min(shutil.get_terminal_size(fallback=(100, 24)).columns, 100)
    desc_indent = 2

    click.echo("")
    click.echo(title)

    for i, skill in enumerate(skills):
        slug = skill["slug"]
        desc = _clean_desc(skill.get("description", ""))

        # Slug line: "⭐ slug" or "+ slug"
        prefix = "⭐ " if skill["slug"] in pinned_slugs else "+ "
        click.echo(f"{prefix}{slug}")

        # Description wrapped and indented
        if desc:
            wrapped_lines = textwrap.wrap(
                desc,
                width=max_width - desc_indent,
                break_long_words=False,
            )
            for line in wrapped_lines:
                click.echo(" " * desc_indent + line)
        else:
            click.echo(" " * desc_indent + "<no description>")

        # Thin separator ("---")
        if i < len(skills) - 1:
            click.echo("---")

    if page_info:
        click.echo("")
        click.echo(f"  {page_info}")


# ---------------------------------------------------------------------------
# COMMAND: list
# ---------------------------------------------------------------------------


@click.command()
@click.option("--page", "-p", default=1, type=int, help="Page number (1-indexed)")
@click.option("--json/--no-json", "-j", default=False, help="Output in JSON format")
@click.pass_context
def cmd_list(ctx: click.Context, page: int, json: bool) -> None:
    """List skills with pagination (pinned first, then alphabetical)."""
    store = ctx.obj["store"]
    ensure_store_initialized(store)
    index = load_index(store)

    skills = index["skills"]
    total = len(skills)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    if page < 1 or page > total_pages:
        console.print(f"[red]✗[/] Page [bold]{page}[/] out of range (1–{total_pages}).")
        sys.exit(1)

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_skills = skills[start:end]

    if total == 0:
        if json:
            click.echo(_json_dumps({
                "page": page,
                "total_pages": total_pages,
                "total": 0,
                "pinned": [],
                "skills": [],
            }))
        else:
            console.print("[yellow]⚠[/] No skills in the store.")
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
        click.echo(_json_dumps(result))
    else:
        page_info = None
        if total_pages > 1:
            page_info = f"Page {page} / {total_pages}  —  use --page N to navigate"
        _display_skills_list(
            f"Skills — Page {page}/{total_pages} ({total} total)",
            page_skills,
            pinned_slugs,
            page_info=page_info,
        )


# ---------------------------------------------------------------------------
# RG helpers for content search
# ---------------------------------------------------------------------------


def _rg_available() -> bool:
    """Check if ripgrep (rg) is available on the system PATH."""
    return shutil.which("rg") is not None


def _rg_search_json(skills_dir: Path, query: str) -> dict[str, dict[str, Any]]:
    """Search skill file contents via ``rg --json``.

    Returns a dict keyed by slug::

        {"my-tool": {"slug": "my-tool", "matches": [{"file": ..., "line": ..., "content": ...}]}}
    """
    try:
        result = subprocess.run(
            ["rg", "--json", "-i", query, str(skills_dir.resolve())],
            capture_output=True,
            text=False,
            timeout=30,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return {}

    if result.returncode not in (0, 1):
        return {}

    resolved = skills_dir.resolve()
    matches_by_slug: dict[str, dict[str, Any]] = {}

    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if obj.get("type") != "match":
            continue

        data = obj.get("data", {})
        path_text = data.get("path", {}).get("text", "")

        try:
            rel = Path(path_text).relative_to(resolved)
            slug = rel.parts[0]
        except (ValueError, IndexError):
            continue

        if slug not in matches_by_slug:
            matches_by_slug[slug] = {"slug": slug, "matches": []}

        match_info = {
            "file": str(rel),
            "line": data.get("line_number", 0),
            "content": data.get("lines", {}).get("text", "").rstrip("\n"),
        }
        matches_by_slug[slug]["matches"].append(match_info)

    return matches_by_slug


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
    store = ctx.obj["store"]
    ensure_store_initialized(store)
    index = load_index(store)

    if not query.strip():
        if json:
            click.echo(_json_dumps({"query": query, "results": 0, "skills": []}))
        else:
            console.print("[yellow]⚠[/] Search query cannot be empty.")
        return

    import re

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
    if _rg_available() and skills_dir.is_dir():
        rg_data = _rg_search_json(skills_dir, query)
        rg_used = bool(rg_data)

    # 3 --- Merge results ----------------------------------------------------
    index_slugs = {r["slug"] for r in index_results}

    # Enrich index results with rg match data
    for r in index_results:
        if r["slug"] in rg_data:
            r["matches"] = rg_data[r["slug"]]["matches"]
            r["match_count"] = len(r["matches"])

    # Add content-only results (found by rg, not by metadata)
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

    # Sort: name → description → content; alphabetical within groups
    _source_rank = {"name": 0, "description": 1, "content": 2}
    index_results.sort(
        key=lambda r: (_source_rank.get(r["match_source"], 99), r["slug"])
    )

    # 4 --- Output -----------------------------------------------------------
    if not index_results:
        if json:
            click.echo(_json_dumps({
                "query": query, "results": 0, "rg_used": rg_used, "skills": [],
            }))
        else:
            msg = f"[yellow]⚠[/] No skills match [bold]'{query}'[/]."
            if not rg_used and _rg_available():
                msg += " (searched metadata only, no content matches)"
            elif not _rg_available():
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
        click.echo(_json_dumps(result_data))
    else:
        pinned_slugs = set(index.get("pinned", []))

        # Build display entries with match info in description
        display_skills: list[dict[str, Any]] = []
        for r in index_results:
            source = r["match_source"]
            if source == "name":
                badge = "name"
            elif source == "description":
                badge = "desc"
            else:
                badge = "content"

            cnt = f" ({r['match_count']})" if r["match_count"] else ""
            tag = f"[{badge}{cnt}]"

            desc = r.get("description", "") or ""
            if tag:
                desc = f"{tag} {desc}" if desc else tag

            display_skills.append({
                "slug": r["slug"],
                "description": desc,
            })

        title = f"Search: '{query}' ({len(index_results)} found)"
        if rg_used:
            title += " \u00b7 rg"

        _display_skills_list(title, display_skills, pinned_slugs)

        if rg_used:
            click.echo("  ripgrep searched file contents -- use --json for full match details")
        elif not _rg_available():
            click.echo("  Install ripgrep (rg) for deeper content-level search")


# ---------------------------------------------------------------------------
# COMMAND: pin / unpin
# ---------------------------------------------------------------------------


@click.command()
@click.argument("slug")
@click.pass_context
def cmd_pin(ctx: click.Context, slug: str) -> None:
    """Pin a skill to the top of the list."""
    store = ctx.obj["store"]
    ensure_store_initialized(store)
    index = load_index(store)

    skill = find_skill(index, slug)
    if not skill:
        console.print(f"[red]✗[/] Skill [bold]'{slug}'[/] not found.")
        sys.exit(1)

    pinned = index.get("pinned", [])
    if slug not in pinned:
        pinned.append(slug)
        index["pinned"] = pinned
        save_index(store, index)
        console.print(f"[green]✓[/] Pinned [bold]{slug}[/]")

        # Move skill entry to pinned section in current sort
        ctx.invoke(cmd_sync)
    else:
        console.print(f"[yellow]⚠[/] [bold]{slug}[/] is already pinned.")


@click.command()
@click.argument("slug")
@click.pass_context
def cmd_unpin(ctx: click.Context, slug: str) -> None:
    """Unpin a skill (remove from top)."""
    store = ctx.obj["store"]
    ensure_store_initialized(store)
    index = load_index(store)

    skill = find_skill(index, slug)
    if not skill:
        console.print(f"[red]✗[/] Skill [bold]'{slug}'[/] not found.")
        sys.exit(1)

    pinned = index.get("pinned", [])
    if slug in pinned:
        pinned.remove(slug)
        index["pinned"] = pinned
        save_index(store, index)
        console.print(f"[green]✓[/] Unpinned [bold]{slug}[/]")

        # Re-sort so this falls to alphabetical
        ctx.invoke(cmd_sync)
    else:
        console.print(f"[yellow]⚠[/] [bold]{slug}[/] is not pinned.")


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
            console.print(f"[red]✗[/] Unknown command: [bold]{command}[/]")
            suggestions = [
                c for c in cli.list_commands(ctx) if command in c
            ]
            if suggestions:
                console.print(
                    f"   Did you mean: {', '.join(suggestions)}?"
                )
            else:
                console.print(
                    "   Run [bold]skill-store --help[/] to see available commands."
                )
            sys.exit(1)
        # Use the parent (cli) context so help shows correct usage prefix
        with click.Context(
            cmd, info_name=command, parent=ctx.parent
        ) as cmd_ctx:
            click.echo(cmd.get_help(cmd_ctx))
    else:
        # Show the root group help via the parent context
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
cli.add_command(cmd_version, "version")
cli.add_command(cmd_help, "help")


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
