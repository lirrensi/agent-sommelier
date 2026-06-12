# FILE: src/agent_sommelier/crony/cli.py
# PURPOSE: Click CLI entry point for crony — schedule parsing, job CRUD, daemon
#          management commands, and migration helpers.
# OWNS: CLI surface (add/list/rm/run/logs/daemon), schedule parsing, job enrichment,
#       OS scheduler migration (scan_os_scheduler / sync_jobs).
# EXPORTS: main (Click group)
# DOCS: docs/product.md (crony section), docs/arch.md (crony component)

import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import click

from agent_sommelier import __version__
from agent_sommelier.crony.daemon import (
    CRONY_DIR,
    JOBS_FILE,
    LOG_DIR,
    is_daemon_alive,
    load_jobs,
    save_jobs,
    start_daemon,
    status_daemon,
    stop_daemon,
)

# Optional dependencies
try:
    import dateparser  # noqa: F401 — used by parse_schedule, calculate_once_next_run
    from croniter import croniter  # noqa: F401 — used by calculate_recurring_next_run
except ImportError:
    click.echo("Error: crony requires extra dependencies.", err=True)
    click.echo("Install with one of:", err=True)
    click.echo("  pip install agent-sommelier-cli[crony]", err=True)
    click.echo("  uv tool install agent-sommelier-cli[crony]", err=True)
    click.echo(
        "  uv tool install 'agent-sommelier-cli[crony]@git+https://github.com/lirrensi/agent-sommelier'",
        err=True,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_crony_dir() -> None:
    """Ensure crony storage directories exist."""
    CRONY_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp safely."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Schedule parsing
# ---------------------------------------------------------------------------


def interval_to_cron(interval: str) -> str:
    """Convert interval string to cron expression."""
    interval = interval.strip().lower()

    mappings: dict[str, str] = {
        "1m": "*/1 * * * *",
        "5m": "*/5 * * * *",
        "10m": "*/10 * * * *",
        "15m": "*/15 * * * *",
        "30m": "*/30 * * * *",
        "1h": "0 * * * *",
        "2h": "0 */2 * * *",
        "6h": "0 */6 * * *",
        "12h": "0 */12 * * *",
        "24h": "0 0 * * *",
        "1d": "0 0 * * *",
        "1w": "0 0 * * 0",
    }

    if interval in mappings:
        return mappings[interval]

    import re

    match = re.match(r"(\d+)([mhdw])", interval)
    if match:
        num, unit = int(match.group(1)), match.group(2)
        if unit == "m":
            return f"*/{num} * * * *"
        elif unit == "h":
            return f"0 */{num} * * *"
        elif unit == "d":
            return f"0 0 */{num} * *"
        elif unit == "w":
            return f"0 0 * * 0"

    day_mappings: dict[str, str] = {
        "monday": "0 0 * * 1",
        "tuesday": "0 0 * * 2",
        "wednesday": "0 0 * * 3",
        "thursday": "0 0 * * 4",
        "friday": "0 0 * * 5",
        "saturday": "0 0 * * 6",
        "sunday": "0 0 * * 0",
        "weekday": "0 0 * * 1-5",
        "weekend": "0 0 * * 0,6",
    }

    if interval in day_mappings:
        return day_mappings[interval]

    return interval


def parse_schedule(schedule: str) -> dict:
    """Parse natural language schedule into structured format."""
    schedule = schedule.strip().lower()

    recurring_prefixes = ["every ", "each "]
    is_recurring = any(schedule.startswith(p) for p in recurring_prefixes)

    if is_recurring:
        interval_part = schedule.replace("every ", "").replace("each ", "")
        cron_expr = interval_to_cron(interval_part)
        return {
            "type": "recurring",
            "interval": interval_part,
            "cron_expr": cron_expr,
            "next_run": None,
        }
    else:
        dt = dateparser.parse(
            schedule,
            settings={"PREFER_DATES_FROM": "future", "TIMEZONE": "local"},
        )
        if not dt:
            raise ValueError(f"Could not parse schedule: {schedule}")
        return {
            "type": "once",
            "schedule": schedule,
            "next_run": dt.isoformat(),
        }


# ---------------------------------------------------------------------------
# Job enrichment (for display)
# ---------------------------------------------------------------------------


def calculate_once_next_run(job: dict) -> str | None:
    """Normalize the next run value for a one-off job."""
    next_run = parse_iso_timestamp(job.get("next_run"))
    if next_run:
        return next_run.isoformat()
    schedule = job.get("schedule")
    if not schedule:
        return None
    parsed = dateparser.parse(
        schedule,
        settings={"PREFER_DATES_FROM": "future", "TIMEZONE": "local"},
    )
    return parsed.isoformat() if parsed else None


def calculate_recurring_next_run(job: dict) -> str | None:
    """Calculate the next occurrence for a recurring job."""
    cron_expr = job.get("cron_expr")
    if not cron_expr:
        return None
    created_at = parse_iso_timestamp(job.get("created_at"))
    base_time = datetime.now(created_at.tzinfo) if created_at else datetime.now()
    try:
        next_run = croniter(cron_expr, base_time).get_next(datetime)
    except (ValueError, TypeError, KeyError):
        return None
    return next_run.isoformat()


def enrich_job(job: dict) -> dict:
    """Return a copy of a job with computed next_run."""
    enriched = dict(job)
    if enriched.get("type") == "recurring":
        enriched["next_run"] = calculate_recurring_next_run(enriched)
    else:
        enriched["next_run"] = calculate_once_next_run(enriched)
    return enriched


def enrich_jobs(jobs: dict) -> dict:
    """Return a copy of jobs enriched with computed next_run."""
    return {name: enrich_job(job) for name, job in jobs.items()}


def format_display_timestamp(value: str | None) -> str:
    """Format a timestamp for table output."""
    timestamp = parse_iso_timestamp(value)
    if not timestamp:
        return "unknown"
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------


def add_job(
    name: str,
    schedule: str,
    cmd: str,
    cron_expr: str | None = None,
    force: bool = False,
) -> dict:
    """Add a new cron job.  No longer registers with OS scheduler."""
    jobs = load_jobs()

    if name in jobs:
        if not force:
            raise ValueError(
                f"Job '{name}' already exists. Use 'crony rm {name}' first."
            )
        unregister_job(jobs[name])

    if cron_expr:
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            raise ValueError(
                f"Invalid cron expression: {cron_expr!r}. "
                "Expected 5 space-separated fields."
            )
        parsed = {
            "type": "recurring",
            "interval": cron_expr,
            "cron_expr": cron_expr,
            "next_run": None,
        }
    else:
        parsed = parse_schedule(schedule)

    job = {
        "name": name,
        "cmd": cmd,
        "created_at": datetime.now().isoformat(),
        "cwd": os.getcwd(),
        **parsed,
    }

    jobs[name] = job
    save_jobs(jobs)

    # Daemon reads jobs.json directly — no OS registration needed.
    return job


def remove_job(name: str) -> bool:
    """Remove a cron job.  Cleans up legacy OS scheduler entries if present."""
    jobs = load_jobs()
    if name not in jobs:
        return False

    job = jobs.pop(name)

    # Best-effort OS cleanup (legacy crontab/schtasks entries)
    unregister_job(job)

    save_jobs(jobs)
    return True


# ---------------------------------------------------------------------------
# OS scheduler stubs — no-ops (daemon is the sole executor)
# ---------------------------------------------------------------------------


def register_job(job: dict) -> None:  # noqa: ARG001
    """Daemon reads jobs.json directly; no OS registration needed."""
    pass


def register_job_crontab(job: dict) -> None:  # noqa: ARG001
    """Stub: daemon handles scheduling."""
    pass


def register_job_at(job: dict) -> None:  # noqa: ARG001
    """Stub: daemon handles scheduling."""
    pass


def unregister_job(job: dict) -> None:
    """Clean up legacy OS scheduler entries if present (best-effort)."""
    system = platform.system()
    name = job.get("name", "")

    if system in ("Linux", "Darwin"):
        try:
            result = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True
            )
            current = result.stdout if result.returncode == 0 else ""
            lines = [l for l in current.split("\n") if f"CRONY:{name}" not in l]
            new_cron = "\n".join(lines)
            subprocess.run(
                ["crontab", "-"],
                input=new_cron,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass

    elif system == "Windows":
        task_name = f"CRONY_{name}"
        subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True,
        )
        # Clean up legacy .bat wrapper
        bat_path = CRONY_DIR / "scripts" / f"{name}.bat"
        try:
            bat_path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Migration: scanning legacy OS scheduler entries
