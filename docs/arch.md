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
│   ├── crony/               # Cron job scheduler (package)
│   ├── screenshot.py        # Screen capture
│   ├── essh.py              # Portable SSH wrapper
│   ├── artify.py            # HTML artifact preview + live-reload
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
- `bg wait JOB_REF` — Wait for terminal state. `--timeout N` (float seconds, `0` disables) overrides the default 120s non-TTY cap.
- `bg wait JOB_REF --match PATTERN` — Wait for output match in stdout/stderr. `--timeout N` available.
- `bg wait-all` — Wait for all known jobs. `--timeout N` available.
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

All wait commands apply an agent-protection timeout: 120 seconds in non-TTY mode (any of stdin/stdout is not a TTY), infinite in TTY (interactive) mode. The user can override with `--timeout N` (float seconds, `N >= 0`); `0` disables the cap entirely. The default is chosen by `is_agent_invocation()`, which detects the common case of an LLM agent invoking the CLI as a subprocess. When the cap fires, the wait loop exits, the underlying job is left running, and an informative message is written to stderr (not stdout). Exit code is `0` on timeout; the message is the contract — agents detect a timed-out wait by reading stderr rather than by the exit code. The default cap is centralized in the `BG_WAIT_AGENT_TIMEOUT_SECONDS` constant in `src/agent_sommelier/bg.py`.

---

## Component: crony

### Files
`src/agent_sommelier/crony/` (package)
- `cli.py` — Click CLI entry point and schedule parsing
- `daemon.py` — Cross-platform scheduler daemon (pure logic, no Click/Rich)
- `__init__.py` — Package marker

### Entry Point
```python
crony = "agent_sommelier.crony.cli:main"
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
├── daemon.lock     # {"pid": N, "started_at": "iso8601", "token": "hex"}
├── logs/
│   └── {name}.log  # Captured stdout/stderr with header/footer
└── jobs.json.tmp   # Temporary file for atomic writes
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

### Scheduler Daemon

The crony daemon is a cross-platform Python process that replaces all OS-level scheduling (crontab, schtasks). It uses `croniter` to calculate exact next-run times and spawns jobs with full log capture.

**Key properties:**
- Auto-starts on first `crony add` — no manual "install daemon" step
- Auto-exits when no jobs remain in `jobs.json`
- Registers itself for login auto-start without admin privileges
- Lockfile with token-based PID verification prevents stale-PID races

#### Lockfile

```
~/.crony/daemon.lock   → {"pid": 1234, "started_at": "2026-06-12T10:00:00+00:00", "token": "hex"}
```

`is_daemon_alive()` validates three conditions via `psutil`:
1. PID from lockfile is a running process
2. Process name contains "crony" or "python"
3. Token from lockfile appears in the process command line

If any check fails the lockfile is removed and `is_daemon_alive()` returns `False`.

#### Auto-Start Registration

| Platform | Mechanism |
|----------|-----------|
| Windows | `schtasks /Create /TN CRONY_DAEMON /TR "crony daemon run-loop" /SC ONLOGON /F` |
| Linux | `~/.config/systemd/user/crony-daemon.service` (Type=simple, Restart=on-failure) |
| macOS | `~/Library/LaunchAgents/com.crony.daemon.plist` (RunAtLoad + KeepAlive) |

`start_daemon()` registers on first launch; `stop_daemon()` unregisters.

#### Scheduler Loop

```
run_daemon_loop(token):
    while True:
        jobs = load_jobs()
        if not jobs: break
        now = datetime.now()
        due = [j for j in jobs if croniter.get_next(j) <= now]
        for job in due:
            spawn_job(job)
            if job.type == "once": mark_completed(job)
        save_jobs(jobs)
        sleep_seconds = min(earliest_next - now, 60)
        sleep(sleep_seconds)
