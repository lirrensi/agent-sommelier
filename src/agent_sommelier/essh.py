# FILE: src/agent_sommelier/essh.py
# PURPOSE: Portable SSH wrapper CLI — save hosts, generate keys, connect with interactive or agent-mode authorization.
# OWNS: SSH host profile storage, key generation, ssh-copy-id, interactive/agent-mode SSH connections, export/import of SSH configurations.
# EXPORTS: main (Click group entry point)
# DOCS: pyproject.toml (project.scripts.essh)

"""Portable SSH wrapper CLI for the Agent Sommelier project.

Usage:
    essh add NAME USER@HOST[:PORT] [-i IDENTITY]
    essh NAME [COMMAND]                    # connect to saved host
    essh authorize NAME
    essh list [--json]
    essh rm NAME
    essh export [OUTPUT]
    essh import ARCHIVE [--force]
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ESSH_DIR = Path.home() / ".essh"
PROFILES_FILE = ESSH_DIR / "profiles.json"
KEYS_DIR = ESSH_DIR / "keys"
REQUESTS_DIR = ESSH_DIR / "requests"
EXPORTS_DIR = ESSH_DIR / "exports"
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts"

DEFAULT_PORT = 22
AUTH_TIMEOUT = 30  # seconds
AUTH_POLL_INTERVAL = 0.5  # seconds

COLORS = [
    "amber", "apricot", "aqua", "azure", "black", "blue", "bronze", "brown",
    "charcoal", "cobalt", "copper", "coral", "crimson", "cyan", "emerald",
    "gold", "gray", "green", "indigo", "ivory", "jade", "lavender", "lime",
    "magenta", "maroon", "mint", "navy", "olive", "orange", "peach", "pink",
    "plum", "purple", "red", "rose", "ruby", "rust", "salmon", "sapphire",
    "scarlet", "silver", "tan", "teal", "turquoise", "violet", "white", "yellow",
]

ANIMALS = [
    "alpaca", "badger", "bat", "bear", "bee", "bison", "boar", "bobcat",
    "butterfly", "camel", "cat", "cheetah", "cobra", "cougar", "cow", "coyote",
    "crab", "crane", "crow", "deer", "dingo", "dog", "dolphin", "dove",
    "dragonfly", "duck", "eagle", "eel", "elephant", "elk", "falcon", "ferret",
    "finch", "fish", "flamingo", "fox", "frog", "gazelle", "gecko", "giraffe",
    "goat", "goose", "gorilla", "hawk", "hedgehog", "heron", "horse",
    "hummingbird", "hyena", "ibex", "iguana", "jackal", "jaguar", "kangaroo",
    "koala", "lemur", "leopard", "lion", "lizard", "llama", "lobster", "lynx",
    "magpie", "meerkat", "mole", "mongoose", "monkey", "moose", "moth", "mouse",
    "mule", "narwhal", "newt", "octopus", "okapi", "orangutan", "orca",
    "ostrich", "otter", "owl", "panda", "panther", "parrot", "peacock",
    "pelican", "penguin", "pheasant", "pigeon", "platypus", "pony", "porcupine",
    "puma", "quail", "rabbit", "raccoon", "ram", "rat", "raven", "reindeer",
    "rhino", "robin", "salamander", "seahorse", "seal", "shark", "sheep",
    "skunk", "sloth", "snail", "snake", "sparrow", "spider", "squid",
    "squirrel", "starfish", "stork", "swallow", "swan", "swordfish", "tiger",
    "toad", "tortoise", "toucan", "trout", "turkey", "turtle", "viper",
    "vulture", "wallaby", "walrus", "wasp", "weasel", "whale", "wolf",
    "wombat", "woodpecker", "yak", "zebra",
]

NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")

console = Console()


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    """Create essential storage directories."""
    ESSH_DIR.mkdir(parents=True, exist_ok=True)
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    REQUESTS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def load_profiles() -> list[dict]:
    """Load the profile list from disk. Returns empty list on any failure."""
    ensure_dirs()
    if not PROFILES_FILE.exists():
        return []
    try:
        data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return data
    except (OSError, json.JSONDecodeError):
        return []


def save_profiles(profiles: list[dict]) -> None:
    """Atomically save the profile list to disk."""
    ensure_dirs()
    tmp_path = PROFILES_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    os.replace(tmp_path, PROFILES_FILE)


def find_profile(name: str) -> dict | None:
    """Look up a profile by name.  Returns None if not found."""
    for p in load_profiles():
        if p.get("name") == name:
            return p
    return None


def list_profile_names() -> list[str]:
    """Return a sorted list of known profile names."""
    return sorted(p["name"] for p in load_profiles())


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_host_string(raw: str) -> tuple[str, str, int]:
    """Parse 'user@host[:port]' → (user, host, port).  Port defaults to 22."""
    if "@" not in raw:
        raise click.ClickException(
            f"Invalid target: '{raw}'. Expected format: user@host[:port]"
        )
    user, host_part = raw.split("@", 1)
    if not user or not host_part:
        raise click.ClickException(
            f"Invalid target: '{raw}'. Expected format: user@host[:port]"
        )
    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            raise click.ClickException(f"Invalid port number: {port_str}")
    else:
        host = host_part
        port = DEFAULT_PORT
    return user, host, port


# ---------------------------------------------------------------------------
# SSH tool discovery
# ---------------------------------------------------------------------------

def _require_tool(name: str, *fallback_names: str) -> str:
    """Find a tool on PATH or raise ClickException."""
    for n in (name, *fallback_names):
        path = shutil.which(n)
        if path:
            return n  # use the base name; PATH handles resolution
    raise click.ClickException(f"{name} not found on PATH.")


def _find_ssh() -> list[str]:
    """Find the SSH binary, falling back to WSL on Windows."""
    if sys.platform != "win32":
        if not shutil.which("ssh"):
            raise click.ClickException(
                "ssh not found on PATH. Install OpenSSH client:\n"
                "  apt install openssh-client    (Debian/Ubuntu)\n"
                "  brew install openssh          (macOS)"
            )
        return ["ssh"]
    # Windows: try native ssh first
    if shutil.which("ssh.exe"):
        return ["ssh"]
    # Fall back to WSL
    if shutil.which("wsl"):
        return ["wsl", "ssh"]
    raise click.ClickException(
        "ssh not found. Install OpenSSH client or WSL:\n"
        "  winget install Microsoft.OpenSSH.Beta\n"
        "  OR: wsl --install"
    )


def ssh_cmd() -> list[str]:
    return _find_ssh()


def ssh_keygen_cmd() -> str:
    return _require_tool("ssh-keygen", "ssh-keygen.exe")


def ssh_add_cmd() -> str:
    return _require_tool("ssh-add", "ssh-add.exe")


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def generate_keypair(name: str, key_path: Path) -> Path:
    """Generate an ed25519 keypair at *key_path*. Returns *key_path*."""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ssh_keygen_cmd(),
            "-t", "ed25519",
            "-f", str(key_path),
            "-N", "",
            "-C", f"essh:{name}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"Key generation failed: {result.stderr.strip() or 'unknown error'}"
        )
    return key_path


# ---------------------------------------------------------------------------
# Default-key probe — try SSH without -i to see if the user already has
# a working key in ~/.ssh/ or an SSH agent.
# ---------------------------------------------------------------------------


def _try_ssh_default_keys(user: str, host: str, port: int) -> bool:
    """Test if we can connect using the default SSH keys (no ``-i``).

    Uses ``PasswordAuthentication=no`` and ``BatchMode=yes`` to avoid any
    interactive prompt.  Returns ``True`` if SSH authenticated with an
    existing key.
    """
    result = subprocess.run(
        [
            *ssh_cmd(),
            "-o", "PasswordAuthentication=no",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=5",
            "-p", str(port),
            f"{user}@{host}",
            "echo essh_ok",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# ssh-copy-id
# ---------------------------------------------------------------------------

def ssh_copy_id(user: str, host: str, port: int, pubkey_path: Path) -> None:
    """Pipe the public key into the remote authorized_keys file."""
    key = pubkey_path.read_text(encoding="utf-8").strip()
    remote_cmd = "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"

    result = subprocess.run(
        [*ssh_cmd(), "-p", str(port), f"{user}@{host}", remote_cmd],
        input=key,
        text=True,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"ssh-copy-id failed (exit {result.returncode})"
        )


# ---------------------------------------------------------------------------
# ssh-agent and key loading
# ---------------------------------------------------------------------------

def ensure_ssh_agent(key_path: Path, is_tty: bool = False) -> None:
    """Optionally add the key to an already-running ssh-agent.

    ``_run_ssh`` always passes ``-i`` with the key path, so ssh-agent is
    never required — this is purely a convenience when the user already
    has an agent running (e.g. for agent forwarding).

    In non-TTY mode the subprocess is fully captured to avoid hanging
    on a passphrase prompt or leaking output into the agent's stream.
    """
    if "SSH_AUTH_SOCK" not in os.environ:
        return  # no agent running — nothing to do

    add_cmd = ssh_add_cmd()
    if is_tty:
        subprocess.run(
            [add_cmd, str(key_path)],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    else:
        subprocess.run(
            [add_cmd, str(key_path)],
            capture_output=True,
        )
    # Non-fatal: agent might reject the key or be unavailable


# ---------------------------------------------------------------------------
# Agent-mode authorization polling
# ---------------------------------------------------------------------------

def create_pending_request(name: str) -> None:
    """Create (or refresh) a pending request file. Errors if too recent."""
    ensure_dirs()
    pending_file = REQUESTS_DIR / f"{name}.pending"

    if pending_file.exists():
        age = time.time() - os.path.getmtime(str(pending_file))
        if age < AUTH_TIMEOUT:
            raise click.ClickException(
                f"Request already pending for '{name}'"
            )
        # Stale — clean it up
        pending_file.unlink()

    pending_file.write_text(datetime.now().isoformat(), encoding="utf-8")


def wait_for_authorization(name: str) -> None:
    """Poll until the pending file is deleted (or timeout)."""
    pending_file = REQUESTS_DIR / f"{name}.pending"
    start = time.time()
    while time.time() - start < AUTH_TIMEOUT:
        if not pending_file.exists():
            return
        time.sleep(AUTH_POLL_INTERVAL)
    raise click.ClickException(
        f"Authorization timeout for '{name}' after {AUTH_TIMEOUT}s"
    )


# ---------------------------------------------------------------------------
# Known-hosts extraction (for export)
# ---------------------------------------------------------------------------

def extract_known_hosts_entries(profiles: list[dict]) -> list[str]:
    """Return known_hosts lines relevant to managed profiles."""
    if not KNOWN_HOSTS.exists():
        return []

    managed: set[str] = set()
    for p in profiles:
        host = p.get("host", "")
        port = p.get("port", DEFAULT_PORT)
        if host:
            managed.add(host)
        if port != DEFAULT_PORT:
            managed.add(f"[{host}]:{port}")

    entries: list[str] = []
    for line in KNOWN_HOSTS.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # first token = host(s), comma-separated
        first_token = stripped.split(None, 1)[0] if stripped.split(None, 1) else ""
        hosts_in_line = first_token.split(",")
        if any(h.strip() in managed for h in hosts_in_line):
            entries.append(stripped)

    return entries


# ---------------------------------------------------------------------------
# Custom Group — routes unknown first-arg to the "connect" shortcut
# ---------------------------------------------------------------------------

class EsshGroup(click.Group):
    """Click Group that treats unknown first arguments as the connect shortcut."""

    def resolve_command(self, ctx: click.Context, args: list[str]):
        # Try explicit subcommand first
        cmd_name = args[0]
        cmd = self.get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd_name, cmd, args[1:]
        # Unknown — treat as `essh NAME [COMMAND]`
        return "connect", self.commands["connect"], args


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group(cls=EsshGroup, invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Portable SSH wrapper for Agent Sommelier.

    Save SSH host profiles, generate keys, and connect with agent-mode
    authorization support.

    \b
    Commands:
      add       Save a new SSH host profile.
      list      List saved profiles.
      rm        Remove a profile.
      export    Export profiles and keys to a tar.gz archive.
      import    Import profiles from a tar.gz archive.
      authorize Authorize a pending connection request.

    \b
    Shortcut:
      essh NAME [COMMAND]   Connect to a saved host directly.
    """
    if ctx.invoked_subcommand is None:
        # No arguments at all — show help
        click.echo(ctx.get_help())
        ctx.exit()


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