# ---------------------------------------------------------------------------


def scan_os_scheduler() -> dict:
    """Scan OS scheduler for legacy CRONY-marked jobs.

    Used by `crony list --sync` to import orphaned entries into jobs.json.
    """
    system = platform.system()
    found: dict = {}

    if system in ("Linux", "Darwin"):
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True
        )
        if result.returncode == 0:
            import re

            for line in result.stdout.split("\n"):
                if "# CRONY:" in line:
                    match = re.search(r"# CRONY:(\w+)", line)
                    if match:
                        name = match.group(1)
                        parts = line.split()
                        if len(parts) >= 6:
                            cron_expr = " ".join(parts[:5])
                            cmd = (
                                " ".join(parts[5:])
                                .split("# CRONY:")[0]
                                .strip()
                            )
                            found[name] = {
                                "name": name,
                                "cmd": cmd,
                                "cron_expr": cron_expr,
                                "type": "recurring",
                                "recovered": True,
                            }

    elif system == "Windows":
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "LIST", "/V"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            import re

            for match in re.finditer(
                r"TaskName:\s+(.+\\)?CRONY_(\w+)", result.stdout
            ):
                name = match.group(2)
                found[name] = {
                    "name": name,
                    "type": "recurring",
                    "recovered": True,
                }

    return found


