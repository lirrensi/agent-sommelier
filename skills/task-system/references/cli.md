# CLI Reference — task-system

All commands run as `tasks <command>` from the project root.

---

## `tasks init`

Bootstrap the task system. Creates `.agents/tasks/`, `inbox.md`, `tasks.yaml`, and `closed.yaml`.

- **Idempotent** — safe to run multiple times. Existing data is never touched.
- Run this first in any new project that should use the task system.

```bash
tasks init
# → Created .agents/tasks/inbox.md
# → Created .agents/tasks/tasks.yaml
# → Created .agents/tasks/closed.yaml
# → Task system initialized.
```

---

## `tasks add "<title>"`

Create a new task. ID is auto-generated.

```bash
tasks add "Refactor auth middleware"
tasks add "Fix login bug" --tag bug --tag urgent --priority p0 --source audit
tasks add "Refactor auth" --claimed rx --created-by rx --tag refactor --priority p1
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
| `--claimed` | — | Who is actively working this task. Non-empty locks it from `next`/`ready` queues |
| `--created-by` | — | Who or what created this task (e.g. `rx`, `agent`, `gmail-import`) |
| `--dep` | — | Dependency in `id:type` format (e.g. `TSK-0042:blocks`). Types: `blocks`, `parent`, `child`, `discovered`, `relates`. Repeatable. |
| `--related` | `-r` | Related task ID (shorthand for `--dep id:relates`) |
| `--notes` | `-n` | Freeform notes (stored as array) |
| `--evidence` | `-e` | Verification evidence / proof (stored as array) |

**Notes:**
- Tags are normalized to lowercase, whitespace becomes hyphens
- New tasks are prepended (newest at top)
- Default source is `agent` if not specified
- Use `evidence` for quick verification breadcrumbs, final proof, or later re-checking

**Tagging strategy — think multi-dimensional:**
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
```

> **Rule of thumb:** If a task only has one tag, you're probably under-describing it. Add at least a **type** tag and an **area** tag.

---

## `tasks list`

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
| `--text` | Full-text search in title and notes |
| `--related` | Filter by dependency target |
| `--json` | JSON output |
| `--reverse` | Reverse sort (oldest first) |

---

## `tasks show TSK-NNNN`

Show full task details.

```bash
tasks show TSK-0001
tasks show TSK-0001 --json
```

---

## `tasks update TSK-NNNN [options]`

Update a task's fields. Only provided fields are changed.

```bash
tasks update TSK-0001 --status in-progress
tasks update TSK-0001 --priority p0 --notes "Client escalation"
tasks update TSK-0001 --tag bug --tag security   # replaces all tags
```

**Options:** Same as `add`: `--status`, `--priority`, `--tag`, `--notes`, `--evidence`, `--claimed`, `--dep`, `--related` (plus `--clear-tags` to reset).

---

## `tasks close TSK-NNNN`

Move a task from `tasks.yaml` to `closed.yaml`. Preserves all statuses.

```bash
tasks close TSK-0001 --note "Deployed to prod, monitors green"
```

---

## `tasks take TSK-NNNN` / `tasks claim TSK-NNNN`

Shorthand for `tasks update TSK-NNNN --status in-progress`. With `--claimed <name>`, also sets the claimed field.

```bash
tasks take TSK-0001
tasks take TSK-0001 --claimed rx
tasks claim TSK-0001 --claimed rx
```

---

## `tasks history`

List all closed tasks (from `closed.yaml`). Newest first.

```bash
tasks history
tasks history --json
tasks history --limit 5
```

---

## `tasks inbox`

Read the inbox file content.

---

## `tasks ready`

Show tasks that are unblocked and ready to work on (excludes blocked, claimed, and non-todo statuses).

---

## `tasks blocked`

Show blocked tasks and why.

---

## `tasks next`

Show full priority queue. Use `--take N` to pick the top N, `--take all` for everything.

```bash
tasks next
tasks next --take 3
tasks next --take all
tasks next --take all --json
```

---

## `tasks status`

Session overview: task counts by status, inbox line count, and ready-to-work summary.

```bash
tasks status
tasks status --json
```

---

## `tasks overview`

Project-wide summary: counts, top priorities, tags, inbox, dependency load.

```bash
tasks overview
tasks overview --json
```

---

## `tasks search <query>`

Full-text search across active tasks.

```bash
tasks search "login bug"
tasks search --json "api"
```

---

## `tasks deps TSK-NNNN`

Show the full dependency graph for a task (both directions).

```bash
tasks deps TSK-0001
```

---

## `tasks serve`

Start the web UI dashboard.

```bash
tasks serve
tasks serve --port 8080
```