def generate_name(existing_names: set[str]) -> str:
    """Generate a Docker-style color-animal name, avoiding collisions."""
    for _ in range(10):
        name = f"{random.choice(COLORS)}-{random.choice(ANIMALS)}"
        if name not in existing_names:
            return name
        # Collision: append short hex suffix
        suffix = random.randint(0, 0xFFF)
        name_with_suffix = f"{name}-{suffix:03x}"
        if name_with_suffix not in existing_names:
            return name_with_suffix
    raise click.ClickException(
        "Could not generate a unique name after 10 attempts"
    )


def validate_name(name: str) -> None:
    """Validate that the name only contains allowed characters."""
    if not NAME_PATTERN.match(name):
        raise click.ClickException(
            f"Invalid name '{name}'. "
            f"Only lowercase letters, digits, hyphens, and underscores allowed: [a-z0-9_-]"
        )


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

@main.command(name="add")
@click.argument("first")
@click.argument("second", required=False)
@click.option(
    "-n", "--name",
    default=None,
    help="Explicit name for the profile (alternative to positional argument).",
)
@click.option(
    "-i", "--identity",
    type=click.Path(exists=True, path_type=Path),
    help="Existing SSH identity (private key) to use instead of generating one.",
)
def add(first: str, second: str | None, name: str | None, identity: Path | None) -> None:
    """Save a new SSH host profile.

    \b
    Usage:
      essh add USER@HOST[:PORT]          auto-generate name
      essh add NAME USER@HOST[:PORT]     use NAME as profile name
      essh add -n NAME USER@HOST[:PORT]  explicit name via flag
    """
    # Resolve name and target from positional args and -n flag
    if name is not None:
        # Explicit -n flag: first is the TARGET
        if second is not None:
            raise click.ClickException(
                "Cannot use both -n/--name and two positional arguments. "
                "Use: essh add -n NAME TARGET  OR  essh add NAME TARGET"
            )
        target = first
    elif second is not None:
        # Two positional args: first=NAME, second=TARGET
        name = first
        target = second
    else:
        # One positional arg: it's the TARGET, auto-generate a suggested name
        target = first
        suggested = generate_name(set(list_profile_names()))

        if sys.stdin.isatty():
            # Interactive: prompt user to accept or override
            user_input = console.input(f"Profile name [bold]{suggested}[/bold]: ")
            name = user_input.strip() or suggested
        else:
            # Agent mode: use the generated name silently
            name = suggested
            console.print(f"Generated name: {name}")

    # Validate the resolved name
    validate_name(name)

    if find_profile(name):
        raise click.ClickException(
            f"Profile '{name}' already exists. Use 'essh rm {name}' first."
        )

    user, host, port = parse_host_string(target)

    if identity:
        # -- identity provided: reference it in-place, push the pubkey -------
        private_key = identity.expanduser().resolve()
        if not private_key.is_file():
            raise click.ClickException(f"Key not found: {private_key}")

        pub_source = Path(str(private_key) + ".pub")
        if not pub_source.is_file():
            pub_source = private_key.with_suffix(private_key.suffix + ".pub")
        if pub_source.is_file():
            console.print(
                f"[bold]Copying public key to {user}@{host}:{port}...[/bold]"
            )
            console.print(
                "[dim]You may be prompted for the remote password.[/dim]"
            )
            ssh_copy_id(user, host, port, pub_source)

        profile: dict = {
            "name": name,
            "user": user,
            "host": host,
            "port": port,
            "key_path": str(private_key),
        }

    else:
        # -- no identity flag: try existing keys first -----------------------
        console.print(
            f"[dim]Checking existing SSH keys for {user}@{host}:{port}...[/dim]"
        )

        if _try_ssh_default_keys(user, host, port):
            # Already have access — save profile with empty key_path
            profile = {
                "name": name,
                "user": user,
                "host": host,
                "port": port,
                "key_path": "",  # means "use default SSH keys"
            }
        else:
            # No working key — offer to generate one in ~/.ssh/
            if sys.stdin.isatty():
                generate = click.confirm(
                    "No existing key works. Generate one?", default=True
                )
            else:
                generate = True

            if not generate:
                raise click.ClickException(
                    "No key provided. "
                    "Use: essh add -i /path/to/key USER@HOST[:PORT]"
                )

            ssh_dir = Path.home() / ".ssh"
            ssh_dir.mkdir(parents=True, exist_ok=True)

            default_key = ssh_dir / "id_ed25519"
            if default_key.is_file():
                key_path = ssh_dir / f"id_ed25519_{name}"
            else:
                key_path = default_key

            console.print(
                f"[dim]Generating ed25519 keypair: {key_path}[/dim]"
            )
            generate_keypair(name, key_path)

            pub_path = key_path.with_suffix(".pub")
            console.print(
                f"[bold]Copying public key to {user}@{host}:{port}...[/bold]"
            )
            console.print(
                "[dim]You may be prompted for the remote password.[/dim]"
            )
            ssh_copy_id(user, host, port, pub_path)

            profile = {
                "name": name,
                "user": user,
                "host": host,
                "port": port,
                "key_path": str(key_path),
            }

    # Persist profile
    profiles = load_profiles()
    profiles.append(profile)
    save_profiles(profiles)
    console.print(f"[green]Profile '{name}' saved.[/green]")


