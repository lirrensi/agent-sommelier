# Agent Sommelier Task System — Behavior Specification

## Abstract

The Agent Sommelier task system is a static, file-backed task tracker for in-repo project work. It provides CLI commands for task creation, status management, dependency resolution, queue views, and archival — all without a database or service. The system is config-driven: every status name is user-defined, and a small set of configuration keys maps user columns to system behaviors.

## Introduction

This document specifies the exact behavior of the task system for implementers. It covers task state, status model, configuration, queue logic, dependency resolution, archival, and error handling.

The canonical data lives in `.agents/tasks/tasks.yaml`, `.agents/tasks/closed.yaml`, and `.agents/tasks/inbox.md` within the project repository. The CLI command group is `tasks`.

## Scope

**In scope:** task CRUD, status management, config-driven queues, dependency types, priority system, overview sections, searching, history, inbox intake, claim model.

**Out of scope:** multi-user access, distributed sync, real-time collaboration, web UI, database backends, daemon processes, cross-repo task sharing.

## Terminology

| Term | Definition |
|---|---|
| active task | A task in `tasks.yaml` that is not yet closed |
| closed task | A task that has been archived to `closed.yaml` |
| claimed | A boolean-like string field (`claimed`) — non-empty means locked for active work |
| config | The `meta.config` block in `tasks.yaml` that defines status names and behavior keys |
| status | A string field denoting which column the task sits in |
| column | A synonym for status in the Kanban model; status is the column name |
| dependency | A typed reference from one task to another: `blocks`, `parent`, `child`, `discovered`, `relates` |

## Normative Language

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in RFC 2119.

## System Model

### Actors

- **User** — Invokes `tasks` CLI commands directly.
- **Agent** — Invokes the same CLI commands programmatically. No distinction is made between user and agent — the system is stateless and symmetric.

### Storage

All state is stored in three files under `.agents/tasks/`:

- `tasks.yaml` — Active task list plus metadata (config, counter).
- `closed.yaml` — Append-only archive of closed tasks.
- `inbox.md` — Free-form markdown scratchpad for intake.

### Task Model

Every task MUST have at least:

| Field | Type | Description |
|---|---|---|
| `id` | string | Stable identifier, format `TSK-NNNN` |
| `title` | string | Human-readable description |
| `status` | string | Column name, MUST match a value in `config.statuses` |
| `created` | string | ISO 8601 timestamp |

Optional fields:

| Field | Type | Description |
|---|---|---|
| `priority` | integer (0-4) | 0=highest, 4=lowest |
| `tags` | string list | Freeform labels |
| `source` | string | Origin context |
| `deps` | object list | Typed dependency references |
| `notes` | string list | Appendable work notes |
| `evidence` | string list | Appendable verification trail |
| `claimed` | string | Who is working this (empty = unclaimed) |
| `createdBy` | string | Who/what created the task |

### Config Model

The `meta.config` object in `tasks.yaml` MUST conform to this schema:

```
config:
  statuses:        [list of string]   # All valid status/column names
  default_status:   string             # Status assigned by tasks add
  ready_status:     string             # Status targeted by tasks next / tasks ready
  active_status:    string             # Status set by tasks take / tasks claim
  close_status:     string             # Status set by tasks close before archive
```

**Defaults** — When `meta.config` is absent or incomplete on load, the system MUST inject a full config with these fallback values:

```yaml
config:
  statuses: [todo, in-progress, done, blocked, postponed, cancelled, review, waiting, parked, deferred, backlog, abandoned]
  default_status: todo
  ready_status: todo
  active_status: in-progress
  close_status: done
```

The injected config MUST be persisted on the next save so subsequent reads find it.

## Behavioral Specification

### Configuration

#### 1. Config Loading

On every `load_tasks_yaml()` call, the system MUST:

1. Read `meta` from `tasks.yaml`.
2. Extract `meta.config` if present.
3. If `meta.config` is absent or any required key is missing, merge the default values.
4. Return the merged config alongside the task list.

`tasks init` MUST write a default config into `tasks.yaml` as part of file bootstrap.

#### 2. Validation

The `--status` flag on `tasks update` and `tasks list` MUST validate the input against `config.statuses`. If the status is not in the list, the system MUST reject with a clear error.

### Task Creation (`tasks add`)

1. A new task MUST be assigned `config.default_status` unless `--status` is explicitly provided.
2. The `--status` flag, if provided, MUST be validated against `config.statuses`.
3. The `claimed` field MUST default to empty (unclaimed).

### Task Update (`tasks update`)

1. The `--status` flag MUST be validated against `config.statuses`.
2. Other fields (tags, priority, deps, notes, evidence, claimed, createdBy) have no config dependency.

### Task Close (`tasks close`)

1. Before archiving, the task's status MUST be changed to `config.close_status`.
2. This change MUST be applied to the task record in `tasks.yaml` before it is moved to `closed.yaml`.

### Column Commands

#### `tasks take` / `tasks claim`

