# Agent Sommelier — Architecture

This document describes the internal implementation of each tool. For product behavior and CLI interface, see `product.md`.

---

## Project Structure

```
agent-sommelier/
├── src/agent_sommelier/
│   ├── __init__.py          # Package init, version
│   ├── notify.py            # Desktop notifications
│   ├── bg.py                # Background job manager
│   ├── crony.py             # Cron job scheduler
│   ├── screenshot.py        # Screen capture
│   ├── essh.py              # Portable SSH wrapper
│   ├── skill_store/          # Skill registry (CLI + MCP)
│   │   ├── __init__.py
│   │   ├── core.py
│   │   ├── cli.py
│   │   └── mcp.py
│   └── tasks/               # In-repo task management package
│       ├── __init__.py
│       ├── cli.py
│       ├── core.py
│       └── render.py
├── skills/                  # Agent skills for self-install
│   ├── bg-jobs/SKILL.md
│   ├── crony/SKILL.md
│   ├── desktop-notifications/SKILL.md
│   ├── memory-bank/SKILL.md
│   ├── screenshot/SKILL.md
│   ├── task-system/SKILL.md
│   ├── skill-store/SKILL.md
│   └── edge-tts/SKILL.md
├── pyproject.toml           # Package metadata
└── README.md                # User-facing docs
```

---

## Common Infrastructure

### CLI Framework
All tools use **Click** (`click >= 8.1.0`) for CLI argument parsing and command routing.

### Output Formatting
**Rich** (`rich >= 13.0.0`) provides table output for list commands.

### Storage Pattern
- **JSON files** for structured data (jobs, metadata)
- **YAML files** for repo-local task state (`.agents/tasks/` — per-task files via pluggable storage backend)
- **Temp directories** for transient data (screenshots, bg job output)
- **Home directory** (`~`) for persistent config (`.crony/`)

---

## Component: notify

### File
`src/agent_sommelier/notify.py`

### Entry Point
```python
notify = "agent_sommelier.notify:main"
```

### Implementation

```
send_notification(title, body) -> bool
    |
    +-- platform.system()
    |       |
    |       +-- "Linux"  --> subprocess.run(["notify-send", title, body])
    |       |
    |       +-- "Darwin" --> subprocess.run(["osascript", "-e", script])
    |       |
    |       +-- "Windows" --> subprocess.run(["powershell", "-Command", ps_script])
    |
    +-- error handling: CalledProcessError, FileNotFoundError
```

### Platform Details

**Windows (PowerShell):**
- Uses `Windows.UI.Notifications.ToastNotificationManager`
- Creates XML toast template with title and body
- Shows toast via `CreateToastNotifier("Agent Sommelier").Show()`

**macOS (AppleScript):**
- Uses `display notification "{body}" with title "{title}"`
- Requires no additional dependencies

**Linux (notify-send):**
- Requires `libnotify` (usually pre-installed)
- Falls back to nothing if unavailable

### Input Handling
- Arguments: `notify "Title" "Body"`
- Pipe: `echo "text" | notify "Title"` — reads from `sys.stdin`
- Explicit pipe: `cat file | notify "Title" -` — body = "-"

---

## Component: bg

### File
`src/agent_sommelier/bg.py`

### Entry Point
```python
bg = "agent_sommelier.bg:main"
```

### Commands
- `bg run "CMD"` — Create background job and return a friendly name
- `bg list` — List all jobs
- `bg status JOB_REF` — Get job metadata by friendly name or UID
- `bg wait JOB_REF` — Wait for terminal state
- `bg wait JOB_REF --match PATTERN` — Wait for output match in stdout/stderr
- `bg wait-all` — Wait for all known jobs
- `bg read JOB_REF` — Read stdout
- `bg logs JOB_REF` — Read stdout + stderr
- `bg rm JOB_REF` — Remove job
- `bg prune` — Remove every job that is not currently running

`bg run` returns immediately after creating the handle. A detached worker process performs launch confirmation and updates the job record later, so strange shell/launcher behavior cannot block the CLI.

### Storage

```
{tempdir}/agentcli_bgjobs/
├── index.json
└── records/
    └── {uid}/
        ├── meta.json    # {"uid", "name", "cmd", "started_at", "status", "pid", "finished_at", "exit_code", "record_issue", "last_event_type", ...}
        ├── stdout.txt   # Captured stdout
        ├── stderr.txt   # Captured stderr
        └── exit_code.txt # Persisted exit code
```

### Runtime Metadata

`meta.json` is the canonical job record and MUST preserve the base fields `uid`, `name`, `cmd`, `started_at`, `status`, and `pid`.

The launch lifecycle MAY temporarily use `launching` or `starting` internally while the detached worker is still starting the target process, but user-facing status should normalize those states to `running` unless failure is proven.

The record MUST also support terminal lifecycle fields:
- `finished_at` — ISO timestamp when the job exits
- `exit_code` — integer exit code when known

The record MAY also track lightweight notable events for list/status surfacing:
- `last_event_type` — `completed`, `failed`, or `matched_output`
- `last_event_at` — ISO timestamp for the most recent notable event
- `matched_pattern` — literal pattern that matched during a wait
- `matched_stream` — `stdout` or `stderr`
- `update_marker` — compact human-readable marker used by `bg list`
- `record_issue` — launch or record problem surfaced to status callers

The record MAY include refreshed runtime inspection fields used by `bg list` and `bg status`, such as:
- `elapsed_seconds`
- `memory_bytes`
- `cpu_percent`

Runtime inspection fields are best-effort snapshots, not guaranteed historical telemetry.

### Job Lifecycle

```
create_job(cmd) -> friendly_name
    |
    +-- generate_uid() --> stable internal UID
    +-- friendly_name_for(cmd) --> <word>-<commandroot>
    |
    +-- mkdir(records/{uid})
    |
    +-- write(meta.json, status="launching", launch_worker_pid=best-effort)
    +-- upsert index.json (name -> uid, uid -> record path)
    |
    +-- spawn detached worker process
    |
    +-- Windows:
    |       |
    |       +-- build_windows_wrapped_command(uid, cmd)
    |       |       |
    |       |       +-- prefer pwsh -> powershell -> cmd.exe
    |       |       +-- write runner.ps1 or runner.cmd
    |       |       +-- if PowerShell exists, write launcher.ps1
    |
    +-- Windows with PowerShell:
    |       |
    |       +-- Start-Process -WindowStyle Hidden -RedirectStandard* -PassThru
    |       +-- worker persists returned PID to meta.json
    |
    +-- Windows fallback without PowerShell:
    |       |
    |       +-- subprocess.Popen(wrapped_cmd, ...)
    |       +-- CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    |       |
    +-- Unix:
    |       |
    |       +-- build_wrapped_command(uid, cmd)
    |       +-- shell wrapper writes exit_code.txt
    |       +-- subprocess.Popen(wrapped_cmd, ...)
    |       +-- start_new_session
    |
    +-- fallback path updates meta.json with proc.pid
    |
    +-- return friendly_name
```