# ---------------------------------------------------------------------------
# connect  (the default / shortcut command)
# ---------------------------------------------------------------------------

@main.command(
    name="connect",
    hidden=True,
    context_settings={"ignore_unknown_options": True},
)
@click.argument("name")
@click.argument("remote_command", nargs=-1)
def connect(name: str, remote_command: tuple[str, ...]) -> None:
    """Connect to a saved host, optionally running a remote command.

    This is also the default behaviour when no subcommand is given:
        essh myserver
        essh myserver uptime

    Names must match [a-z0-9_-] (lowercase letters, digits, hyphens, underscores).
    """
    validate_name(name)
    profile = find_profile(name)
    if profile is None:
        known = list_profile_names()
        if known:
            hint = f" Known profiles: {', '.join(known)}"
        else:
            hint = " No profiles saved. Use 'essh add' to create one."
        raise click.ClickException(f"Profile '{name}' not found.{hint}")

    user = profile["user"]
    host = profile["host"]
    port = profile.get("port", DEFAULT_PORT)
    key_path_raw = profile.get("key_path") or ""
    key_path: Path | None = Path(key_path_raw) if key_path_raw else None

    is_tty = sys.stdin.isatty()

    # Agent mode: request authorization and poll
    if not is_tty:
        console.print(
            f"[yellow]Agent mode: requesting authorization for '{name}'...[/yellow]"
        )
        create_pending_request(name)
        try:
            wait_for_authorization(name)
            console.print(
                f"[green]Authorization granted for '{name}'.[/green]"
            )
        except click.ClickException:
            console.print(
                f"[red]Authorization denied or timed out for '{name}'.[/red]"
            )
            raise

    # Optionally add the key to ssh-agent (if one is already running)
    if key_path:
        ensure_ssh_agent(key_path, is_tty)

    # Build and run SSH
    exit_code = _run_ssh(user, host, port, key_path, list(remote_command), is_tty)
    sys.exit(exit_code)


