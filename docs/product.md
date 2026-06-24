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
uv tool install agent-sommelier-cli[crony]      # Cron job support
uv tool install agent-sommelier-cli[screenshot]  # Screenshot support
uv tool install agent-sommelier-cli[mcp-srv]     # MCP server for skill-store
uv tool install agent-sommelier-cli[all]         # Everything
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

**Options:**
- `--timeout N` (float seconds, `>= 0`) — Max seconds to wait before the wait loop exits. `0` disables the cap. Default: `120` in non-TTY (agent/script) mode, infinite in TTY (interactive) mode.

**Behavior:**
- The wait loop polls the job record every 0.2s until the job is terminal.
- In non-TTY/agent mode, a 120-second safety cap fires by default so the wait cannot block an agent indefinitely. Override with `--timeout N`; pass `--timeout 0` to wait without a cap.
- When the cap fires, a clear message is printed to stderr (not stdout) and the process exits `0`. The message names the job, its current elapsed time and PID, explains that the wait loop — not the job — was terminated, and lists the re-poll commands (`bg status`, `bg wait <name> --timeout N`, `bg wait <name> --timeout 0`, `bg logs`).
- The stderr message is the contract — agents should treat its presence as "wait loop hit the safety cap, the job is still running".

#### `bg wait JOB_REF --match PATTERN`

Wait until PATTERN appears in stdout or stderr, then record a matched-output event.

**Options:**
- `--timeout N` (float seconds, `>= 0`) — Same as `bg wait`. The default 120s cap applies in non-TTY/agent mode and is disabled with `--timeout 0`.

**Behavior:**
- Scans stdout and stderr incrementally until the pattern is found or the job exits. If the job exits first, raises a `ClickException` ("Pattern not found before job finished").
- On timeout, the stderr message additionally states that the pattern was not found yet and includes the pattern string in the re-poll commands.

#### `bg wait-all`

Wait until all known jobs are terminal.

**Options:**
- `--timeout N` (float seconds, `>= 0`) — Same default and override semantics as `bg wait`.

**Behavior:**
- Polls all known jobs every 0.2s. Returns once none are still running.
- On timeout in non-TTY/agent mode, the stderr message names every still-running job with its current elapsed time and lists the re-poll commands. Exit code is `0`.

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
- Reads jobs directly from jobs.json (managed by the scheduler daemon)
- Use `--sync` to import orphaned crontab/schtasks entries from prior versions
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

| Platform | Scheduler | Details |
|----------|-----------|---------|
| All | crony daemon | Cross-platform Python daemon using croniter; auto-starts on first `crony add` |

### Daemon Commands

| Command | Description |
|---------|-------------|
| `crony daemon status` | Show daemon status (running/stopped/stale) |
| `crony daemon start` | Start the daemon and register auto-start on login |
| `crony daemon stop` | Stop the daemon |
| `crony daemon restart` | Stop and restart the daemon |

The daemon auto-starts when the first job is added (`crony add ...`) and auto-exits when no jobs remain. Manual control is available via the commands above.

### Edge Cases

- Job name already exists: error, must remove first
- Invalid schedule: error with message
- Missing optional dependencies: error with install hint
- **Daemon auto-start:** After `crony add`, the daemon starts automatically. On next login it restarts automatically if jobs exist.
- **Daemon auto-exit:** When all jobs are removed (`crony rm` for the last job), the daemon stops silently.
- **Stale daemon:** `crony daemon status` detects crashed/zombie daemons via lockfile+token verification (not PID alone).
- One-off jobs: executed by daemon and marked completed automatically
- **Working directory:** When a job is added, crony captures the current working directory. The daemon runs the command from that directory via `cwd` in `subprocess.Popen`.

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

## Tool: artify — HTML Artifact Preview

Open, live-reload, manage, and snapshot HTML artifacts produced by the `artify` skill. Six commands: a simple offline opener, a local HTTP server with polling-based live-reload, a process list, a kill-by-port, a restart-by-port, and a form-state snapshot reader that talks to the page via a small command/response protocol.

### Commands

#### `artify open FILE`

Open FILE in the default browser via a `file://` URL. Offline, no server, no live-reload.

**Arguments:**
- `FILE` — Path to the HTML file. Must exist and not be a directory.

**Behavior:**
- Resolves FILE to an absolute path and opens it in the default browser as a new tab
- No HTTP server, no injection, no reload
- Use this when the file is finished and you just want to view it

**Exit codes:**
- `0` — Browser launched (or already open)
- `1` — Browser failed to launch

#### `artify serve FILE [--webview]`

Serve FILE on a random local port with polling-based live-reload, then open the served URL in a browser tab (or in a chromeless app-mode window with `--webview`).

**Arguments:**
- `FILE` — Path to the HTML file. Must exist and not be a directory.

