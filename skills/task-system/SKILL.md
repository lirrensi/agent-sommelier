---
name: task-system
description: "Use this skill when you need to manage project tasks — create, update, complete, prioritize, filter, or review work items. Trigger on: 'add a task', 'create task', 'show tasks', 'what's next', 'mark done', 'update task', 'task status', 'task history', 'next task', 'task inbox', 'list tasks', 'init tasks'. Also use proactively when starting a new work session — check the `tasks status` and `tasks next` to orient yourself before beginning new work. This skill covers the project's static, file-based task system (persistent, in-repo history) — NOT ephemeral runtime task tools."
---

# AgentCLI Task System

A lightweight, **static, file-based task management system** embedded directly in the repository. Tasks are permanent history — once created, they are never deleted. This system exists to help AI agents and humans manage project complexity with a simple CLI that needs no database, no server, and no internet.

**This is separate from any runtime/ephemeral task tools you may have.** Those are temporary — they disappear when the session ends. This task system lives in the repository. Its tasks are kept forever.

---

## Core Philosophy

- **Static, file-based, in-repo** — Three files in `tasks/`: `inbox.md`, `tasks.yaml`, `done.yaml`. No database, no service, no setup beyond `tasks init`.
- **Tasks are permanent history** — Nothing is ever deleted. Tasks flow from `tasks.yaml` (active) to `done.yaml` (archive). Once a task is created, its record persists for the life of the repository.
- **Purpose: manage project complexity** — Track what needs doing, what is in progress, what is done. Keep the agent and human aligned on priorities without losing context across sessions.
- **Simple state machine** — `todo → in-progress → done`. That's it. Complex workflows are not the goal; clarity is.
- **Counter-based IDs** — Tasks get `TSK-NNNN` IDs (e.g. `TSK-0001`). Auto-incrementing, no gaps are guaranteed (skipped IDs on concurrent writes are acceptable).
- **Newest-first ordering** — Recent tasks appear at the top of the active list. Done tasks are oldest-first (chronological append).
- **Growing complexity only when needed** — The system intentionally started minimal. New features are added only when a real need emerges.

### ⚠️ Golden Rule: YAML files are CLI-only. Inbox.md is free-form.

| File | Who edits it | How |
|------|-------------|-----|
| `tasks.yaml` | **Never by hand** | CLI only (`tasks add`, `tasks update`, `tasks done`) |
| `done.yaml` | **Never by hand** | CLI only (`tasks done` appends automatically) |
| `inbox.md` | **Anyone, any time** | Free-form text — humans dump ideas, agents paste notes, raw scraps live here |

The two YAML files are machine-managed. Editing them by hand will result in overwritten changes the next time a CLI command runs. **Always use the CLI to create, update, or complete tasks.**

The inbox is the opposite — it is explicitly a free-form scratch file. Write to it, paste into it, reorganize it, clean it up. The typical flow is:
1. Someone dumps raw ideas, notes, or scraps into `inbox.md`
2. An agent reads the inbox, translates each actionable item into a proper task via `tasks add "..." --source inbox`
3. Once processed, the inbox is cleaned up (emptied or annotated)

---

## File Layout

```
<project-root>/
  tasks/
    inbox.md       # Raw dump zone — paste ideas, notes, scraps here
    tasks.yaml     # Active task list (todo + in-progress)
    done.yaml      # Completed task archive (append-only, permanent)
```

All files are self-documenting — open any YAML file and the header explains the system.

---

## CLI Reference

All commands run as `tasks <command>` from the project root.

### `tasks init`

Bootstrap the task system. Creates `tasks/`, `inbox.md`, `tasks.yaml`, and `done.yaml`.

- **Idempotent** — safe to run multiple times. Existing data is never touched.
- Run this first in any new project that should use the task system.

```bash
tasks init
# → Created tasks/inbox.md
# → Created tasks/tasks.yaml
# → Created tasks/done.yaml
# → Task system initialized.
```

---

### `tasks add "<title>"`

Create a new task. ID is auto-generated.

```bash
tasks add "Refactor auth middleware"
tasks add "Fix login bug" --tag bug --tag urgent --priority high --source audit
tasks add "Write tests" --related TSK-0003 --notes "Must cover edge cases"
```

**Options:**
| Flag | Shorthand | Description |
|------|-----------|-------------|
| `--tag` | `-t` | Tag(s) to apply (repeatable, e.g. `-t bug -t ui`) |
| `--priority` | `-p` | Priority: `urgent`, `high`, `medium`, `low` |
| `--source` | `-s` | Source: `inbox`, `audit`, `test`, `jira`, `agent`, `idea` (default: `agent`) |
| `--related` | `-r` | Related task ID (e.g. `TSK-0042`) |
| `--notes` | `-n` | Freeform notes |