def _run_ssh(
    user: str,
    host: str,
    port: int,
    key_path: Path | None,
    remote_command: list[str],
    is_tty: bool,
) -> int:
    """Execute the ssh process and return its exit code."""
    args = [*ssh_cmd()]
    if key_path:
        args += ["-i", str(key_path)]
    args += ["-p", str(port), f"{user}@{host}"]
    if remote_command:
        args.extend(remote_command)

    if is_tty:
        # Interactive: inherit all streams
        result = subprocess.run(
            args,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    else:
        result = subprocess.run(args, capture_output=True, text=True)
        if result.stdout:
            click.echo(result.stdout, nl=False)
        if result.stderr:
            console.print(f"[dim]{result.stderr}[/dim]", end="")

    return result.returncode


# ---------------------------------------------------------------------------
# authorize
# ---------------------------------------------------------------------------

@main.command(name="authorize")
@click.argument("name")
def authorize(name: str) -> None:
    """Approve a pending agent-mode connection request."""
    validate_name(name)
    if find_profile(name) is None:
        raise click.ClickException(f"Profile '{name}' not found.")

    pending_file = REQUESTS_DIR / f"{name}.pending"
    if not pending_file.exists():
        console.print(f"No pending request for '{name}'.")
        return

    pending_file.unlink()
    console.print(f"[green]'{name}' authorized.[/green]")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@main.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON array.")
def list_profiles(as_json: bool) -> None:
    """List saved SSH host profiles."""
    profiles = load_profiles()

    if as_json:
        click.echo(json.dumps(profiles, indent=2))
        return

    if not profiles:
        console.print(
            "[dim]No profiles saved. Use 'essh add' to create one.[/dim]"
        )
        return

    table = Table(title="SSH Profiles")
    table.add_column("Name", style="cyan")
    table.add_column("User", style="green")
    table.add_column("Host", style="green")
    table.add_column("Port", style="white")
    table.add_column("Key", style="dim")

    for p in profiles:
        raw = p.get("key_path") or ""
        if not raw:
            key_display = "[dim](default keys)[/dim]"
        elif Path(raw).exists():
            key_display = raw
        else:
            key_display = f"[red]{raw} (missing)[/red]"
        table.add_row(
            p["name"],
            p["user"],
            p["host"],
            str(p.get("port", DEFAULT_PORT)),
            key_display,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# rm
# ---------------------------------------------------------------------------

@main.command(name="rm")
@click.argument("name")
def remove(name: str) -> None:
    """Remove a saved profile and its keys."""
    validate_name(name)
    if find_profile(name) is None:
        raise click.ClickException(f"Profile '{name}' not found.")

    # Remove from profiles.json
    profiles = [p for p in load_profiles() if p.get("name") != name]
    save_profiles(profiles)

    # Delete key directory (only if inside essh's own store — legacy profiles)
    key_dir = KEYS_DIR / name
    if key_dir.exists():
        shutil.rmtree(key_dir, ignore_errors=True)

    # Clean stale pending request
    pending_file = REQUESTS_DIR / f"{name}.pending"
    pending_file.unlink(missing_ok=True)

    console.print(f"[green]Profile '{name}' removed.[/green]")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@main.command(name="export")
@click.argument("output", required=False, default=None)
def export_profiles(output: str | None) -> None:
    """Export profiles and keys to a tar.gz archive.

    Default output: ~/.essh/exports/essh-export-{timestamp}.tar.gz
    """
    profiles = load_profiles()
    if not profiles:
        raise click.ClickException("No profiles to export.")

    if output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = str(EXPORTS_DIR / f"essh-export-{timestamp}.tar.gz")
    else:
        output = str(Path(output).expanduser().resolve())

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    known_hosts_entries = extract_known_hosts_entries(profiles)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # profiles.json
        shutil.copy2(PROFILES_FILE, tmp / "profiles.json")

        # keys/ — include both legacy ~/.essh/keys/ and external referenced keys
        keys_tmp = tmp / "keys"
        keys_tmp.mkdir()
        for p in profiles:
            raw = p.get("key_path") or ""
            if not raw:
                continue
            key_path = Path(raw).expanduser().resolve()
            pname = p["name"]

            # Legacy key in ~/.essh/keys/ — copy the whole directory
            legacy_src = KEYS_DIR / pname
            if legacy_src.exists():
                shutil.copytree(legacy_src, keys_tmp / pname)
            # External key — copy the private + public key files only
            elif key_path.is_file():
                dest_dir = keys_tmp / pname
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(key_path, dest_dir / "id_ed25519")
                pub = Path(str(key_path) + ".pub")
                if pub.is_file():
                    shutil.copy2(pub, dest_dir / "id_ed25519.pub")

        # known_hosts (filtered)
        if known_hosts_entries:
            (tmp / "known_hosts").write_text(
                "\n".join(known_hosts_entries) + "\n", encoding="utf-8"
            )

        # Create tar.gz — add top-level entries only to avoid duplication
        with tarfile.open(output_path, "w:gz") as tar:
            for entry in sorted(tmp.iterdir()):
                tar.add(entry, arcname=entry.name)

    console.print(f"[green]Exported to {output_path}[/green]")


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

@main.command(name="import")
@click.argument("archive", type=click.Path(exists=True, path_type=Path))
@click.option("--force", is_flag=True, help="Overwrite existing profiles.")
def import_(archive: Path, force: bool) -> None:
    """Import profiles from a tar.gz archive.

    ARCHIVE must be a .tar.gz file produced by 'essh export'.
    """
    archive_path = archive.expanduser().resolve()

    if not archive_path.name.endswith(".tar.gz"):
        raise click.ClickException(
            f"Expected a .tar.gz archive, got: {archive_path.name}"
        )

    if not tarfile.is_tarfile(archive_path):
        raise click.ClickException(
            f"Not a valid tar archive: {archive_path}"
        )

    imported_count = 0
    skipped_count = 0

    existing_profiles = load_profiles()
    profile_names: dict[str, dict] = {
        p["name"]: p for p in existing_profiles
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(tmp)

        profiles_file = tmp / "profiles.json"
        if not profiles_file.exists():
            raise click.ClickException(
                "Archive does not contain profiles.json"
            )

        try:
            imported_data = json.loads(
                profiles_file.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            raise click.ClickException(
                "Archive contains invalid profiles.json"
            )

        if not isinstance(imported_data, list):
            raise click.ClickException("profiles.json is not a JSON array")

        for ip in imported_data:
            ip_name = ip.get("name")
            if not ip_name:
                continue

            if ip_name in profile_names:
                if not force:
                    console.print(
                        f"[yellow]Skipping '{ip_name}' (already exists). "
                        "Use --force to overwrite.[/yellow]"
                    )
                    skipped_count += 1
                    continue
                # Remove existing entry so it gets replaced
                existing_profiles = [
                    p
                    for p in existing_profiles
                    if p.get("name") != ip_name
                ]

            # Re-root key_path: legacy exports have keys in the archive;
            # modern exports with no key_path preserve that as-is.
            ip_raw = ip.get("key_path") or ""
            source_keys = tmp / "keys" / ip_name

            if not ip_raw:
                # Profile uses default SSH keys — no key to restore
                pass
            elif source_keys.exists():
                # Legacy export — restore key to essh store
                ip["key_path"] = str(KEYS_DIR / ip_name / "id_ed25519")
                dest_keys = KEYS_DIR / ip_name
                if dest_keys.exists():
                    shutil.rmtree(dest_keys, ignore_errors=True)
                shutil.copytree(source_keys, dest_keys)
            else:
                # External key path — keep the stored path, keys stay in place
                console.print(
                    f"[yellow]Warning: key for '{ip_name}' not in archive. "
                    f"Stored path '{ip_raw}' may not exist on this machine.[/yellow]"
                )

            existing_profiles.append(ip)
            imported_count += 1

        save_profiles(existing_profiles)

        # Merge known_hosts
        known_hosts_src = tmp / "known_hosts"
        if known_hosts_src.exists():
            new_lines = [
                l.strip()
                for l in known_hosts_src.read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            if KNOWN_HOSTS.exists():
                existing_lines = set(
                    l.strip()
                    for l in KNOWN_HOSTS.read_text(encoding="utf-8").splitlines()
                    if l.strip()
                )
                to_append = [
                    l for l in new_lines if l not in existing_lines
                ]
                if to_append:
                    with open(KNOWN_HOSTS, "a", encoding="utf-8") as f:
                        f.write("\n".join(to_append) + "\n")
            else:
                KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
                KNOWN_HOSTS.write_text(
                    "\n".join(new_lines) + "\n", encoding="utf-8"
                )

    console.print(
        f"[green]Imported: {imported_count}, Skipped: {skipped_count}[/green]"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