```

Sleep is capped at 60 seconds so freshly-added jobs are picked up promptly. On wake, `jobs.json` is reloaded from disk to pick up changes from `crony add`/`crony rm`.

#### Job Spawning

`spawn_job(job)` uses `subprocess.Popen` with:
- `cwd=job["cwd"]` — preserves the working directory at `add` time
- `shell=True`
- Windows: `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` (detached)
- Unix: `start_new_session=True` (detached)
- stdout/stderr piped to `~/.crony/logs/{name}.log` (append mode)
- Header: `--- crony run: {timestamp} (PID {pid}) ---`
- Footer: `--- exit: {code} at {timestamp} ---`

#### OS Scheduler Stubs

`register_job()`, `register_job_crontab()`, and `register_job_at()` are no-ops — the daemon reads `jobs.json` directly and is the sole executor on all platforms.

`unregister_job()` still cleans up legacy crontab/schtasks entries (best-effort) for migration.

#### Migration (`crony list --sync`)

```
sync_jobs() -> dict
    |
    +-- load_jobs() --> stored jobs
    |
    +-- scan_os_scheduler() --> legacy OS jobs with CRONY markers
    |
    +-- for each OS job not in stored:
    |       |
    |       +-- add to stored (recovery)
    |
    +-- save_jobs(stored)
    |
    +-- return stored
```

`scan_os_scheduler()` still detects legacy crontab `# CRONY:name` markers and Windows `schtasks CRONY_name` tasks so they can be imported into daemon-managed `jobs.json`. No new OS registrations are created.

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

## Component: artify

### File
`src/agent_sommelier/artify.py`

### Entry Point
```python
artify = "agent_sommelier.artify:main"
```

### Commands
- `artify open FILE` — Open FILE in the default browser via `file://` (no server, no reload)
- `artify serve FILE [--webview]` — Serve FILE on a random local port with polling-based live-reload, open in browser tab (or in a chromeless app-mode window with `--webview`)
- `artify list` — List every registered `serve` instance with live liveness detection
- `artify kill PORT` — Terminate the `serve` instance on PORT and clean up its registry entry
- `artify restart PORT` — Kill the instance on PORT (if alive) and re-serve the same file on a new, free port
- `artify snapshot PORT [--timeout N]` — Read the current form state of the page served on PORT (via a command/response protocol) and print it as JSON

### Implementation Flow

```
main()  @click.group
    |
    +-- open(file)
    |       |
    |       +-- url = f"file:///{file.resolve().as_posix()}"
    |       +-- webbrowser.open_new_tab(url)
    |       +-- click.echo(...)
    |
    +-- serve(file, webview)
    |       |
    |       +-- server, port, state = start_server(file)
    |       |       |
    |       |       +-- state = InstanceState(file, snapshot_timeout=...)
    |       |       +-- handler = functools.partial(ArtifyHandler,
    |       |       |                                  state=state,
    |       |       |                                  file_path=file)
    |       |       +-- ReusableTCPServer(("127.0.0.1", 0), handler)
    |       |       +-- port = server.server_address[1]
    |       |
    |       +-- write_registry_entry(port, os.getpid(), file)
    |       +-- open_with_browser(url, webview)
    |       |       |
    |       |       +-- if webview and find_app_browser() returns launcher:
    |       |       |       +-- subprocess.Popen(launcher, detached)
    |       |       +-- else: webbrowser.open_new_tab(url)
    |       |
    |       +-- watch_and_serve(file, server, state)
    |       |       +-- watchdog.Observer scheduled on file.parent
    |       |       +-- Observer.start() in background thread
    |       |       +-- server.serve_forever() on main thread
    |       |       +-- on KeyboardInterrupt: observer.stop(), observer.join()
    |       |
    |       +-- finally: server.shutdown(), server.server_close(),
    |                  remove_registry_entry(port)
    |
    +-- list_cmd()
    |       +-- entries = read_registry()  # augments each with alive bool
    |       +-- render Rich table
    |
    +-- kill(port)
    |       +-- entry = _read_registry_entry(port)
    |       +-- was_alive = is_pid_alive(entry.pid)
    |       +-- if was_alive: _terminate_pid(pid)  # SIGTERM -> 2s -> SIGKILL
    |       +-- remove_registry_entry(port)
    |       +-- print success or "already not running" message
    |
    +-- restart(port)
    |       +-- entry = _read_registry_entry(port)
    |       +-- if file missing on disk: error, exit 1
    |       +-- best-effort _terminate_pid(entry.pid)
    |       +-- remove_registry_entry(port)
    |       +-- subprocess.Popen([sys.executable, "-m",
    |       |                    "agent_sommelier.artify", "serve", str(file)],
    |       |                    creationflags=... or start_new_session=True,
    |       |                    stdin/stdout/stderr=DEVNULL)
    |       +-- time.sleep(0.3)  # let the new instance write its registry entry
    |
    +-- snapshot(port, timeout)
            +-- urlopen(POST http://127.0.0.1:{port}/__snapshot_request,
            |            timeout=timeout + 5)
            +-- on 200: print payload as JSON
            +-- on 408: "Page did not respond within {timeout}s", exit 1
            +-- on URLError: "No artify instance on port {port}", exit 1
```