### Status Checking

```
resolve_job_ref(job_ref) -> uid
    |
    +-- name -> uid via index.json
    |
    +-- uid -> record path via index.json
    |
    +-- legacy uid path fallback when needed
    |
    +-- inspect_process(pid) via psutil
            |
            +-- process exists and is_running() --> alive
            |
            +-- missing/zombie/error --> dead / zombie / unknown
```

Process inspection SHOULD be refreshed during `list` and `status` calls. On supported platforms, inspection reads the live process state and enriches the job record with runtime details such as elapsed time, memory usage, and CPU usage. Record problems MUST remain visible even when a live process probe succeeds.

### List Behavior

```
list_jobs() -> list[dict]
    |
    +-- merge index entries with on-disk records
    |
    +-- classify record_state independently from process_state
    +-- refresh process_state live
    |
    +-- sort by started_at descending
    |
    +-- return jobs
```

`bg list` is an operational view, not just a metadata dump. It SHOULD surface live process information such as PID, elapsed runtime, and memory usage in addition to stored job metadata.

`bg list` SHOULD also surface a compact update marker when a job has a notable event such as completion, failure, or matched output.

### Retention / Cleanup

Background job storage is self-pruning.

- Running jobs are never auto-removed.
- Terminal jobs are kept for 1 hour by default.
- If more than 32 terminal jobs exist, the oldest terminal jobs are removed first even if they are younger than 1 hour.
- `scan_jobs_from_disk()` and `load_job_snapshot()` perform cleanup opportunistically during normal CLI calls.

`bg status` MUST return the same enriched metadata model for a single job, including explicit `record_state` and `process_state` fields.

`bg prune` is an aggressive cleanup command. It MUST delete every job whose live process is not active and whose record is not still launching, including stale and broken records, while leaving running jobs untouched.

### Wait Behavior

`bg wait` and `bg wait-all` are polling commands over the existing files; they MUST NOT introduce a daemon or database.

For output-match waits, the implementation SHOULD scan stdout/stderr incrementally until the pattern is found or the job exits.

---

## Component: crony

### File
`src/agent_sommelier/crony.py`

### Entry Point
```python
crony = "agent_sommelier.crony:main"
```

### Commands
- `crony add NAME SCHEDULE "CMD" [--cron]` — Add job (with optional raw cron expression flag)
- `crony list` — List jobs
- `crony rm NAME` — Remove job
- `crony run NAME` — Run immediately
- `crony logs NAME` — View logs

### Job Data Model

Stored jobs preserve the schedule definition (`type`, `schedule` or `interval`, `cron_expr` when applicable) plus execution metadata such as `created_at` and `cwd` (the working directory at add time).

For display and automation, list responses MUST enrich each job with a `next_run` value when derivable:
- One-off jobs use the stored scheduled timestamp
- Recurring jobs compute the next upcoming occurrence from the stored cron expression or interval definition

### Storage

```
~/.crony/
├── jobs.json       # {"job_name": {...}}
├── logs/
│   └── {name}.log
└── scripts/        # Windows .bat wrappers for CWD preservation
    └── {name}.bat
```

### Schedule Parsing

```
parse_schedule(schedule_str) -> dict
    |
    +-- check for "every " or "each " prefix
    |
    +-- if recurring:
    |       |
    |       +-- interval_to_cron(interval) --> cron expression
    |       |
    |       +-- return {"type": "recurring", "interval", "cron_expr"}
    |
    +-- else (one-off):
            |
            +-- dateparser.parse(schedule, settings)
            |
            +-- return {"type": "once", "schedule", "next_run"}
```

When `--cron` is passed on the CLI, `add_job()` bypasses `parse_schedule()` entirely and constructs a recurring job dict directly with the raw cron expression as `interval` and `cron_expr`. This allows users to specify any valid 5-field cron expression instead of being limited to the natural language grammar.

### Interval to Cron Mapping

| Input | Cron Output |
|-------|-------------|
| `1m`, `5m`, `15m`, `30m` | `*/{n} * * * *` |
| `1h`, `2h`, `6h`, `12h` | `0 */{n} * * *` |
| `1d`, `24h` | `0 0 * * *` |
| `1w` | `0 0 * * 0` |
| `monday` - `sunday` | `0 0 * * {0-6}` |
| `weekday` | `0 0 * * 1-5` |
| `weekend` | `0 0 * * 0,6` |

### OS Integration

**Linux/macOS (crontab):**
```
register_job_crontab(job)
    |
    +-- if cwd present: cmd = f"cd {shlex.quote(cwd)} && {cmd}"
    |
    +-- subprocess.run(["crontab", "-l"]) --> current crontab
    |
    +-- remove lines containing "CRONY:{name}"
    |
    +-- append "{cron_expr} {wrapped_cmd}  # CRONY:{name}"
    |
    +-- subprocess.run(["crontab", "-"], input=new_cron)
```

**Windows (Task Scheduler):**
```
register_job_task_scheduler(job)
    |
    +-- if cwd present:
    |       |
    |       +-- write ~/.crony/scripts/{name}.bat (cd /d "CWD" && CMD)
    |       +-- cmd = str(bat_path)
    |
    +-- schtasks /Delete /TN CRONY_{name} /F  # Remove existing
    |
    +-- if recurring:
    |       |
    |       +-- schtasks /Create /TN CRONY_{name} /TR {cmd} /SC DAILY ...
    |
    +-- else (one-off):
            |
            +-- schtasks /Create /TN CRONY_{name} /TR {cmd} /SC ONCE /ST {time} /SD {date}
```

When jobs are removed via `unregister_job()`, the `.bat` wrapper at `~/.crony/scripts/{name}.bat` is also deleted on Windows.

### Sync Mechanism

```
sync_jobs() -> dict
    |
    +-- load_jobs() --> stored jobs
    |
    +-- scan_os_scheduler() --> OS jobs with CRONY markers
    |
    +-- for each OS job not in stored:
    |       |
    |       +-- add to stored (recovery)
    |
    +-- for each stored job not in OS:
    |       |
    |       +-- re-register with OS
    |
    +-- save_jobs(stored)
    |
    +-- return stored
```

### List Rendering

`crony list` is an operational view and MUST show upcoming execution timing when it can be derived.

