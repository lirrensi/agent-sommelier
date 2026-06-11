"""tmx — tmux/psmux session control for agents.

Cross-platform tmux/psmux wrapper for programmatic terminal session management.
Auto-detects tmux on Unix and psmux/tmux on Windows.

Usage:
    tmx install                    # Ensure tmux/psmux is available
    tmx create NAME [CMD]          # Create session
    tmx rm NAME                    # Kill session
    tmx sk NAME "CMD"              # Send keys (fire and forget)
    tmx r NAME                     # Read output (scrollback)
    tmx run NAME "CMD" [--timeout] # Send + wait + read
    tmx list [--json]              # List sessions
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from typing import Any, TypedDict


class _SessionInfo(TypedDict):
    """Typed dict for tmux session info."""
    name: str
    windows: int
    status: str

import click
from rich.console import Console
from rich.table import Table

from agent_sommelier import __version__


# ─── helpers ────────────────────────────────────────────────────────────────


def _find_tmux() -> str:
    """Detect tmux or psmux on the current system."""
    if platform.system() == "Windows":
        for binary in ("psmux", "tmux", "tmux.exe"):
            if shutil.which(binary):
                return binary
    else:
        if shutil.which("tmux"):
            return "tmux"

    raise click.ClickException(
        "No tmux or psmux found on PATH. Run 'tmx install' first."
    )


def _tmux(*args: str) -> subprocess.CompletedProcess:
    """Run a tmux/psmux command with auto-detected binary."""
    binary = _find_tmux()
    try:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise click.ClickException(
            f"'{binary}' not found. Run 'tmx install' first."
        )


def _session_exists(name: str) -> bool:
    """Return True if a named tmux session exists."""
    result = _tmux("has-session", "-t", name)
    return result.returncode == 0


def _ensure_session(name: str) -> None:
    """Raise if the named session does not exist."""
    if not _session_exists(name):
        raise click.ClickException(f"Session '{name}' not found.")


def _set_scrollback(name: str) -> None:
    """Set generous scrollback limit on a session."""
    _tmux("set-option", "-t", name, "history-limit", "10000")


# ─── commands ────────────────────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="tmx")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """tmx — tmux/psmux session control for agents.

    Cross-platform wrapper for tmux (Linux/macOS) and psmux (Windows).
    Manage terminal sessions programmatically: create, send keys,
    capture output, and run commands with timeout-based sync.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
def install() -> None:
    """Ensure tmux/psmux is available on this system.

    On Windows, tries to install psmux automatically via winget,
    scoop, choco, or cargo. On macOS, suggests or runs brew.
    On Linux, prints the install command for the detected package manager.
    """
    # Check if already installed
    try:
        binary = _find_tmux()
        click.echo(f"✓ {binary} found.", err=True)
        return
    except click.ClickException:
        pass

    system = platform.system()

    if system == "Windows":
        _install_windows()
    elif system == "Darwin":
        _install_macos()
    elif system == "Linux":
        _install_linux()
    else:
        click.echo(
            f"Unsupported platform: {system}. Install tmux manually.", err=True
        )
        sys.exit(1)


def _install_windows() -> None:
    """Attempt psmux installation on Windows via available package managers."""
    click.echo("Installing psmux...", err=True)

    installers: list[tuple[str, str, list[str]]] = [
        ("winget", "winget", ["winget", "install", "psmux"]),
        ("scoop", "scoop", ["scoop", "install", "psmux"]),
        ("choco", "choco", ["choco", "install", "psmux"]),
        ("cargo", "cargo", ["cargo", "install", "psmux"]),
    ]

    for name, binary, cmd in installers:
        if not shutil.which(binary):
            continue
        click.echo(f"  Trying {name}...", err=True)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                # Verify installation
                if shutil.which("psmux") or shutil.which("tmux") or shutil.which("tmux.exe"):
                    click.echo("✓ psmux installed.", err=True)
                    return
                click.echo(f"  {name} ran but psmux not found on PATH.", err=True)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    # Manual fallback
    click.echo(
        "Could not install psmux automatically. Install manually:\n"
        "  1. Download from: https://github.com/marlocarlo/psmux/releases\n"
        "  2. Extract the .zip and add to PATH\n"
        "  Or try: cargo install psmux",
        err=True,
    )
    sys.exit(1)


