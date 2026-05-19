---
name: task-system
description: "Use this skill when you need to manage project tasks — create, update, complete, prioritize, filter, review, track dependencies, or find unblocked work. Trigger on: 'add a task', 'create task', 'show tasks', 'what's next', 'mark done', 'update task', 'task status', 'task history', 'next task', 'task inbox', 'list tasks', 'init tasks', 'task deps', 'ready tasks', 'blocked tasks', 'search tasks', 'tag-any', 'dependency graph'. Also use proactively when starting a new work session — check `tasks status` and `tasks ready` to orient yourself. This skill covers the project's static, file-based task system (persistent, in-repo history) with typed dependency tracking, ready queue, and priority management — NOT ephemeral runtime task tools."
---

# Agent Sommelier Task System

A lightweight, **static, file-based task management system** embedded directly in the repository. Tasks are permanent history — once created, they are never deleted. This system exists to help AI agents and humans manage project complexity with a simple CLI that needs no database, no server, and no internet.

**This is separate from any runtime/ephemeral task tools you may have.** Those are temporary — they disappear when the session ends. This task system lives in the repository. Its tasks are kept forever.

---

## Core Philosophy

- **Static, file-based, in-repo** — Three files in `tasks/`: `inbox.md`, `tasks.yaml`, `closed.yaml`. No database, no service, no setup beyond `tasks init`.
- **Tasks are permanent history** — Nothing is ever deleted. Tasks flow from `tasks.yaml` (active) to `closed.yaml` (archive). Once a task is created, its record persists for the life of the repository.
- **Purpose: manage project complexity** — Track what needs doing, what is in progress, what is done. Keep the agent and human aligned on priorities without losing context across sessions.
- **12 statuses, any lifecycle** — Tasks can hold any of 12 statuses: `todo`, `in-progress`, `done`, `blocked`, `postponed`, `cancelled`, `review`, `waiting`, `parked`, `deferred`, `backlog`, `abandoned`. You move between them freely with `tasks update --status <name>` — there are no restrictions on transitions.
- **Counter-based IDs** — Tasks get `TSK-NNNN` IDs (e.g. `TSK-0001`). Auto-incrementing, no gaps are guaranteed (skipped IDs on concurrent writes are acceptable).
- **Numeric priorities (p0-p4)** — `p0` (critical) > `p1` > `p2` > `p3` > `p4` (backlog). Named aliases like `urgent`, `high`, `medium`, `low` still work as input.
- **Dependency system** — Tasks can declare typed dependencies (`blocks`, `parent`, `child`, `discovered`, `relates`). The `blocks` type affects the ready queue — `tasks ready` shows unblocked work, `tasks blocked` shows what's stuck.
- **Newest-first ordering** — Recent tasks appear at the top of the active list. Closed tasks are oldest-first (chronological append).
- **Growing complexity only when needed** — The system intentionally started minimal. New features are added only when a real need emerges.

### ⚠️ Golden Rule: YAML files are CLI-only. Inbox.md is free-form.

| File | Who edits it | How |
|------|-------------|-----|
| `tasks.yaml` | **Never by hand** | CLI only (`tasks add`, `tasks update`, `tasks close`) |
| `closed.yaml` | **Never by hand** | CLI only (`tasks close` appends automatically) |
| `inbox.md` | **Anyone, any time** | Free-form text — humans dump ideas, agents paste notes, raw scraps live here |

The two YAML files are machine-managed. Editing them by hand will result in overwritten changes the next time a CLI command runs. **Always use the CLI to create, update, or complete tasks.**

The inbox is the opposite — it is explicitly a free-form scratch file. Write to it, paste into it, dump ideas. The typical flow is:
1. Someone dumps raw ideas, notes, or scraps into `inbox.md`
2. An agent reads the inbox, translates each actionable item into a proper task via `tasks add "..." --source inbox`
3. Once processed, the inbox is wiped clean — left completely empty for the next round

---

## File Layout

