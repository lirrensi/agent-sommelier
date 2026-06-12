# FILE: src/agent_sommelier/crony/daemon.py
# PURPOSE: Cross-platform scheduler daemon that reads jobs.json, uses croniter to
#          determine due jobs, spawns them with log capture, and auto-starts on login.
# OWNS: Daemon lifecycle (start/stop/status), scheduler loop, job spawning, lockfile
#       management, platform-specific auto-start registration.
# EXPORTS: is_daemon_alive, start_daemon, stop_daemon, status_daemon,
#          register_autostart, unregister_autostart, run_daemon_loop, spawn_job,
#          load_jobs, save_jobs, CRONY_DIR, JOBS_FILE, LOCKFILE, LOG_DIR
# DOCS: docs/arch.md (Scheduler Daemon section), docs/product.md (crony daemon commands)

import json
import os
import platform
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psutil
from croniter import croniter

# os.getuid is Unix-only; guarded by platform check at call sites.
_GETUID = getattr(os, "getuid", lambda: 0)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CRONY_DIR = Path.home() / ".crony"
JOBS_FILE = CRONY_DIR / "jobs.json"
LOCKFILE = CRONY_DIR / "daemon.lock"
LOG_DIR = CRONY_DIR / "logs"

# ---------------------------------------------------------------------------
# Job I/O (shared with CLI)
# ---------------------------------------------------------------------------


def load_jobs() -> dict:
    """Load all jobs from storage."""
    CRONY_DIR.mkdir(parents=True, exist_ok=True)
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_jobs(jobs: dict) -> None:
    """Save jobs to storage atomically.

    Writes to a .tmp file first, then os.replace for atomicity.
    Retries once on OSError (target locked on Windows).
    """
    CRONY_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = JOBS_FILE.with_suffix(".tmp")
    payload = json.dumps(jobs, indent=2, ensure_ascii=False)

    for attempt in range(2):
        try:
            tmp_file.write_text(payload, encoding="utf-8")
            os.replace(tmp_file, JOBS_FILE)
            return
        except OSError:
            if attempt == 0:
                time.sleep(0.1)
            else:
                raise


# ---------------------------------------------------------------------------
# Lockfile (process identity)
# ---------------------------------------------------------------------------


def _write_lockfile(pid: int, started_at: str, token: str) -> None:
    """Write daemon lockfile as JSON."""
    CRONY_DIR.mkdir(parents=True, exist_ok=True)
    LOCKFILE.write_text(
        json.dumps(
            {"pid": pid, "started_at": started_at, "token": token},
            indent=2,
        ),
        encoding="utf-8",
    )