**Options:**
- `--webview` — Open the served page in a chromeless native window using the host browser's `--app=URL` mode (no tabs, no address bar, no menu)

**Behavior:**
- Binds an HTTP server to `127.0.0.1` on a random free port
- Registers the running instance under `~/.artify/instances/<port>.json` so `artify list`, `artify kill`, and `artify restart` can find it later
- Injects a small live-reload script into the served HTML (skipped for non-`.html`/`.htm` files)
- The client polls `/__reload_check` every 500ms; when the file's mtime changes, the page reloads
- The same client polls `/__commands` every 500ms so the CLI can ask the page to perform actions (currently: `snapshot`)
- A `watchdog` observer runs in the background and coalesces filesystem-event bursts with a 150ms throttle (handles editor save patterns that write to a temp file then rename)
- Edit the file in any editor; the tab reloads within ~1 second of save
- `Ctrl+C` cleanly stops the server, removes the registry entry, and the watchdog observer

**App-mode browser detection (best-effort):**

| Platform | Primary | Fallback |
|----------|---------|----------|
| Windows | `msedge.exe --app=URL` | `chrome.exe --app=URL` |
| macOS | `open -na "Google Chrome" --args --app=URL` (only if Chrome is installed) | — |
| Linux | `google-chrome --app=URL` | `chromium`, `chromium-browser` |

If no app-mode browser is found, `artify serve --webview` falls back to the default browser tab and prints a one-line warning to stderr. It does not error.

**Exit codes:**
- `0` — Server stopped cleanly (Ctrl+C or normal exit)
- `1` — Failed to start server or read file

#### `artify list`

List every artify `serve` instance currently registered on this machine, with live liveness detection.

**Behavior:**
- Reads `~/.artify/instances/*.json` (one file per running `serve` instance, named after its port)
- Augments each entry with a live `alive` flag (computed via `psutil.pid_exists` on the stored PID)
- Renders a Rich table with columns: PORT, PID, FILE, STATUS, STARTED, URL
  - `STATUS` is `running` if the PID is alive, `dead` otherwise
  - `URL` is `http://127.0.0.1:<port>/` for running entries, `-` for dead ones
  - `FILE` is truncated to the last 60 characters with a leading `...` for long Windows paths
- Stale (dead) entries are kept so the user can see and decide what to do with them; the CLI never auto-removes them
- Exits 0 even when there are no entries; prints `No artify instances running.` to stdout

**Exit codes:**
- `0` — Always (the command is a read-only view)

#### `artify kill PORT`

Terminate the `serve` instance bound to PORT and clean up its registry entry.

**Arguments:**
- `PORT` — TCP port number (1..65535). Must match a registered instance.

**Behavior:**
- Reads the registry entry for PORT; if missing, prints an error to stderr and exits 1
- Probes liveness via `psutil.pid_exists`; if alive, sends `SIGTERM` (via `psutil.Process.terminate`) and waits up to 2 seconds, escalating to `SIGKILL` (`psutil.Process.kill`) if the process is still alive
- Removes the registry entry for PORT regardless of whether the process was alive (a dead entry is still cleaned up)
- Success message: `Killed artify instance on port <PORT> (pid <PID>)`
- If the PID was already dead, prints `Instance on port <PORT> (pid <PID>) was already not running; cleaned up registry.` (still exit 0 — cleanup is the user-visible operation)
- If the OS refuses the kill (e.g. process owned by another user), prints `Access denied killing pid <PID>: <reason>` to stderr and exits 1

**Exit codes:**
- `0` — Process terminated (or was already dead) and registry entry removed
- `1` — No instance on PORT, or `psutil.AccessDenied` from the OS

#### `artify restart PORT`

Kill the instance on PORT (if alive) and re-serve the same file on a new, free port.

**Arguments:**
- `PORT` — TCP port number (1..65535). Must match a registered instance.

**Behavior:**
- Reads the registry entry for PORT; if missing, prints an error to stderr and exits 1
- If the registry entry's `file` field is empty or the file no longer exists on disk, prints an error to stderr and exits 1 (no kill happens)
- Best-effort kill of the old PID (`SIGTERM` → 2s grace → `SIGKILL`); any `psutil` failure is swallowed silently
- Removes the old registry entry so no stale entry points at a now-dead port
- Spawns a fresh, fully-detached `artify serve <FILE>` subprocess:
  - **Windows:** `creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` so the new process survives this CLI's exit and has no shared console
  - **Unix:** `start_new_session=True` plus `stdin/stdout/stderr=DEVNULL`
- Sleeps 300ms after spawn to give the new instance a beat to bind its port and write its own registry entry
- Prints `Restarted. New instance on a different port — run 'artify list' to find it.`
- The new instance picks its own port (the kernel assigns a free one); the caller should run `artify list` afterwards to find it

**Exit codes:**
- `0` — Old instance was killed, new instance was spawned (caller should verify with `artify list`)
- `1` — No instance on PORT, missing file path, or file no longer exists