```
<project-root>/
  tasks/
    inbox.md       # Raw dump zone — paste ideas, notes, scraps here
    tasks.yaml     # Active task list (any status, closed=false)
    closed.yaml    # Closed task archive (append-only, permanent)
```

All files are self-documenting — open any YAML file and the header explains the system.

---

## Statuses

Every task has a `status` field. The system accepts exactly these 12 statuses:

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

### Moving between statuses

Use `tasks update TSK-NNNN --status <name>` to change any task's status at any time:

```bash
tasks update TSK-0001 --status in-progress      # start working
tasks update TSK-0001 --status blocked           # hit a blocker
tasks update TSK-0001 --status waiting           # waiting on someone
tasks update TSK-0001 --status review            # needs review
tasks update TSK-0001 --status done              # finished (moves to archive)
```

> **`tasks close TSK-NNNN`** moves a task from `tasks.yaml` to `closed.yaml`
> without changing its status. Use it when a task is no longer active.
> Use `tasks update --status done` when you only want to change the status field
> without closing the task.

### Status lifecycle (common patterns)

```
# Typical flow:
  todo → in-progress → review → done

# Blocked flow:
  todo → in-progress → blocked → waiting → todo → in-progress → done

# Deferred flow:
  todo → in-progress → postponed → backlog → todo → done

# Cancellation:
  todo → in-progress → cancelled   (or abandoned)
```

Statuses are **not a fixed pipeline** — you can jump to any status from any other status.
`tasks update --status <name>` is the single way to change it, and it works on any transition.

### Filtering by status

```bash
tasks list --status todo                        # only todo tasks
tasks list --status blocked                     # find blockers
tasks list --status in-progress                 # what's being worked on
tasks list --status done                        # completed in active list
tasks next                                      # only shows "todo" tasks
```

---

## Closed (Archival)

The `closed` boolean flag separates active tasks from historical ones.

| `closed` value | Lives in | Meaning |
|---|---|---|
| `false` | `tasks.yaml` | Active — shows up in `tasks list`, `tasks status`, `tasks next` |
| `true` | `closed.yaml` | Historical — does NOT show up in daily views |

Close a task without changing its status:
```bash
tasks close TSK-NNNN                      # archive as-is, status preserved
tasks close TSK-NNNN --note "No longer needed"  # archive with note
```

Or set a specific status and close at the same time:
```bash
tasks update TSK-NNNN --status cancelled --closed   # cancelled + archived
tasks update TSK-NNNN --status abandoned  --closed  # abandoned + archived
```

> A task's status says *what state it is in*. The `closed` flag says *whether to look at it*.
> You can close a task with any status — `blocked`, `cancelled`, `postponed`, `backlog` — and
> it moves to `closed.yaml` without modifying the status.

---

## CLI Reference

All commands run as `tasks <command>` from the project root.

### `tasks init`

Bootstrap the task system. Creates `tasks/`, `inbox.md`, `tasks.yaml`, and `closed.yaml`.

- **Idempotent** — safe to run multiple times. Existing data is never touched.
- Run this first in any new project that should use the task system.

```bash
tasks init
# → Created tasks/inbox.md
# → Created tasks/tasks.yaml
# → Created tasks/closed.yaml
# → Task system initialized.
```

---

### `tasks add "<title>"`

Create a new task. ID is auto-generated.

```bash
tasks add "Refactor auth middleware"
tasks add "Fix login bug" --tag bug --tag urgent --priority p0 --source audit
tasks add "Write tests" --dep TSK-0003:blocks --notes "Must cover edge cases"
tasks add "Proof trail" --evidence "file: src/agent_sommelier/tasks/core.py"
tasks add "Blocked by API" --dep TSK-0002:blocks --priority high
tasks add "Related docs" --related TSK-0005
```