Before rendering list output:
- one-off jobs reuse stored `next_run`
- recurring jobs compute the next occurrence from the canonical schedule fields

Human-readable list output SHOULD include `Name`, `Type`, `Schedule`, `Next Run`, and `Command`.

JSON list output SHOULD include the same computed `next_run` field so scripts and agents can reason about upcoming execution.

---

## Component: tasks

### Files
`src/agent_sommelier/tasks/`

### Entry Point
```python
tasks = "agent_sommelier.tasks:main"
```

### Module Split
- `storage.py` — `TaskStorage` ABC with `MonolithicYamlStorage` (v1, migration source) and `PerFileYamlStorage` (v2, default)
- `core.py` — CRUD, search, dependency math, storage-aware load/save, migration orchestration
- `render.py` — Rich console/table helpers, priority formatting, and overview section rendering
- `cli.py` — Click group and command wiring

### Commands
- `tasks init` — Bootstrap or repair the task files
- `tasks add` — Create a new task
- `tasks list` — List active tasks
- `tasks next` — Show the next unblocked todo(s)
- `tasks ready` — Show ready work only
- `tasks blocked` — Show blocked work and blockers
- `tasks status` — Session overview for active work
- `tasks overview` — Read-only vertical overview of active work
- `tasks take` — Shorthand to mark a task in-progress (idempotent)
- `tasks show` — Render one task in full
- `tasks update` — Edit task fields and status (supports `--claimed` and `--created-by`)
- `tasks claim` — Alias for `tasks take`
- `tasks close` — Archive a task
- `tasks history` — Browse closed tasks
- `tasks search` — Full-text search across active + closed tasks
- `tasks inbox` — Read the free-form inbox

### Storage Layer

Task persistence is abstracted behind a pluggable `TaskStorage` interface in `storage.py`:

| Component | Backend | File Layout | Status |
|---|---|---|---|
| `MonolithicYamlStorage` | Single `tasks.yaml` + `closed.yaml` | Legacy (v1) | Migration source |
| `PerFileYamlStorage` | One `TSK-xxxx.yaml` per task + `meta.yaml` | Current (v2) | Default |

**Detection:** `detect_storage_version()` checks for `meta.yaml` (v2) or `tasks.yaml` (v1).

**Migration:** `migrate_to_perfile()` reads from monolithic, writes per-task files, backs up old files, and switches the active backend.

### File Layout (current)

```
.agents/tasks/
├── inbox.md           # Free-form scratchpad for ideas and intake
├── meta.yaml           # version, counter, config
├── TSK-0001.yaml       # Active task (closed: false)
├── TSK-0002.yaml       # Active task
└── TSK-0010.yaml       # Closed task (closed: true)
```

`meta.yaml`:
```yaml
version: 2
counter: 42
config:
  statuses: [todo, in-progress, done, blocked, ...]
  default_status: todo
  ready_status: todo
  active_status: in-progress
  close_status: done
```

Each task file is a standalone YAML dict. A closed task stays in the same file with `closed: true` added — there is no separate archive file.

The inbox is deliberately free-form intake. Statuses, priorities, dependencies, notes, evidence, and the optional `claimed` / `createdBy` fields are all persisted in YAML so the repo can carry work across sessions.

### Config-Driven Status Model

Status names are **not hardcoded** in the source. They live in `tasks.yaml` under `meta.config`:

```yaml
meta:
  config:
    statuses: [backlog, todo, in-progress, review, done, cancelled]
    default_status: todo
    ready_status: todo
    active_status: in-progress
    close_status: done
```

The `VALID_STATUSES` constant no longer exists. Five config keys drive all status-dependent behavior:

- **`statuses`** — valid names for validation (used by `--status` on `list` / `update`)
- **`default_status`** — used by `core.add_task()` when no `--status` is given
- **`ready_status`** — queue filter for `tasks next` / `tasks ready`
- **`active_status`** — target status for `tasks take` / `tasks claim`
- **`close_status`** — status assigned before archive by `tasks close`

When `meta.config` is absent, defaults are injected on load (see `spec.md` for the default values). `tasks init` always writes the default config.

### Overview Section Logic (replaces old status-based grouping)

| Section | Inclusion rule |
|---|---|
| **now** | Non-empty `claimed` (any status) |
| **ready** | `status == config.ready_status`, not claimed, not blocked by deps |
| **waiting** | Blocked by unresolved `blocks`-type dependencies (any status) |
| **parked** | Everything else |

The old hardcoded mapping (`in-progress`/`review` → now, `todo` → ready, `blocked`/`waiting` → waiting, `postponed`/`parked`/`deferred`/`backlog` → parked) is gone. Only the config keys + dependency math drive sections.

### How the Config Flows Through the Code

```
load_tasks_yaml()
    └─ extracts meta.config or injects defaults
    └─ returns merged config alongside task list

add_task(config)        ── uses config.default_status
update_task(status)     ── validates against config.statuses
take/claim(cli)         ── uses config.active_status
next/ready(queue)       ── filters by config.ready_status
close(cli)              ── uses config.close_status
build_overview_data()   ── references config.ready_status only
```

### Behavior Notes

- `tasks next` and `tasks ready` are queue views over the `ready_status` column
- typed deps include `blocks`, `parent`, `child`, `discovered`, and `relates`
- `blocks` drives readiness and blocked-state reporting; other types are informational
- `tasks overview` uses overview-specific Rich section rendering for a vertical dashboard-like view without adding interactivity
- `tasks take` and `tasks claim` are shorthands for `tasks update --status active_status --claimed agent`; they accept an optional `--claimed` flag but otherwise perform no additional side effects
- `tasks update` can change status, tags, priority, deps, notes, evidence, closure, and the optional `claimed` / `createdBy` fields in one pass
- `tasks history` and `tasks search` make the archive useful, not just hidden

---

## Component: skill_store

### Module Split
The skill store has been refactored into a package with three layers:

```
src/agent_sommelier/skill_store/
├── __init__.py      # Re-exports main() from cli for backward compat
├── core.py          # Pure data logic (index, search, validation, parsing)
├── cli.py           # Click CLI commands
└── mcp.py           # FastMCP server with 4 tools
```

### core.py — Data Layer

**Purpose:** Pure functions with no Click or Rich console coupling. Used by both CLI and MCP entry points.

**Location:** `src/agent_sommelier/skill_store/core.py`

