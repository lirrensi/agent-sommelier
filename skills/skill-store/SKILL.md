---
name: skill-store
description: >
  Manage, browse, and import agent skills using the skill-store CLI at
  ~/.skill-store/. Trigger on: "skill store", "list skills", "find a skill",
  "create a skill", "load skill", "import .skill file", "skill-store",
  "browse skills", "search skills", "pin skill", or any request involving
  managing a collection of agent skills. Use this skill whenever the user
  wants to organize, discover, or distribute skills across sessions.
---

# Skill Store

A CLI for managing a folder-based registry of agent skills at `~/.skill-store/`.
Load skills on demand, pin favorites, search by name/description, and import
`.skill` bundle files.

---

## Quick Start

```bash
# Initialize the store (one-time)
skill-store init

# Create your first skill
skill-store create-new

# See what's available
skill-store list

# Read a skill's contents
skill-store preview <slug>
skill-store load <slug>
```

---

## Commands

### `skill-store init`

Scaffold the store directory, create `index.json`, and `git init` for
automatic backups. Idempotent — safe to run again.

```bash
skill-store init
```

### `skill-store sync`

Scan `~/.skill-store/skills/` for skill folders and `.skill` files, rebuild
the index, and create a git snapshot.

- `.skill` files are auto-extracted (zip format → folder) on sync
- On collision: in a terminal you get prompted to overwrite/skip/rename;
  in non-TTY mode it errors cleanly
- Bad zips or corrupt files are reported and skipped

```bash
skill-store sync
```

### `skill-store create-new`

Interactive wizard. Prompts for slug (validated: lowercase kebab-case, no
collisions), name, and description. Creates the folder and a templated
`SKILL.md`.

```bash
skill-store create-new
```

### `skill-store load <slug>`

Print the absolute path, SKILL.md path, and folder tree for a skill.

```bash
skill-store load web-scraper          # human-readable
skill-store load web-scraper --json   # machine-readable
```

JSON output includes: `slug`, `name`, `description`, `path`, `skillmd`,
`tree` (array of tree lines).

### `skill-store preview <slug>`

Print the first 100 lines of a skill's `SKILL.md` as plain text to stdout.
No formatting, no tree — just the raw content so you know what you're
dealing with.

```bash
skill-store preview web-scraper
```

If the file exceeds 100 lines, a `... (truncated, N total lines)` message
is printed to stderr.

### `skill-store list`

Paginated listing (20 per page). Pinned skills appear first in order,
then alphabetical by slug.

```bash
skill-store list               # page 1
skill-store list --page 2      # page 2
skill-store list --json        # machine-readable
```

JSON output: `{page, total_pages, total, pinned[], skills[{slug, name,
description, pinned}]}`

### `skill-store search <query>`

Case-insensitive full-text search across skill names and descriptions.
Name matches sort before description matches, then alphabetical.

```bash
skill-store search scraper
skill-store search "web" --json
```

JSON output: `{query, results, skills[{slug, name, description,
matched_field}]}`

### `skill-store pin <slug>` / `skill-store unpin <slug>`

Pin a skill to the top of the list. Unpin to return it to alphabetical
order.

```bash
skill-store pin web-scraper
skill-store unpin web-scraper
```

---

## The Store Layout

```
~/.skill-store/
├── index.json          # Auto-managed catalog (never hand-edit)
├── skills/             # All skill folders
│   ├── web-scraper/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   └── references/
│   └── ...
└── .git/               # Automatic git backup
```

### `index.json` schema

```json
{
  "version": 1,
  "pinned": ["web-scraper"],
  "skills": [
    {
      "slug": "web-scraper",
      "name": "Web Scraper",
      "description": "Extract and structure data from web pages",
      "path": "skills/web-scraper",
      "created": "2026-05-16T10:00:00Z",
      "updated": "2026-05-16T10:00:00Z"
    }
  ],
  "stats": {
    "total": 1,
    "pinned": 1,
    "updated_at": "2026-05-16T10:00:00Z"
  }
}
```

---

## Skill Format

Every skill is a folder with a `SKILL.md` at its root:

```
<slug>/
├── SKILL.md              # Required. YAML frontmatter + markdown body
│   ├── name: string      # Human-readable name
│   └── description: string  # Trigger description (what this skill does)
├── scripts/              # Optional. Executable helpers
├── references/           # Optional. Docs loaded on demand
└── assets/               # Optional. Templates, icons, etc.
```

---

## Importing `.skill` Bundles

A `.skill` file is just a zip of a skill folder. Drop it into
`~/.skill-store/skills/` and run `skill-store sync` — it gets extracted
and indexed automatically.

```bash
# Drop the bundle
cp path/to/web-scraper.skill ~/.skill-store/skills/

# Sync picks it up
skill-store sync
```

---

## Environment

| Variable | Purpose |
|---|---|
| `SKILL_STORE_PATH` | Override the store location (default: `~/.skill-store/`) |

Useful for testing or isolating stores per project:

```bash
export SKILL_STORE_PATH=/path/to/custom-store
skill-store init
skill-store create-new
```