def sync_jobs() -> dict:
    """Reconcile jobs.json with legacy OS scheduler entries.

    Finds orphaned tasks in OS scheduler and adds them to the index.
    Does NOT re-register — the daemon handles all scheduling.
    """
    stored = load_jobs()
    os_jobs = scan_os_scheduler()
    changed = False

    for name, job in os_jobs.items():
        if name not in stored:
            stored[name] = job
            changed = True

    if changed:
        save_jobs(stored)

    return stored


# ---------------------------------------------------------------------------
# Job execution (immediate run)
# ---------------------------------------------------------------------------


def run_job(name: str) -> bool:
    """Run a job immediately (manual trigger).

    Captures stdout/stderr to the job's log file, writes header/footer lines,
    and waits for completion before returning.
    """
    jobs = load_jobs()
    if name not in jobs:
        return False

    cmd = jobs[name]["cmd"]
    cwd = jobs[name].get("cwd") or os.getcwd()

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
        start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"--- crony run: {start_ts} (PID {proc.pid}) ---\n"
        log_fh.write(header)
        log_fh.flush()

        proc.wait()

        code_str = str(proc.returncode) if proc.returncode is not None else "?"
        end_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        footer = f"--- exit: {code_str} at {end_ts} ---\n"
        log_fh.write(footer)

    return True


def get_job_logs(name: str) -> str | None:
    """Get logs for a job."""
    log_file = LOG_DIR / f"{name}.log"
    if log_file.exists():
        return log_file.read_text(encoding="utf-8")
    return None


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(__version__, prog_name="crony")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Cron job manager with natural language scheduling."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


def _log(ctx: click.Context, message: str) -> None:
    """Print a timestamped verbose log line to stderr."""
    if ctx.obj.get("verbose"):
        click.echo(
            f"[{datetime.now().strftime('%H:%M:%S')}] {message}", err=True
        )


# ---- add ----


@main.command()
@click.argument("name")
@click.argument("schedule")
@click.argument("cmd")
@click.option(
    "--cron",
    is_flag=True,
    help="Treat schedule as a raw cron expression (5 fields)",
)
@click.option(
    "--force", is_flag=True, help="Overwrite existing job with the same name"
)
@click.pass_context
def add(
    ctx: click.Context,
    name: str,
    schedule: str,
    cmd: str,
    cron: bool,
    force: bool,
) -> None:
    """Add a new cron job.

    NAME: Job name (unique identifier)

    SCHEDULE: Natural language schedule (e.g., "in 5m", "every 1h", "at
    15:30") or a raw cron expression when --cron is used (e.g., "*/5 * * *
    *")

    CMD: Command to run
    """
    _log(ctx, f"add: {name} — schedule: {schedule}")
    try:
        if cron:
            job = add_job(name, schedule, cmd, cron_expr=schedule, force=force)
            click.echo(f"Added job: {name}")
            click.echo(f"  Schedule: {schedule} (recurring, raw cron)")
        else:
            job = add_job(name, schedule, cmd, force=force)
            click.echo(f"Added job: {name}")
            click.echo(f"  Schedule: {schedule} ({job['type']})")
            if job.get("cron_expr"):
                click.echo(f"  Cron: {job['cron_expr']}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Auto-start daemon if not already running
    newly_started = start_daemon(quiet=True)
    if newly_started:
        click.echo("Scheduler started.")


# ---- list ----


@main.command("list")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.option(
    "--sync", is_flag=True, help="Force sync with OS scheduler (migration)"
)
@click.pass_context
def list_cmd(
    ctx: click.Context, json_output: bool, sync: bool
) -> None:
    """List all cron jobs.

    Use --sync to import orphaned jobs from the legacy OS scheduler.
    """
    _log(ctx, "list")
    jobs = enrich_jobs(sync_jobs() if sync else load_jobs())

    if json_output:
        click.echo(json.dumps(jobs, indent=2))
    else:
        if not jobs:
            click.echo("No jobs found.")
            return

        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="Crony Jobs")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="yellow")
        table.add_column("Schedule", style="green")
        table.add_column("Next Run", style="blue")
        table.add_column("Command", style="white", max_width=40)

        for name, job in jobs.items():
            table.add_row(
                name,
                job.get("type", "?"),
                job.get("interval") or job.get("schedule", "?"),
                format_display_timestamp(job.get("next_run")),
                job.get("cmd", "?")[:40],
            )

        console.print(table)