**Exports:**
- Path resolution: `resolve_store_path()`, `ensure_store_initialized()`
- Index CRUD: `load_index()`, `save_index()`, `find_skill()`
- Parsing: `parse_skill_frontmatter()`, `validate_slug()`
- Search: `rg_available()`, `rg_search_json()`
- File ops: `process_skill_file()`, `git_run()`, `git_auto_commit()`
- Display helpers: `clean_desc()`, `display_skills_list()`, `build_tree_lines()`, `e()`
- Group helpers: `group_exists()`, `get_group()`, `validate_group_slug()`

### cli.py — CLI Layer

**Purpose:** Click command definitions that wire core functions to the terminal.

**Location:** `src/agent_sommelier/skill_store/cli.py`

**Entry Point:**
```python
skill-store = "agent_sommelier.skill_store.cli:main"
```

**Commands:** `init`, `sync`, `create-new`, `load`, `preview`, `list`, `search`, `pin`, `unpin`, `groups`, `status`, `version`, `help`.

### mcp.py — MCP Server

**Purpose:** FastMCP server exposing the skill store as MCP tools for agent integration.

**Location:** `src/agent_sommelier/skill_store/mcp.py`

**Entry Point:**
```python
skill-store-mcp = "agent_sommelier.skill_store.mcp:main"
```

**Tools:**

| Tool | Args | Returns |
|---|---|---|
| `search_skills` | `query: str` | JSON — matched skills with per-file match details |
| `get_skill` | `slug: str` | JSON — metadata, resolved path, directory tree |
| `preview_skill` | `slug: str, lines: int` | JSON — SKILL.md content preview |
| `list_skills` | `page: int, group: str \| None` | JSON — paginated skill listing |

**Transport:** stdio (default). The server is spawned as a subprocess by the agent, communicates over JSON-RPC via stdin/stdout. No HTTP server needed.

**Dependencies:** Requires `mcp >= 1.0.0` (optional `[mcp-srv]` extra).

**Integration in opencode.json:**
```json
"mcpServers": {
    "skill-store": {
        "command": "skill-store-mcp"
    }
}
```

### Storage

The skill registry lives outside the repo at `~/.skill-store/` (configurable via `SKILL_STORE_PATH` env var) and is managed as a local catalog of skill folders and metadata.

### Behavior Notes

- `skill-store` is a lazy loader, not a runtime dependency injection system
- registry operations should keep the on-disk index and installed skill copies in sync
- skills are loaded on demand so the agent only pulls context for what it needs
- the MCP server exposes a read-only subset (search, get, preview, list); admin operations (init, sync, pin, groups) remain CLI-only

---

## Component: screenshot

### File
`src/agent_sommelier/screenshot.py`

### Entry Point
```python
screenshot = "agent_sommelier.screenshot:main"
```

### Commands
- `screenshot [OUTPUT]` — Capture screen

### Auto-Naming

```
auto_name_screenshot() -> Path
    |
    +-- timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    |
    +-- return {tempdir}/agentcli_screenshots/screenshot_{timestamp}.png
```

### Capture Strategy

```
main(output)
    |
    +-- if output provided:
    |       |
    |       +-- resolve to absolute path
    |       |
    |       +-- mkdir parent
    |
    +-- else:
            |
            +-- output_path = auto_name_screenshot()
    |
    +-- if HAS_MSS:
    |       |
    |       +-- screenshot_mss(output_path)
    |
    +-- else:
            |
            +-- screenshot_native(output_path)
```

### mss Capture

```
screenshot_mss(output_path) -> bool
    |
    +-- with mss.mss() as sct:
    |       |
    |       +-- monitors = sct.monitors
    |       |
    |       +-- if len(monitors) > 1:
    |       |       |
    |       |       +-- monitor = monitors[0]  # All monitors combined
    |       |
    |       +-- else:
    |               |
    |               +-- monitor = monitors[1]  # Primary
    |       |
    |       +-- sct_img = sct.grab(monitor)
    |       |
    |       +-- mss.tools.to_png(sct_img.rgb, sct_img.size, output=output_path)
    |
    +-- return True
```

### Native Fallbacks

| Platform | Tool | Command |
|----------|------|---------|
| Linux | gnome-screenshot | `gnome-screenshot -f {path}` |
| Linux | scrot | `scrot {path}` |
| Linux | ImageMagick | `import -window root {path}` |
| Linux | flameshot | `flameshot full -p {path}` |
| macOS | screencapture | `screencapture -x {path}` |
| Windows | PowerShell | `System.Drawing.Bitmap` + `Graphics.CopyFromScreen` |

---

## Component: essh

### File
`src/agent_sommelier/essh.py`

### Entry Point
```python
essh = "agent_sommelier.essh:main"
```

### Commands
- `essh add NAME USER@HOST[:PORT]` — Save a new host profile and push key
- `essh NAME [COMMAND]` — Connect or run command (gated for non-TTY)
- `essh authorize NAME` — Authorize a pending agent request
- `essh list [--json]` — List saved profiles
- `essh rm NAME` — Remove a profile and its keys
- `essh export [OUTPUT]` — Export profiles to portable archive
- `essh import ARCHIVE` — Import profiles from archive
- `essh filter add|rm|list|clear TARGET PATTERN` — Manage command filter rules

### Storage
```
~/.essh/
├── profiles.json       # Array of profile objects
├── keys/               # Legacy only — keys from older versions
│   └── {name}/
│       ├── id_ed25519       # Private key
│       └── id_ed25519.pub   # Public key
├── filters.json          # Global command filter rules
├── requests/
│   └── {name}.pending       # Authorization request lock (contains timestamp)
└── exports/
    └── essh-export-*.tar.gz # Portable export archives

~/.ssh/                     # Primary key location for new profiles
├── id_ed25519              # Default key (or id_ed25519_{name} if default exists)
└── id_ed25519.pub
```

