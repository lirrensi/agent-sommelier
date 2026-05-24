"""skill-store — MCP server for the local skill registry.

Provides FastMCP tools: search_skills, get_skill, preview_skill, list_skills.
Run with: skill-store-mcp (stdio transport, for agent integration).

Usage in opencode.json:
    "mcpServers": {
        "skill-store": {
            "command": "skill-store-mcp"
        }
    }
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agent_sommelier.skill_store.core import (
    SKILLS_DIR_NAME,
    build_tree_lines,
    ensure_store_initialized,
    find_skill,
    json_dumps,
    load_index,
    parse_skill_frontmatter,
    resolve_store_path,
    rg_available,
    rg_search_json,
)


def _resolve_store() -> Path:
    """Resolve store path from env or default."""
    return resolve_store_path()


def _get_store_or_exit() -> Path:
    """Get the store path, ensuring it's initialized. Raises if not."""
    store = _resolve_store()
    ensure_store_initialized(store)
    return store


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("skill-store")

    @mcp.tool()
    def search_skills(query: str) -> str:
        """Full-text search across skill metadata and file contents.

        Searches skill names, descriptions (from index) and file contents
        (when ripgrep is available). Returns structured JSON results with
        match sources and per-file match details.
        """
        store = _get_store_or_exit()
        index = load_index(store)

        import re

        pattern = re.compile(re.escape(query), re.IGNORECASE)
        results: list[dict] = []

        # Index-based search (name + description)
        for skill in index["skills"]:
            name_match = bool(pattern.search(skill.get("name", "")))
            desc_match = bool(pattern.search(skill.get("description", "")))
            if name_match or desc_match:
                results.append({
                    "slug": skill["slug"],
                    "name": skill["name"],
                    "version": skill.get("version", "1"),
                    "description": skill.get("description", ""),
                    "match_source": "name" if name_match else "description",
                    "match_count": 0,
                    "matches": [],
                })

        # Content search via rg
        rg_results: dict[str, dict] = {}
        skills_dir = store / SKILLS_DIR_NAME
        if rg_available() and skills_dir.is_dir():
            rg_results = rg_search_json(skills_dir, query)

        index_slugs = {r["slug"] for r in results}

        # Enrich index results with rg match data
        for r in results:
            if r["slug"] in rg_results:
                r["matches"] = rg_results[r["slug"]]["matches"]
                r["match_count"] = len(r["matches"])

        # Add content-only results
        for slug, data in rg_results.items():
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
                results.append({
                    "slug": slug,
                    "name": name,
                    "version": vers,
                    "description": desc,
                    "match_source": "content",
                    "match_count": len(data["matches"]),
                    "matches": data["matches"],
                })

        # Sort: name -> description -> content; alphabetical within groups
        _source_rank = {"name": 0, "description": 1, "content": 2}
        results.sort(key=lambda r: (_source_rank.get(r["match_source"], 99), r["slug"]))

        return json_dumps({
            "query": query,
            "results": len(results),
            "skills": results,
        })

    @mcp.tool()
    def get_skill(slug: str) -> str:
        """Get detailed info about a skill: metadata, file tree, and paths.

        Returns JSON with slug, name, version, description, resolved path,
        SKILL.md location, and directory tree.
        """
        store = _get_store_or_exit()
        index = load_index(store)

        skill = find_skill(index, slug)
        if not skill:
            return json_dumps({"error": f"Skill '{slug}' not found"})

        skill_path = store / skill["path"]
        tree_lines = build_tree_lines(skill_path) if skill_path.is_dir() else []

        return json_dumps({
            "slug": slug,
            "name": skill["name"],
            "version": skill.get("version", "1"),
            "description": skill.get("description", ""),
            "path": str(skill_path.resolve()),
            "skillmd": str((skill_path / "SKILL.md").resolve()),
            "tree": tree_lines,
        })

    @mcp.tool()
    def preview_skill(slug: str, lines: int = 100) -> str:
        """Read the first N lines of a skill's SKILL.md file.

        Args:
            slug: The skill identifier (e.g. 'memory-bank')
            lines: Number of lines to show (default 100, max 500)
        """
        store = _get_store_or_exit()
        index = load_index(store)

        skill = find_skill(index, slug)
        if not skill:
            return json_dumps({"error": f"Skill '{slug}' not found"})

        skill_path = store / skill["path"]
        skill_md = skill_path / "SKILL.md"

        if not skill_md.exists():
            return json_dumps({"error": f"No SKILL.md found in '{slug}'"})

        max_lines = min(max(lines, 1), 500)
        content_lines = skill_md.read_text(encoding="utf-8").splitlines()
        preview = content_lines[:max_lines]
        truncated = len(content_lines) > max_lines

        return json_dumps({
            "slug": slug,
            "name": skill["name"],
            "skillmd": str(skill_md.resolve()),
            "total_lines": len(content_lines),
            "preview_lines": max_lines,
            "truncated": truncated,
            "content": "\n".join(preview),
        })

    @mcp.tool()
    def list_skills(page: int = 1, group: str | None = None) -> str:
        """List skills with pagination and optional group filter.

        Args:
            page: Page number (1-indexed, default 1)
            group: Optional group slug to filter by (e.g. 'testing', 'deploy')
        """
        store = _get_store_or_exit()
        index = load_index(store)

        from agent_sommelier.skill_store.core import PAGE_SIZE

        all_skills = index["skills"]

        # Filter by group if specified
        if group:
            group_data = index.get("groups", {}).get(group)
            if not group_data:
                return json_dumps({"error": f"Group '{group}' not found"})
            group_slugs = set(group_data.get("skills", []))
            all_skills = [s for s in all_skills if s["slug"] in group_slugs]

        total = len(all_skills)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

        if page < 1 or page > total_pages:
            return json_dumps({
                "error": f"Page {page} out of range (1–{total_pages})",
                "total_pages": total_pages,
            })

        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        page_skills = all_skills[start:end]
        pinned_slugs = set(index.get("pinned", []))

        return json_dumps({
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "group": group,
            "pinned": list(pinned_slugs),
            "skills": [
                {
                    "slug": s["slug"],
                    "name": s["name"],
                    "version": s.get("version", "1"),
                    "description": s.get("description", ""),
                    "pinned": s["slug"] in pinned_slugs,
                }
                for s in page_skills
            ],
        })

except ImportError:
    # The mcp package is optional — expose a clear error if missing
    FastMCP = None  # type: ignore[assignment]

    def main() -> None:
        """Entry point that tells the user to install mcp."""
        print("skill-store-mcp requires the 'mcp' package.", file=sys.stderr)
        print("Install with: uv tool install agent-sommelier-cli[mcp]", file=sys.stderr)
        print("        or: pip install agent-sommelier-cli[mcp]", file=sys.stderr)
        sys.exit(1)

else:

    def main() -> None:
        """Run the MCP server on stdio transport."""
        mcp.run()


if __name__ == "__main__":
    main()