# ---- rm ----


@main.command()
@click.argument("name")
@click.pass_context
def rm(ctx: click.Context, name: str) -> None:
    """Remove a cron job."""
    _log(ctx, f"rm: {name}")
    if remove_job(name):
        click.echo(f"Removed job: {name}")
    else:
        click.echo(f"Job not found: {name}", err=True)
        sys.exit(1)

    # If no jobs remain, stop the daemon silently
    jobs = load_jobs()
    if not jobs:
        try:
            stop_daemon()
        except Exception:
            pass


# ---- run ----


@main.command()
@click.argument("name")
@click.pass_context
def run(ctx: click.Context, name: str) -> None:
    """Run a job immediately."""
    _log(ctx, f"run: {name}")
    if run_job(name):
        click.echo(f"Triggered job: {name}")
    else:
        click.echo(f"Job not found: {name}", err=True)
        sys.exit(1)


# ---- logs ----


@main.command()
@click.argument("name")
def logs(name: str) -> None:
    """View job logs."""
    log_content = get_job_logs(name)
    if log_content:
        click.echo(log_content)
    else:
        click.echo(f"No logs found for job: {name}")


# ---- daemon ----


@main.group()
def daemon() -> None:
    """Manage the crony scheduler daemon."""
    pass


@daemon.command("status")
def daemon_status() -> None:
    """Check if the scheduler daemon is running."""
    ensure_crony_dir()
    state = status_daemon()
    if state == "running":
        click.echo("Daemon is running.")
    elif state == "stale":
        click.echo("Daemon is stale (lockfile found but process is dead).")
    else:
        click.echo("Daemon is stopped.")


@daemon.command("start")
def daemon_start() -> None:
    """Start the scheduler daemon and register for auto-start."""
    ensure_crony_dir()
    newly_started = start_daemon(quiet=False)
    if newly_started:
        click.echo("Daemon started.")
    else:
        click.echo("Daemon is already running.")


@daemon.command("stop")
def daemon_stop() -> None:
    """Stop the scheduler daemon."""
    ensure_crony_dir()
    if stop_daemon():
        click.echo("Daemon stopped.")
    else:
        click.echo("Daemon is not running.")


@daemon.command("restart")
def daemon_restart() -> None:
    """Restart the scheduler daemon."""
    ensure_crony_dir()
    stop_daemon()
    start_daemon(quiet=False)
    click.echo("Daemon restarted.")


@daemon.command(name="run-loop", hidden=True)
@click.option("--token", default=None, hidden=True)
def daemon_run_loop(token: str | None) -> None:
    """Internal: run the daemon scheduler loop (launched by start_daemon)."""
    from agent_sommelier.crony.daemon import run_daemon_loop

    run_daemon_loop(token)


# ---- completions ----


@main.command(hidden=True)
@click.argument(
    "shell",
    type=click.Choice(["bash", "zsh", "fish", "powershell"]),
    default="bash",
)
@click.pass_context
def completions(ctx: click.Context, shell: str) -> None:
    """Print shell completion setup instructions."""
    tool: str = (
        ctx.parent.info_name
        if ctx.parent is not None and ctx.parent.info_name is not None
        else "crony"
    )
    click.echo(f"# Enable shell completion for {tool}:")
    click.echo("# Add the following to your shell profile:")
    click.echo(f"eval $(_{tool.upper()}_COMPLETE={shell}_source {tool})")


if __name__ == "__main__":
    main()