**Options:**
| Flag | Shorthand | Description |
|------|-----------|-------------|
| `--tag` | `-t` | Tag(s) to apply (repeatable, e.g. `-t bug -t ui`) |
| `--priority` | `-p` | Priority: `0`–`4` or name (`critical`, `urgent`, `high`, `medium`, `low`, `backlog`) |
| `--source` | `-s` | Source: `inbox`, `audit`, `test`, `jira`, `agent`, `idea` (default: `agent`) |
| `--dep` | — | Dependency in `id:type` format (e.g. `TSK-0042:blocks`). Types: `blocks`, `parent`, `child`, `discovered`, `relates`. Repeatable. |
| `--related` | `-r` | Related task ID (shorthand for `--dep id:relates`) |
| `--notes` | `-n` | Freeform notes (stored as array — see below) |
| `--evidence` | `-e` | Verification evidence / proof (stored as array — see below) |

**Notes:**
- Tags are normalized to lowercase, whitespace becomes hyphens
- New tasks are prepended (newest at top)
- Default source is `agent` if not specified
- Use `evidence` for quick verification breadcrumbs, final proof, or later re-checking

**Tagging strategy — think multi-dimensional:**

Tags are not a single category. They are **dimensions** you combine freely. A task can and should carry multiple tags that answer different questions:

| Dimension | Examples |
|---|---|
| **Type / Kind** | `bug`, `feature`, `improvement`, `research`, `docs`, `spike`, `deliverable` |
| **Area / Component** | `auth`, `api`, `ui`, `database`, `deployment`, `finance`, `legal` |
| **Qualifier** | `security`, `performance`, `accessibility`, `breaking-change`, `urgent` |
| **Process** | `needs-review`, `blocked`, `autonomous-ready`, `milestone-v2` |
| **Project-specific** | `q2-report`, `client-alpha`, `migration` |

```bash
# Good: multiple dimensions
tasks add "Fix race condition in auth" --tag bug --tag auth --tag security --tag autonomous-ready

# Good: non-software project
tasks add "Review Q2 contracts" --tag deliverable --tag legal --tag milestone-q2
```

> **Rule of thumb:** If a task only has one tag, you're probably under-describing it. Add at least a **type** tag and an **area** tag. The rest are context-dependent.

---

### `tasks list`

List active tasks (not closed). Newest first.

```bash
tasks list                                    # All active tasks
tasks list --status todo                      # Only todo tasks
tasks list --tag bug --tag auth               # Tasks with BOTH 'bug' AND 'auth' tags
tasks list --tag-any urgent --tag-any security # Tasks with EITHER 'urgent' OR 'security' tag
tasks list --priority p1 --status in-progress
tasks list --text "login"                     # Full-text search in active tasks
tasks list --related TSK-0001                 # Tasks with a dep pointing to TSK-0001
tasks list --json                             # JSON output for programmatic use
```

**Options:**
| Flag | Description |
|------|-------------|
| `--status` | Filter by status (12 options) |
| `--tag` | Filter by tag (repeatable, **AND** logic — all must match) |
| `--tag-any` | Filter by tag (repeatable, **OR** logic — any can match) |
| `--priority` | Filter by priority (`0`–`4` or name like `urgent`, `high`) |
| `--source` | Filter by source |
| `--related` | Filter by dependency task ID (matches any dep type) |
| `--text` | Full-text search across titles, notes, tags, and fields |
| `--json` | Output raw JSON instead of a Rich table |

---

### `tasks show <TSK-NNNN>`

Full detail of one task. Resolves dependencies inline (shows their type, status, and title).

```bash
tasks show TSK-0001
```

**Sample output:**
```
  [TSK-0001] todo  priority: p1
  Refactor auth middleware
  tags: refactor, auth
  source: audit
  dep (blocks): TSK-0003 (in-progress) — "Write tests for auth"
  dep (relates): TSK-0005 (done) — "Design API spec"
  created: 2026-05-10

```

- Looks in both active and closed lists
- Dependencies show their type badge, current status, and title
- Stale dep references show `(not found)`
- Notes and evidence are shown separately as appendable lists

---

### `tasks update <TSK-NNNN>`

Modify a task's fields. The most common use is changing the status.

