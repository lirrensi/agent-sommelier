# Agent Sommelier — Product Specification

> Desktop superpowers for your AI agent. Simple CLI tools that just work.

## Overview

Agent Sommelier is a collection of CLI tools, skills, and systems that extend AI agent capabilities with desktop integration. Each tool is a standalone command with no daemon, no database, and no configuration required.

**Installation:**
```bash
uv tool install agent-sommelier-cli
```

**Optional extras:**
```bash
uv tool install agent-sommelier-cli[crony]    # Cron job support
uv tool install agent-sommelier-cli[screenshot]  # Screenshot support
uv tool install agent-sommelier-cli[all]      # Everything
```

---

## Tool: notify — Desktop Notifications

Cross-platform desktop notifications using native OS tools.

### Commands

#### `notify TITLE [BODY]`

Send a desktop notification.

**Arguments:**
- `TITLE` — Notification title (required)
- `BODY` — Notification body (optional). If omitted, TITLE becomes the body and title defaults to "Notification".

**Options:**
- `--sound` — Play notification sound (platform dependent)

**Input modes:**
1. Arguments: `notify "Build Done" "All tests passed!"`
2. Piped stdin: `echo "Status update" | notify "Progress"`
3. Explicit stdin: `cat log.txt | notify "Logs" -`

**Exit codes:**
- `0` — Notification sent successfully
- `1` — Failed to send notification

### Platform Behavior

| Platform | Tool Used | Notes |
|----------|-----------|-------|
| Windows | PowerShell Toast API | Uses `Windows.UI.Notifications` |
| macOS | `osascript` | Uses AppleScript notification |
| Linux | `notify-send` | Requires `libnotify` package |

### Edge Cases

- If BODY is `-`, reads from stdin (explicit pipe mode)
- If no body provided, TITLE is used as body with title "Notification"
- If stdin is empty and no body provided, sends empty notification
- If notification tool not found, exits with code 1 and error message

---

## Tool: bg — Background Jobs

Run and track background jobs with friendly names, stable UIDs, and separate record/process state.

### Commands

#### `bg run "CMD"`

Start a command in the background.

**Arguments:**
- `CMD` — Command to execute (string)

**Output:**
- Returns a friendly name like `sleepy-pytest`
- The stable UID stays internal but is shown by `bg list` and `bg status`

**Behavior:**
- Job runs detached from terminal
- Process continues even if parent shell exits
- stdout and stderr are captured to files
- Names use `<word>-<commandroot>` and gain a short suffix only on collision
- On Windows, commands run in `pwsh` when available, then `powershell`, then `cmd.exe`
- On Windows, PowerShell-backed jobs are launched hidden so they do not expose a closable console window
- Windows commands should use syntax for the selected shell unless they explicitly invoke another shell
- `bg run` returns immediately after creating the handle; a detached worker finishes launch in the background

#### `bg list [--json]`

List all background jobs.

**Options:**
- `--json` — Output as JSON array

**Output (human-readable):**
- Table with columns: Name, UID, Record, Process, Status, Update, PID, Started, Elapsed, Command
- Status colors: yellow=running, green=completed, red=failed

**Output (JSON):**
```json
[
    {
      "uid": "b71d4e2f9a8c",
      "id": "b71d4e2f9a8c",
      "name": "sleepy-pytest",
      "cmd": "pytest tests",
      "record_state": "ok",
      "process_state": "alive",
      "status": "running",
      "pid": 12345,
      "elapsed_seconds": 42,
      "memory_bytes": 104857600,
      "cpu_percent": 3.2,
      "finished_at": null,
      "exit_code": null,
      "last_event_type": null,
      "last_event_at": null,
      "matched_pattern": null,
      "matched_stream": null,
      "update_marker": null
    }
]
```

**Behavior:**
- Automatically checks if running processes are still alive
- Preserves record problems separately from process state
- Refreshes live process details before rendering list output
- Live resource metrics are best-effort and MAY be omitted on platforms where they cannot be read reliably
- Terminal jobs are pruned opportunistically: keep them for at least 1 hour, cap history at 32 jobs, and evict the oldest terminal records first; running jobs are never removed automatically

