---
name: skill-store
description: >
  On-demand skill loading from a local skill registry. Trigger on: "skill store",
  "load skill", "find a skill", "list skills", "import skill", "skill-store",
  "browse skills", "search skills", or any request to fetch a skill that is NOT
  currently loaded in the active context.

  This skill is NOT for managing the already-loaded skills in your prompt.
  It is for accessing the much larger skill storage (~100s to 1000s) that you
  only bring into context when you need them. Think of it as a lazy loader:
  the skills here stay on disk until you explicitly call for them via CLI.
---

# Skill Store

Your active context holds ~10 skills. The skill store holds everything else.

Use this CLI to browse, search, and pull skills on demand from
`~/.skill-store/` — without polluting your context window.

## Quick Start

```bash
skill-store list          # what's available (not what's loaded)
skill-store search web    # find by keyword
skill-store load <slug>   # read a skill into context
```

## Commands

| Command | What it does |
|---|---|
| `list` | Paginated list (pinned first). `--json` for machine output |
| `search <query>` | Full-text search names + descriptions |
| `load <slug>` | Print path, SKILL.md path, tree. `--json` for machine output |
| `preview <slug>` | Print first 100 lines of SKILL.md |
| `pin <slug>` / `unpin <slug>` | Move to top / back to alphabetical |

## Installing the CLI

See [`references/install.md`](references/install.md).

## Adding Skills

| Source | Where to look |
|---|---|
| Community packages | See [`references/npx-skills.md`](references/npx-skills.md) |
| `.skill` bundles | See [`references/importing.md`](references/importing.md) |
| Create your own | See [`references/creating.md`](references/creating.md) |

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `SKILL_STORE_PATH` | `~/.skill-store/` | Override store location |