```bash
# Change status (most common operation)
tasks update TSK-0001 --status in-progress      # start working
tasks update TSK-0001 --status blocked           # hit a blocker
tasks update TSK-0001 --status review            # needs review
tasks update TSK-0001 --status postponed         # push to later
tasks update TSK-0001 --status cancelled         # kill it
tasks update TSK-0001 --status waiting           # waiting on someone
tasks update TSK-0001 --status parked            # set aside
tasks update TSK-0001 --status deferred          # delayed to known date
tasks update TSK-0001 --status backlog           # lower priority queue
tasks update TSK-0001 --status abandoned         # permanently dropped

# Close while setting a specific status
tasks update TSK-0001 --status cancelled --closed

# Modify other fields
tasks update TSK-0001 --priority p0 --tag needs-review
tasks update TSK-0001 --notes "Revised approach: use OAuth2"
tasks update TSK-0001 --evidence "file: src/auth/session.py"
tasks update TSK-0001 --dep TSK-0005:blocks     # append a blocking dependency
tasks update TSK-0001 --related TSK-0003         # shorthand for --dep id:relates
```

**Options:**
| Flag | Shorthand | Description |
|------|-----------|-------------|
| `--status` | — | Change status. Any of: `todo`, `in-progress`, `done`, `blocked`, `postponed`, `cancelled`, `review`, `waiting`, `parked`, `deferred`, `backlog`, `abandoned` |
| `--priority` | `-p` | Change priority: `0`–`4` or name (`critical`, `urgent`, `high`, `medium`, `low`, `backlog`) |
| `--tag` | `-t` | **Append** tag(s) to existing tags (repeatable) — does NOT replace |
| `--dep` | — | **Append** dependency in `id:type` format (repeatable). Types: `blocks`, `parent`, `child`, `discovered`, `relates`. |
| `--related` | `-r` | Set related task ID (shorthand for `--dep id:relates`) |
| `--notes` | `-n` | **Append** a note to the existing notes array |
| `--replace-notes` | — | **Replace** all notes (use sparingly) |
| `--evidence` | `-e` | **Append** evidence to the existing evidence array |
| `--replace-evidence` | — | **Replace** all evidence (use sparingly) |
| `--closed` | `-c` | Close the task (move to `closed.yaml`) — can be combined with `--status` |

> **Tip:** Status is the most fluid field in the system. Change it freely as the task moves through your workflow. There is no pipeline — any status to any status is allowed.

#### Notes as an appendable array

The `notes` field is an **array of strings**, not a single text block. Every `--notes` update appends a new entry:

```bash
tasks update TSK-0001 --notes "Started investigation"      # appends entry 1
tasks update TSK-0001 --notes "Found root cause: race condition"  # appends entry 2
tasks show TSK-0001                                       # shows all entries
```

- Use `--notes` to **append** context, blockers, decisions, and progress
- Use `--replace-notes` only when you need to overwrite the entire array (rare)
- This turns `notes` into a **coordination log** — a running history of everything that happened on the task
- Backwards compatibility: existing string-style notes are automatically converted to a single-element array on the next update

#### Evidence as an appendable array

The `evidence` field is also an **array of strings**, not a single text block. Every `--evidence` update appends a new entry:

```bash
tasks update TSK-0001 --evidence "file: src/agent_sommelier/tasks/core.py"   # appends entry 1
tasks update TSK-0001 --evidence "email: sent to finance@example.com"    # appends entry 2
tasks show TSK-0001                                                      # shows all entries
```

- Use `--evidence` to **append** proof, verification clues, or re-check breadcrumbs
- Use `--replace-evidence` only when you need to overwrite the entire array (rare)
- This turns `evidence` into a **verification log** — a running history of what proves the task is real, blocked, or done
- Backwards compatibility: existing string-style evidence is automatically converted to a single-element array on the next update

---

### `tasks close <TSK-NNNN>`

Close a task. Moves it from `tasks.yaml` to `closed.yaml`.

```bash
tasks close TSK-0001
tasks close TSK-0001 --note "All tests passing, deployed to staging"
tasks close TSK-0001 --evidence "file: docs/release-notes.md"
```