#### `bg wait JOB_REF`

Wait until a job reaches a terminal state.

#### `bg wait JOB_REF --match PATTERN`

Wait until PATTERN appears in stdout or stderr, then record a matched-output event.

#### `bg wait-all`

Wait until all known jobs are terminal.

#### `bg status JOB_REF`

Check job status.

**Arguments:**
- `JOB_REF` — Friendly name or UID

**Output:**
- Full enriched job metadata as JSON, including `record_state`, `process_state`, `status`, and terminal fields such as `finished_at` and `exit_code`
- Also includes `last_event_type`, `last_event_at`, `matched_pattern`, `matched_stream`, `update_marker`, and `record_issue`

**Behavior:**
- Refreshes process details before returning output
- Surfaces corrupted or missing records explicitly instead of normalizing them away

#### `bg read JOB_REF`

Read job stdout.

**Arguments:**
- `JOB_REF` — Friendly name or UID

**Output:**
- Complete stdout contents

#### `bg logs JOB_REF`

Read job stdout and stderr.

**Arguments:**
- `JOB_REF` — Friendly name or UID

**Output:**
```
=== STDOUT ===
<stdout content>

=== STDERR ===
<stderr content>
```

#### `bg rm JOB_REF`

Remove a job record.

**Arguments:**
- `JOB_REF` — Friendly name or UID

**Behavior:**
- If job is still running, kills the process first
- Removes all job files from storage

#### `bg prune`

Remove every job that is not currently running.

**Behavior:**
- Keeps all running jobs intact
- Deletes completed, failed, stale, missing, corrupt, and orphaned jobs
- Acts as an aggressive privacy cleanup / storage reset for terminal state

### Storage

Runtime state stored in: `{tempdir}/agentcli_bgjobs/`

Terminal job records are self-pruning under the retention policy above.

| File | Contents |
|------|----------|
| `index.json` | Friendly-name and UID lookup index |
| `records/{uid}/meta.json` | Canonical job metadata (uid, name, cmd, pid, status, timestamps, last event fields, launch issues) |
| `records/{uid}/stdout.txt` | Captured stdout |
| `records/{uid}/stderr.txt` | Captured stderr |
| `records/{uid}/exit_code.txt` | Persisted exit code |

### Status Values

- `running` — Process is active
- `launching` — Internal only; user-facing status is shown as running until failure is proven
- `completed` — Process finished successfully
- `failed` — Process exited with non-zero code
- `stale` — Record is healthy but PID is gone and no exit code was found
- `missing` / `corrupt` / `orphaned` — Record problem surfaced by `bg list` / `bg status`

### Edge Cases

- Job reference not found: exits with code 1, error message to stderr
- Process already dead when checking status: reported separately from record state
- Live metrics such as memory and CPU are best-effort and MAY be missing when the host platform does not expose them cheaply
- Windows: uses hidden `Start-Process` launches when PowerShell is available, else `CREATE_NEW_PROCESS_GROUP` + `CREATE_NO_WINDOW`
- Unix: uses `start_new_session` for full detachment
- Launch happens in a detached worker; launch failures preserve the job record and mark it failed instead of deleting the handle
- A delayed best-effort probe retries PID discovery for a few seconds and updates the record when possible

---

## Tool: crony — Cron Jobs, Human-Readable

Natural language cron job scheduler with OS-level integration.

### Commands

#### `crony add NAME SCHEDULE "CMD" [--cron]`

Add a new cron job.

**Arguments:**
- `NAME` — Unique job name (identifier)
- `SCHEDULE` — Natural language schedule (see below), or a raw cron expression when `--cron` is used
- `CMD` — Command to execute

**Options:**
- `--cron` — Treat SCHEDULE as a raw cron expression instead of natural language (5 space-separated fields)

**Schedule formats:**

| Format | Example | Description |
|--------|---------|-------------|
| Relative | `in 5m`, `in 1h`, `in 2d` | One-off, runs once |
| Time | `at 15:30`, `at "2026-03-10 10:00"` | One-off at specific time |
| Interval | `every 1h`, `every 30m`, `every 24h` | Recurring |
| Day | `every monday`, `every weekday`, `every weekend` | Weekly or daily |
| Raw cron | `*/5 * * * *` (with `--cron`) | Recurring, bypasses natural language parser |

