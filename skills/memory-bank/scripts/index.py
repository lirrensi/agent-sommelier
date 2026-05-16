#!/usr/bin/env python3
"""Rebuild memory/INDEX.md from all .md files in ./memory/."""

import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

MEMORY_DIR = Path("./memory")
INDEX_PATH = MEMORY_DIR / "INDEX.md"

# Order memory types will appear in the index
TYPE_ORDER = ["episodic", "semantic", "procedural", "decision", "person", "project", "unknown"]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and remaining body from markdown text."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    return data, match.group(2)


def relative_link(full_path: Path) -> str:
    """Return a relative markdown link from memory root."""
    rel = full_path.relative_to(MEMORY_DIR).as_posix()
    return f"[{full_path.stem}]({rel})"


def gather_memories() -> list[dict]:
    """Walk memory/ and collect metadata from every .md file (except INDEX.md)."""
    memories = []
    if not MEMORY_DIR.exists():
        return memories

    for path in sorted(MEMORY_DIR.rglob("*.md")):
        if path.name.lower() == "index.md":
            continue
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        memories.append(
            {
                "path": path,
                "meta": meta,
                "body": body,
                "summary": str(meta.get("summary", "")).strip().strip('"').strip("'"),
                "memory_type": str(meta.get("memory_type", "unknown")).lower().strip(),
                "tags": meta.get("tags", []) or [],
                "status": str(meta.get("status", "active")).lower().strip(),
                "updated": str(meta.get("updated", "")),
            }
        )
    return memories


def build_index(memories: list[dict]) -> str:
    """Render INDEX.md content."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Memory Index",
        "",
        f"> Last rebuilt: {now}  ",
        "> Run `python skills/memory-bank/scripts/index.py` to refresh.",
        "",
        "---",
        "",
    ]

    # Group by memory_type
    by_type = defaultdict(list)
    for m in memories:
        by_type[m["memory_type"]].append(m)

    # Table of contents
    lines.append("## Contents")
    lines.append("")
    for t in TYPE_ORDER:
        if t in by_type:
            count = len(by_type[t])
            label = t.capitalize()
            noun = "memory" if count == 1 else "memories"
            lines.append(f"- [{label}](#{label.lower()}) — {count} {noun}")
    lines.append("- [Tag Index](#tag-index)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Per-type sections
    for t in TYPE_ORDER:
        if t not in by_type:
            continue
        label = t.capitalize()
        lines.append(f"## {label}")
        lines.append("")
        for m in by_type[t]:
            link = relative_link(m["path"])
            summary = m["summary"] or "*No summary*"
            status_badge = ""
            if m["status"] != "active":
                status_badge = f" `[{m['status']}]`"
            updated = f" _updated {m['updated']}_" if m["updated"] else ""
            lines.append(f"- {link}{status_badge} — {summary}{updated}")
        lines.append("")

    # Tag index
    tag_map = defaultdict(list)
    for m in memories:
        for tag in m["tags"]:
            tag_map[str(tag)].append(m)

    lines.append("## Tag Index")
    lines.append("")
    if tag_map:
        for tag in sorted(tag_map.keys(), key=str.lower):
            entries = tag_map[tag]
            lines.append(f"### {tag}")
            lines.append("")
            for m in entries:
                link = relative_link(m["path"])
                summary = m["summary"] or "*No summary*"
                lines.append(f"- {link} — {summary}")
            lines.append("")
    else:
        lines.append("_No tags found yet._")
        lines.append("")

    return "\n".join(lines)


def main():
    if not MEMORY_DIR.exists():
        print(f"Error: {MEMORY_DIR} does not exist. Run init.py first.")
        sys.exit(1)

    memories = gather_memories()
    content = build_index(memories)
    INDEX_PATH.write_text(content, encoding="utf-8")
    print(f"Rebuilt {INDEX_PATH} ({len(memories)} memories indexed)")


if __name__ == "__main__":
    main()
