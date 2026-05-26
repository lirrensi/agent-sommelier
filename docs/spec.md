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

---

# Agent Sommelier essh — Behavior Specification

## Abstract

`essh` is a portable SSH wrapper CLI providing name abstraction, agent authorization gating, and profile portability. It uses filesystem semaphores for authorization and JSON for profile storage — no daemon, no database.

## Terminology

| Term | Definition |
|---|---|
| profile | A saved host definition: name, user, host, port, key path |
| request lock | A pending authorization file that blocks agent SSH |
| export archive | A `.tar.gz` bundle of profiles, keys, and known_hosts entries |
| TTY | Interactive terminal; detected via `sys.stdin.isatty()` |

## System Model

### Actors

- **User** — Invokes `essh` interactively with a TTY.
- **Agent** — Invokes `essh` programmatically without a TTY (blocked unless authorized).

### Storage

| File | Contents |
|---|---|
| `~/.essh/profiles.json` | Array of profile objects |
| `~/.essh/keys/{name}/` | **Legacy only.** Key directory for profiles created by older versions |
| `~/.ssh/id_ed25519` (or `~/.ssh/id_ed25519_{name}`) | Primary key location for new profiles |
| `~/.essh/requests/{name}.pending` | Authorization request lock file |
| `~/.essh/exports/` | Output directory for export archives |