- Sets `closed: true`, adds `closed_at` timestamp — does NOT change `status`
- Appends optional `--note` to the notes array (same as `tasks update --notes`)
- Appends optional `--evidence` to the evidence array (same as `tasks update --evidence`)
- Task is removed from `tasks.yaml` and appended to `closed.yaml`
- **Error if already closed**: "Task already closed: TSK-NNNN"
- **Error if not found**: "Task not found: TSK-NNNN"

> **`tasks close` vs `tasks update --closed`:**
> Both archive the task to `closed.yaml`. `tasks close` is the dedicated command.
> `tasks update --closed` is for when you also want to set a different status:
> `tasks update TSK-NNNN --status cancelled --closed`.

---

### `tasks next`

Show the highest-priority todo task(s). Sorted by priority then newest first.

```bash
tasks next                           # Top 1 highest-priority todo
tasks next --take 3                  # Top 3
tasks next --take all                # All todo tasks, sorted by priority
tasks next --tag bug                 # Filter by tag first, then sort
tasks next --priority p1             # Only show p1 priority tasks
tasks next --skip-blocks             # Exclude tasks with unresolved blocks-type deps
tasks next --skip-related            # Alias for --skip-blocks
```

**Options:**
| Flag | Description |
|------|-------------|
| `--take` | Number of tasks to show, or `all` (default: `1`) |
| `--tag` | Filter by tag before sorting |
| `--priority` | Filter by priority (`0`–`4` or name) |
| `--skip-blocks` | Exclude tasks whose `blocks`-type deps are not yet done/closed |
| `--skip-related` | Alias for `--skip-blocks` |

**Priority order:** `p0` > `p1` > `p2` > `p3` > `p4` > unset

---

### `tasks status`

Session overview — in-progress tasks, top priorities, tag frequency, inbox count.

```bash
tasks status              # Human-readable summary
tasks status --json       # JSON for programmatic use
```

**Sample output:**
```
Tasks: 1 in-progress, 3 todo, 1 blocked, 2 postponed

IN PROGRESS
  [TSK-0001] p1    Refactor auth middleware  tags: refactor, auth

BLOCKED
  [TSK-0004] p1    Deploy to production      tags: devops

TOP PRIORITY
  [TSK-0002] p0    Fix login vulnerability   tags: bug, security
  [TSK-0003] p1    Write auth tests          tags: test

TAGS: auth(2), refactor(1), bug(1), security(1), test(1)

Inbox has 3 unprocessed entries. Run `tasks inbox` to view.
```

---

### `tasks history`

Show recently closed tasks (newest first). Default limit: 30.

```bash
tasks history                        # Show 30 most recent (default)
tasks history --limit 10             # Show 10 most recent
tasks history --limit all            # Show everything
tasks history --offset 30            # Skip 30, show next page
tasks history --offset 30 --limit 10 # Page 4: skip 30, show 10
tasks history --json                 # JSON array (respects --limit/--offset)
tasks history --tag deploy           # Filter by tag (AND logic)
tasks history --tag-any bug --tag-any security  # Filter by tag (OR logic)
tasks history --text "auth"          # Full-text search in closed tasks
```

**Options:**
| Flag | Description |
|------|-------------|
| `--limit` | Number of tasks to show, or `all` (default: `30`) |
| `--offset` | Skip N entries from the newest (default: `0`) |
| `--tag` | Filter by tag (repeatable, **AND** logic) |
| `--tag-any` | Filter by tag (repeatable, **OR** logic) |
| `--related` | Filter by related task ID |
| `--text` | Full-text search across titles, notes, tags, and fields |
| `--json` | Output raw JSON instead of a Rich table |

- When total exceeds the shown count, a hint is printed: `Showing 30 of 120. Use --offset 30 to see next page, --limit all to see everything.`
- Use `--json` for machine consumption or filtering without the hint

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
1. Read the inbox: `tasks inbox` (or open `tasks/inbox.md` directly)
2. Process **every** actionable item — format each into a proper task with `tasks add "..." --source inbox --tags ...`
3. Show the user the full list of tasks you're about to create — **get explicit confirmation** before proceeding
4. Only after confirmation, wipe `tasks/inbox.md` completely — leave it empty so it starts fresh for new ideas

