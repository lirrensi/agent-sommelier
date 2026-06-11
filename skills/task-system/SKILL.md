---
name: task-system
description: "Use this skill when you need to manage project tasks — create, update, complete, prioritize, filter, review, track dependencies, or find unblocked work. Trigger on: 'add a task', 'create task', 'show tasks', 'what's next', 'mark done', 'update task', 'task status', 'task history', 'next task', 'task inbox', 'list tasks', 'init tasks', 'task deps', 'ready tasks', 'blocked tasks', 'search tasks', 'tag-any', 'dependency graph'. Also use proactively when starting a new work session — check `tasks status` and `tasks ready` to orient yourself. This skill covers the project's static, file-based task system (persistent, in-repo history) with typed dependency tracking, ready queue, and priority management — NOT ephemeral runtime task tools."
---

# Task System

A lightweight, **static, file-based task management system** embedded in the repository. Tasks are permanent history — once created, never deleted. No database, no server, no internet.

**This is separate from runtime/ephemeral task tools.** Those disappear when the session ends. This task system lives in the repo. Its tasks are kept forever.

---

## Core Philosophy

- **Static, file-based, in-repo** — Three files in `.agents/tasks/`: `inbox.md`, `tasks.yaml`, `closed.yaml`. No database, no service, no setup beyond `tasks init`.
- **Tasks are permanent history** — Nothing is ever deleted. Tasks flow from `tasks.yaml` (active) to `closed.yaml` (archive).
- **12 statuses, any lifecycle** — `todo`, `in-progress`, `done`, `blocked`, `postponed`, `cancelled`, `review`, `waiting`, `parked`, `deferred`, `backlog`, `abandoned`. Move freely between them.
- **Counter-based IDs** — Tasks get `TSK-NNNN` IDs (e.g. `TSK-0001`). Auto-incrementing.
- **Numeric priorities (p0-p4)** — `p0` (critical) > `p4` (backlog). Named aliases also work.
- **Dependency system** — Typed deps (`blocks`, `parent`, `child`, `discovered`, `relates`). `blocks` affects the ready queue.
- **Newest-first** ordering in active list, oldest-first in archive.

### ⚠️ Golden Rule: YAML files are CLI-only. Inbox.md is free-form.

| File | Who edits it | How |
|------|-------------|-----|
| `tasks.yaml` | **Never by hand** | CLI only (`tasks add`, `tasks update`, `tasks close`) |
| `closed.yaml` | **Never by hand** | CLI only (`tasks close` appends automatically) |
| `inbox.md` | **Anyone, any time** | Free-form text — humans dump ideas, agents paste notes, raw scraps live here |

---

## File Layout

```
<project-root>/
  .agents/tasks/
    inbox.md       # Raw dump zone — paste ideas, notes, scraps here
    tasks.yaml     # Active task list (any status, closed=false)
    closed.yaml    # Closed task archive (append-only, permanent)
```

All files are self-documenting — open any YAML file and the header explains the system.

---

## Statuses

| Status | Meaning | Typical next step |
|--------|---------|-------------------|
| `todo` | Needs to be done | `in-progress` |
| `in-progress` | Being actively worked on | `done`, `blocked`, `review` |
| `done` | Complete (moves to archive) | — |
| `blocked` | Cannot proceed — waiting on external dependency | `waiting`, `todo` |
| `postponed` | Pushed to later — not blocked, just delayed | `todo`, `backlog` |
| `cancelled` | Will not be done | — |
| `review` | Needs review or approval | `done`, `todo`, `in-progress` |
| `waiting` | Waiting on someone else | `todo`, `review` |
| `parked` | Set aside, may come back | `todo`, `backlog` |
| `deferred` | Deliberately delayed to a known future time | `todo`, `in-progress` |
| `backlog` | Lower-priority, no current intent to start | `todo`, `parked` |
| `abandoned` | Started but permanently abandoned | — |

---

## Quick Start

```bash
# Bootstrap
tasks init

# Create tasks
tasks add "Refactor auth middleware" --priority p1 --tag refactor
tasks add "Fix login bug" --tag bug --tag security --priority p0
tasks add "Write tests" --dep TSK-0003:blocks --notes "Must cover edge cases"
tasks add "Related docs" --related TSK-0005

# Start working
tasks take TSK-0001
tasks update TSK-0001 --status blocked --notes "Waiting on API key from IT"
tasks update TSK-0001 --status in-progress      # unblocked

# Review and close
tasks close TSK-0001 --note "Deployed to prod, monitors green"

# Session check-in
tasks status           # counts by status, inbox line count
tasks ready            # what's actionable right now
tasks blocked           # what's stuck and why
tasks next --take 3    # priority queue
```

---

## How to Operate

### Starting a Session

```bash
# 1. Check what's pending
tasks status

# 2. See what's unblocked and actionable
tasks ready

# 3. Or see full queue (including blocked)
tasks next --take all

# 4. Check for blockers if things feel stuck
tasks blocked
```

### Inbox Processing — The Most Important Workflow

Turn raw notes into structured tasks, then leave the inbox empty. This is the **ingress pipeline** — the gateway where vague ideas become delegatable work.