1. These are shorthands for `tasks update` with two changes:
   - Status set to `config.active_status`
   - `claimed` set to `"agent"` (overridable via `--claimed`)
2. The commands MUST be idempotent — safe to re-run on an already-active task.

### Queue Commands

#### `tasks next`

1. MUST examine only active (not closed) tasks.
2. MUST filter to tasks where `status == config.ready_status`.
3. MUST exclude tasks with non-empty `claimed`.
4. MUST exclude tasks blocked by unresolved `blocks`-type dependencies.
5. MUST sort by priority (0 highest), then by creation date (newest first).
6. MUST return the top N tasks where N defaults to 1, configurable via `--take`.

#### `tasks ready`

1. Same filtering rules as `tasks next` above.
2. MUST display tasks in the same priority-sorted order but defaults to showing all matching tasks.

### Overview Sections

`tasks overview` MUST partition active tasks into these sections:

| Section | Inclusion rule |
|---|---|
| **now** | Tasks with non-empty `claimed` (regardless of status) |
| **ready** | Tasks with `status == config.ready_status`, not claimed, not blocked by deps |
| **waiting** | Tasks blocked by unresolved `blocks`-type dependencies (any status) |
| **parked** | All remaining active tasks not in now, ready, or waiting |

### Blocked State

A task is considered **blocked** when:

- It has at least one `dep` entry with type `blocks` pointing to another task that is not done and not closed.
- The target task's status is NOT `config.done_status` and the target is not archived.

Note: `blocked` as a status label is just a column name. The system's understanding of "blocked" comes from dependency math, not from the status string.

### Dependency Model

Every dependency is a typed reference:

| Type | Meaning |
|---|---|
| `blocks` | This task blocks the target — the target is blocked until this is done |
| `parent` | This task is a parent of the target |
| `child` | This task is a child of the target |
| `discovered` | The target was discovered during this task |
| `relates` | Generic relationship, no blocking semantics |

Only `blocks` affects queue views (next, ready, overview). Other types are informational.

### Priority System

Priorities are integers 0 through 4:

| Value | Label | Meaning |
|---|---|---|
| 0 | p0 / urgent / critical | Must address immediately |
| 1 | p1 / high | Important, should address soon |
| 2 | p2 / medium | Normal priority |
| 3 | p3 / low | Lower priority |
| 4 | p4 / backlog | When there's time |

Named aliases (`urgent`, `high`, `medium`, `low`, `backlog`, `critical`) MUST resolve to their numeric equivalents. Missing priority SHOULD sort after all explicit priorities.

### Search

Full-text search MUST scan these fields in both active and closed tasks: `id`, `title`, `notes`, `evidence`, `status`, `priority`, `tags`, `source`. Matching is case-insensitive.

Optionally, search MAY be scoped to a single field via `--in <field>`.

## Data and State Model

### tasks.yaml Structure

```yaml
# Comment header
meta:
  counter: 42
  config:
    statuses: [backlog, todo, in-progress, review, done, cancelled]
    default_status: todo
    ready_status: todo
    active_status: in-progress
    close_status: done
tasks:
  - id: TSK-0001
    title: Fix login redirect
    status: in-progress
    priority: 1
    tags: [bug, auth]
    claimed: agent
    created: "2026-05-20T10:30:00"
```

### closed.yaml Structure

```yaml
meta:
  total_closed: 1
tasks:
  - id: TSK-0001
    title: Fix login redirect
    status: done
    priority: 1
    tags: [bug, auth]
    created: "2026-05-20T10:30:00"
    closed: "2026-05-24T15:00:00"
```

### State Transitions

There are no enforced state transitions. A task may move from any status to any other status freely. The only transition with side effects is **close**, which:
1. Sets status to `config.close_status`
2. Appends a `closed` timestamp
3. Moves the task from `tasks.yaml` to `closed.yaml`

## Error Handling and Edge Cases

| Scenario | Behavior |
|---|---|
| Tasks file not found | Error message with `tasks init` hint, exit 1 |
| Missing meta.config | Default config injected silently |
| Unknown status in `--status` | Validation error, list valid statuses, exit 1 |
| Task ID not found | Error message, exit 1 |
| Close already-closed task | Error message, exit 1 |
| Counter overflow (>9999 tasks) | Format `TSK-NNNN` — overflows to 5 digits automatically |
| Empty title on add | Error message, exit 1 |
| Dependency target not found | Record the dep reference anyway, treat as unresolved |
| Cyclic dependencies | Not detected — left as user responsibility |
| Concurrent file edits | Last writer wins — no locking |
| Corrupt YAML | Error message, suggest `tasks init`, exit 1 |

## Security Considerations

All task data is stored as plain-text YAML in the project repository. No authentication, encryption, or access control is provided. The system trusts the user and the agent equally — any process that can read the files can read the tasks.

## References

- **RFC 2119** — Key words for use in RFCs to Indicate Requirement Levels
- **YAML 1.2** — YAML Ain't Markup Language, yaml.org/spec/1.2/