Keys no longer live in `~/.essh/keys/` by default. New profiles store keys in `~/.ssh/` (the user's standard SSH directory). The `~/.essh/keys/{name}/` directory is only used for **legacy** profiles created by earlier versions and is preserved for backward compatibility.

### Profile Model

```json
{
  "name": "lenny",
  "user": "root",
  "host": "1.2.3.4",
  "port": 2222,
  "key_path": ""
}
```

`key_path` has two valid forms:

| Value | Meaning |
|---|---|
| `""` (empty string) | Use default SSH keys — no `-i` flag is passed; SSH uses its own key discovery (`~/.ssh/id_*` or ssh-agent) |
| An absolute path (e.g. `"~/.ssh/id_ed25519_myhost"` or any external path) | Passed as `-i <key_path>` to the SSH command |

## Behavioral Specification

### 1. `essh add USER@HOST[:PORT]` or `essh add NAME USER@HOST[:PORT]`

NAME is optional. When omitted, the system MUST auto-generate a name.

**Name validation:**

MUST:
1. Only allow characters `[a-z]`, `[0-9]`, `-`, `_`.
2. Reject names containing uppercase letters, spaces, dots, or any other ASCII character.
3. Reject with a clear error message listing the allowed character set.
4. Auto-generated names MUST always pass validation.

**Name auto-generation:**

When NAME is not provided, the system MUST:
1. Pick a random color from a curated list (e.g. `blue`, `red`, `amber`, `jade`, `coral`).
2. Pick a random animal from a curated list (e.g. `whale`, `falcon`, `otter`, `gecko`, `puma`).
3. Combine as `{color}-{animal}` (lowercase, hyphen-separated).
4. If the generated name collides with an existing profile, append a 3-character hex suffix: `{color}-{animal}-{hex}` (e.g. `blue-whale-a3f`).
5. Retry with a new suffix if the suffixed name also collides (up to 10 attempts, then error).

**Profile creation:**

MUST:
1. Parse `USER@HOST[:PORT]` — port defaults to 22.
2. If NAME was provided: validate it; if a profile with NAME already exists, reject with error.

3. **If `-i KEY` is provided:** reference the key in-place (no copy). Push the public key (`KEY.pub`) to the remote host via `ssh-copy-id`. Save profile with `key_path` set to the absolute path of `KEY`.

4. **If `-i` is NOT provided (default keys mode):**
   a. Probe the remote host with `PasswordAuthentication=no, BatchMode=yes, ConnectTimeout=5` to test whether an existing SSH key already works.
   b. **If the probe succeeds** (exit code 0): save profile with `key_path: ""` (empty string — no key generation, no `-i` flag at connect time).
   c. **If the probe fails:** offer to generate a new ed25519 keypair (always proceeds in agent mode / non-TTY).
      i.   Generate key at `~/.ssh/id_ed25519` if that file does not exist, otherwise at `~/.ssh/id_ed25519_{name}`.
      ii.  Generate via `ssh-keygen -t ed25519 -f {key_path} -N "" -C "essh:{name}"`.
      iii. Run the ssh-copy-id equivalent: pipe the public key into `ssh -p {port} {user}@{host} "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"`. The user is prompted for the remote password once.
      iv.  Save profile with `key_path` set to the generated key path.

5. On save, append the profile to `profiles.json` atomically (write to `.tmp` then `os.replace`).

### 2. `essh NAME [COMMAND]`

MUST:

1. Look up NAME in `profiles.json`. If not found, list known names and exit 1.
2. Detect TTY: check `sys.stdin.isatty()`.
3. **If TTY is present (interactive):** connect immediately.
4. **If TTY is absent (agent):**
   a. Create `~/.essh/requests/{name}.pending` with timestamp.
   b. If a pending file already exists and is younger than 30 seconds, exit with "Request already pending" error.
   c. Poll every 500ms: if the file is deleted, proceed with SSH. If 30 seconds elapse, exit with "Authorization timeout" error.
   d. On authorization, delete any stale pending file and proceed with SSH.

5. Optionally add the key to an already-running ssh-agent:
   a. Check if `SSH_AUTH_SOCK` is set in the environment.
   b. If set, run `ssh-add {key_path}` to add the key.
   c. In TTY mode: inherit stdin/stdout/stderr (user can enter a passphrase).
   d. In non-TTY mode: capture output silently (no hang, no passphrase leak).
   e. **Never starts ssh-agent.** This step is purely a convenience for agent forwarding. Failure is non-fatal.
   f. Skip this step entirely if `key_path` is empty (default keys mode).

6. Build SSH args:
   a. `[ssh]` — resolved via `shutil.which("ssh")`, validated to exist.
   b. `[-i key_path]` — **only** if `key_path` is non-empty.
   c. `[-p port]`
   d. `[user@host]`
   e. `[command]` — optional remote command.

7. Execute SSH:
   a. In TTY mode: inherit all stdio streams, forward the exit code.
   b. In non-TTY mode: capture stdout/stderr, echo stdout to stdout, print stderr with dim styling, forward the exit code.

### 3. `essh authorize NAME`

MUST:

1. Look up NAME in `profiles.json`. If not found, exit 1.
2. Check for `~/.essh/requests/{name}.pending`.
3. If no pending request, report "No pending request for {name}" and exit 0.
4. Delete the pending file.
5. Report "{name} authorized."

### 4. `essh list [--json]`

MUST:

1. Read `profiles.json`.
2. Human-readable: table with columns Name, User, Host, Port, Key.
   - Empty `key_path` displays `(default keys)` in dim style.
   - Missing key file displays the path in red with `(missing)` label.
3. `--json`: raw JSON array output.

### 5. `essh rm NAME`

MUST:

1. Remove the profile from `profiles.json`.
2. Delete the key directory `~/.essh/keys/{name}/` **only if it exists** (legacy profiles with keys in the essh store). External/referenced keys are never deleted.
3. Clean any stale pending requests for the name.

### 6. `essh export [OUTPUT]`

MUST:

1. Default OUTPUT to `~/.essh/exports/essh-export-{YYYYMMDD_HHMMSS}.tar.gz`.
2. Create a temp directory.
3. Copy `profiles.json` into the temp directory.
4. For each profile, copy its key(s) into `keys/{name}/` in the archive:
   a. **Empty key_path** (`""`): skip — no key to export.
   b. **Legacy key** in `~/.essh/keys/{name}/`: copy the entire directory.
   c. **External key path**: copy the private key file (as `id_ed25519`) and public key file (as `id_ed25519.pub`, if it exists) into `keys/{name}/`.
5. Extract `known_hosts` entries for all managed hosts.
6. Create a `.tar.gz` archive.
7. Clean up temp directory.
8. Print the output path.

### 7. `essh import ARCHIVE`

MUST:

1. Validate ARCHIVE exists and is a `.tar.gz`.
2. Extract to a temp directory.
3. Read the imported `profiles.json`.
4. For each imported profile:
   a. If name already exists and `--force` is not set, skip with warning.
   b. If `--force`, overwrite the existing profile.
   c. Restore key based on key_path:
      i.  **Empty key_path** (`""`): preserved as-is (default keys mode).
      ii. **Keys found in archive's `keys/{name}/`**: restored to `~/.essh/keys/{name}/` (legacy compat).
      iii.**External key path** (keys not in archive): path preserved as-is, warning printed that the key may not exist on this machine.
5. Merge imported `known_hosts` entries into the local `known_hosts`.
6. Clean up temp directory.
7. Report number of profiles imported/skipped.

## Authorization Model Detail

The authorization gate is a filesystem semaphore:

```
Agent process                    User process
  |                                |
  |-- create {name}.pending        |
  |-- poll every 500ms             |
  |   (file exists? keep waiting)  |
  |                                |-- essh authorize {name}
  |                                |-- delete {name}.pending
  |-- file deleted!                |
  |-- proceed with SSH             |
```

**Expiry:** If `{name}.pending` is older than 30 seconds, the agent SHOULD delete it and exit with a timeout error. This prevents permanent hangs if the user never authorizes.

**Concurrent requests:** The pending file contains a timestamp. If a new agent request arrives while one is already pending (and not expired), it MUST NOT create a second pending file — it exits with "Request already pending."

## Error Handling

| Scenario | Behavior |
|---|---|
| Unknown host name | Error with known names list, exit 1 |
| Add duplicate name | Error, name already exists, exit 1 |
| Invalid name characters | Error, allowed chars: a-z, 0-9, -, _. exit 1 |
| Auto-gen collision (10 attempts) | Error, exit 1 |
| Request already pending | Error with hint, exit 1 |
| Authorization timeout | Error, exit 1 |
| SSH connection fails | Forward SSH exit code |
| `ssh` binary not found on PATH | Error with platform-specific install hint, exit 1 |
| Import name conflict | Warning, skip, unless `--force` |
| Missing export archive | Error, exit 1 |
| `-i KEY` file not found | Error, exit 1 |
| Key generation fails | Error with stderr, exit 1 |

## Security Considerations

`essh` is **not a real security tool**. It provides guardrails + convenience:

- Keys are stored in the user's standard `~/.ssh/` directory (or referenced in-place via `-i`), using system default filesystem permissions.
- Legacy keys in `~/.essh/keys/` continue to work for backward compatibility.
- The authorization gate is a filesystem semaphore — any process with filesystem access can bypass it.
- `known_hosts` uses the system's native `~/.ssh/known_hosts` — no isolation from other SSH users.
- If an attacker has user-level privileges on your machine, `essh` provides no meaningful defense.

The authorization gate exists solely to prevent accidental agent foot-guns — not to defend against adversaries.
