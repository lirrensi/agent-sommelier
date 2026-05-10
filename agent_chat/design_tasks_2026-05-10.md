# Tasks CLI — Design Decisions

> Captured 2026-05-10. Temporary design record. Feeds into implementation plan and canon docs later.

---

## Philosophy

- Everything is a task. A task has id, title, status, timestamp, and tags. Everything else is a tag.
- Inbox is a dump zone — raw text, no format. An agent reads it, extracts tasks, promotes them.
- New tasks sort to the top. Humans skim the top. Agents read everything.
- Grow complexity only when needed. No premature fields.

## Files

Three files total, all repo-local:

```
<repo-root>/
  tasks/
    inbox.md        # raw dump, freeform, append-only by humans
    tasks.yaml      # canonical list + metadata, agent-managed
    done.yaml       # completed history, append-only, never deleted
```

No `.tasks-counter` file — the counter lives inside `tasks.yaml` as metadata.

## Task Statuses

Exactly three. A task has exactly one status:

```
todo → in-progress → done → (moves to done.yaml)
```

No `blocked`, no `cancelled` until they earn their place.

## Data Model

### tasks.yaml structure

```yaml
meta:
  counter: 47
tasks:
  - id: TSK-0047
    title: Fix login button alignment on iPhone SE
    status: todo
    priority: low
    created: 2025-05-10
    tags: [ui, mobile, bug]
    source: inbox
    related: TSK-0039
    notes: >
      User reported misalignment on iPhone SE specifically.

  - id: TSK-0046
    title: SQL injection risk in user search endpoint
    status: todo
    priority: high
    created: 2025-05-10
    tags: [security, backend, bug]
    source: audit
    source_ref: audit-report-2025-05-10.md#finding-3
    notes: >
      User search constructs query with string interpolation.
      Needs parameterized queries + regression test.
```

### Task fields

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | Yes | string | `TSK-NNNN`, zero-padded 4 digits, auto-generated |
| `title` | Yes | string | One line, imperative mood |
| `status` | Yes | enum | `todo` / `in-progress` / `done` |
| `created` | Yes | date | ISO 8601 |
| `priority` | No | enum | `urgent` / `high` / `medium` / `low` — default unset |
| `updated` | No | date | ISO 8601 |
| `completed` | No | date | ISO 8601, only in done.yaml |
| `tags` | No | string[] | Freeform, lowercase, kebab-case |
| `source` | No | enum | `inbox` / `audit` / `test` / `jira` / `agent` / `idea` |
| `source_ref` | No | string | External reference |
| `related` | No | string | Related task ID (e.g. `TSK-0042`). One-way reference, no graph. |
| `notes` | No | string | Freeform context |

Missing optional fields simply don't appear in the YAML. No nulls, no placeholders.

### done.yaml

Same field model. Tasks move here when completed. `completed` field gets added with ISO 8601 timestamp. Append to bottom (oldest-first chronological order).

### Sorting

- `tasks.yaml`: newest at **top** (by `created` descending)
- `done.yaml`: oldest at **top** (append to bottom, chronological)

## ID Generation

- Counter stored in `meta.counter` inside `tasks.yaml`
- On `tasks add`: read YAML → increment counter → write back → use new value
- Format: `TSK-` + zero-padded 4-digit integer (`TSK-0001`, `TSK-0047`)
- No locking. Single-threaded by nature. Worst case: a skipped ID, not data loss.
- IDs are opaque identifiers, never reused.

## CLI Surface

Nine commands total:

| Command | R/W | Purpose |
|---|---|---|
| `tasks init` | W | Bootstrap. Idempotent. Creates `tasks/` dir and three files if missing. |
| `tasks next` | R | Highest-priority todo(s). `--take N\|all` (default 1), `--tag`, `--priority`, `--skip-related` filters. |
| `tasks list` | R | Active tasks. `--status`, `--tag`, `--priority`, `--source` filters. `--json`. |
| `tasks show TSK-NNNN` | R | Full detail of one task. When `related` is set, resolves and shows the target task's title and status inline. |
| `tasks history` | R | Done.yaml entries. `--tag` filter. `--json`. |
| `tasks status` | R | Full overview: top-priority tasks, counts by status, tags, inbox status. `--json` for machine-readable output. |
| `tasks add "title"` | W | Create task at top. Auto-ID from counter. `--tag`, `--priority`, `--source`, `--related`, `--notes`. |
| `tasks update TSK-NNNN` | W | Mutate fields: `--status`, `--tag` (append), `--priority`, `--related`, `--notes`. |
| `tasks done TSK-NNNN` | W | Move to done.yaml. Sets `status: done`, adds `completed` timestamp. `--note`. |
| `tasks inbox` | R | Print inbox.md contents to stdout. |