### Server Architecture

The `serve` command runs an in-process HTTP server bound to `127.0.0.1` on a random free port (passed as `0` to `TCPServer`, the OS picks). The server is local-only — it does not bind to `0.0.0.0` and is not reachable from other machines.

#### InstanceState (per-server state)

`InstanceState` is the single per-instance state object passed to every request via the handler factory. It carries three independent concerns, each protected by its own `threading.Lock` so they do not contend with each other:

| Field | Lock | Purpose |
|---|---|---|
| `_mtime: float` (legacy `get()` / `update_from_disk()` / `bump()`) | `_mtime_lock` | File mtime cache for live-reload (same semantics as the old `ReloadState`) |
| `pending_commands: list[dict]` | `commands_lock` | Command queue: `__snapshot_request` enqueues here; the page's `__commands` poll drains it |
| `pending_snapshots: dict[str, threading.Event]` + `snapshot_results: dict[str, dict \| None]` | `results_lock` | Snapshot wait/result registry: per-sid `Event` for the blocking handler to wait on, plus a `None`-then-dict slot for the result payload |

The mtime API is kept for backward compatibility (the old `ReloadState` class is a module-level alias for `InstanceState`). The command queue and snapshot registry are the new pieces used by the snapshot protocol — see "Snapshot Mechanism" below.

| Method | Effect |
|---|---|
| `get() -> float` | Returns the cached mtime (legacy) |
| `update_from_disk()` | Re-reads the file's mtime via `stat()` and updates the cache (called by the handler on every poll) |
| `bump()` | Sets the cached mtime to `time.time()` (called by the watchdog event handler on a debounced file modification) |
| `enqueue_command(cmd)` | Append a command to the page-bound queue (under `commands_lock`) |
| `drain_commands() -> list[dict]` | Atomically return and clear the queue (under `commands_lock`) |
| `register_snapshot(sid) -> Event` | Allocate a `(event, result=None)` slot for sid (under `results_lock`); returns the event the handler will wait on |
| `set_snapshot_result(sid, data) -> bool` | Record the page's response for sid and signal the waiter; returns False if sid is unknown (caller responds 404) |
| `wait_for_snapshot(sid, timeout=None) -> dict \| None` | Block on sid's event (or `InstanceState.snapshot_timeout` if `timeout` is None); on signal, return the result; on timeout, drop the slot and return None |

#### ArtifyHandler

`http.server.BaseHTTPRequestHandler` subclass. Bound per-request with `functools.partial(ArtifyHandler, state=state, file_path=file_path)`.

| Route | Method | Response |
|---|---|---|
| `/` or `/index.html` | GET | 200 `text/html`; reads file from disk, injects reload script (idempotent), serves body |
| `/__reload.js` | GET | 200 `application/javascript`; returns the small `RELOAD_JS` polling script |
| `/__reload_check` | GET | 200 `text/plain`; returns the current cached mtime as a float string |
| `/__commands` | GET | 200 `application/json`; drains and returns the pending command queue as a JSON array |
| `/__snapshot_request` | POST | 200 `application/json` with the page's response; 408 on server-side timeout; blocks the handler thread until the page replies or `snapshot_timeout` elapses |
| `/__snapshot_result/<sid>` | POST | 200 `{"ok": true}` on success; 404 on unknown sid; 400 on malformed JSON |
| Anything else | any | 404 |

The handler overrides `log_message()` to silence the default per-request log line; pass `quiet=False` on the class to restore it.

Non-HTML files (not `.html` / `.htm`) are served as `text/html` without script injection — the browser attempts to render whatever the content is (e.g., SVG).

#### ReusableTCPServer (threaded)

`ReusableTCPServer` is a `socketserver.ThreadingMixIn + socketserver.TCPServer` subclass:

```python
class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
```

- `allow_reuse_address = True` so port 0 + rapid restart (e.g. iterating `artify serve` in a shell loop) does not hit `OSError: [Errno 98] Address already in use` during the TIME_WAIT window.
- `ThreadingMixIn` + `daemon_threads = True` is required by the snapshot design. `POST /__snapshot_request` blocks in its handler thread for up to `snapshot_timeout` seconds while it waits for the page to POST its response. During that window, the page must still be able to `GET /__commands` (so the snapshot command is delivered) and `POST /__snapshot_result/<id>` (so the response is delivered) on different worker threads. If the server were single-threaded (the `TCPServer` default), those would queue behind the snapshot request and the protocol would deadlock. `daemon_threads = True` ensures any in-flight handler threads die with the server if the process is hard-killed.

#### Watchdog Integration

`watch_and_serve(file, server, state)` spawns a `watchdog.observers.Observer` on the file's parent directory (non-recursive) and runs `server.serve_forever()` on the main thread.

The internal event handler:
1. Ignores directory events
2. Throttles bursts: if two `on_modified` events fire within 150ms (`time.monotonic()`), the second is dropped. This handles editors that write to a temp file and rename — the rename fires a synthetic `on_modified` against the original path that is a duplicate of the actual save.
3. Resolves the event's `src_path` and compares to the target file's resolved path; only reacts to events on the served file.
4. On a real change, calls `state.bump()` to mark the cache as changed.

The handler also re-reads the file's mtime on every `/__reload_check` request, so a missed watchdog event does not cause a stale view. The watchdog events are essentially a "fast path" that pre-empts the next poll; the mtime-on-poll is the source of truth.

#### Script Injection

`inject_reload_script(html)` is called by the handler before serving `GET /`:

1. If `INJECT_MARKER` (`<!--ARTIFY_RELOAD-->`) is already in the HTML, returns unchanged (idempotency guard for users who may have manually embedded the marker).
2. Otherwise, builds `<!--ARTIFY_RELOAD--><script src="/__reload.js"></script>` and inserts it just before the last `</body>`, else before `</html>`, else at the end of the string.
3. Search is case-insensitive for the closing tag, but insertion uses the original casing from the source HTML.

The injected script (`RELOAD_JS`) does two things in two independent 500ms `setInterval` loops:
- The **live-reload poll** (`/__reload_check`): if the mtime changes between polls, calls `location.reload()`.
- The **command poll** (`/__commands`): for each command of `type == "snapshot"`, collects the current form field values (or calls `window.__artify_collect__` if the page defined it) and POSTs them to `/__snapshot_result/<id>`.

### Snapshot Mechanism

The snapshot protocol is a small command/response dance on top of the existing `/__commands` polling loop. The blocking `__snapshot_request` handler and the page's two polling endpoints (one for commands, one for the result) run on independent threads because the server is `ThreadingMixIn`-based.

```
   CLI                                    server (artify serve)                    page
    |                                            |                                  |
    |  POST /__snapshot_request                  |                                  |
    |-------------------------------------------->|                                  |
    |  (blocks in handler thread)                |                                  |
    |                                            | 1. uuid = uuid4().hex           |
    |                                            | 2. enqueue {"type":"snapshot",  |
    |                                            |              "id": uuid}        |
    |                                            | 3. register_snapshot(uuid)      |
    |                                            |    -- allocates Event slot      |
    |                                            | 4. wait_for_snapshot(uuid,      |
    |                                            |         timeout=snapshot_t)     |
    |                                            |                                  |
    |                                            |     GET /__commands              |
    |                                            |<---------------------------------|
    |                                            |     [snapshot, ...]              |
    |                                            |--------------------------------->|
    |                                            |                                  |
    |                                            |   page collects form fields     |
    |                                            |   (or window.__artify_collect__) |
    |                                            |                                  |
    |                                            |   POST /__snapshot_result/uuid  |
    |                                            |   {"fields": {...}}              |
    |                                            |<---------------------------------|
    |                                            |                                  |
    |                                            | set_snapshot_result(uuid, ...)  |
    |                                            |   -- stores payload             |
    |                                            |   -- event.set()                |
    |                                            | wait_for_snapshot() unblocks,   |
    |                                            |   returns 200 with payload      |
    |  200 OK, body = {"fields": {...}}           |                                  |
    |<--------------------------------------------|                                  |
```