**Output (natural language):**
```
Added job: health-check
  Schedule: every 1h (recurring)
  Cron: 0 * * * *
```

**Output (with `--cron`):**
```
Added job: nightly
  Schedule: 0 2 * * * (recurring, raw cron)
```

**Examples with `--cron`:**
```bash
crony add myjob "*/5 * * * *" "python script.py" --cron
crony add nightly "0 2 * * *" "backup.sh" --cron
```

#### `crony list [--json] [--sync]`

List all cron jobs.

**Options:**
- `--json` — Output as JSON
- `--sync` — Force sync with OS scheduler

**Output (human-readable):**
- Table with columns: Name, Type, Schedule, Next Run, Command

**Output (JSON):**
- Job objects include computed next-run data when derivable

**Behavior:**
- Auto-syncs with OS scheduler on every call (reconciles jobs.json with crontab/Task Scheduler)
- Finds orphaned tasks in OS and adds to index
- Re-registers jobs missing from OS
- Calculates the next upcoming execution time for recurring jobs before rendering list output
- One-off jobs use their stored scheduled timestamp as `next_run`

#### `crony rm NAME`

Remove a cron job.

**Arguments:**
- `NAME` — Job name

**Behavior:**
- Removes from jobs.json
- Removes from OS scheduler (crontab or Task Scheduler)

#### `crony run NAME`

Run a job immediately.

**Arguments:**
- `NAME` — Job name

**Behavior:**
- Executes the command immediately (does not modify schedule)

#### `crony logs NAME`

View job logs.

**Arguments:**
- `NAME` — Job name

**Output:**
- Log file contents if exists

### Storage

| Location | Contents |
|----------|----------|
| `~/.crony/jobs.json` | Job definitions and metadata |
| `~/.crony/logs/{name}.log` | Job execution logs |

### OS Integration

| Platform | Scheduler | Marker |
|----------|-----------|--------|
| Linux | crontab | `# CRONY:{name}` |
| macOS | crontab | `# CRONY:{name}` |
| Windows | Task Scheduler | `CRONY_{name}` |

### Edge Cases

- Job name already exists: error, must remove first
- Invalid schedule: error with message
- Missing optional dependencies: error with install hint
- OS scheduler unavailable: error, but job saved to index
- One-off jobs: stored in index but not added to recurring scheduler
- **Working directory:** When a job is added, crony captures the current working directory.
  When the OS scheduler runs the job, it first `cd`s to that directory, so relative paths work.
  On Unix, this uses `shlex.quote()` for safe path handling. On Windows, a `.bat` wrapper script
  is created in `~/.crony/scripts/` with proper quoting.

---

## Tool: screenshot — Screen Capture

Cross-platform screenshot capture with auto-naming.

### Commands

#### `screenshot [OUTPUT]`

Take a screenshot.

**Arguments:**
- `OUTPUT` — Optional output file path. If omitted, auto-generates.

**Options:**
- `--all-monitors` — Capture all monitors (default behavior)

**Output:**
- Prints the saved file path to stdout

**Auto-naming:**
- Format: `screenshot_{YYYYMMDD}_{HHMMSS}.png`
- Location: `{tempdir}/agentcli_screenshots/`

### Platform Behavior

| Platform | Primary | Fallback |
|----------|---------|----------|
| Windows | mss library | PowerShell + System.Drawing |
| macOS | mss library | `screencapture` |
| Linux | mss library | `gnome-screenshot`, `scrot`, `import`, `flameshot` |

### Edge Cases

- mss not installed: falls back to native tools
- No native tool available: exits with code 1, suggests install
- Output path has parent directories: creates them automatically
- Multiple monitors: captures combined virtual screen (monitor 0)

---

## Tool: tasks — In-Repo Task Management

Static, file-backed task tracking for project work. Lives in `tasks/` with no database or service, and preserves history across sessions.

