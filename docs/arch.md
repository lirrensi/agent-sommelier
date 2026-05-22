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
- **YAML files** for repo-local task state (`tasks/tasks.yaml`, `tasks/closed.yaml`)
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
- `core.py` — YAML storage, migrations, CRUD, search, dependency math
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
- `tasks update` — Edit task fields and status (supports `--owner` and `--claim` when that flag is preferred)
- `tasks close` — Archive a task
- `tasks history` — Browse closed tasks
- `tasks search` — Full-text search across active + closed tasks
- `tasks inbox` — Read the free-form inbox

### Storage

```
tasks/
├── inbox.md      # Free-form scratchpad for ideas and intake
├── tasks.yaml    # Active task list and metadata
└── closed.yaml   # Append-only closed archive
```

The task system is static and file-based. `tasks.yaml` is the active source of truth, `closed.yaml` is the archive, and the inbox is deliberately free-form intake. Statuses, priorities, dependencies, notes, evidence, and the optional `owner` field are all persisted in YAML so the repo can carry work across sessions.

### Behavior Notes

- `tasks next` and `tasks ready` are queue views over `todo` work
- typed deps include `blocks`, `parent`, `child`, `discovered`, and `relates`
- `blocks` drives readiness and blocked-state reporting
- `tasks overview` uses overview-specific Rich section rendering for a vertical dashboard-like view without adding interactivity
- `tasks take` is a dedicated shorthand for `tasks update --status in-progress`; it accepts an optional `--owner` flag but otherwise performs no additional side effects
- `tasks update` can change status, tags, priority, deps, notes, evidence, closure, and the optional `owner` field in one pass
- `tasks history` and `tasks search` make the archive useful, not just hidden

---

## Component: skill_store

### File
`src/agent_sommelier/skill_store.py`

### Entry Point
```python
skill-store = "agent_sommelier.skill_store:main"
```

### Purpose
CLI for the local skill registry. It supports browsing, searching, loading, pinning, and syncing skill entries stored on disk so agents can keep context small while still discovering tools on demand.

### Storage

The skill registry lives outside the repo at `~/.skill-store/` and is managed as a local catalog of skill folders and metadata.

### Behavior Notes

- `skill-store` is a lazy loader, not a runtime dependency injection system
- registry operations should keep the on-disk index and installed skill copies in sync
- skills are loaded on demand so the agent only pulls context for what it needs

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

## Dependencies Graph

```
click >= 8.1.0          <-- All tools (CLI framework)
    |
rich >= 13.0.0          <-- bg, crony (table output)
    |
dateparser >= 1.2.0    <-- crony (optional, natural language)
    |
schedule >= 1.2.0      <-- crony (optional, not currently used)
    |
mss >= 9.0.0           <-- screenshot (optional, primary capture)
    |
pillow >= 10.0.0       <-- screenshot (optional, mss dependency)
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