The inbox is the input funnel. Tasks are the structured output. **Never leave old scraps behind.**

---

### `tasks --help`

Full help with all commands and options.

```bash
tasks --help
tasks <command> --help         # Help for a specific command
```

---

### `tasks search <text>`

Full-text search across **all tasks** — both active and closed. Case-insensitive, searches titles, notes, evidence, tags, status, priority, source, and IDs.

```bash
tasks search login                      # any mention of "login"
tasks search "dark mode"                # multi-word search
tasks search bug --json                 # JSON output
tasks search login --in title           # only in title field
tasks search "auth" --in notes          # only in notes field
tasks search "proof" --in evidence      # only in evidence field
tasks search "TSK-0001" --in deps       # search dependency IDs
```

**Options:**
| Flag | Description |
|------|-------------|
| `--in` | Scope search to a specific field: `title`, `notes`, `evidence`, `id`, `status`, `priority`, `tags`, `source`, `deps` |
| `--json` | Output raw JSON instead of a Rich table |

> For structured filters use `tasks list` or `tasks history` with `--tag`, `--tag-any`, `--priority`, `--status`, `--related`.

---

### `tasks ready`

Show top-priority **unblocked** todo tasks. Excludes tasks whose `blocks`-type deps aren't done/closed. Sugar over `tasks next --skip-blocks` with better defaults.

```bash
tasks ready                       # Top 5 ready tasks
tasks ready --take all            # All ready tasks
tasks ready --take 3              # Top 3
tasks ready --tag bug             # Filter by tag
tasks ready --json                # JSON output
```

**Options:**
| Flag | Description |
|------|-------------|
| `--take` | Number of tasks, or `all` (default: `5`) |
| `--tag` | Filter by tag |
| `--json` | Output raw JSON |

---

### `tasks blocked`

Show tasks that are currently blocked by unresolved `blocks`-type dependencies. Lists what's blocking each task and the blocker's current status.

```bash
tasks blocked                      # Human-readable
tasks blocked --json               # JSON with blockers info
```

**Sample output:**
```
BLOCKED (2):
  [TSK-0003] p1  Login flow  tags: auth
    ⛔ blocked by TSK-0001 (todo) — "Design login API"
  [TSK-0012] p2  Dark mode   tags: ui
    ⛔ blocked by TSK-0010 (in-progress) — "Pick color scheme"
```

---

### `tasks deps <TSK-NNNN>`

Show the dependency graph for a task (both outgoing and incoming directions).

```bash
tasks deps TSK-0001                # Show all deps for TSK-0001
```

**Sample output:**
```
  [TSK-0001] Refactor auth middleware

  DEPENDS ON:
    TSK-0003 (blocks) [in-progress] — "Write tests for auth"
    TSK-0005 (relates) [done] — "Design API spec"

  DEPENDED BY:
    TSK-0008 (blocks) — "Deploy auth module"
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

### Inbox Processing

The most important multi-step operation. Turn raw notes into structured tasks, then leave the inbox empty. This is the **ingress pipeline** — the gateway where vague ideas become delegatable work.

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
echo. > tasks/inbox.md          # Windows: wipe file to blank
# > tasks/inbox.md              # Unix: wipe file to blank
```

**Ingress Rules:**
- **Take ALL items**, not just the easy ones
- **Ask clarifying questions** when an item is vague, ambiguous, or missing context. The goal is to make tasks as complete as possible — but if the user chooses not to answer or hasn't thought it through yet, create the task anyway with whatever context is available. The user owns the task; the agent is not a gatekeeper.
- **Get confirmation** before creating tasks and wiping the file
- After processing, the inbox must be **empty** — not partially cleaned, not annotated, not "organized." Empty.
- If an item is unclear and the user is unavailable, **leave it in the inbox** and process what you can

> **Default behavior:** Wipe the inbox clean after processing. Only skip wiping if the user explicitly says to keep it (e.g. "don't clear it yet" or "leave the unprocessed ones"). When in doubt, empty it.