**Commands:** `tasks init`, `tasks add`, `tasks list`, `tasks next`, `tasks ready`, `tasks blocked`, `tasks status`, `tasks show`, `tasks take`, `tasks update`, `tasks close`, `tasks history`, `tasks search`, `tasks inbox`.

**Use it for:** capturing work, tracking dependencies, finding the next unblocked task, and keeping the archive of completed work visible.

Tasks can also carry `notes` and `evidence` as appendable string lists; `evidence` is the quick verification trail for later re-checks.

The optional `owner` field records who is working on a task — set via `tasks add --owner <name>`, `tasks update TSK-NNNN --owner <name>`, or `tasks take TSK-NNNN --owner <name>`. When not set, the task is unowned and single-user workflows need not concern themselves with it.

`tasks take TSK-NNNN` is a shorthand for marking a task in-progress — equivalent to `tasks update TSK-NNNN --status in-progress`. Idempotent: safe to re-run if already in-progress.

---

## Skill: task-system — How to Use Tasks Well

Quick guidance for the in-repo task CLI. It explains statuses, dependency types, priority ordering, inbox flow, and the ready/blocked queues.

**Location:** `skills/task-system/`

---

## Skill: memory-bank — Persistent Session Memory

Long-lived memory for durable facts, decisions, and repeatable workflows across sessions. Use it when something should be remembered later, not just this chat.

**Location:** `skills/memory-bank/`

**Default shape:** `./memory/episodic/`, `./memory/semantic/`, `./memory/procedural/`, with an auto-maintained `./memory/INDEX.md`.

---

## Skill: skill-store — On-Demand Skill Registry

Local skill registry for loading, browsing, pinning, and syncing agent skills without stuffing everything into context.

**Location:** `skills/skill-store/`

**Use it for:** discovering available skills, loading one when needed, and keeping a local registry current.

---

## Skill: tmux — Terminal Session Control

Control tmux/psmux sessions programmatically for SSH, REPLs, and parallel processes. Cross-platform: tmux on Linux/macOS, psmux on Windows.

**Location:** `skills/tmux/`

### Helper: tmx

```bash
tmx new <name> [cmd]       # Create session
tmx send <session> "<cmd>" # Send keys
tmx capture <session> [n]  # Capture output (default 500 lines)
tmx sync <session> "<cmd>" # Send + wait + capture
tmx list                   # List sessions
tmx kill <session>         # Kill session
```

### Sync Modes

- `--prompt <pattern>` / `-Prompt <pattern>` — Wait for prompt pattern
- `--timeout <sec>` / `-Timeout <sec>` — Fixed wait N seconds

### Primary Use Case: SSH

```bash
tmx new server "ssh user@myserver.com"
tmx sync server "hostname"
tmx sync server "tail -100 /var/log/app.log" --timeout 5
tmx capture server
```

### Installation

| Platform | Tool | Install |
|----------|------|---------|
| Linux | tmux | `apt install tmux` |
| macOS | tmux | `brew install tmux` |
| Windows | psmux | `winget install psmux` |

### Windows (psmux)

psmux is a native Windows tmux implementation — same commands, same config. 95%+ tmux syntax compatible.

```powershell
winget install psmux
# or: scoop install psmux
# or: choco install psmux
```

If issues: download from https://github.com/marlocarlo/psmux/releases

---

## Dependencies

### Core (always installed)
- `click >= 8.1.0` — CLI framework
- `rich >= 13.0.0` — Terminal formatting

### Optional
- `dateparser >= 1.2.0` — Natural language date parsing (crony)
- `schedule >= 1.2.0` — Schedule library (crony)
- `mss >= 9.0.0` — Cross-platform screenshot (screenshot)
- `pillow >= 10.0.0` — Image processing (screenshot)

---

## Exit Codes

All tools follow this convention:
- `0` — Success
- `1` — Error (invalid input, tool not found, operation failed)

---

## Design Principles

1. **No daemon** — All tools are stateless CLI invocations
2. **No database** — JSON files for persistence
3. **No configuration** — Sensible defaults, auto-detection
4. **Pipe-friendly** — All tools accept stdin where appropriate
5. **Cross-platform** — Same interface on Windows, macOS, Linux