**Server-side handler flow (`_handle_snapshot_request`):**
1. Allocate `sid = uuid.uuid4().hex`.
2. Enqueue `{"type": "snapshot", "id": sid}` so the page's `/__commands` poll sees it.
3. Register the snapshot slot (`pending_snapshots[sid] = Event`, `snapshot_results[sid] = None`).
4. Block on `wait_for_snapshot(sid, timeout=InstanceState.snapshot_timeout)`.
5. On result: return 200 with the payload dict.
6. On timeout: return 408 with `{"error": "page did not respond"}`; the slot is dropped in `wait_for_snapshot` so a late page response does not accumulate.

**Server-side result handler (`_handle_snapshot_result`):**
1. Read `Content-Length` bytes from the request body.
2. JSON-decode; on `UnicodeDecodeError` / `JSONDecodeError` return 400 `{"error": "invalid json body"}`.
3. Normalize: if the top-level is not a dict, treat as `{}`; if `fields` is not a dict, treat as `{}`.
4. Call `state.set_snapshot_result(sid, {"fields": fields})`. If `sid` is unknown (already timed out and removed, or bogus), return 404 `{"error": "unknown snapshot id"}`.
5. Return 200 `{"ok": true}`.

**Page-side flow (inside `RELOAD_JS`):**
- Every 500ms, `GET /__commands` and parse the JSON array.
- For each item, if `cmd.type === "snapshot"`: call `collect()` (default form-field collector, or `window.__artify_collect__` if the page defined one) and `POST /__snapshot_result/<encodeURIComponent(cmd.id)>` with `{"fields": <collected>}`.

**Why threading matters:** the request handler thread is blocked in `wait_for_snapshot` for the entire duration. If the server were `TCPServer` (single-threaded, request-at-a-time), the page's `/__commands` poll would queue behind the snapshot request and the protocol would deadlock. `ThreadingMixIn` + `daemon_threads = True` ensures the page can deliver its response on a different worker thread.

### Instance Registry

Every `serve` instance writes a small JSON file under `~/.artify/instances/` (path: `Path.home() / ".artify" / "instances"`), one file per instance, named after the bound port: `<port>.json`.

```
~/.artify/
└── instances/
    ├── 54321.json     # {"port": 54321, "pid": 12345, "file": "...", "started_at": "..."}
    ├── 54322.json
    └── ...
```

**Schema:**

```json
{
  "port": 54321,
  "pid": 12345,
  "file": "/abs/path/to/index.html",
  "started_at": "2026-06-20T15:30:00+00:00"
}
```

**Helpers:**

| Function | Effect |
|---|---|
| `write_registry_entry(port, pid, file)` | Atomic write: writes `<port>.json.tmp` first, then `os.replace` onto `<port>.json`. Best-effort: any `OSError` is swallowed (registry is observability, not correctness). |
| `remove_registry_entry(port)` | `Path.unlink()` on `<port>.json`. Silent on `FileNotFoundError` and other `OSError` (idempotent, safe to call when entry is already gone). |
| `read_registry() -> list[dict]` | Reads every `*.json` in `REGISTRY_DIR`, parses, augments with `alive = psutil.pid_exists(pid)`, skips corrupt/non-dict entries, returns sorted by `port` ascending. |
| `_read_registry_entry(port) -> dict \| None` | Read and parse a single entry by port; returns `None` on missing or corruption. |
| `is_pid_alive(pid) -> bool` | `psutil.pid_exists(int(pid))`; returns False on bogus values (`ValueError` / `TypeError` are caught). |
| `collect_serving_url(port) -> str` | Returns the canonical `http://127.0.0.1:<port>/` URL. |

**Liveness detection:** `artify list` augments each entry with an `alive` flag via `psutil.pid_exists`. Stale (dead) entries are kept — the CLI never auto-removes them. The user sees a `STATUS=dead` row and can decide to `artify kill <port>` to clean it up.

**Concurrency:** the atomic `.tmp` → `os.replace` write means a concurrent `artify list` (which iterates the directory and reads each file) never sees a half-written file. The registry is intentionally simple — no lockfile, no database — because it is observability, not a coordination point.

**Cleanup responsibilities:**