### Creating Work

```bash
# Quick task from a known priority
tasks add "Fix the login redirect" --priority p1 --tag bug

# Task from a detailed inbox note
tasks add "Implement rate limiting" --source inbox --priority medium --notes "See inbox.md for requirements"

# Task that depends on something else
tasks add "Deploy to production" --dep TSK-0007:blocks --priority p0

# Task with a soft relationship
tasks add "Investigate edge case" --related TSK-0002
```

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

There are no restrictions on status transitions. You can move a task from any status to any other:

```bash
# Direct skip: backlog → done
tasks update TSK-0010 --status done

# Backwards: review → todo (more work needed)
tasks update TSK-0011 --status todo --notes "Review found issues, needs rework"

# Dead end: in-progress → abandoned
tasks update TSK-0012 --status abandoned --notes "Proof of concept failed"
```

The only special status is `done` — use `tasks close TSK-NNNN` to move it to the archive.
Use `tasks update TSK-NNNN --status done` only when the task is already in the archive
and you are backdating or correcting its recorded status.

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

- **Session start**: Run `tasks status` and `tasks ready` to orient yourself before starting new work.
- **When you discover unblocked work**: Run `tasks ready` to see what can be picked up right now.
- **When something feels stuck**: Run `tasks blocked` to find why — it shows blockers inline.
- **When you discover work**: If you find something that needs doing (a bug, missing test, refactor opportunity), create a task:
  ```bash
  tasks add "Investigate slow query in reports" --priority p2 --tag performance
  ```
- **When you complete work**: Close the task with a closing note:
  ```bash
  tasks close TSK-0005 --note "Found: missing index on user_id. Added migration."
  ```
- **When context shifts**: Update the task — change status, priority, add deps, append context:
  ```bash
  tasks update TSK-0002 --status blocked --notes "Waiting on IT for API key"
  tasks update TSK-0002 --dep TSK-0010:blocks   # record the blocker
  tasks update TSK-0002 --status in-progress      # unblocked
  tasks update TSK-0002 --priority p0 --tag security
  tasks update TSK-0002 --status postponed         # pushed to later
  ```
- **When exploring relationships**: Run `tasks deps TSK-NNNN` to see the full dependency graph (both directions).
- **When priorities need review**: Run `tasks next --take all` to see the full queue sorted by priority.
- **When the inbox has items**: Check `tasks status` — if inbox count > 0, read and promote:
  ```bash
  tasks inbox                             # read ALL raw entries
  tasks add "..." --source inbox          # draft tasks for every actionable item
  # Confirm with user, then wipe tasks/inbox.md completely (empty file)
  ```
- **Before significant changes**: Check `tasks list --status in-progress` to know what's actively being worked on.

### When NOT to use it

- Don't create tasks for things already tracked in the task system (duplicates)
- **Never edit `tasks.yaml` or `closed.yaml` by hand** — always use the CLI. Manual edits will be overwritten.
- **Inbox.md IS the exception** — edit it freely. It is a scratch file, not a managed data store.
- Don't delete tasks — they're permanent history. Close them instead.
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
- The closed archive grows as a project history that can be searched, analyzed, and learned from.

### Why separate from runtime task tools?

- Runtime tools (like `crony`, `bg-jobs`, or any ephemeral scheduler) are **per-session** — they track what's happening right now.
- This task system is **cross-session** and **permanent** — it tracks what needs doing and what was done over the life of the project.
- They complement each other: use runtime tools for immediate execution, use this task system for project-level planning and history.

### Execution strategy

Tasks run **sequentially by default**. The orchestrator may run tasks in parallel when they have no conflicts (touch different files, different subsystems). When conflicts are detected, the orchestrator can either:

1. Run them sequentially in dependency order
2. Isolate them in **separate git worktrees** to allow safe parallel execution

This decision is made at execution time — the task system itself does not enforce ordering. The batch executor or orchestrator analyzes the task set and picks the safest strategy.