### Explicitly NOT included

- `tasks clear-inbox` — agent can truncate the file directly. No need for CLI.
- `tasks next-id` — IDs are auto-generated on add. No reason to expose.
- `tasks process-inbox` as an AI extraction command — the inbox dump is for agents to read and react to. The CLI just prints it.

## `tasks init` — Idempotent Bootstrap

First run:
```
tasks init
  Created tasks/
  Created tasks/inbox.md
  Created tasks/tasks.yaml
  Created tasks/done.yaml
```

Subsequent runs:
```
tasks init
  All files already exist. Nothing to do.
```

Initial content:
| File | Content |
|---|---|
| `tasks/inbox.md` | Empty |
| `tasks/tasks.yaml` | `meta:\n  counter: 0\ntasks: []` |
| `tasks/done.yaml` | `[]` |

## `tasks status` — Session Overview

The "where am I?" command. Combines what was `stats` with a priority snapshot:

```bash
tasks status
```

Output:

```
Tasks: 2 in-progress, 14 todo

IN PROGRESS
  [TSK-0042] high  Password reset fails for emails with +  tags: auth, bug

TOP PRIORITY
  [TSK-0014] high    Fix SQL injection in user search         tags: security, backend, bug
  [TSK-0023] high    Hash passwords with argon2               tags: security, auth
  [TSK-0007] medium  Add dark mode toggle                     tags: ui, enhancement

TAGS: auth(3), security(2), ui(4), bug(6), enhancement(2)

Inbox has 3 unprocessed entries. Run `tasks inbox` to view.
```

Shows: in-progress tasks first, then top-priority todos (up to 5), tag frequency, inbox status. `--json` outputs the same data as structured JSON for agent consumption.

## `tasks next` — Take Control

```bash
tasks next                  # 1 task (default)
tasks next --take 3         # top 3
tasks next --take all       # everything todo
tasks next --tag auth       # filtered + take
tasks next --skip-related   # exclude tasks whose `related` target is not done
```

Internally: `filter(status=todo) → sort by priority (urgent>high>medium>low>unset), then created descending → slice(take)`. Same `--tag` and `--priority` filters available. When `--skip-related` is set, tasks with an unresolved `related` field (target exists and is not `done`) are excluded from results. Tasks with no `related` field or whose `related` target is `done` pass through.

## `tasks show` — Inline Related Resolution

When a task has a `related` field, `tasks show` resolves it on the fly:

```
$ tasks show TSK-0042

  [TSK-0042] todo  priority: high
  Fix login button alignment on iPhone SE
  tags: ui, mobile, bug
  source: inbox
  related: TSK-0039 (todo) — "Refactor CSS to use CSS variables"
  created: 2025-05-10
  notes: >
    User reported misalignment on iPhone SE specifically.
```

The related line shows the target task's current status and title. No need to run `tasks show` twice. If the related target doesn't exist (stale reference), show `TSK-XXXX (not found)`.

## Dependencies

- `click` — already in pyproject.toml
- `rich` — already in pyproject.toml  
- `pyyaml` — **new**, add to core dependencies

No `filelock`, no `dateparser`, no other new deps.

## Implementation

- Single file: `src/agentcli_helpers/tasks.py`
- Entry point: `tasks = "agentcli_helpers.tasks:main"` in pyproject.toml
- Follows existing project patterns: Click group, Rich tables, flat functions, plain dicts

## Edge Cases (preliminary)

- No `meta.counter` or malformed YAML → error, suggest `tasks init`
- Empty `tasks.yaml` → treat as `meta: {counter: 0}, tasks: []`
- Update/done non-existent ID → error, exit 1
- Already done task → error, exit 1
- File not found → graceful error message