**Notes:**
- Tags are normalized to lowercase, whitespace becomes hyphens
- New tasks are prepended (newest at top)
- Default source is `agent` if not specified

---

### `tasks list`

List active tasks (not done). Newest first.

```bash
tasks list                                    # All active tasks
tasks list --status todo                      # Only todo tasks
tasks list --tag bug                          # Only tasks with 'bug' tag
tasks list --priority high --status in-progress
tasks list --json                             # JSON output for programmatic use
```

**Options:**
| Flag | Description |
|------|-------------|
| `--status` | Filter by status: `todo`, `in-progress`, `done` |
| `--tag` | Filter by tag (single tag) |
| `--priority` | Filter by priority |
| `--source` | Filter by source |
| `--json` | Output raw JSON instead of a Rich table |

---

### `tasks show <TSK-NNNN>`

Full detail of one task. Resolves related tasks inline (shows their status and title).

```bash
tasks show TSK-0001
```

**Sample output:**
```
  [TSK-0001] todo  priority: high
  Refactor auth middleware
  tags: refactor, auth
  source: audit
  related: TSK-0003 (in-progress) — "Write tests for auth"
  created: 2026-05-10

```

- Looks in both active and done lists
- Related tasks show their current status and title
- Stale related references show `(not found)`

---

### `tasks update <TSK-NNNN>`

Modify a task's fields.

```bash
tasks update TSK-0001 --status in-progress
tasks update TSK-0001 --priority urgent --tag needs-review
tasks update TSK-0001 --notes "Revised approach: use OAuth2" --related TSK-0005
```

**Options:**
| Flag | Description |
|------|-------------|
| `--status` | Set status: `todo`, `in-progress`, `done` |
| `--priority` | Set priority: `urgent`, `high`, `medium`, `low` |
| `--tag` / `-t` | **Append** tag(s) to existing tags (repeatable) — does NOT replace |
| `--related` | Set related task ID |
| `--notes` | **Replace** notes (does NOT append) |

---

### `tasks done <TSK-NNNN>`

Complete a task. Moves it from `tasks.yaml` to `done.yaml`.

```bash
tasks done TSK-0001
tasks done TSK-0001 --note "All tests passing, deployed to staging"
```

- Sets `status: done`, adds `completed` timestamp
- Appends optional `--note` to existing notes (line break separated)
- Task is removed from `tasks.yaml` and appended to `done.yaml`
- **Error if task already done**: "Task already done: TSK-NNNN"
- **Error if task not found**: "Task not found: TSK-NNNN"

---

### `tasks next`

Show the highest-priority todo task(s). Sorted by priority (urgent first) then newest first.

```bash
tasks next                           # Top 1 highest-priority todo
tasks next --take 3                  # Top 3
tasks next --take all                # All todo tasks, sorted by priority
tasks next --tag bug                 # Filter by tag first, then sort
tasks next --skip-related            # Exclude tasks whose related target isn't done
```

**Options:**
| Flag | Description |
|------|-------------|
| `--take` | Number of tasks to show, or `all` (default: `1`) |
| `--tag` | Filter by tag before sorting |
| `--priority` | Filter by priority before sorting |
| `--skip-related` | Exclude tasks whose `related` target is not yet done (ensures dependencies are resolved first) |

**Priority order:** `urgent` > `high` > `medium` > `low` > unset

---

### `tasks status`

Session overview — in-progress tasks, top priorities, tag frequency, inbox count.

```bash
tasks status              # Human-readable summary
tasks status --json       # JSON for programmatic use
```

**Sample output:**
```
Tasks: 1 in-progress, 4 todo

IN PROGRESS
  [TSK-0001] high    Refactor auth middleware  tags: refactor, auth

TOP PRIORITY
  [TSK-0002] urgent  Fix login vulnerability   tags: bug, security
  [TSK-0003] high    Write auth tests          tags: test

TAGS: auth(2), refactor(1), bug(1), security(1), test(1)

Inbox has 3 unprocessed entries. Run `tasks inbox` to view.
```

---

### `tasks history`

Show recently completed tasks (newest first).

```bash
tasks history                  # Rich table (newest first)
tasks history --json           # JSON array (newest first)
tasks history --tag deploy     # Filter by tag
```

- Done tasks are slightly stale — shows the most recently completed
- Use `--json` for machine consumption or filtering

---

### `tasks inbox`

Print the contents of `inbox.md`.

```bash
tasks inbox                    # Print raw inbox content
tasks inbox | wc -l            # Count inbox lines (or use `tasks status`)
```