```bash
# 1. Read everything in the inbox — don't skip items
tasks inbox

# 2. Draft tasks for every actionable item
#    For each item, ask: "Is this clear enough to execute without further input?"
#    If unclear → ask the user clarifying questions BEFORE creating the task
#    If clear  → draft with title, notes, priority, tags

tasks add "Fix login timeout on mobile" --source inbox --priority high --tag bug --notes "Must reproduce on iOS Safari and Android Chrome"
tasks add "Update README with API examples" --source inbox --priority low --tag docs --notes "Add curl examples for all 5 endpoints"

# 3. Present the draft tasks to the user for confirmation
#    "I understood your inbox as 4 tasks. Here's what I'll create: [...] Proceed?"
#    Only create tasks after explicit confirmation.

# 4. Clear the inbox completely — leave it empty
echo. > .agents/tasks/inbox.md          # Windows: wipe to blank
# > .agents/tasks/inbox.md              # Unix: wipe to blank
```

**Ingress Rules:**
- **Take ALL items**, not just the easy ones
- **Ask clarifying questions** when an item is vague. If the user chooses not to answer, create the task anyway with whatever context is available — the user owns the task, not you.
- **Get confirmation** before creating tasks and wiping the file
- After processing, the inbox must be **empty** — not partially cleaned, not annotated, not "organized." Empty.
- If an item is unclear and the user is unavailable, **leave it in the inbox** and process what you can

> **Default behavior:** Wipe the inbox clean after processing. Only skip if the user explicitly says to keep it. When in doubt, empty it.

### Tracking Progress

```bash
# Start working
tasks update TSK-0004 --status in-progress

# Hit a blocker — mark it
tasks update TSK-0004 --status blocked --notes "Waiting on API key from IT"
tasks update TSK-0004 --dep TSK-0010:blocks  # record what's blocking it
# Later: unblocked
tasks update TSK-0004 --status in-progress

# Needs review before shipping
tasks update TSK-0004 --status review

# Priority changed — push it to later
tasks update TSK-0004 --status postponed --priority p3

# Put it in the backlog for future triage
tasks update TSK-0004 --status backlog

# Something came up — change priority, add context
tasks update TSK-0004 --priority p0 --notes "Client escalation, needs immediate attention"

# Cancelled — no longer relevant
tasks update TSK-0004 --status cancelled --notes "Requirement dropped"

# Close it — could be cancelled, abandoned, or finished
tasks close TSK-0004 --note "Deployed to prod, monitors green"
```

### Moving Between Statuses

No restrictions on status transitions. You can move a task from any status to any other:

```bash
# Direct skip: backlog → done
tasks update TSK-0010 --status done

# Backwards: review → todo (more work needed)
tasks update TSK-0011 --status todo --notes "Review found issues, needs rework"

# Dead end: in-progress → abandoned
tasks update TSK-0012 --status abandoned --notes "Proof of concept failed"
```

The only special status is `done` — use `tasks close TSK-NNNN` to move it to the archive.
Use `tasks update TSK-NNNN --status done` only when backdating or correcting a closed task's status.

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

## Examples

```bash
# Create a task with rich metadata
tasks add "Proof trail" --evidence "file: src/agent_sommelier/tasks/core.py"

# Task with dependency blocking another
tasks add "Deploy to production" --dep TSK-0007:blocks --priority p0

# Start working, claim it
tasks take TSK-0001 --claimed rx
tasks claim TSK-0001 --claimed rx

# Multi-dimensional tagging — don't just tag one thing
tasks add "Fix race condition in auth" --tag bug --tag auth --tag security --tag autonomous-ready

# From a detailed inbox note
tasks add "Implement rate limiting" --source inbox --priority medium --notes "See inbox.md for requirements"

# Change everything at once
tasks update TSK-0004 --status postponed --priority p3 --notes "Client deprioritized this"
```

### Dependency graph example
```bash
# Create tasks with chains
tasks add "Design API"
tasks add "Implement API" --dep TSK-0001:blocks
tasks add "Test API" --dep TSK-0002:blocks
tasks add "Deploy API" --dep TSK-0003:blocks

# View the chain
tasks deps TSK-0004
# → TSK-0001 (blocks) → TSK-0002 (blocks) → TSK-0003 (blocks) → TSK-0004
```

---

## When to Use This Skill

Use the task system **proactively** — not just when asked:

- **Session start**: `tasks status` + `tasks ready` to orient
- **When you discover work**: `tasks add "..." --tag type --tag area`
- **When context shifts**: `tasks update TSK-NNNN --status ...`
- **When you complete work**: `tasks close TSK-NNNN --note "..."`
- **When the inbox has items**: Read → draft → confirm → wipe clean
- **Before significant changes**: Check what's in-progress
- **When priorities need review**: `tasks next --take all`
- **When exploring relationships**: `tasks deps TSK-NNNN`

### When NOT to use it
- Don't create duplicates of tasks already in the system
- **Never edit `tasks.yaml` or `closed.yaml` by hand** — always use the CLI
- **Inbox.md IS the exception** — edit it freely. It is a scratch file.
- Don't delete tasks — they're permanent history. Close them instead.
- Don't use for ephemeral runtime tracking (use `crony`, `bg-jobs`, or runtime tools for that).

---

## Deeper Reading

| Topic | File |
|---|---|
| Full CLI reference (all commands, flags, option tables) | [`references/cli.md`](references/cli.md) |