| Trigger | Removal site |
|---|---|
| `Ctrl+C` on a `serve` instance | `serve()` command's `finally` block calls `remove_registry_entry(port)` |
| `artify kill PORT` | After the process terminates, `remove_registry_entry(port)` is called |
| `artify restart PORT` | After the old process is killed, `remove_registry_entry(old_port)` is called; the new instance writes its own entry to `<new_port>.json` |

### Cross-Platform Process Termination

`artify kill` and `artify restart` use a small shared helper, `_terminate_pid(pid, grace_seconds=2.0)`, to terminate the target process cross-platform:

```
_terminate_pid(pid, grace_seconds=2.0)
    |
    +-- if not is_pid_alive(pid): return           # already dead
    |
    +-- proc = psutil.Process(pid)
    +-- proc.terminate()                            # SIGTERM (Posix) / TerminateProcess (Windows)
    |
    +-- deadline = time.monotonic() + grace_seconds
    |   while time.monotonic() < deadline and is_pid_alive(pid):
    |       time.sleep(0.05)                        # poll for exit
    |
    +-- if is_pid_alive(pid):                       # still alive after grace
            psutil.Process(pid).kill()              # SIGKILL (Posix) / TerminateProcess (Windows)
```

- `psutil.Process.terminate()` is the polite signal: it asks the process to exit. On Windows it calls `TerminateProcess`; on Unix it sends `SIGTERM`. The process gets a chance to flush, run cleanup handlers, etc.
- If the process has not exited within `grace_seconds` (default 2s), `psutil.Process.kill()` is used as a hard escalation. This is non-graceful and does not give the process a chance to run cleanup.
- The helper is **best-effort** by design: it is allowed to silently swallow `psutil.NoSuchProcess` (the process exited between the `is_pid_alive` check and the `terminate()` call). `psutil.AccessDenied` is NOT swallowed — the caller decides what to do with it (the `kill` command prints the error to stderr and exits 1; `restart` swallows it as part of its best-effort semantics).
- `artify kill` uses the helper in its main path; `artify restart` wraps the call in a `try/except (psutil.NoSuchProcess, psutil.AccessDenied): pass` so a kill failure on the old instance never blocks the new instance from spawning.

### App-Mode Browser Detection

`find_app_browser() -> list[str] | None` returns an argv template with a literal `{url}` slot, or `None` if no supported browser is installed:

| Platform | Detection | Launcher |
|---|---|---|
| Windows | `shutil.which("msedge.exe")` (preferred) | `[msedge.exe, "--app={url}"]` |
| Windows | `shutil.which("chrome.exe")` (fallback) | `[chrome.exe, "--app={url}"]` |
| macOS | `Path("/Applications/Google Chrome.app").exists()` | `["open", "-na", "Google Chrome", "--args", "--app={url}"]` |
| Linux | `shutil.which("google-chrome")` | `[google-chrome, "--app={url}"]` |
| Linux | `shutil.which("chromium")` | `[chromium, "--app={url}"]` |
| Linux | `shutil.which("chromium-browser")` | `[chromium-browser, "--app={url}"]` |

`open_in_webview(url)` substitutes `{url}` into the template and spawns the process detached:

- Windows: `subprocess.Popen(cmd, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP, close_fds=True)`
- Unix: `subprocess.Popen(cmd, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)`

If no launcher is found, `open_with_browser()` falls back to `webbrowser.open_new_tab(url)` and prints a one-line warning to stderr. The `--webview` flag never errors on a missing browser.

### Dependencies
- **click** — CLI framework (existing core dep)
- **watchdog >= 4.0.0** — filesystem observation (new core dep, added for artify)
- **psutil** — process liveness + cross-platform termination (existing core dep, reused for `kill` and `restart`)
- **rich** — table output for `artify list` (existing core dep)

`watchdog` is imported lazily inside `watch_and_serve` so the `open` command does not require it. `psutil` is used at the top of the module because `is_pid_alive` is on the hot path of `kill` and `list`.

---

## Dependencies Graph

```
click >= 8.1.0          <-- All tools (CLI framework)
    |
rich >= 13.0.0          <-- bg, crony, essh, skill-store (table output)
    |
watchdog >= 4.0.0       <-- artify (filesystem events for live-reload)
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