**Inbox is a free-form human-writable file** — the only file in the system that is meant to be edited directly. Use it for:
- Dumping raw ideas, meeting notes, or bug reports as they come in
- Pasting CLI output, error messages, or logs for later triage
- Writing quick notes without worrying about structure

**Typical inbox workflow:**
1. Read the inbox: `tasks inbox`
2. Translate each actionable item into a proper task: `tasks add "..." --source inbox --tags ...`
3. Clean up the processed entries (edit `tasks/inbox.md` directly, clear out what's been handled)

The inbox is the input funnel. Tasks are the structured output.

---

### `tasks --help`

Full help with all commands and options.

```bash
tasks --help
tasks <command> --help         # Help for a specific command
```

---

## How to Operate

### Starting a Session

```bash
# 1. Check what's pending
tasks status

# 2. See what to do next
tasks next
```

### Inbox Processing

The most common multi-step operation. Turn raw notes into structured tasks:

```bash
# 1. Read what's in the inbox
tasks inbox

# 2. Promote each actionable item to a proper task
tasks add "Fix login timeout on mobile" --source inbox --priority high --tag bug
tasks add "Update README with API examples" --source inbox --priority low --tag docs

# 3. Clear processed entries from the inbox file
#    (edit tasks/inbox.md directly — remove or mark what's been handled)
```

### Creating Work

```bash
# Quick task from a known priority
tasks add "Fix the login redirect" --priority high --tag bug

# Task from a detailed inbox note
tasks add "Implement rate limiting" --source inbox --priority medium --notes "See inbox.md for requirements"

# Task that depends on something else
tasks add "Deploy to production" --related TSK-0007 --priority urgent
```

### Tracking Progress

```bash
# Start working
tasks update TSK-0004 --status in-progress

# Something came up — note it and change priority
tasks update TSK-0004 --priority urgent --notes "Client escalation, needs immediate attention"

# Done
tasks done TSK-0004 --note "Deployed to prod, monitors green"
```

### Reviewing

```bash
# What's on the plate
tasks list --status todo

# What's been accomplished
tasks history --json

# Full session overview
tasks status
```

---

## When to Use This Skill

Use the task system **proactively** — not just when asked. Good triggers:

- **Session start**: Run `tasks status` and `tasks next` to orient yourself before starting new work.
- **When you discover work**: If you find something that needs doing (a bug, missing test, refactor opportunity), create a task:
  ```bash
  tasks add "Investigate slow query in reports" --priority medium --tag performance
  ```
- **When you complete work**: Mark the task done with a closing note:
  ```bash
  tasks done TSK-0005 --note "Found: missing index on user_id. Added migration."
  ```
- **When context shifts**: Update the task — change priority, add tags, append context:
  ```bash
  tasks update TSK-0002 --priority urgent --tag security
  ```
- **When priorities need review**: Run `tasks next --take all` to see the full queue sorted by priority.
- **When the inbox has items**: Check `tasks status` — if inbox count > 0, read and promote:
  ```bash
  tasks inbox                             # read raw entries
  tasks add "..." --source inbox          # promote each actionable item
  # Then clean up tasks/inbox.md directly (remove processed entries)
  ```
- **Before significant changes**: Check `tasks list --status in-progress` to know what's actively being worked on.

### When NOT to use it

- Don't create tasks for things already tracked in the task system (duplicates)
- **Never edit `tasks.yaml` or `done.yaml` by hand** — always use the CLI. Manual edits will be overwritten.
- **Inbox.md IS the exception** — edit it freely. It is a scratch file, not a managed data store.
- Don't delete tasks — they're permanent history. Mark them done instead.
- Don't use this for ephemeral runtime tracking (use your runtime task tools for that).

---

## Design Rationale

### Why static, file-based, and in-repo?

- **Zero infrastructure** — No database, no server, no API keys. A Python script and three files.
- **Version controlled** — Task history travels with the repository. `git log` shows when tasks were created and completed.
- **Portable** — Any machine with the repo and `uv run tasks` can use it. No cloud dependency.
- **Transparent** — Open the YAML files and see everything. No hidden state.

### Why permanent history?

- Tasks document what was done and why — valuable for post-mortems, retrospectives, and onboarding.
- "Done" is not "delete." A completed task is a record of accomplishment, not garbage.
- The done archive grows as a project history that can be searched, analyzed, and learned from.

### Why separate from runtime task tools?

- Runtime tools (like `crony`, `bg-jobs`, or any ephemeral scheduler) are **per-session** — they track what's happening right now.
- This task system is **cross-session** and **permanent** — it tracks what needs doing and what was done over the life of the project.
- They complement each other: use runtime tools for immediate execution, use this task system for project-level planning and history.