#### `artify snapshot PORT [--timeout N]`

Ask the running `serve` instance on PORT to read the page's current form state and print it as JSON to stdout. The page is asked via a command/response protocol — the server enqueues a `snapshot` command that the page picks up on its next 500ms `/__commands` poll.

**Arguments:**
- `PORT` — TCP port number (1..65535). Must be a live, responding `serve` instance.

**Options:**
- `--timeout` — Seconds to wait for the page to respond before giving up (default: 30, minimum: 1.0). The server enforces its own internal timeout (`InstanceState.snapshot_timeout`, default 30s) which is independent of this value; the CLI uses `timeout + 5s` as its socket read timeout so a server-side 408 has time to come back.

**Behavior:**
- Sends `POST http://127.0.0.1:<port>/__snapshot_request` with an empty body
- The server allocates a snapshot id, enqueues a `{"type": "snapshot", "id": <sid>}` command, and blocks the request thread waiting for the page to POST back to `/__snapshot_result/<sid>`
- The page's polling JS picks up the command, collects the current form field values (or, if the page defined `window.__artify_collect__`, calls that function and uses its return value), and POSTs `{"fields": {...}}` to `/__snapshot_result/<sid>`
- The server signals the blocked request thread and returns the payload as `{"fields": {...}}` with status 200
- The CLI pretty-prints the JSON to stdout
- On a 408 from the server (page never responded within the server's internal timeout), prints `Page did not respond within <timeout>s` to stderr and exits 1
- On a connection refused / unreachable port, prints `No artify instance on port <port>` to stderr and exits 1
- If the instance exists but returns a non-200/non-408, prints `HTTP <code> from artify on port <port>: <reason>` and exits 1
- Empty `fields` is a valid, expected result for pages with no inputs (e.g. a landing page) — exits 0 and prints `{"fields": {}}`

**Exit codes:**
- `0` — Page responded; JSON was printed to stdout
- `1` — No instance, page did not respond, or server returned a non-success status

### Instance Registry

Every running `serve` instance is recorded as a small JSON file under `~/.artify/instances/`, one file per port. The file is named after the port: `<port>.json`.

**File format:**

```json
{
  "port": 54321,
  "pid": 12345,
  "file": "/abs/path/to/index.html",
  "started_at": "2026-06-20T15:30:00+00:00"
}
```

| Field | Type | Description |
|---|---|---|
| `port` | int | TCP port the server is bound to |
| `pid` | int | OS PID of the `serve` process |
| `file` | str | Absolute path of the file being served |
| `started_at` | str | ISO 8601 UTC timestamp written by the instance at startup |

**Atomic writes:** The file is written via `<port>.json.tmp` then `os.replace` so a concurrent reader (e.g. `artify list` running at the same moment) never observes a half-written file. Best-effort: any `OSError` is swallowed silently because the registry is observability, not correctness.

**Liveness detection:** `artify list` augments each entry with an `alive` boolean by calling `psutil.pid_exists(pid)`. Stale entries are kept (not auto-removed) so the user can see and decide what to do with them — usually a manual `artify kill <port>`.

**Cleanup:** A `serve` instance removes its own registry entry on `Ctrl+C` (from inside the `finally` block). `artify kill` removes the entry after terminating the process. `artify restart` removes the old entry after killing the old process and before spawning the new one.

### Snapshot Mechanism

`artify snapshot` and the `__snapshot_request` / `__snapshot_result/<id>` endpoints form a small command/response protocol on top of the existing `/__commands` polling loop that the injected reload script already runs.

```
   CLI                                    server (artify serve)                    page
    |                                            |                                  |
    |  POST /__snapshot_request                  |                                  |
    |-------------------------------------------->|                                  |
    |  200 OK  (blocks in handler thread)         |                                  |
    |  body = {"fields": {...}}                   |                                  |
    |                                            |-- enqueue {"type":"snapshot"} -->|
    |                                            |   (page polls /__commands)       |
    |                                            |                                  |
    |                                            |   GET /__commands                |
    |                                            |<---------------------------------|
    |                                            |   [snapshot, snapshot, ...]      |
    |                                            |--------------------------------->|
    |                                            |                                  |
    |                                            |   POST /__snapshot_result/<id>   |
    |                                            |   {"fields": {...}}              |
    |                                            |<---------------------------------|
    |                                            |                                  |
    |  200 OK with payload                        |   (handler unblocks, returns)   |
    |<--------------------------------------------|                                  |
```

**Server-side details:**

- Each `__snapshot_request` allocates a fresh `uuid.uuid4().hex` and registers a `(event, result)` slot in `InstanceState.pending_snapshots` / `snapshot_results` (both protected by `results_lock`)
- The handler then calls `wait_for_snapshot(sid, timeout=InstanceState.snapshot_timeout)` which blocks on the slot's `threading.Event`
- The page's `POST /__snapshot_result/<sid>` validates the JSON, calls `set_snapshot_result(sid, payload)`, which stores the payload and `event.set()`s the waiter
- The handler reads the result, removes the slot, and returns the payload to the CLI
- On timeout, the handler returns 408 with `{"error": "page did not respond"}` and the slot is dropped so a late page response does not accumulate
- Unknown snapshot ids at the result endpoint return 404 with `{"error": "unknown snapshot id"}`; malformed JSON returns 400 with `{"error": "invalid json body"}`

**Page-side details:**

- The injected JS polls `/__commands` every 500ms; for each command of `type == "snapshot"`, it collects the current form field values and POSTs them to `/__snapshot_result/<id>`
- The default collector scans `input`, `textarea`, and `select` elements and produces a flat dict of `name → value` (with `checkbox` → bool, multi-select → list)
- A page can override the default by defining `window.__artify_collect__ = function() { return {...}; }`; the JS will call it instead and POST its return value

### Live-Reload Mechanism

1. Server binds to `127.0.0.1` on a random port
2. On `GET /`, server reads the file from disk, injects `<!--ARTIFY_RELOAD--><script src="/__reload.js"></script>` (idempotent — skips if marker is present), and returns `text/html`
3. The injected script polls `/__reload_check` every 500ms; the endpoint returns the file's current mtime as a text/plain float
4. When the mtime changes between polls, the page calls `location.reload()`
5. A `watchdog.observers.Observer` watches the file's parent directory; `on_modified` events update the in-memory mtime cache, with a 150ms throttle to ignore editor bursts (temp file + rename)
6. The handler also re-reads the file's mtime on every poll request, so missed watchdog events don't cause stale views

### Edge Cases

- Missing file: Click rejects before the command runs; exit code 2 (usage error) with a clear "File ... does not exist" message
- Path is a directory: Click rejects (the argument type is `dir_okay=False`); exit code 2
- Non-HTML file (e.g. `.svg`): served as `text/html` with no script injection; the browser attempts to render it natively; reload still works
- No app-mode browser on `--webview`: prints warning to stderr, falls back to default browser tab
- File saved with editor burst pattern (temp file + rename): the 150ms watchdog throttle ignores the first event; the next legitimate event still triggers a reload
- Ctrl+C on `serve`: server shuts down, watchdog observer stops, registry entry is removed, port is released
- File deleted while serving: subsequent requests get HTTP 500 with a "Failed to read file" message
- Idempotent injection: re-serving the same file does not stack multiple `<script>` tags
- `artify list` with empty registry: prints `No artify instances running.` to stdout, exits 0
- `artify list` with a dead entry: the entry is shown with `STATUS=dead` and `URL=-` (no link), the registry file is kept (not auto-removed) so the user can decide
- `artify kill` on an unknown port: prints `No artify instance on port <port>` to stderr, exits 1; no state change
- `artify kill` on a dead PID: the registry file is still removed and the friendly `was already not running` message is printed; exit 0 (cleanup is the user-visible operation)
- `artify kill` with `psutil.AccessDenied`: prints `Access denied killing pid <pid>: <reason>` to stderr, exits 1; the registry entry is left in place so the user can investigate
- `artify restart` when the file no longer exists: error to stderr, exit 1; the existing instance is not killed and the registry entry is left in place
- `artify snapshot` with no page connected: server waits up to `snapshot_timeout` seconds (default 30s), then returns 408; the CLI surfaces this as `Page did not respond within <timeout>s` and exits 1
- `artify snapshot` against a page that has no inputs: returns `{"fields": {}}` (empty dict is a valid result), exits 0
- `artify snapshot` with `--timeout` < 1.0: Click rejects with a usage error (FloatRange minimum 1.0), exit 2
- Concurrent `artify list` and a serving instance writing its registry: atomic `.tmp` → `os.replace` means the reader never sees a partial file
- `~/.artify/instances/` does not exist at startup: `artify list` treats that as an empty registry and prints `No artify instances running.`
- A corrupt entry under `~/.artify/instances/`: `artify list` silently skips it (the bad JSON doesn't poison the whole list)

---

## Tool: essh — Portable SSH Wrapper

Portable SSH wrapper CLI that makes SSH sane across Windows/WSL/Linux. Adds name abstraction, agent authorization gating, and cross-environment portability.

### Commands

#### `essh add USER@HOST[:PORT]` or `essh add NAME USER@HOST[:PORT]`

Save a new SSH host profile.

**Arguments:**
- `USER@HOST[:PORT]` — SSH target, port defaults to 22 (always required)
- `NAME` — Optional friendly name. If omitted, auto-generates Docker-style: `{color}-{animal}` (e.g. `blue-whale`, `red-falcon`). Collisions append a short hex suffix: `blue-whale-a3f`.

**Name rules:**
- Only `[a-z]`, `[0-9]`, `-`, `_` allowed. No uppercase, no spaces, no special characters.
- Auto-generated names always pass validation; user-provided names are rejected with a clear error if invalid.

**Behavior:**
- Generates an ed25519 keypair for the host if none exists
- Pushes the public key to the remote host via `ssh-copy-id` equivalent (password prompt once)
- Saves the profile (name, user, host, port, key path) to `~/.essh/profiles.json`
- If `-i KEY` is passed, uses the specified existing key instead of generating one

#### `essh NAME [COMMAND]`

Connect to a saved host or run a command.

**Arguments:**
- `NAME` — Friendly host name
- `COMMAND` — Optional command to execute remotely

**Behavior:**
- Resolves NAME to the saved profile
- If running from a context without a TTY (agent, script, cron), the request is **blocked** until authorized
- If running interactively (has TTY), connects immediately
- Ensures the key is added to `ssh-agent` before connecting

#### `essh authorize NAME`

Authorize a pending agent request for the named host.

**Arguments:**
- `NAME` — Friendly host name

**Behavior:**
- Clears the pending request lock for the named host
- The blocked agent process proceeds with the SSH connection
- Request locks auto-expire after 30 seconds to prevent permanent hangs

#### `essh list [--json]`

List all saved host profiles.

**Options:**
- `--json` — Output as JSON

#### `essh rm NAME`

Remove a saved host profile and its keypair.

#### `essh export [OUTPUT]`

Export all profiles, keys, and known_hosts to a portable archive.

**Arguments:**
- `OUTPUT` — Optional output path (defaults to `~/.essh/exports/essh-export-{timestamp}.tar.gz`)

**Behavior:**
- Bundles `profiles.json`, all managed keypairs, and `known_hosts` entries into a single `.tar.gz`
- Archive is platform-agnostic — importable on Windows, WSL, or Linux

#### `essh import ARCHIVE`

Import profiles and keys from an export archive.

**Arguments:**
- `ARCHIVE` — Path to a `.tar.gz` export file

**Behavior:**
- Merges imported profiles with existing ones (refuses to overwrite by default)
- Installs keypairs and merges `known_hosts` entries
- `--force` flag to overwrite conflicts

#### `essh scp [SCP_OPTIONS...] SOURCE DEST`

Copy files using `scp` with saved profile names. Uses `NAME:path` instead of `user@host:path`.

**Arguments:**
- `SOURCE` — Source path, can be `NAME:path` (remote) or a local path
- `DEST` — Destination path, can be `NAME:path` (remote) or a local path

**Options:**
- All standard scp options are passed through (e.g. `-r`, `-P`, `-C`, `-3`)

**Behavior:**
- Resolves `NAME:path` to the saved profile's `user@host:path`
- Applies the profile's identity key (`-i`) and port (`-P`) automatically
- If the profile has default keys mode (empty `key_path`), no `-i` flag is passed
- In agent mode (non-TTY): requires authorization per host, same semaphore as `essh connect`

**Examples:**
```
essh scp my-server:/var/log/app.log ./logs/
essh scp ./deploy.sh my-server:/home/ubuntu/
essh scp -r my-server:/etc/nginx/ ./backup/
```

**Edge Cases:**
- Multiple remote hosts with different keys: warning issued, scp's single `-i` applies globally
- Profile not found: error with known names list, exit 1
- `scp` binary not found: error with install hint, exit 1

#### `essh rsync [RSYNC_OPTIONS...] SOURCE DEST`

Sync files using `rsync` with saved profile names. Uses `NAME:path` instead of `user@host:path`.

**Arguments:**
- `SOURCE` — Source path, can be `NAME:path` (remote) or a local path
- `DEST` — Destination path, can be `NAME:path` (remote) or a local path

**Options:**
- All standard rsync options are passed through (e.g. `-avz`, `--progress`, `--delete`)

**Behavior:**
- Resolves `NAME:path` to the saved profile's `user@host:path`
- Builds the SSH transport command (`-e "ssh -i KEY -p PORT"`) from the profile's settings
- If the profile has default keys mode (empty `key_path`), the transport command uses plain `ssh`
- In agent mode (non-TTY): requires authorization per host, same semaphore as `essh connect`

**Examples:**
```
essh rsync -avz my-server:/var/www/ ./www-backup/
essh rsync --progress ./build/ my-server:/srv/app/
```

**Edge Cases:**
- Multiple remote hosts with different keys: only the first profile's key is used for the SSH transport, warning issued
- Profile not found: error with known names list, exit 1
- `rsync` binary not found: error with install hint, exit 1

### Command Filters

#### `essh filter add|rm|list|clear TARGET PATTERN [--action deny|ask|allow]`

Manage command filter rules for SSH connections. Filters use wildcard matching
to allow, ask, or deny specific commands.

**Target:**
- `global` — Applies to all profiles (stored in `~/.essh/filters.json`)
- `{name}` — Applies to a specific profile (stored in the profile's `filters` key)

**Actions:**
- `--action deny` (default) — Hard block. Command is rejected immediately with a message.
- `--action ask` — Blocks until the command is authorized (same mechanism as `essh authorize`).
- `--action allow` — Passes through silently without prompting.

**Pattern matching** (ported from opencode/anomalyco):
- `*` matches any sequence of characters
- `?` matches any single character
- Trailing ` *` (space then star) makes arguments optional — `rm *` matches `rm` AND `rm -rf /`
- Patterns are case-insensitive

**Last-match-wins:** More specific rules placed after general ones override them.
Per-profile rules are evaluated after global rules and take precedence.

**Examples:**
```
essh filter add global "rm *" --action ask
essh filter add global "rm -rf *" --action deny --message "No recursive force remove"
essh filter add global "sudo *" --action ask
essh filter add prod-web "sudo systemctl restart nginx" --action allow
essh filter add prod-web "docker *" --action ask
essh filter list global
essh filter list prod-web
essh filter rm global "rm *"
essh filter clear prod-web
```

**Behavior in connect:**
1. If no filters match, the existing authorization gate applies (non-TTY always gates).
2. If a `deny` rule matches: hard block, exit 1, even after authorization.
3. If an `ask` rule matches: creates a pending request showing the exact command.
4. If an `allow` rule matches: command runs without additional prompting.

### Authorization Model

The authorization gate is a **filesystem semaphore** — no daemon, no IPC.

1. Agent runs `essh lenny "ls"` without a TTY
2. A request file `~/.essh/requests/lenny.pending` is created
3. The agent process blocks, polling the file
4. User runs `essh lenny authorize` → deletes the pending file
5. Agent sees the file is gone, proceeds with SSH
6. If not authorized within 30 seconds, the request expires and agent exits with error

**What it's NOT:** A real security tool. If someone has privileges on your machine, it's already over. This is guardrails + convenience — stops accidental agent foot-guns.

### Storage

| Location | Contents |
|----------|----------|
| `~/.essh/profiles.json` | Host profiles (name, user, host, port, key path) |
| `~/.essh/keys/{name}/` | Per-host ed25519 keypairs |
| `~/.essh/requests/` | Pending authorization request files |
| `~/.essh/exports/` | Exported portable archives |

### Edge Cases

- Unknown host name: error with list of known names, exit 1
- Request already pending: error with hint to authorize or wait, exit 1
- SSH connection fails: error with exit code
- `essh import` on existing profile: error unless `--force`
- No native SSH found on Windows: error with install hint (OpenSSH Client via Windows Features)
- Key not in ssh-agent: auto-adds before connecting (with passphrase prompt if needed)

### Platform Notes

| Platform | SSH Binary | Notes |
|----------|-----------|-------|
| Windows | `ssh.exe` (native OpenSSH) | Detects via `Get-Command ssh.exe`; falls back to WSL `ssh` |
| WSL/Linux | `/usr/bin/ssh` | Standard OpenSSH |
| macOS | `/usr/bin/ssh` | Standard OpenSSH |

WSL can import exports directly from Windows paths: `essh import /mnt/c/Users/rx/.essh/exports/essh-export.tar.gz`

---

## Tool: tasks — In-Repo Task Management

Static, file-backed task tracking for project work. Lives in `.agents/tasks/` with no database or service, and preserves history across sessions.

**Commands:** `tasks init`, `tasks add`, `tasks list`, `tasks next`, `tasks ready`, `tasks blocked`, `tasks status`, `tasks show`, `tasks take`, `tasks update`, `tasks close`, `tasks history`, `tasks search`, `tasks inbox`.

**Use it for:** capturing work, tracking dependencies, finding the next unblocked task, and keeping the archive of completed work visible.

Tasks also carry `notes` and `evidence` as appendable string lists, plus optional identity fields:

- **`claimed`** — who is actively working this task. When non-empty, the task is locked and excluded from `next`/`ready` queues.
- **`createdBy`** — who or what created the task (metadata only).

### Statuses Are Config-Driven

Statuses are not hardcoded — they're defined in the `meta.config` block at the top of `tasks.yaml`. You can rename, reorder, add, or remove statuses freely. The system uses a few config keys to drive queue behavior:

| Key | Purpose | Default |
|---|---|---|
| `statuses` | Complete list of valid status names | `[todo, in-progress, done, ...]` |
| `default_status` | Where `tasks add` puts new tasks | `todo` |
| `ready_status` | Which column `tasks next` / `tasks ready` pull from | `todo` |
| `active_status` | Where `tasks take` / `tasks claim` move the card | `in-progress` |
| `close_status` | Which status `tasks close` sets before archiving | `done` |

Example:
```yaml
# .agents/tasks/tasks.yaml
meta:
  config:
    statuses: [backlog, todo, in-progress, review, done, cancelled]
    default_status: todo
    ready_status: todo
    active_status: in-progress
    close_status: done
```

If no config exists, defaults are injected silently — existing files work untouched.

### How Status Drives Behavior

- **`tasks add`** → status = `default_status`, not claimed
- **`tasks take` / `tasks claim`** → claimed = `"agent"`, moves to `active_status`
- **`tasks next` / `tasks ready`** → only shows tasks matching `ready_status`, unclaimed, unblocked
- **`tasks close`** → sets `close_status`, then archives to `closed.yaml`
- **Overview (now/ready/waiting/parked)** → driven by claimed flag + config keys + dependency blocking

A status is just a column name. Move the card, change the status. Everything else (blocked, blocked-by-deps, etc.) is orthogonal.

---

## Tool: tmx — Tmux/Psmux Session Control

Cross-platform tmux/psmux wrapper for agent-driven terminal session management.

**Installation:** Ships with agent-sommelier-cli. No extra dependencies.

### Prerequisites

| Platform | Tool | Install |
|----------|------|---------|
| Linux | tmux | `apt install tmux` |
| macOS | tmux | `brew install tmux` |
| Windows | psmux | `tmx install` |

### Commands

#### `tmx install`

Ensure tmux or psmux is available on this system. On Windows, automatically attempts installation via winget, scoop, choco, or cargo.

#### `tmx create NAME [CMD]`

Create a new detached session with generous scrollback (10000 lines).

**Arguments:**
- `NAME` — Session name (required)
- `CMD` — Optional initial command (e.g. `ssh user@host`)

**Output:**
```
Created session: mysession
```

#### `tmx rm NAME`

Kill a session.

**Arguments:**
- `NAME` — Session name

**Output:**
```
Killed session: mysession
```

#### `tmx sk NAME "CMD"`

Send keys to a session (fire and forget).

**Arguments:**
- `NAME` — Session name
- `CMD` — Command string to type and execute

**Behavior:**
- Types the command and presses Enter
- Returns immediately without waiting or reading output

#### `tmx r NAME`

Read session output (full scrollback buffer).

**Arguments:**
- `NAME` — Session name

**Behavior:**
- Captures the entire scrollback (up to 10000 lines)
- Prints to stdout

#### `tmx run NAME "CMD" [--timeout N]`

Send keys, wait, and read output — the primary agent workflow.

**Arguments:**
- `NAME` — Session name
- `CMD` — Command to execute

**Options:**
- `--timeout`, `-t` — Seconds to wait before reading output (default: 5)

**Behavior:**
1. Sends the command and presses Enter
2. Waits N seconds (default 5)
3. Captures and prints the scrollback output

**Examples:**
```bash
tmx create myserver "ssh admin@prod.example.com"
tmx run myserver "hostname"
tmx run myserver "tail -100 /var/log/syslog" --timeout 10
tmx sk myserver "long-running-task"
tmx r myserver
```

#### `tmx list [--json]`

List all sessions.

**Options:**
- `--json` — Output as JSON

**Output (human-readable):**
- Rich table with columns: Name, Windows, Status

**Output (JSON):**
```json
[
    {
        "name": "myserver",
        "windows": 1,
        "status": "detached"
    }
]
```

#### `tmx manager`

Interactive session picker (human-friendly TUI). Browse tmux sessions with arrow keys, attach, kill, or create new ones. After detaching from a session (Ctrl+B then d), returns to the picker.

**Requires:** An interactive terminal (TTY).

**Behavior:**

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate sessions |
| `Enter` | Attach to selected session or create new (on "+ new session" row) |
| `k` | Kill selected session |
| `n` | Create new session (detached, no attach) |
| `q` | Quit |

When attaching, the session name is prompted. Leaving it empty auto-generates a name from the current working directory (e.g. `myproject-fox`).

**Example flow:**
```bash
$ tmx manager
  tmx  session picker
    +  new session
    •  server-prod    1w
    •  api-dev        2w
    @  db-admin       1w
```

### Platform Behavior

| Platform | Backend | Notes |
|----------|---------|-------|
| Windows | psmux | First-class support via `tmx install` |
| Windows | tmux (WSL) | Falls back if psmux unavailable |
| macOS | tmux | System or Homebrew |
| Linux | tmux | System package manager |

### Edge Cases

- No tmux/psmux found: `tmx install` guides installation
- Session not found: error message with name, exit 1
- Session already exists on create: error message, exit 1
- List with no sessions: "No sessions." message (exit 0)
- Empty scrollback on `r` or `run`: prints nothing

---

## Tool: amun — Deep Thinking LLM Question-Asker

Send complex questions to a configurable LLM endpoint and stream the response (including reasoning from thinking models like OpenAI o3).

### Configuration

Config lives at `~/.amun/config.toml`:

```toml
endpoint = "https://api.openai.com/v1/chat/completions"
model = "o3-4h"
api_key = "$AMUN_API_KEY"

[body]
reasoning_effort = "high"
```

- `endpoint` — Any OpenAI-compatible `/v1/chat/completions` URL
- `model` — Model name to use
- `api_key` — Plain text or `$ENV_VAR` reference; prefixed with `$` to resolve from the environment
- `[body]` — Extra JSON fields merged into the request body (e.g. `reasoning_effort`, `max_completion_tokens`)

### Commands

#### `amun init`

Create a default config file at `~/.amun/config.toml`.

```
$ amun init
Config created at C:\Users\you\.amun\config.toml
Edit this file with your endpoint, model, and API key.
```

#### `amun ask "QUESTION"`

Send a question to the configured LLM.

**Arguments:**
- `QUESTION` — The question to ask (positional, required)

**Options:**
- `--system`, `-s` — System prompt (default: *"You are a senior architect and engineer. Think deeply before answering."*)
- `--model`, `-m` — Override the configured model
- `--no-stream` — Disable streaming; collect the full response then print
- `--timeout` — HTTP request timeout in seconds (default: 120)

**Examples:**

```bash
# Simple streaming question
amun ask "What is the complexity of quicksort?"

# With custom system prompt
amun ask "Explain the CAP theorem" --system "You are a distributed systems professor."

# Override model
amun ask "Write a Python decorator" --model "gpt-4o"

# Non-streaming with Markdown rendering
amun ask "Write a detailed comparison" --no-stream
```

**Behavior:**
1. Loads config from `~/.amun/config.toml`
2. Sends an HTTP POST to the configured endpoint
3. **Streaming (default):** Reads SSE events, prints tokens as they arrive. If the model emits `reasoning` or `reasoning_content` fields, they appear in dim yellow before the final answer.
4. **Non-streaming (`--no-stream`):** Collects the full response and renders the answer with `rich.markdown.Markdown`.
5. On HTTP or connection errors, displays a clear message and exits with code 1.

**Edge Cases:**
- Config not found: prompts user to run `amun init`
- Environment variable not set: clear error message
- API error: prints HTTP status and response body
- Connection timeout: error message with timeout value
- Malformed SSE lines in stream: silently skipped

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

### Entry Points

| Command | Purpose |
|---|---|
| `skill-store` | Click CLI — interactive browsing, admin (init, sync, pin, groups) |
| `skill-store-mcp` | FastMCP server (stdio) — agent-facing tools: `search_skills`, `get_skill`, `preview_skill`, `list_skills` |

The MCP server is an optional extra (`agent-sommelier-cli[mcp-srv]`) and is configured in the agent's MCP servers list (e.g. `opencode.json`). It exposes a read-only subset of the skill store for agent consumption.

---

## Skill: tmux — Terminal Session Control

Control tmux/psmux sessions programmatically for SSH, REPLs, and parallel processes. Cross-platform: tmux on Linux/macOS, psmux on Windows.

**Location:** `skills/tmux/`

**Primary CLI tool:** `tmx` — ships with agent-sommelier-cli (see `## Tool: tmx` above).

### Commands

| Command | Description |
|---------|-------------|
| `tmx install` | Ensure tmux/psmux is available (auto-install on Windows) |
| `tmx create <name> [cmd]` | Create session, optionally run init cmd |
| `tmx rm <name>` | Kill session |
| `tmx sk <name> "<cmd>"` | Send keys (fire and forget) |
| `tmx r <name>` | Read output (full scrollback) |
| `tmx run <name> "<cmd>" [--timeout N]` | Send + wait + read |
| `tmx list [--json]` | List all sessions |

### Quick Start

```bash
# Create an SSH session
tmx create server "ssh user@myserver.com"

# Run commands, get output
tmx run server "hostname"
tmx run server "tail -100 /var/log/syslog" --timeout 10

# Fire and forget
tmx sk server "long-running-build.sh"

# Read output later
tmx r server

# List and clean up
tmx list
tmx rm server
```

### Fallback Scripts

Shell scripts at `skills/tmux/scripts/tmx.sh` (bash) and `tmx.ps1` (PowerShell) are available as fallbacks when the Python CLI tool is not installed.

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
- `mcp >= 1.0.0` — MCP server runtime for skill-store (`[mcp-srv]` extra)

---

## Exit Codes

All tools follow this convention:
- `0` — Success
- `1` — Error (invalid input, tool not found, operation failed)

---

## Design Principles

1. **Minimal state** — All tools are CLI invocations; crony uses a lightweight user-daemon for scheduling
2. **No database** — JSON files for persistence
3. **No configuration** — Sensible defaults, auto-detection
4. **Pipe-friendly** — All tools accept stdin where appropriate
5. **Cross-platform** — Same interface on Windows, macOS, Linux