def _install_macos() -> None:
    """Suggest or run brew install tmux on macOS."""
    click.echo("tmux not found. Install with: brew install tmux", err=True)
    if shutil.which("brew"):
        click.echo("  Running brew install tmux...", err=True)
        try:
            result = subprocess.run(
                ["brew", "install", "tmux"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0 and shutil.which("tmux"):
                click.echo("✓ tmux installed.", err=True)
                return
            click.echo(f"  brew install failed:\n{result.stderr}", err=True)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            click.echo(f"  Error: {e}", err=True)
    sys.exit(1)


def _install_linux() -> None:
    """Print install command for the detected Linux package manager."""
    managers: dict[str, str] = {
        "apt": "sudo apt install tmux",
        "apt-get": "sudo apt-get install tmux",
        "dnf": "sudo dnf install tmux",
        "yum": "sudo yum install tmux",
        "pacman": "sudo pacman -S tmux",
        "apk": "sudo apk add tmux",
        "zypper": "sudo zypper install tmux",
    }

    for binary, cmd in managers.items():
        if shutil.which(binary):
            click.echo(f"tmux not found. Install with: {cmd}", err=True)
            sys.exit(1)

    click.echo(
        "tmux not found. Install via your package manager "
        "(e.g. apt install tmux, dnf install tmux, etc.).",
        err=True,
    )
    sys.exit(1)


@cli.command()
@click.argument("name")
@click.argument("cmd", required=False)
@click.option("--json/--no-json", "json_output", is_flag=True, default=False, help="Output as JSON.")
def create(name: str, cmd: str | None, json_output: bool) -> None:
    """Create a new detached session.

    NAME is the session name. CMD is an optional initial command
    (e.g. "ssh user@host").
    """
    if _session_exists(name):
        raise click.ClickException(f"Session '{name}' already exists.")

    _tmux("new-session", "-d", "-s", name, "-x", "120", "-y", "40")
    _set_scrollback(name)

    if cmd:
        _tmux("send-keys", "-t", name, "-l", "--", cmd)
        _tmux("send-keys", "-t", name, "Enter")

    if json_output:
        click.echo(json.dumps({"session": name, "created": True}))
    else:
        click.echo(f"Created session: {name}", err=True)


@cli.command()
@click.argument("name")
@click.option("--json/--no-json", "json_output", is_flag=True, default=False, help="Output as JSON.")
def rm(name: str, json_output: bool) -> None:
    """Kill a session."""
    _ensure_session(name)
    _tmux("kill-session", "-t", name)
    if json_output:
        click.echo(json.dumps({"session": name, "killed": True}))
    else:
        click.echo(f"Killed session: {name}", err=True)


@cli.command("sk")
@click.argument("name")
@click.argument("cmd")
def sk(name: str, cmd: str) -> None:
    """Send keys to a session (fire and forget).

    Types CMD and presses Enter. Returns immediately without waiting
    for output.
    """
    _ensure_session(name)
    _tmux("send-keys", "-t", name, "-l", "--", cmd)
    _tmux("send-keys", "-t", name, "Enter")


@cli.command("r")
@click.argument("name")
@click.option("--json/--no-json", "json_output", is_flag=True, default=False, help="Output as JSON.")
def r(name: str, json_output: bool) -> None:
    """Read session output (full scrollback).

    Captures the entire scrollback buffer and prints to stdout.
    """
    _ensure_session(name)
    result = _tmux("capture-pane", "-p", "-J", "-t", name, "-S", "-50000")
    if json_output:
        click.echo(json.dumps({"session": name, "output": result.stdout}))
    else:
        sys.stdout.write(result.stdout)


@cli.command()
@click.argument("name")
@click.argument("cmd")
@click.option(
    "--timeout",
    "-t",
    default=5,
    type=int,
    show_default=True,
    help="Seconds to wait after sending before reading output.",
)
@click.option("--json/--no-json", "json_output", is_flag=True, default=False, help="Output as JSON.")
def run(name: str, cmd: str, timeout: int, json_output: bool) -> None:
    """Send keys, wait, and read output.

    Sends CMD to the session, waits TIMEOUT seconds (default 5),
    then captures and prints the scrollback. This is the primary
    agent workflow for executing commands and getting results.
    """
    _ensure_session(name)
    _tmux("send-keys", "-t", name, "-l", "--", cmd)
    _tmux("send-keys", "-t", name, "Enter")
    time.sleep(timeout)
    result = _tmux("capture-pane", "-p", "-J", "-t", name, "-S", "-50000")
    if json_output:
        click.echo(json.dumps({"session": name, "output": result.stdout}))
    else:
        sys.stdout.write(result.stdout)


@cli.command("list")
@click.option("--json/--no-json", "as_json", is_flag=True, help="Output as JSON.")
def list_(as_json: bool) -> None:
    """List all sessions."""
    result = _tmux(
        "list-sessions",
        "-F",
        "#{session_name}\t#{session_windows}\t#{?session_attached,attached,detached}",
    )

    if result.returncode != 0 or not result.stdout.strip():
        click.echo("No sessions.", err=True)
        return

    sessions: list[_SessionInfo] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            sessions.append(
                {"name": parts[0], "windows": int(parts[1]), "status": parts[2]}
            )

    if as_json:
        click.echo(json.dumps(sessions, indent=2))
    else:
        _render_sessions_table(sessions)


def _render_sessions_table(sessions: list[_SessionInfo]) -> None:
    """Render a Rich table of sessions to stderr."""
    console = Console()
    table = Table(title="tmux sessions")
    table.add_column("Name", style="cyan")
    table.add_column("Windows", style="yellow", justify="right")
    table.add_column("Status", style="green")

    for s in sessions:
        table.add_row(str(s["name"]), str(s["windows"]), str(s["status"]))

    console.print(table)


# ─── manager (interactive picker) ────────────────────────────────────────────


def _get_session_list() -> list[_SessionInfo]:
    """Fetch current session list from tmux."""
    result = _tmux(
        "list-sessions",
        "-F",
        "#{session_name}\t#{session_windows}\t#{?session_attached,attached,detached}",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    sessions: list[_SessionInfo] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            sessions.append(
                {"name": parts[0], "windows": int(parts[1]), "status": parts[2]}
            )
    return sessions


def _clear_screen() -> None:
    """Clear terminal screen cross-platform."""
    os.system("cls" if os.name == "nt" else "clear")


def _getch() -> str:
    """Read a single keypress cross-platform.

    Returns:
        Regular characters as-is.
        Arrow keys: 'UP', 'DOWN', 'LEFT', 'RIGHT'
        Enter: '\\r'
        Escape: '\\x1b'
    """
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415

        ch = msvcrt.getch()
        if ch == b"\xe0":  # Arrow key prefix on Windows
            ch = msvcrt.getch()
            mapping = {b"H": "UP", b"P": "DOWN", b"M": "RIGHT", b"K": "LEFT"}
            return mapping.get(ch, "?")
        if ch == b"\x03":  # Ctrl+C
            raise KeyboardInterrupt
        return ch.decode("utf-8", errors="replace")
    else:
        import termios  # noqa: PLC0415
        import tty  # noqa: PLC0415

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                mapping = {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}
                return mapping.get(seq, "\x1b")
            if ch == "\x03":
                raise KeyboardInterrupt
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_picker(
    sessions: list[_SessionInfo],
    cursor: int,
    message: str = "",
) -> None:
    """Render the session picker screen."""
    console = Console()

    # ── header ──
    console.print("  [bold white]tmx[/] [dim]session picker[/]")
    console.print()

    # ── new session row (always first) ──
    if cursor == 0:
        console.print("   [black on white]  +  new session  [/]")
    else:
        console.print("   [dim]  +  new session[/]")

    if sessions:
        console.print()
        for i, s in enumerate(sessions):
            row_idx = i + 1
            dot = "@" if s["status"] == "attached" else "•"
            label = f"  {dot}  {s['name']}  [dim]{s['windows']}w[/]"

            if cursor == row_idx:
                console.print(f"   [black on cyan]{label:<35}[/]")
            else:
                fg = "cyan" if s["status"] == "attached" else "white"
                console.print(f"   [{fg}]{label}[/]")

    # ── footer ──
    console.print()
    console.print("   [dim]──[/]")
    console.print(
        "   [dim]↑↓ move   enter attach[/]   "
        "[dim]k kill[/]   "
        "[dim]n new[/]   "
        "[dim]q quit[/]"
    )
    if message:
        console.print(f"   [yellow]{message}[/]")


def _prompt_new_session() -> str | None:
    """Prompt user for a new session name. Returns name or None if cancelled."""
    _clear_screen()
    console = Console()
    console.print("  [bold white]tmx[/] [dim]new session[/]")
    console.print()
    console.print("   Enter session name ([dim]empty = auto-name from cwd[/]):")
    console.print()
    try:
        name = input("   > ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not name:
        cwd = os.getcwd()
        base = os.path.basename(cwd)
        name = base.replace(" ", "-").lower() if base else "session"
        # add a short random suffix
        import random  # noqa: PLC0415

        suffix = random.choice(
            ["red", "blue", "green", "amber", "coral", "jade", "mint", "fox", "owl", "wolf"]
        )
        name = f"{name}-{suffix}"
        # check for collision
        existing = {s["name"] for s in _get_session_list()}
        if name in existing:
            name = f"{name}-{random.randint(10, 99)}"
    return name


@cli.command()
def manager() -> None:
    """Interactive session picker (human-friendly TUI).

    Browse tmux sessions with arrow keys, attach, kill, or create new.
    After detaching from a session, returns to the picker.
    """
    if not sys.stdin.isatty():
        click.echo("manager requires an interactive terminal.", err=True)
        sys.exit(1)

    binary = _find_tmux()
    sessions = _get_session_list()
    cursor = 0
    message = ""

    while True:
        _clear_screen()
        _render_picker(sessions, cursor, message)
        message = ""

        key = _getch()

        if key == "q":
            break

        elif key == "UP":
            total = len(sessions) + 1  # +1 for "new session"
            cursor = (cursor - 1 + total) % total

        elif key == "DOWN":
            total = len(sessions) + 1
            cursor = (cursor + 1) % total

        elif key in ("\r", "\n"):
            if cursor == 0:
                # Create new session and attach
                name = _prompt_new_session()
                if name is None:
                    continue
                if _session_exists(name):
                    message = f"Session '{name}' already exists."
                    continue
                _tmux("new-session", "-d", "-s", name, "-x", "120", "-y", "40")
                _set_scrollback(name)
                # Attach (blocking)
                _clear_screen()
                console = Console()
                console.print(f"  [bold]Attaching to [cyan]{name}[/]...[/]")
                console.print("  [dim](detach: Ctrl+B then d)[/]")
                console.print()
                subprocess.run([binary, "attach", "-t", name])
                # After detach, refresh
                sessions = _get_session_list()
                cursor = 0
            else:
                # Attach to selected session
                target = sessions[cursor - 1]
                _clear_screen()
                console = Console()
                console.print(f"  [bold]Attaching to [cyan]{target['name']}[/]...[/]")
                console.print("  [dim](detach: Ctrl+B then d)[/]")
                console.print()
                subprocess.run([binary, "attach", "-t", target["name"]])
                # After detach, refresh
                sessions = _get_session_list()
                cursor = min(cursor, len(sessions))

        elif key == "k" and cursor > 0:
            # Kill selected session
            target = sessions[cursor - 1]
            _tmux("kill-session", "-t", target["name"])
            sessions = _get_session_list()
            cursor = min(cursor, max(0, len(sessions)))
            message = f"Killed: {target['name']}"

        elif key == "n":
            # Create new session (no attach)
            name = _prompt_new_session()
            if name is None:
                continue
            if _session_exists(name):
                message = f"Session '{name}' already exists."
                continue
            _tmux("new-session", "-d", "-s", name, "-x", "120", "-y", "40")
            _set_scrollback(name)
            sessions = _get_session_list()
            cursor = len(sessions)  # move cursor to the new session
            message = f"Created: {name}"


# ─── entry point ─────────────────────────────────────────────────────────────

@cli.command(hidden=True)
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish", "powershell"]), default="bash")
@click.pass_context
def completions(ctx: click.Context, shell: str) -> None:
    """Print shell completion setup instructions.

    Use this to enable tab-completion for tmx.

    Examples:

        tmx completions bash   eval in .bashrc

        tmx completions zsh   eval in .zshrc

        tmx completions fish   source in config.fish

        tmx completions powershell   add to $PROFILE
    """
    tool: str = ctx.parent.info_name if ctx.parent is not None and ctx.parent.info_name is not None else "tmx"
    click.echo(f"# Enable shell completion for {tool}:")
    click.echo(f"# Add the following to your shell profile:")
    click.echo(f"eval $(_{tool.upper()}_COMPLETE={shell}_source {tool})")


if __name__ == "__main__":
    cli()
