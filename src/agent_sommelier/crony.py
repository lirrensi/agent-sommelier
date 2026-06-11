"""Cron job manager CLI with natural language scheduling.

Usage:
    crony add <name> <schedule> <command>
    crony add <name> "<cron_expr>" <command> --cron
    crony list
    crony rm <name>
    crony run <name>
    crony logs <name>

Schedule formats (natural language):
    in 5m, in 1h, in 2d
    at 15:30, at "2026-03-10 10:00"
    every 1h, every 30m, every 24h
    every monday, every weekday

Raw cron expression (with --cron):
    */5 * * * *    Every 5 minutes
    0 2 * * *      Daily at 2 AM
"""

import os
import sys
import json
import shlex
import shutil
import subprocess
import platform
from pathlib import Path
from datetime import datetime
import click

from agent_sommelier import __version__

# Check for optional dependencies
try:
    import dateparser
    from croniter import croniter
except ImportError:
    click.echo("Error: crony requires extra dependencies.", err=True)
    click.echo("Install with one of:", err=True)
    click.echo("  pip install agent-sommelier-cli[crony]", err=True)
    click.echo("  uv tool install agent-sommelier-cli[crony]", err=True)
    click.echo("  uv tool install 'agent-sommelier-cli[crony]@git+https://github.com/lirrensi/agent-sommelier'", err=True)
    sys.exit(1)

# Job storage directory
CRONY_DIR = Path.home() / ".crony"
JOBS_FILE = CRONY_DIR / "jobs.json"


def ensure_crony_dir():
    """Ensure crony directory exists."""
    CRONY_DIR.mkdir(parents=True, exist_ok=True)


def load_jobs() -> dict:
    """Load all jobs from storage."""
    ensure_crony_dir()
    if JOBS_FILE.exists():
        return json.loads(JOBS_FILE.read_text())
    return {}


def save_jobs(jobs: dict):
    """Save jobs to storage."""
    ensure_crony_dir()
    JOBS_FILE.write_text(json.dumps(jobs, indent=2))


def parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp safely."""
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


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
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": "local",
        },
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


def parse_schedule(schedule: str) -> dict:
    """Parse natural language schedule into structured format.

    Returns dict with:
        - type: "once" or "recurring"
        - next_run: ISO timestamp
        - cron_expr: cron expression (for recurring)
        - interval: interval string (for recurring)
    """
    schedule = schedule.strip().lower()

    # Check for recurring patterns
    recurring_prefixes = ["every ", "each "]
    is_recurring = any(schedule.startswith(p) for p in recurring_prefixes)

    if is_recurring:
        # Parse recurring schedule
        interval_part = schedule.replace("every ", "").replace("each ", "")

        # Convert to cron expression
        cron_expr = interval_to_cron(interval_part)

        return {
            "type": "recurring",
            "interval": interval_part,
            "cron_expr": cron_expr,
            "next_run": None,  # Will be calculated by scheduler
        }
    else:
        # One-off schedule
        # Try to parse with dateparser
        dt = dateparser.parse(
            schedule,
            settings={
                "PREFER_DATES_FROM": "future",
                "TIMEZONE": "local",
            },
        )

        if not dt:
            raise ValueError(f"Could not parse schedule: {schedule}")

        return {
            "type": "once",
            "schedule": schedule,
            "next_run": dt.isoformat(),
        }


def interval_to_cron(interval: str) -> str:
    """Convert interval string to cron expression."""
    interval = interval.strip().lower()

    # Simple intervals
    mappings = {
        # Minutes
        "1m": "*/1 * * * *",
        "5m": "*/5 * * * *",
        "10m": "*/10 * * * *",
        "15m": "*/15 * * * *",
        "30m": "*/30 * * * *",
        # Hours
        "1h": "0 * * * *",
        "2h": "0 */2 * * *",
        "6h": "0 */6 * * *",
        "12h": "0 */12 * * *",
        "24h": "0 0 * * *",
        # Days
        "1d": "0 0 * * *",
        # Weeks
        "1w": "0 0 * * 0",
    }

    if interval in mappings:
        return mappings[interval]

    # Parse numeric + unit
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

    # Named days
    day_mappings = {
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

    # Default: try to interpret as cron directly
    return interval


def add_job(name: str, schedule: str, cmd: str, cron_expr: str | None = None, force: bool = False) -> dict:
    """Add a new cron job."""
    jobs = load_jobs()

    if name in jobs:
        if not force:
            raise ValueError(f"Job '{name}' already exists. Use 'crony rm {name}' first.")
        # Remove old registration before re-adding
        unregister_job(jobs[name])

    if cron_expr:
        # Validate: 5 space-separated fields
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            raise ValueError(
                f"Invalid cron expression: {cron_expr!r}. Expected 5 space-separated fields."
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

    # Register with OS scheduler
    register_job(job)

    return job


def register_job(job: dict):
    """Register job with OS-level scheduler."""
    system = platform.system()

    if system == "Linux":
        register_job_crontab(job)
    elif system == "Darwin":
        register_job_crontab(job)
    elif system == "Windows":
        register_job_task_scheduler(job)


def register_job_crontab(job: dict):
    """Register job with crontab (Linux/macOS)."""
    name = job["name"]
    cmd = job["cmd"]
    cron_expr = job.get("cron_expr", "")

    # Wrap command to cd into job's working directory
    cwd = job.get("cwd", "")
    if cwd:
        cmd = f"cd {shlex.quote(cwd)} && {cmd}"

    if not cron_expr:
        # One-off job - use at command instead
        register_job_at(job)
        return

    # Add marker comment for identification
    marker = f"# CRONY:{name}"
    cron_line = f"{cron_expr} {cmd}  # CRONY:{name}"

    # Get current crontab
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    current = result.stdout if result.returncode == 0 else ""

    # Remove existing entry for this job if any
    lines = [l for l in current.split("\n") if f"CRONY:{name}" not in l]

    # Add new entry
    lines.append(cron_line)

    # Write back
    new_cron = "\n".join(lines)
    proc = subprocess.run(
        ["crontab", "-"], input=new_cron, capture_output=True, text=True
    )

    if proc.returncode != 0:
        raise RuntimeError(f"Failed to update crontab: {proc.stderr}")


def register_job_at(job: dict):
    """Register one-off job with 'at' command (Linux/macOS)."""
    # Check if 'at' is available
    if not shutil.which("at"):
        raise RuntimeError(
            "The 'at' command is not available. Install it with:\n"
            "  apt install at    (Debian/Ubuntu)\n"
            "  brew install at   (macOS)"
        )

    cmd = job["cmd"]
    name = job["name"]
    next_run = job.get("next_run")

    if not next_run:
        raise RuntimeError(f"Job '{name}' has no next_run time")

    # Parse the next_run time
    try:
        run_time = datetime.fromisoformat(next_run)
    except ValueError:
        raise RuntimeError(f"Cannot parse next_run time: {next_run}")

    # Format for 'at' command
    time_str = run_time.strftime("%H:%M %Y-%m-%d")

    # Wrap command to log output and capture exit code
    log_dir = CRONY_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{name}.log"

    # at accepts the command via stdin
    at_cmd = f"{cmd}\n"

    proc = subprocess.run(
        ["at", time_str],
        input=at_cmd,
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        raise RuntimeError(f"Failed to schedule 'at' job: {proc.stderr}")

    # 'at' prints a line like "job 42 at Thu Mar 10 15:30:00 2026"
    # Store this for later cancellation
    return proc.stdout


def register_job_task_scheduler(job: dict):
    """Register job with Windows Task Scheduler."""
    name = job["name"]
    cmd = job["cmd"]
    cron_expr = job.get("cron_expr", "")

    # Wrap command to cd into job's working directory via .bat wrapper
    cwd = job.get("cwd", "")
    if cwd:
        scripts_dir = CRONY_DIR / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        bat_path = scripts_dir / f"{name}.bat"
        bat_content = f"@echo off\ncd /d \"{cwd}\"\n{cmd}\n"
        bat_path.write_text(bat_content)
        cmd = str(bat_path)

    # Create a scheduled task
    # For simplicity, we'll create a basic task
    task_name = f"CRONY_{name}"

    # Delete existing task if any
    subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True)

    if job["type"] == "recurring":
        # Parse cron for Windows Task Scheduler
        # This is simplified - real implementation would need full cron parser
        parts = cron_expr.split()
        minute, hour, day_of_month, month, day_of_week = parts

        # Create recurring task
        subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                task_name,
                "/TR",
                cmd,
                "/SC",
                "DAILY",  # Simplified
                "/ST",
                "00:00",  # Simplified
                "/F",
            ],
            capture_output=True,
            check=True,
        )
    else:
        # One-off task
        # Parse next_run and create a one-time task
        dt = datetime.fromisoformat(job["next_run"])
        start_time = dt.strftime("%H:%M")
        start_date = dt.strftime("%m/%d/%Y")

        subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                task_name,
                "/TR",
                cmd,
                "/SC",
                "ONCE",
                "/ST",
                start_time,
                "/SD",
                start_date,
                "/F",
            ],
            capture_output=True,
            check=True,
        )


def remove_job(name: str) -> bool:
    """Remove a cron job."""
    jobs = load_jobs()

    if name not in jobs:
        return False

    job = jobs[name]

    # Unregister from OS
    unregister_job(job)

    # Remove from storage
    del jobs[name]
    save_jobs(jobs)

    return True


def unregister_job(job: dict):
    """Unregister job from OS scheduler."""
    system = platform.system()
    name = job["name"]

    if system in ["Linux", "Darwin"]:
        # Remove from crontab
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = result.stdout if result.returncode == 0 else ""
        lines = [l for l in current.split("\n") if f"CRONY:{name}" not in l]
        new_cron = "\n".join(lines)
        subprocess.run(["crontab", "-"], input=new_cron, capture_output=True, text=True)

    elif system == "Windows":
        task_name = f"CRONY_{name}"
        subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True
        )

        # Clean up Windows batch wrapper
        bat_path = CRONY_DIR / "scripts" / f"{name}.bat"
        if bat_path.exists():
            bat_path.unlink()


def run_job(name: str) -> bool:
    """Run a job immediately."""
    jobs = load_jobs()

    if name not in jobs:
        return False

    job = jobs[name]
    cmd = job["cmd"]

    # Run the command
    if sys.platform == "win32":
        subprocess.Popen(cmd, shell=True, creationflags=subprocess.DETACHED_PROCESS)
    else:
        subprocess.Popen(cmd, shell=True, start_new_session=True)

    return True


def get_job_logs(name: str) -> str | None:
    """Get logs for a job."""
    log_file = CRONY_DIR / "logs" / f"{name}.log"
    if log_file.exists():
        return log_file.read_text()
    return None


def scan_os_scheduler() -> dict:
    """Scan OS scheduler for CRONY jobs.

    Returns dict of {name: job_info} for all CRONY tasks found.
    Used for auto-recovery if jobs.json gets corrupted.
    """
    system = platform.system()
    jobs = {}

    if system in ["Linux", "Darwin"]:
        # Scan crontab for CRONY: markers
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "# CRONY:" in line:
                    # Parse: "0 * * * * curl ...  # CRONY:ping"
                    import re

                    match = re.search(r"# CRONY:(\w+)", line)
                    if match:
                        name = match.group(1)
                        # Extract cron expression (first 5 fields)
                        parts = line.split()
                        if len(parts) >= 6:
                            cron_expr = " ".join(parts[:5])
                            cmd = " ".join(parts[5:]).split("# CRONY:")[0].strip()
                            jobs[name] = {
                                "name": name,
                                "cmd": cmd,
                                "cron_expr": cron_expr,
                                "type": "recurring",
                                "recovered": True,
                            }

    elif system == "Windows":
        # Scan Task Scheduler for CRONY_ tasks
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "LIST", "/V"], capture_output=True, text=True
        )
        if result.returncode == 0:
            import re

            # Find all CRONY_ task names
            for match in re.finditer(r"TaskName:\s+(.+\\)?CRONY_(\w+)", result.stdout):
                name = match.group(2)
                jobs[name] = {
                    "name": name,
                    "type": "recurring",  # Assume recurring for now
                    "recovered": True,
                }

    return jobs


def sync_jobs() -> dict:
    """Reconcile jobs.json with OS scheduler.

    - Finds orphan tasks in OS scheduler, adds to index
    - Re-registers jobs in index but missing from OS
    - Returns the reconciled jobs dict
    """
    stored = load_jobs()
    os_jobs = scan_os_scheduler()

    changed = False

    # Find orphans: in OS but not in index
    for name, job in os_jobs.items():
        if name not in stored:
            stored[name] = job
            changed = True

    # Find missing: in index but not in OS
    for name, job in stored.items():
        if name not in os_jobs and job.get("type") == "recurring":
            # Re-register with OS
            try:
                register_job(job)
            except Exception:
                pass  # Best effort

    if changed:
        save_jobs(stored)

    return stored


@click.group()
@click.version_option(__version__, prog_name="crony")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.pass_context
def main(ctx, verbose):
    """Cron job manager with natural language scheduling."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


