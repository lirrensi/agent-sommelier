"""skill-store — Core data logic for the skill registry.

Pure functions with no Click or Rich console coupling.
Used by both the CLI (cli.py) and MCP server (mcp.py).
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

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_STORE_DIR = Path.home() / ".skill-store"
SKILLS_DIR_NAME = "skills"
INDEX_FILE_NAME = "index.json"
INDEX_VERSION = 2
PAGE_SIZE = 20

# ---------------------------------------------------------------------------
# JSON helper
# ---------------------------------------------------------------------------


def json_dumps(obj: Any) -> str:
    """Serialize to JSON with consistent formatting."""
    return json.dumps(obj, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Unicode / emoji safety
# ---------------------------------------------------------------------------


def supports_unicode() -> bool:
    """Detect if stdout can safely render unicode symbols/emojis."""
    if not sys.stdout.isatty():
        return False
    if any(os.environ.get(k) for k in ("WT_SESSION", "VSCODE_CWD", "TERMINUS_SUBLIME")):
        return True
    if os.environ.get("TERM_PROGRAM") in ("iTerm.app", "Apple_Terminal", "vscode", "Hyper"):
        return True
    if os.environ.get("COLORTERM") in ("truecolor", "24bit"):
        return True
    term = os.environ.get("TERM", "")
    if "256color" in term or "xterm" in term:
        return True
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return encoding in ("utf-8", "utf-8-sig", "utf_8", "utf_8_sig", "cp65001")


def e(symbol: str, fallback: str = "") -> str:
    """Return symbol if terminal supports unicode, else fallback."""
    return symbol if supports_unicode() else fallback


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
        raise RuntimeError(
            f"Store not initialized at {store}\n"
            f"  Run skill-store init first."
        )


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------


def load_index(store: Path) -> dict[str, Any]:
    """Load the index.json from the store."""
    idx_path = store / INDEX_FILE_NAME
    if not idx_path.exists():
        return {
            "version": INDEX_VERSION,
            "pinned": [],
            "skills": [],
            "groups": {},
            "stats": {"total": 0, "pinned": 0, "groups": 0, "organized": 0, "updated_at": ""},
        }
    with open(idx_path, encoding="utf-8") as f:
        index = json.load(f)
    index.setdefault("groups", {})
    stats = index.setdefault("stats", {})
    stats.setdefault("groups", 0)
    stats.setdefault("organized", 0)
    return index


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

    if target_dir.exists():
        if not sys.stdin.isatty():
            raise RuntimeError(
                f"Collision: '{original_slug}' already exists. "
                "Delete or rename the existing folder, or run in a terminal."
            )

        print(f"Skill '{original_slug}' already exists.")
        action = input("  [O]verwrite, [S]kip, [R]ename: ").strip().lower()

        if action.startswith("s"):
            return False
        elif action.startswith("o"):
            shutil.rmtree(target_dir)
        elif action.startswith("r"):
            while True:
                new_slug = input("  New slug: ").strip()
                err = validate_slug(new_slug)
                if err:
                    print(f"  {err}")
                    continue
                if (skills_dir / new_slug).exists():
                    print(f"  '{new_slug}' already exists.")
                    continue
                break
            target_slug = new_slug
            target_dir = skills_dir / target_slug
        else:
            return False

    try:
        with zipfile.ZipFile(skill_file, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                print(f"  Corrupt zip: {bad}")
                return False
            with tempfile.TemporaryDirectory(prefix="skill-extract-") as tmp:
                tmp_path = Path(tmp)
                zf.extractall(tmp_path)
                entries = list(tmp_path.iterdir())
                if len(entries) == 1 and entries[0].is_dir():
                    source = entries[0]
                else:
                    source = tmp_path
                target_dir.mkdir(parents=True, exist_ok=True)
                for item in source.iterdir():
                    dest = target_dir / item.name
                    if dest.exists():
                        if dest.is_dir():
                            shutil.rmtree(dest)
                        else:
                            dest.unlink()
                    shutil.move(str(item), str(dest))
    except zipfile.BadZipFile:
        print(f"  {skill_file.name} is not a valid zip.")
        return False
    except Exception as exc:
        print(f"  Failed to extract {skill_file.name}: {exc}")
        return False

    skill_file.unlink()
    print(f"  Extracted {skill_file.name} -> {target_slug}")
    return True


# ---------------------------------------------------------------------------
# Tree builder (text-based, for JSON output)
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
# Timestamp helpers
# ---------------------------------------------------------------------------


def get_created_time(path: Path) -> str:
    """Get a reasonable 'created' timestamp for a skill folder."""
    skill_md = path / "SKILL.md"
    if skill_md.exists():
        ts = skill_md.stat().st_mtime
    else:
        ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def get_updated_time(path: Path) -> str:
    """Get the latest modification time from any file in the skill folder."""
    latest = 0.0
    for f in path.rglob("*"):
        if f.is_file():
            latest = max(latest, f.stat().st_mtime)
    if latest == 0.0:
        latest = path.stat().st_mtime
    return datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Ripgrep helpers for content search
# ---------------------------------------------------------------------------


def rg_available() -> bool:
    """Check if ripgrep (rg) is available on the system PATH."""
    return shutil.which("rg") is not None


def rg_search_json(skills_dir: Path, query: str) -> dict[str, dict[str, Any]]:
    """Search skill file contents via ``rg --json``.

    Returns a dict keyed by slug::

        {"my-tool": {"slug": "my-tool", "matches": [...]}}
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
# Display helpers (shared by CLI display and MCP output)
# ---------------------------------------------------------------------------


def clean_desc(text: str) -> str:
    """Normalize a description: collapse newlines and excessive whitespace."""
    if not text:
        return ""
    return " ".join(text.split())


def display_skills_list(
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
    """
    max_width = min(shutil.get_terminal_size(fallback=(100, 24)).columns, 100)
    desc_indent = 2

    print("")
    print(title)

    for i, skill in enumerate(skills):
        slug = skill["slug"]
        desc = clean_desc(skill.get("description", ""))

        prefix = e("⭐ ", "* ") if skill["slug"] in pinned_slugs else "+ "
        print(f"{prefix}{slug}")

        if desc:
            wrapped_lines = textwrap.wrap(
                desc,
                width=max_width - desc_indent,
                break_long_words=False,
            )
            for line in wrapped_lines:
                print(" " * desc_indent + line)
        else:
            print(" " * desc_indent + "<no description>")

        if i < len(skills) - 1:
            print(e("───", "---"))

    if page_info:
        print("")
        print(f"  {page_info}")


# ---------------------------------------------------------------------------
# Group helpers
# ---------------------------------------------------------------------------


def group_exists(index: dict[str, Any], group_slug: str) -> bool:
    """Check if a group exists in the index."""
    return group_slug in index.get("groups", {})


def get_group(index: dict[str, Any], group_slug: str) -> dict[str, Any] | None:
    """Get a group by slug from the index."""
    return index.get("groups", {}).get(group_slug)


def validate_group_slug(slug: str) -> str | None:
    """Validate a group slug. Returns an error message or None."""
    if not slug:
        return "Group slug cannot be empty."
    if not slug.isascii() or not slug.replace("-", "").isalnum():
        return "Group slug must be kebab-case: only lowercase letters, numbers, and hyphens."
    if slug != slug.lower():
        return "Group slug must be lowercase."
    if slug.startswith("-") or slug.endswith("-"):
        return "Group slug cannot start or end with a hyphen."
    if "--" in slug:
        return "Group slug cannot have consecutive hyphens."
    return None