Keys now live in `~/.ssh/` by default (user's standard SSH directory). The `~/.essh/keys/` directory is only populated by legacy profiles created with older versions of `essh`.

### Profile JSON Schema

Two valid forms:

```json
{
  "name": "lenny",
  "user": "root",
  "host": "1.2.3.4",
  "port": 2222,
  "key_path": ""
}
```

```json
{
  "name": "myhost",
  "user": "deploy",
  "host": "example.com",
  "port": 22,
  "key_path": "/home/user/.ssh/id_ed25519_myhost"
}
```

| `key_path` value | Behavior |
|---|---|
| `""` (empty) | "Default keys" mode — no `-i` flag at connect time; SSH uses its own key discovery |
| Absolute path (e.g. `~/.ssh/id_ed25519`) | Passed as `-i <key_path>` to SSH command |

### Implementation Flow

#### `essh add`
```
add(first, second, name=None, identity=None)
    |
    +-- resolve name & target from positional args and -n flag
    |   +-- -n NAME + first=TARGET                     --> name=NAME, target=TARGET
    |   +-- first=NAME + second=TARGET                  --> name=NAME, target=TARGET
    |   +-- first=TARGET (only one arg)                  --> auto-generate name
    |       +-- if TTY: prompt user (accept/suggested)
    |       +-- if non-TTY: use generated name silently
    |
    +-- validate_name(name)
    |       |
    |       +-- allowed: [a-z], [0-9], -, _
    |       +-- reject uppercase, spaces, special chars, dots
    |
    +-- parse_target("root@1.2.3.4:2222") --> user, host, port
    |
    +-- if profile_exists(name): error
    |
    +-- ================= BRANCH: -i provided =================
    |
    +-- if identity:
    |       |
    |       +-- resolve & validate identity path exists
    |       +-- find pubkey (identity.pub or identity + ".pub")
    |       +-- ssh_copy_id(user, host, port, pubkey)
    |       +-- profile.key_path = str(resolved_identity)   # reference in-place
    |
    +-- ================= BRANCH: no -i (default keys mode) ==
    |
    +-- else:
    |       |
    |       +-- _try_ssh_default_keys(user, host, port)
    |       |       |
    |       |       +-- ssh -o PasswordAuthentication=no -o BatchMode=yes
    |       |       |      -o StrictHostKeyChecking=accept-new
    |       |       |      -o ConnectTimeout=5 ... "echo essh_ok"
    |       |       +-- returncode == 0 --> existing key works
    |       |
    |       +-- if probe SUCCEEDS:
    |       |       profile.key_path = ""   # use default SSH keys
    |       |
    |       +-- if probe FAILS:
    |               |
    |               +-- if TTY: confirm "Generate one?"
    |               +-- if non-TTY: auto-proceed
    |               |
    |               +-- if user declines: error with hint to use -i
    |               |
    |               +-- key_path = ~/.ssh/id_ed25519
    |               |   (or ~/.ssh/id_ed25519_{name} if default exists)
    |               |
    |               +-- mkdir ~/.ssh/ (if missing)
    |               +-- ssh-keygen -t ed25519 -f {key_path} -N "" -C "essh:{name}"
    |               +-- ssh_copy_id(user, host, port, key_path.pub)
    |               +-- profile.key_path = str(key_path)
    |
    +-- append profile to profiles.json (atomic write via .tmp)
    +-- on keygen failure: raise ClickException with stderr
```

### Name Generation

Two curated word lists are inlined in the module:

**Colors (~30):** amber, apricot, aqua, azure, black, blue, bronze, brown, charcoal, cobalt, copper, coral, crimson, cyan, emerald, gold, gray, green, indigo, ivory, jade, lavender, lime, magenta, maroon, mint, navy, olive, orange, peach, pink, plum, purple, red, rose, ruby, rust, salmon, sapphire, scarlet, silver, tan, teal, turquoise, violet, white, yellow

**Animals (~60):** alpaca, badger, bat, bear, bee, bison, boar, bobcat, butterfly, camel, cat, cheetah, cobra, cougar, cow, coyote, crab, crane, crow, deer, dingo, dog, dolphin, dove, dragonfly, duck, eagle, eel, elephant, elk, falcon, ferret, finch, fish, flamingo, fox, frog, gazelle, gecko, giraffe, goat, goose, gorilla, hawk, hedgehog, heron, horse, hummingbird, hyena, ibex, iguana, jackal, jaguar, kangaroo, koala, lemur, leopard, lion, lizard, llama, lobster, lynx, magpie, meerkat, mole, mongoose, monkey, moose, moth, mouse, mule, narwhal, newt, octopus, okapi, orangutan, orca, ostrich, otter, owl, panda, panther, parrot, peacock, pelican, penguin, pheasant, pigeon, platypus, pony, porcupine, puma, quail, rabbit, raccoon, ram, rat, raven, reindeer, rhino, robin, salamander, seahorse, seal, shark, sheep, skunk, sloth, snail, snake, sparrow, spider, squid, squirrel, starfish, stork, swallow, swan, swordfish, tiger, toad, tortoise, toucan, trout, turkey, turtle, viper, vulture, wallaby, walrus, wasp, weasel, whale, wolf, wombat, woodpecker, yak, zebra

Name generation uses `random.choice()` from each list. The full 30×140 combination space (4200+ unique names before any suffix is needed) makes collisions extremely rare in practice.

### Name Validation

```
validate_name(name) -> None | raises ClickException
    |
    +-- regex: ^[a-z0-9_-]+$
    +-- rejects uppercase, spaces, dots, special chars
    +-- error message lists allowed character set
```

#### `essh NAME [COMMAND]` (connect/run)
```
connect(name, remote_command=None)
    |
    +-- validate_name(name)
    |
    +-- resolve_profile(name) --> profile or error (list known names if not found)
    |
    +-- resolve key_path from profile
    |       |
    |       +-- key_path_raw = profile.get("key_path") or ""
    |       +-- key_path = Path(key_path_raw) if key_path_raw else None
    |
    +-- detect_tty()
    |       |
    |       +-- sys.stdin.isatty() --> interactive
    |       +-- not isatty() --> agent mode (requires authorization)
    |
    +-- [agent mode] wait_for_authorization(name)
    |       |
    |       +-- check for existing pending file
    |       |       |
    |       |       +-- exists & younger than 30s: error "already pending"
    |       |       +-- exists & older than 30s: cleanup, create new
    |       |       +-- not exists: create new
    |       |
    |       +-- write ~/.essh/requests/{name}.pending (timestamp only)
    |       |
    |       +-- poll loop (500ms intervals, 30s timeout):
    |       |       |
    |       |       +-- file deleted? --> authorized, proceed
    |       |       +-- 30s elapsed? --> timeout error
    |       |
    |
    +-- [optional] ensure_ssh_agent(key_path, is_tty)
    |       |
    |       +-- if SSH_AUTH_SOCK not set: return (nothing to do)
    |       +-- if TTY: subprocess.run(ssh-add, inherit stdio)
    |       |           (user can enter passphrase if needed)
    |       +-- if non-TTY: subprocess.run(ssh-add, capture_output=True)
    |       |               (silent, no hang, no leak)
    |       +-- failure is non-fatal
    |       +-- skipped entirely when key_path is None
    |
    +-- _run_ssh(user, host, port, key_path, command, is_tty)
    |       |
    |       +-- find_ssh()
    |       |       +-- shutil.which("ssh") -- raises ClickException if missing
    |       |
    |       +-- build args:
    |       |       |
    |       |       +-- args = [ssh]
    |       |       +-- if key_path: args += ["-i", str(key_path)]
    |       |       +-- args += ["-p", str(port), f"{user}@{host}"]
    |       |       +-- if command: args.extend(command)
    |       |
    |       +-- if TTY: subprocess.run(args, inherit stdio)
    |       +-- if non-TTY: subprocess.run(args, capture_output=True)
    |       |               echo stdout, print stderr with dim styling
    |       |
    |       +-- return exit code
    |
    +-- sys.exit(exit_code)
```

#### `essh authorize NAME`
```
authorize(name)
    |
    +-- resolve_profile(name) --> profile or error
    |
    +-- check ~/.essh/requests/{name}.pending
    |       |
    |       +-- not exists: "No pending request" (exit 0)
    |       +-- exists: delete file, report "authorized"
```

#### `essh export`
```
export_profiles(output=None)
    |
    +-- output = default or user-provided path
    |
    +-- create temp dir
    |
    +-- copy profiles.json to temp
    |
    +-- copy keys to temp/keys/  (per-profile logic)
    |       |
    |       +-- for each profile:
    |       |       |
    |       |       +-- key_path empty?      --> skip (no key to export)
    |       |       +-- legacy (~/.essh/keys/{name}/ exists)?
    |       |       |       --> copytree(legacy_src, temp/keys/{name}/)
    |       |       +-- external key file?
    |       |               --> copy2(private_key, temp/keys/{name}/id_ed25519)
    |       |               --> copy2(public_key, temp/keys/{name}/id_ed25519.pub)
    |       |
    |
    +-- extract known_hosts entries for managed hosts
    |       |
    |       +-- read ~/.ssh/known_hosts
    |       +-- filter lines matching managed hosts/IPs
    |       +-- write to temp/known_hosts
    |
    +-- create tar.gz archive from temp dir (top-level entries only)
    |
    +-- cleanup temp dir
    |
    +-- print output path
```

#### `essh import`
```
import_(archive, force=False)
    |
    +-- validate archive exists, is .tar.gz
    |
    +-- extract to temp dir
    |
    +-- read temp/profiles.json
    |
    +-- for each profile:
    |       |
    |       +-- if name exists and not force: skip with warning
    |       +-- if name exists and force: remove existing entry
    |       |
    |       +-- resolve key_path and restore keys:
    |       |       |
    |       |       +-- key_path empty ("")?     --> preserve as-is (default keys)
    |       |       +-- archive has keys/{name}/? --> restore to ~/.essh/keys/{name}/
    |       |       |                               (legacy compat, re-root key_path)
    |       |       +-- external path (no keys    --> preserve path, warn if missing
    |       |           in archive)?
    |       |
    |       +-- append to profiles.json
    |
    +-- merge known_hosts entries
    |       |
    |       +-- read temp/known_hosts
    |       +-- append unique lines to ~/.ssh/known_hosts
    |
    +-- cleanup temp dir
    |
    +-- report counts: "Imported: N, Skipped: M"
```

### TTY Detection
```
detect_tty():
    return sys.stdin.isatty()
```
Used to decide whether the connection is interactive (user at terminal, proceeds immediately) or agent-driven (no TTY, requires authorization gate).

### SSH Agent Management

`ensure_ssh_agent(key_path, is_tty)` is purely optional — never required for connectivity since `_run_ssh` passes `-i` directly:

1. Only acts if `SSH_AUTH_SOCK` is already set in the environment (an agent is already running).
2. In TTY mode: runs `ssh-add` inheriting stdin/stdout/stderr — the user can enter a passphrase if needed.
3. In non-TTY mode: runs `ssh-add` with `capture_output=True` — silent, no hanging, no passphrase leak.
4. **Never starts ssh-agent.** Failure is always non-fatal.
5. Skipped entirely when `key_path` is `None` (default keys mode).

### Platform SSH Detection

```
_find_ssh() -> list[str]:
    |
    +-- Unix (Linux/macOS):
    |       |
    |       +-- shutil.which("ssh")
    |       +-- found?  --> return ["ssh"]
    |       +-- missing? --> raise ClickException with install hint
    |                       ("apt install openssh-client" / "brew install openssh")
    |
    +-- Windows:
            |
            +-- shutil.which("ssh.exe") found?  --> return ["ssh"]
            +-- shutil.which("wsl") found?       --> return ["wsl", "ssh"]
            +-- nothing found --> raise ClickException with install hint
                                ("winget install Microsoft.OpenSSH.Beta" / "wsl --install")
```

`_require_tool(name)` is a generic helper used for `ssh-keygen` and `ssh-add` discovery — calls `shutil.which()` and raises `ClickException` with the binary name if not found.

The old functions `copy_identity_file()`, `_set_private_perms()`, `ssh_agent_cmd()`, and `_parse_agent_output()` have been removed — they are no longer relevant to the current key model.

### Authorization Flow Diagram
```
Agent (no TTY)                    User (interactive)
  |                                  |
  | essh lenny "deploy.sh"           |
  |                                  |
  |-- create requests/lenny.pending  |
  |-- poll (500ms)                   |
  |   |                              |
  |   | (waiting...)                 |
  |   |                              |-- essh authorize lenny
  |   |                              |-- delete requests/lenny.pending
  |-- file gone!                     |
  |-- proceed with SSH               |
  |                                  |
  | ssh -i ... root@host deploy.sh   |
```

### Transfer Commands: `essh scp` / `essh rsync`

Both commands share the same argument resolution pattern and authorization flow:

```
scp/rsync command (ctx.args)
    |
    +-- _resolve_transfer_args(raw_args)
    |       |
    |       +-- for each arg:
    |               |
    |               +-- matches ^([a-z0-9_-]+):(.*)$ ?
    |               |       |
    |               |       +-- YES: find_profile(name) → resolve to user@host:path
    |               |       |       |
    |               |       |       +-- not found? → error with known names list
    |               |       |       +-- found? → replace NAME:path with user@host:path
    |               |
    |               +-- NO: pass arg through unchanged
    |
    +-- returns (resolved_args, profiles_dict)
    |
    +-- _authorize_transfer_profiles(profiles, is_tty)
    |       |
    |       +-- if is_tty: return (no gate)
    |       +-- for each profile: create_pending_request() + wait_for_authorization()
    |
    +-- _run_scp(resolved_args, profiles, is_tty)
    |       |
    |       +-- find scp binary (shutil.which)
    |       +-- collect unique -i/-P args from profiles
    |       +-- warn on multiple different keys
    |       +-- cmd = [scp, -i KEY... -P PORT...] + resolved_args
    |       +-- execute (TTY: inherit streams / non-TTY: capture)
    |       +-- return exit code
    |
    +-- _run_rsync(resolved_args, profiles, is_tty)
            |
            +-- find rsync binary (shutil.which)
            +-- build -e "ssh [-i KEY] [-p PORT]" from first profile
            +-- warn on multiple different keys
            +-- cmd = [rsync, -e, "ssh ..."] + resolved_args
            +-- execute (TTY: inherit streams / non-TTY: capture)
            +-- return exit code
```

**Implementation files:** `src/agent_sommelier/essh.py` (added to the existing module — no new files)

**New functions:**
| Function | Purpose |
|---|---|
| `_resolve_transfer_args(args)` | Scans arg list, resolves `NAME:path` → `user@host:path` |
| `_authorize_transfer_profiles(profiles, is_tty)` | Authorizes all involved hosts in agent mode |
| `_run_scp(resolved_args, profiles, is_tty)` | Builds and executes the scp subprocess |
| `_run_rsync(resolved_args, profiles, is_tty)` | Builds and executes the rsync subprocess |

**New Click commands:**
| Command | Purpose |
|---|---|
| `scp` | `essh scp [SCP_OPTIONS...] SOURCE DEST` — uses `context_settings={"ignore_unknown_options": True, "allow_extra_args": True}` |
| `rsync` | `essh rsync [RSYNC_OPTIONS...] SOURCE DEST` — uses `context_settings={"ignore_unknown_options": True, "allow_extra_args": True}` |

Both commands use `@click.pass_context` and read raw args from `ctx.args`, bypassing Click's option parsing so that all scp/rsync flags pass through transparently.

### Command Filters (allow/ask/deny)
Filters use last-match-wins evaluation against wildcard patterns ported from
anomalyco/opencode. Global rules are loaded from `~/.essh/filters.json`,
per-profile rules from the profile's `filters` key. Per-profile overrides global.

```
connect(name, remote_command)
  |-- ... existing auth gate ...
  |-- if remote_command:
  |     |-- load global rules + profile rules (merged, profile last)
  |     |-- evaluate "bash" + command_str against rules
  |     |   |-- deny → print msg, exit 1
  |     |   |-- ask → TTY: click.confirm()
  |     |   |         non-TTY: create_pending_request(name, command)
  |     |   |                   wait_for_authorization(name)
  |     |   |-- allow → pass through
  |     +-- _run_ssh(...)
```

**New functions:**

| Function | Purpose |
|---|---|
| `_wildcard_match(input, pattern)` | Match command against wildcard pattern |
| `_load_global_filters()` | Load `~/.essh/filters.json` → rule list |
| `_load_profile_filters(profile)` | Extract per-profile filter rules |
| `_evaluate_filters(perm, cmd, rules)` | Last-match-wins evaluation → action |
| `_action_message(perm, cmd, rules)` | Extract msg from last-matching rule |

**New Click commands:**

| Command | Purpose |
|---|---|
| `filter add` | `essh filter add TARGET PATTERN [--action deny|ask|allow]` |
| `filter rm` | `essh filter rm TARGET PATTERN` |
| `filter list` | `essh filter list TARGET` |
| `filter clear` | `essh filter clear TARGET` |

**Storage format** — global `~/.essh/filters.json`:
```json
{
  "bash": {
    "rm *": "ask",
    "rm -rf *": "deny",
    "sudo *": "ask"
  }
}
```

Per-profile in `profiles.json`:
```json
{
  "name": "prod-web",
  "filters": {
    "bash": {
      "sudo systemctl restart nginx": "allow",
      "docker *": "ask"
    }
  }
}
```

**Wildcard matching rules (port of anomalyco/opencode):**

| Pattern | Input | Match? |
|---|---|---|
| `rm *` | `rm -rf /` | ✅ |
| `rm *` | `rm` | ✅ (trailing ` *` makes args optional) |
| `rm *` | `rmdir foo` | ❌ |
| `git *` | `git status` | ✅ |
| `git *` | `git` | ✅ |
| `rm -rf *` | `rm -rf /` | ✅ |
| `sudo systemctl restart nginx` | `sudo systemctl restart nginx` | ✅ |

---

## Component: tmx

### File
`src/agent_sommelier/tmx.py`

### Entry Point
```python
tmx = "agent_sommelier.tmx:cli"
```

### Commands
- `tmx install` — Ensure tmux/psmux is available
- `tmx create NAME [CMD]` — Create a new detached session
- `tmx rm NAME` — Kill a session
- `tmx sk NAME "CMD"` — Send keys (fire and forget)
- `tmx r NAME` — Read full scrollback
- `tmx run NAME "CMD" [--timeout N]` — Send + wait + read
- `tmx list [--json]` — List all sessions
- `tmx manager` — Interactive session picker (human TUI)

### Detection Logic

```
_find_tmux() -> str
    |
    +-- platform.system() == "Windows":
    |       |
    |       +-- shutil.which("psmux") --> "psmux"
    |       +-- shutil.which("tmux")/("tmux.exe") --> "tmux"
    |       +-- not found --> raise ClickException("Run 'tmx install'")
    |
    +-- platform.system() != "Windows":
            |
            +-- shutil.which("tmux") --> "tmux"
            +-- not found --> raise ClickException("Run 'tmx install'")
```

### Command Implementation

```
create(name, cmd=None)
    |
    +-- _has_session(name)? --> error "already exists"
    +-- send_keys: new-session -d -s {name} -x 120 -y 40
    +-- set-option history-limit 10000
    +-- if cmd: send-keys + Enter
    +-- print "Created session: {name}"

rm(name)
    +-- _ensure_session(name)
    +-- kill-session -t {name}
    +-- print "Killed session: {name}"

sk(name, cmd_str)
    +-- _ensure_session(name)
    +-- send-keys -l -- {cmd_str}
    +-- send-keys Enter

r(name)
    +-- _ensure_session(name)
    +-- capture-pane -p -J -t {name} -S -50000
    +-- print captured stdout

run(name, cmd_str, timeout=5)
    +-- sk (send-keys logic)
    +-- time.sleep(timeout)
    +-- r (capture logic)

list(json=False)
    +-- list-sessions -F format string
    +-- if no sessions: print "No sessions."
    +-- if --json: json.dumps(list of dicts)
    +-- else: Rich Table (Name, Windows, Status)

install()
    +-- if already installed: print "✓ tmux found"
    +-- Windows: try winget -> scoop -> choco -> cargo -> manual URL
    +-- macOS: suggest/run brew install tmux
    +-- Linux: detect package manager, print install command

manager()
    +-- if not isatty(): error "needs interactive terminal"
    +-- loop:
    |       +-- _get_session_list() -> list of sessions
    |       +-- _render_picker(sessions, cursor)
    |       +-- _getch() -> key
    |       +-- UP/DOWN: adjust cursor (0 = "+ new session")
    |       +-- Enter + cursor==0: prompt name -> create -> attach (blocking)
    |       +-- Enter + cursor>0: attach to selected (blocking)
    |       +-- k + cursor>0: kill selected, refresh
    |       +-- n: prompt name -> create (no attach), refresh
    |       +-- q: exit loop
```

### Platform Install Behavior

| Platform | Attempt Order | Auto? |
|----------|---------------|-------|
| Windows | winget, scoop, choco, cargo | Yes (tries each) |
| macOS | brew | Yes (if brew exists) |
| Linux | apt, dnf, pacman, apk | No (prompt only) |

### Manager Input Handling

```
_getch() -> str
    |
    +-- Windows (msvcrt):
    |       |
    |       +-- msvcrt.getch() reads one byte
    |       +-- \xe0 prefix = arrow key, read second byte
    |       +-- H/P/M/K mapped to UP/DOWN/RIGHT/LEFT
    |       +-- Ctrl+C (\x03) raises KeyboardInterrupt
    |
    +-- Unix (termios):
            |
            +-- tty.setraw(fd), read(1)
            +-- \x1b prefix = escape sequence, read(2)
            +-- [A/[B/[C/[D mapped to UP/DOWN/RIGHT/LEFT
            +-- Ctrl+C (\x03) raises KeyboardInterrupt
```

### Edge Cases

- No tmux binary: deferred to install command with platform guidance
- Session collision: clear error on create
- Session missing: clear error on send/read/kill
- Empty list: graceful "No sessions." message
- Manager invoked without TTY: error message, exit 1

---

## Component: amun

### File
`src/agent_sommelier/amun.py`

### Entry Point
```python
amun = "agent_sommelier.amun:main"
```

### Commands
- `amun init` — Write default config to `~/.amun/config.toml`
- `amun ask "QUESTION"` — Send question to configured LLM and stream response

### Config Structure (`~/.amun/config.toml`)

```toml
endpoint = "https://api.openai.com/v1/chat/completions"
model = "o3-4h"
api_key = "$AMUN_API_KEY"

[body]
reasoning_effort = "high"
```

- Parsed with `tomllib` (stdlib in Python ≥ 3.11) with a manual fallback for Python 3.10
- `$VAR` values in config are resolved from the environment
- `[body]` section is merged into the POST request body as extra JSON fields

### Implementation Flow

```
amun ask "QUESTION" --system "..." --model X --no-stream
    |
    +-- load_config()
    |       |
    |       +-- read ~/.amun/config.toml
    |       +-- tomllib (3.11+) → fallback parser (3.10)
    |       +-- resolve $ENV_VAR references
    |       +-- return {"endpoint", "model", "api_key", "body"}
    |
    +-- build request body:
    |       |
    |       +-- { model, messages: [system, user], stream: bool }
    |       +-- merge config["body"] (reasoning_effort, …)
    |       +-- CLI flags (--model, --no-stream) take precedence
    |
    +-- _make_request(endpoint, api_key, body_bytes, timeout)
    |       |
    |       +-- urllib.request.Request(POST, headers, data)
    |       +-- urllib.request.urlopen(timeout=120)
    |       |
    |       +-- HTTPError  → ClickException("API error {status}: {body}")
    |       +-- URLError   → ClickException("Connection error: {reason}")
    |       +-- OSError    → ClickException("Request failed: {msg}")
    |
    +-- [streaming (default)]:
    |       |
    |       +-- _handle_streaming_response(response, console)
    |               |
    |               +-- parse_sse_lines() → generator of JSON dicts
    |               |       |
    |               |       +-- read 4096-byte chunks
    |               |       +-- buffer & split on "\n\n"
    |               |       +-- extract "data: {...}" lines
    |               |       +-- "[DONE]" → stop
    |               |       +-- yield json.loads(payload)
    |               |
    |               +-- for each parsed event:
    |               |       |
    |               |       +-- delta.reasoning / delta.reasoning_content?
    |               |       |       → write dim yellow to stdout, flush
    |               |       +-- delta.content?
    |               |               → write to stdout, flush
    |               |
    |               +-- return full content string
    |
    +-- [non-streaming (--no-stream)]:
            |
            +-- _handle_non_streaming_response(response, console)
                    |
                    +-- json.loads(response body)
                    +-- choices[0].message.reasoning?
                    |       → console.print dim yellow
                    +-- choices[0].message.content?
                            → console.print(rich.markdown.Markdown(...))
```

### SSE Parser

```python
def parse_sse_lines(response):
    """Yield parsed JSON objects from an SSE stream."""
    buffer = ""
    for chunk in iter(lambda: response.read(4096), b""):
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for line in block.split("\n"):
                if line.startswith("data: "):
                    data = line[6:].strip()
                    if data == "[DONE]":
                        return
                    if data:
                        yield json.loads(data)
```

### Dependencies
- **stdlib only:** `urllib.request`, `urllib.error`, `json`, `os`, `sys`, `pathlib`, and either `tomllib` (≥ 3.11) or a built-in fallback
- **click** — CLI framework (existing core dep)
- **rich** — Markdown rendering, styling (existing core dep)

No new external dependencies are introduced.

---

## Dependencies Graph

```
click >= 8.1.0          <-- All tools (CLI framework)
    |
rich >= 13.0.0          <-- bg, crony, essh, skill-store (table output)
    |
dateparser >= 1.2.0    <-- crony (optional, natural language)
    |
schedule >= 1.2.0      <-- crony (optional, not currently used)
    |
mss >= 9.0.0           <-- screenshot (optional, primary capture)
    |
pillow >= 10.0.0       <-- screenshot (optional, mss dependency)
    |
mcp >= 1.0.0           <-- skill-store (optional [mcp-srv], MCP runtime)
```

---

## Error Handling Patterns

1. **Tool not found** — `FileNotFoundError` → user-friendly message → exit 1
2. **Subprocess failure** — `CalledProcessError` → capture stderr → exit 1
3. **Invalid input** — ValueError/ClickUsageError → message → exit 1
4. **Missing optional dep** — ImportError at runtime → install hint → exit 1

---

## Platform Detection

All tools use `platform.system()` to detect OS:
- `"Windows"` — Windows
- `"Darwin"` — macOS
- `"Linux"` — Linux

For process detection:
- Windows: `sys.platform == "win32"`
- Unix: `sys.platform != "win32"`