def _log(ctx, message: str):
    """Print a timestamped verbose log line to stderr."""
    if ctx.obj.get("verbose"):
        click.echo(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", err=True)


@main.command()
@click.argument("name")
@click.argument("schedule")
@click.argument("cmd")
@click.option("--cron", is_flag=True, help="Treat schedule as a raw cron expression (5 fields)")
@click.option("--force", is_flag=True, help="Overwrite existing job with the same name")
@click.pass_context
def add(ctx, name: str, schedule: str, cmd: str, cron: bool, force: bool):
    """Add a new cron job.

    NAME: Job name (unique identifier)

    SCHEDULE: Natural language schedule (e.g., "in 5m", "every 1h", "at 15:30")
             or a raw cron expression when --cron is used (e.g., "*/5 * * * *")

    CMD: Command to run

    Examples:
        crony add ping "every 1h" "curl http://api/ping"
        crony add backup "at 15:30" "backup.sh"
        crony add report "every monday" "weekly-report.sh"
        crony add nightly "0 2 * * *" "backup.sh" --cron
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


@main.command("list")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.option("--sync", is_flag=True, help="Force sync with OS scheduler")
@click.pass_context
def list_cmd(ctx, json_output: bool, sync: bool):
    """List all cron jobs.

    Automatically syncs with OS scheduler to recover orphaned tasks.
    """
    _log(ctx, "list")
    # Always sync on list to auto-heal
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


@main.command()
@click.argument("name")
@click.pass_context
def rm(ctx, name: str):
    """Remove a cron job."""
    _log(ctx, f"rm: {name}")
    if remove_job(name):
        click.echo(f"Removed job: {name}")
    else:
        click.echo(f"Job not found: {name}", err=True)
        sys.exit(1)


@main.command()
@click.argument("name")
@click.pass_context
def run(ctx, name: str):
    """Run a job immediately."""
    _log(ctx, f"run: {name}")
    if run_job(name):
        click.echo(f"Triggered job: {name}")
    else:
        click.echo(f"Job not found: {name}", err=True)
        sys.exit(1)


@main.command()
@click.argument("name")
def logs(name: str):
    """View job logs."""
    log_content = get_job_logs(name)
    if log_content:
        click.echo(log_content)
    else:
        click.echo(f"No logs found for job: {name}")


@main.command(hidden=True)
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish", "powershell"]), default="bash")
@click.pass_context
def completions(ctx: click.Context, shell: str) -> None:
    """Print shell completion setup instructions.

    Use this to enable tab-completion for crony.

    Examples:

        crony completions bash   eval in .bashrc

        crony completions zsh   eval in .zshrc

        crony completions fish   source in config.fish

        crony completions powershell   add to $PROFILE
    """
    tool: str = ctx.parent.info_name if ctx.parent is not None and ctx.parent.info_name is not None else "crony"
    click.echo(f"# Enable shell completion for {tool}:")
    click.echo(f"# Add the following to your shell profile:")
    click.echo(f"eval $(_{tool.upper()}_COMPLETE={shell}_source {tool})")


if __name__ == "__main__":
    main()