def _read_lockfile() -> dict | None:
    """Read lockfile; return dict or None if missing/corrupt."""
    if not LOCKFILE.exists():
        return None
    try:
        return json.loads(LOCKFILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _cleanup_lockfile() -> None:
    """Remove stale lockfile."""
    try:
        LOCKFILE.unlink(missing_ok=True)
    except OSError:
        pass


def _ensure_aware(dt_str: str | None) -> datetime | None:
    """Parse an ISO datetime string and ensure it is timezone-aware.

    Naive datetimes are treated as UTC.
    """
    if dt_str is None:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _find_daemon_process() -> psutil.Process | None:
    """Scan running processes for a crony daemon instance.

    Looks for a process whose command line contains both ``crony`` and
    ``run-loop`` (or ``run_daemon_loop``) — the actual daemon entry
    points.  This avoids false positives from ``crony daemon status``,
    ``crony daemon start``, etc.
    """
    daemon_markers = ('run-loop', 'run_daemon_loop')
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            cmdline_flat = ' '.join(cmdline).lower()
            if any(m in cmdline_flat for m in daemon_markers):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def is_daemon_alive() -> bool:
    """Check whether the daemon process is genuinely alive.

    Validates three things via psutil:
      1. PID from lockfile points to a running process.
      2. Process name contains "crony" or "python".
      3. The token from the lockfile appears in the process command line.

    If any check fails the lockfile is considered stale and removed.
    """
    lock = _read_lockfile()
    if not lock:
        return _find_daemon_process() is not None

    pid = lock.get("pid")
    token = lock.get("token")
    if not pid or not token:
        _cleanup_lockfile()
        return False

    # 1. PID is running
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        _cleanup_lockfile()
        return False

    # 2. Process name check (be lenient — the binary may be "python" on Windows)
    try:
        pname = proc.name().lower()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        _cleanup_lockfile()
        return False

    if "crony" not in pname and "python" not in pname:
        _cleanup_lockfile()
        return False

    # 3. Token must appear in the command line
    try:
        cmdline = proc.cmdline()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        _cleanup_lockfile()
        return False

    cmdline_flat = " ".join(cmdline)
    if token not in cmdline_flat:
        _cleanup_lockfile()
        return False

    return True


# ---------------------------------------------------------------------------
# Auto-start registration (platform-specific)
# ---------------------------------------------------------------------------


def register_autostart() -> None:
    """Register the daemon to start on user login.

    Windows : schtasks /Create ... /SC ONLOGON (user-level, no admin)
    Linux   : systemd user service at ~/.config/systemd/user/
    macOS   : LaunchAgent plist at ~/Library/LaunchAgents/
    """
    system = platform.system()

    if system == "Windows":
        subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                "CRONY_DAEMON",
                "/TR",
                "crony daemon run-loop",
                "/SC",
                "ONLOGON",
                "/F",
            ],
            capture_output=True,
        )

    elif system == "Linux":
        service_dir = Path.home() / ".config" / "systemd" / "user"
        service_dir.mkdir(parents=True, exist_ok=True)
        service_path = service_dir / "crony-daemon.service"
        service_path.write_text(
            "[Unit]\n"
            "Description=Crony Scheduler Daemon\n"
            "After=network.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            "ExecStart=crony daemon run-loop\n"
            "Restart=on-failure\n"
            "RestartSec=10\n\n"
            "[Install]\n"
            "WantedBy=default.target\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], capture_output=True
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "crony-daemon.service"],
            capture_output=True,
        )

    elif system == "Darwin":
        launch_dir = Path.home() / "Library" / "LaunchAgents"
        launch_dir.mkdir(parents=True, exist_ok=True)
        plist_path = launch_dir / "com.crony.daemon.plist"
        plist_path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            "    <key>Label</key>\n"
            "    <string>com.crony.daemon</string>\n"
            "    <key>ProgramArguments</key>\n"
            "    <array>\n"
            "        <string>crony</string>\n"
            "        <string>daemon</string>\n"
            "        <string>run-loop</string>\n"
            "    </array>\n"
            "    <key>RunAtLoad</key>\n"
            "    <true/>\n"
            "    <key>KeepAlive</key>\n"
            "    <true/>\n"
            "</dict>\n"
            "</plist>\n",
            encoding="utf-8",
        )
        uid = _GETUID()
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
            capture_output=True,
        )


def unregister_autostart() -> None:
    """Remove the daemon from login auto-start (reverse of register_autostart)."""
    system = platform.system()

    if system == "Windows":
        subprocess.run(
            ["schtasks", "/Delete", "/TN", "CRONY_DAEMON", "/F"],
            capture_output=True,
        )

    elif system == "Linux":
        service_path = (
            Path.home() / ".config" / "systemd" / "user" / "crony-daemon.service"
        )
        subprocess.run(
            ["systemctl", "--user", "disable", "crony-daemon.service"],
            capture_output=True,
        )
        try:
            service_path.unlink(missing_ok=True)
        except OSError:
            pass
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], capture_output=True
        )

    elif system == "Darwin":
        plist_path = (
            Path.home() / "Library" / "LaunchAgents" / "com.crony.daemon.plist"
        )
        uid = _GETUID()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
            capture_output=True,
        )
        try:
            plist_path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Job spawning
# ---------------------------------------------------------------------------


def spawn_job(job: dict) -> subprocess.Popen:
    """Spawn a job process with stdout/stderr captured to a log file.

    Returns the Popen object.  Caller is responsible for waiting.
    """
    name = job.get("name", "unknown")
    cmd = job.get("cmd", "")
    cwd = job.get("cwd") or os.getcwd()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"

    popen_kwargs: dict = {
        "cwd": cwd,
        "shell": True,
    }

    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    with open(log_path, "a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            **popen_kwargs,
        )

        # Write header AFTER Popen so we capture the spawned job's real PID
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"--- crony run: {timestamp} (PID {proc.pid}) ---\n"
        log_fh.write(header)
        log_fh.flush()

    return proc


def _write_job_footer(name: str, exit_code: int | None) -> None:
    """Write completion footer to a job's log file."""
    log_path = LOG_DIR / f"{name}.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    code_str = str(exit_code) if exit_code is not None else "?"
    footer = f"--- exit: {code_str} at {timestamp} ---\n"
    try:
        with open(log_path, "a", encoding="utf-8") as log_fh:
            log_fh.write(footer)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------


def run_daemon_loop(token: str | None = None) -> None:
    """Main daemon scheduling loop.

    Reads jobs.json, calculates next fire times with croniter, sleeps until
    the next job is due (or 60 s max), spawns due jobs, and repeats.

    Exits cleanly when there are no jobs left.
    """
    generated_token = token or uuid.uuid4().hex
    _write_lockfile(
        os.getpid(),
        datetime.now(timezone.utc).isoformat(),
        generated_token,
    )

    while True:
        jobs = load_jobs()
        if not jobs:
            break  # no jobs → exit

        now = datetime.now(timezone.utc)

        # Determine which jobs are due
        due: list[dict] = []
        next_times: list[datetime] = []

        for _name, job in jobs.items():
            next_run = _compute_next_run(job, now)
            if next_run is None:
                continue
            next_times.append(next_run)
            if next_run <= now:
                due.append(job)

        # Spawn due jobs
        for job in due:
            proc = spawn_job(job)
            name = job.get("name", "unknown")

            # For one-off jobs: mark completed and wait briefly
            if job.get("type") == "once":
                proc.wait()
                _write_job_footer(name, proc.returncode)
                job["status"] = "completed"
                job["completed_at"] = datetime.now(timezone.utc).isoformat()
                jobs[name] = job
            else:
                # Recurring: fire-and-forget — footer written when daemon
                # wakes up later (best-effort).  Store the PID for tracking.
                job["last_pid"] = proc.pid
                job["last_run_at"] = datetime.now(timezone.utc).isoformat()
                jobs[name] = job

        # Persist any updates (completed one-off jobs)
        if due:
            try:
                save_jobs(jobs)
            except OSError:
                pass  # best-effort save

        # Re-read jobs (in case CLI added/removed while we were spawning)
        jobs = load_jobs()
        if not jobs:
            break

        # Calculate sleep duration
        now = datetime.now(timezone.utc)
        next_times = []
        for _name, job in jobs.items():
            nt = _compute_next_run(job, now)
            if nt:
                next_times.append(nt)

        if next_times:
            earliest = min(next_times)
            sleep_seconds = (earliest - now).total_seconds()
            if sleep_seconds < 0:
                sleep_seconds = 0
            sleep_seconds = min(sleep_seconds, 60)  # cap at 60 s
        else:
            sleep_seconds = 60

        time.sleep(max(sleep_seconds, 0.1))

    # No jobs left — clean exit
    _cleanup_lockfile()


def _compute_next_run(job: dict, now: datetime) -> datetime | None:
    """Compute the next fire time for a job relative to *now*."""
    if job.get("type") == "once":
        if job.get("status") == "completed":
            return None
        # Parse stored next_run and make timezone-aware
        raw = job.get("next_run")
        if raw:
            return _ensure_aware(raw)
        return None

    # Recurring
    cron_expr = job.get("cron_expr")
    if not cron_expr:
        return None

    try:
        # Use now as the reference time
        base = now
        # If we already have a last_run_at, use that for more precision
        last_run_raw = job.get("last_run_at")
        if last_run_raw:
            aware = _ensure_aware(last_run_raw)
            if aware:
                base = aware

        return croniter(cron_expr, base).get_next(datetime)
    except (ValueError, TypeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Daemon lifecycle commands (called from CLI)
# ---------------------------------------------------------------------------


def start_daemon(quiet: bool = False) -> bool:
    """Start the daemon if it is not already running.

    Returns True if the daemon was *newly started* by this call.
    Returns False if it was already running.
    """
    if is_daemon_alive():
        return False

    # Launch the daemon as a detached subprocess
    token = uuid.uuid4().hex

    popen_kwargs: dict = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    subprocess.Popen(
        ["crony", "daemon", "run-loop", "--token", token],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **popen_kwargs,
    )

    # Give the daemon a moment to write its lockfile
    time.sleep(0.3)

    # Register for auto-start on login
    try:
        register_autostart()
    except Exception:
        pass  # best-effort

    return True


def stop_daemon() -> bool:
    """Stop the daemon if it is running.

    Returns True if we sent a termination signal, False if nothing was running.
    """
    lock = _read_lockfile()
    if not lock:
        _cleanup_lockfile()
        return False

    pid = lock.get("pid")
    if not pid:
        _cleanup_lockfile()
        return False

    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        _cleanup_lockfile()
        return False

    # Send termination signal
    try:
        if sys.platform == "win32":
            # Windows: use taskkill
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass

    time.sleep(0.2)
    _cleanup_lockfile()

    # Optionally unregister autostart (silent)
    try:
        unregister_autostart()
    except Exception:
        pass

    return True


def status_daemon() -> str:
    """Return the daemon status: "running", "stopped", or "stale".

    Reads the lockfile BEFORE calling is_daemon_alive() so that a stale
    lockfile can be detected even after is_daemon_alive() cleans it up.
    """
    lock = _read_lockfile()
    if lock and not is_daemon_alive():
        return "stale"
    if is_daemon_alive():
        return "running"
    return "stopped"
