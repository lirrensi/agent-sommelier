# FILE: src/agent_sommelier/essh.py
# PURPOSE: Portable SSH wrapper CLI — save hosts, generate keys, connect with interactive or agent-mode authorization.
# OWNS: SSH host profile storage, key generation, ssh-copy-id, interactive/agent-mode SSH connections, export/import of SSH configurations.
# EXPORTS: main (Click group entry point)
# DOCS: pyproject.toml (project.scripts.essh)

"""Portable SSH wrapper CLI for the Agent Sommelier project.

Usage:
    essh add NAME USER@HOST[:PORT] [-i IDENTITY]
    essh NAME [COMMAND]                    # connect to saved host
    essh scp [SCP_OPTIONS...] SOURCE DEST  # copy files with scp
    essh rsync [RSYNC_OPTIONS...] SOURCE DEST  # sync files with rsync
    essh authorize NAME
    essh list [--json]
    essh rm NAME
    essh edit NAME [--host|--port|--user|--identity|--new-name]
    essh filter add|rm|list|clear TARGET PATTERN [--action deny|ask|allow]
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

from agent_sommelier import __version__

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ESSH_DIR = Path.home() / ".essh"
PROFILES_FILE = ESSH_DIR / "profiles.json"
KEYS_DIR = ESSH_DIR / "keys"
REQUESTS_DIR = ESSH_DIR / "requests"
EXPORTS_DIR = ESSH_DIR / "exports"
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts"
FILTERS_FILE = ESSH_DIR / "filters.json"

DEFAULT_PORT = 22
AUTH_TIMEOUT = 30  # seconds
AUTH_POLL_INTERVAL = 0.5  # seconds
FILTER_ACTIONS = frozenset(["allow", "ask", "deny"])

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
# Wildcard matching (ported from anomalyco/opencode)
# ---------------------------------------------------------------------------
def _wildcard_match(input_str: str, pattern: str) -> bool:
    """Match ``input_str`` against a wildcard ``pattern``.

    Rules:
    - Backslashes normalize to forward slashes.
    - ``*`` matches any sequence (``.*`` in regex).
    - ``?`` matches any single char (``.`` in regex).
    - All other regex special chars are escaped.
    - Trailing `` *`` becomes ``( .*)?`` — the space+args are OPTIONAL,
      so ``rm *`` matches ``rm`` AND ``rm -rf /`` but NOT ``rmdir``.
    """
    normalized = input_str.replace("\\", "/")
    escaped = pattern.replace("\\", "/")
    escaped = re.sub(r"[.+^${}()|[\]\\]", r"\\\g<0>", escaped)
    escaped = escaped.replace("*", ".*").replace("?", ".")
    if escaped.endswith(" .*"):
        escaped = escaped[:-3] + "( .*)?"
    return bool(re.match("^" + escaped + "$", normalized, re.DOTALL))

def _ensure_filter_storage() -> None:
    """Ensure the filter storage directory exists."""
    ESSH_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Filter rule loading & evaluation
# ---------------------------------------------------------------------------
def _load_global_filters() -> list[dict]:
    """Load global filter rules from ``~/.essh/filters.json``.

    Expected format: ``{"bash": {"pattern": "action", ...}}``
    Each entry becomes ``{"permission": "bash", "pattern": "...", "action": "..."}``.
    Returns empty list if file missing or invalid.
    """
    if not FILTERS_FILE.exists():
        return []
    try:
        data = json.loads(FILTERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    rules: list[dict] = []
    for permission_key, actions in data.items():
        if not isinstance(actions, dict):
            continue
        for pattern, value in actions.items():
            if isinstance(value, dict):
                action_str = str(value.get("action", "deny")).lower().strip()
                msg = value.get("msg")
            else:
                action_str = str(value).lower().strip()
                msg = None
            if action_str not in FILTER_ACTIONS:
                continue
            rule: dict[str, object] = {
                "permission": permission_key,
                "pattern": pattern,
                "action": action_str,
            }
            if msg:
                rule["msg"] = str(msg)
            rules.append(rule)
    return rules

def _load_profile_filters(profile: dict | None) -> list[dict]:
    """Load per-profile filters from a profile dict.

    Expects ``profile.get("filters", {})`` as either a dict or absent.
    Same format as global: ``{"bash": {"pattern": "action", ...}}``
    """
    if not profile:
        return []
    raw = profile.get("filters")
    if not isinstance(raw, dict):
        return []
    rules: list[dict] = []
    for permission_key, actions in raw.items():
        if not isinstance(actions, dict):
            continue
        for pattern, value in actions.items():
            if isinstance(value, dict):
                action_str = str(value.get("action", "deny")).lower().strip()
                msg = value.get("msg")
            else:
                action_str = str(value).lower().strip()
                msg = None
            if action_str not in FILTER_ACTIONS:
                continue
            rule: dict[str, object] = {
                "permission": permission_key,
                "pattern": pattern,
                "action": action_str,
                "_source": "profile",
            }
            if msg:
                rule["msg"] = str(msg)
            rules.append(rule)
    return rules

def _evaluate_filters(permission: str, command: str, rules: list[dict]) -> str:
    """Evaluate ``command`` against consolidated ``rules`` using last-match-wins.

    Returns ``"allow"``, ``"ask"``, or ``"deny"``. Default ``"allow"``.
    """
    result = "allow"
    for rule in rules:
        if rule.get("permission") != permission:
            continue
        if _wildcard_match(command, rule.get("pattern", "")):
            result = rule.get("action", "allow")
    return result

def _action_message(permission: str, command: str, rules: list[dict]) -> str | None:
    """Return the custom ``msg`` from the last-matching rule, if any."""
    message: str | None = None
    for rule in rules:
        if rule.get("permission") != permission:
            continue
        if _wildcard_match(command, rule.get("pattern", "")):
            if "msg" in rule:
                message = str(rule["msg"])
    return message


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

def _verify_key_works(user: str, host: str, port: int, key_path: Path) -> bool:
    """Verify that SSH connects using *key_path* (no password fallback)."""
    try:
        result = subprocess.run(
            [
                *ssh_cmd(),
                "-i", str(key_path),
                "-o", "PasswordAuthentication=no",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10",
                "-p", str(port),
                f"{user}@{host}",
                "echo essh_ok",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _write_ssh_config(host: str, key_path: Path, user: str | None = None,
                     port: int = DEFAULT_PORT) -> None:
    """Add or update a Host entry in ~/.ssh/config so plain ``ssh`` works.

    Managed entries are marked with ``# essh:managed`` — they survive
    ``uv tool uninstall`` and keep working with plain OpenSSH.
    """
    config_path = Path.home() / ".ssh" / "config"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the Host block
    lines = [
        f"Host {host}",
        f"    HostName {host}",
    ]
    if user:
        lines.append(f"    User {user}")
    if port != DEFAULT_PORT:
        lines.append(f"    Port {port}")
    lines.append(f"    IdentityFile {key_path}")
    lines.append("    # essh:managed")

    # Read existing config
    if config_path.exists():
        content = config_path.read_text(encoding="utf-8")
    else:
        content = ""

    # Remove any previous essh:managed block for this host
    marker = "# essh:managed"
    if marker in content:
        # Split into blocks, remove ones ending with our marker for this host
        blocks = content.split("\n\n")
        kept = []
        for block in blocks:
            if marker in block and f"Host {host}" in block:
                continue  # remove old entry for this host
            kept.append(block)
        content = "\n\n".join(kept)

    # Append new entry
    if content and not content.endswith("\n"):
        content += "\n"
    if content and not content.endswith("\n\n"):
        content += "\n"
    content += "\n".join(lines) + "\n"

    config_path.write_text(content, encoding="utf-8")


def ssh_copy_id(user: str, host: str, port: int, pubkey_path: Path) -> None:
    """Pipe the public key into the remote authorized_keys file.

    The key is embedded directly in the remote command (not piped via stdin)
    because stdin-pipe timing breaks on password auth: Python closes the pipe
    before SSH finishes authenticating, so the remote ``cat`` gets EOF.
    """
    key = pubkey_path.read_text(encoding="utf-8").strip()
    # Escape single quotes so the key survives shell quoting
    key_escaped = key.replace("'", "'\\''")
    remote_cmd = (
        "mkdir -p ~/.ssh && "
        f"echo '{key_escaped}' >> ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys"
    )

    result = subprocess.run(
        [*ssh_cmd(), "-p", str(port), f"{user}@{host}", remote_cmd],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"ssh-copy-id FAILED (exit {result.returncode}).\n"
            f"The public key was NOT installed on {host}.\n"
            f"Possible causes:\n"
            f"  1. Wrong password — the key was generated but never copied.\n"
            f"  2. Remote server rejected the connection.\n"
            f"Fix: run 'essh add' again with the correct password, or manually:\n"
            f"  type {pubkey_path} | ssh -p {port} {user}@{host} "
            f"\"cat >> ~/.ssh/authorized_keys\""
        )


def _install_and_verify(user: str, host: str, port: int,
                       private_key: Path, pubkey: Path) -> None:
    """Install *pubkey* on remote and verify *private_key* works.

    Prints clear step-by-step output.  Raises ClickException on failure
    with an actionable fix command — never leaves the user guessing.
    """
    console.print(
        f"[bold]▶ Step 2/3: Installing key on {user}@{host}:{port}...[/bold]"
    )
    console.print("[dim]   You may be prompted for the remote password.[/dim]")
    ssh_copy_id(user, host, port, pubkey)

    # Try ssh-agent so passphrase-protected keys still verify
    ensure_ssh_agent(private_key)

    console.print("[bold]▶ Step 3/3: Verifying key...[/bold]")
    if not _verify_key_works(user, host, port, private_key):
        # Build OS-appropriate fix command
        if sys.platform == "win32":
            fix_cmd = (
                f'Get-Content "{pubkey}" | ssh -p {port} {user}@{host} '
                f'"cat >> ~/.ssh/authorized_keys"'
            )
        else:
            fix_cmd = (
                f"cat {pubkey} | ssh -p {port} {user}@{host} "
                f'"cat >> ~/.ssh/authorized_keys"'
            )
        raise click.ClickException(
            f"✗ VERIFICATION FAILED — key is NOT authorized on {host}.\n"
            f"   Possible causes:\n"
            f"     1. Remote server did not save the key.\n"
            f"     2. Key has a passphrase and ssh-agent is not running.\n"
            f"        Start agent:  ssh-agent  then  ssh-add {private_key}\n"
            f"     3. Remote sshd config rejects key-based auth.\n"
            f"   Fix — run this manually:\n"
            f"     {fix_cmd}\n"
            f"   Then test with:\n"
            f"     ssh -i {private_key} -p {port} {user}@{host} echo ok"
        )
    console.print(
        f"[green]   ✓ Key verified — password-less SSH to {host} works.[/green]"
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

def create_pending_request(name: str, command: str | None = None) -> None:
    """Create (or refresh) a pending request file. Errors if too recent.

    When ``command`` is provided, it is included in the JSON payload so the
    authorization step can display what command is being requested.
    """
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

    payload: dict[str, object] = {"timestamp": datetime.now().isoformat()}
    if command is not None:
        payload["command"] = command
    pending_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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
@click.version_option(__version__, prog_name="essh")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Portable SSH wrapper for Agent Sommelier.

    Save SSH host profiles, generate keys, and connect with agent-mode
    authorization support.

    \b
    Commands:
      add       Save a new SSH host profile.
      scp       Copy files with scp using saved profile names.
      rsync     Sync files with rsync using saved profile names.
      list      List saved profiles.
      rm        Remove a profile.
      edit      Modify an existing profile.
      export    Export profiles and keys to a tar.gz archive.
      import    Import profiles from a tar.gz archive.
      authorize Authorize a pending connection request.
      filter    Manage command filter rules for SSH connections.

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
@click.option("-y", "--yes", is_flag=True, help="Auto-approve key generation")
@click.option("--non-interactive", is_flag=True, help="Fail fast with error instead of prompting")
def add(first: str, second: str | None, name: str | None, identity: Path | None,
        yes: bool, non_interactive: bool) -> None:
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
    user, host, port = parse_host_string(target)

    # ── Self-repair: existing profile ──────────────────────────────────
    existing = find_profile(name)
    if existing:
        console.print(f"[dim]Profile '{name}' already exists.[/dim]")
        existing_key = existing.get("key_path")

        if existing_key:
            # Old-style named key (e.g. id_ed25519_k-int-cron).
            # Don't migrate keys — just write SSH config so plain `ssh` works.
            existing_key_path = Path(existing_key)
            console.print("[dim]Old-style named key — adding SSH config entry...[/dim]")
            if existing_key_path.is_file():
                _write_ssh_config(host, existing_key_path, user, port)
                console.print(
                    f"[green]✓ SSH config updated — plain `ssh {user}@{host}` now works.[/green]"
                )
                return
            # Key file missing — fall through to regenerate
            console.print("[yellow]⚠ Key file missing. Regenerating...[/yellow]")
        else:
            # Uses default keys — check if they still work
            console.print("[dim]Profile uses default SSH keys. Checking...[/dim]")
            if _try_ssh_default_keys(user, host, port):
                console.print("[green]✓ Default keys still work — no changes needed.[/green]")
                return
            console.print(
                "[yellow]⚠ Default keys no longer work. Generating a dedicated key...[/yellow]"
            )

    # ── New profile (or rebuild) ───────────────────────────────────────
    if identity:
        # Explicit identity provided
        private_key = identity.expanduser().resolve()
        if not private_key.is_file():
            raise click.ClickException(f"Key not found: {private_key}")

        console.print(
            f"[bold]▶ Step 1/3: Using existing key {private_key}[/bold]"
        )

        pub_source = Path(str(private_key) + ".pub")
        if not pub_source.is_file():
            pub_source = private_key.with_suffix(private_key.suffix + ".pub")
        if pub_source.is_file():
            _install_and_verify(user, host, port, private_key, pub_source)
        else:
            console.print(
                "[yellow]⚠ No public key found for this identity — skipping install.[/yellow]"
            )
            console.print(
                "[dim]   The key will be used for connections but was not pushed to the remote.[/dim]"
            )

        final_key_path = str(private_key)

    else:
        # No identity flag: try default keys or generate
        console.print(
            f"[bold]▶ Step 1/3: Checking SSH access to {user}@{host}:{port}...[/bold]"
        )

        if _try_ssh_default_keys(user, host, port):
            console.print(
                "[green]   ✓ Already have access via default SSH keys.[/green]"
            )
            final_key_path = ""
        else:
            console.print("[dim]   No existing key works.[/dim]")

            if non_interactive:
                raise click.ClickException(
                    "No working SSH key found. Use -i/--identity to provide one, "
                    "or -y/--yes to auto-generate."
                )

            if yes or not sys.stdin.isatty():
                generate = True
            else:
                generate = click.confirm(
                    "No existing key works. Generate one?", default=True
                )

            if not generate:
                raise click.ClickException(
                    "No key provided. "
                    "Use: essh add -i /path/to/key USER@HOST[:PORT]"
                )

            ssh_dir = Path.home() / ".ssh"
            ssh_dir.mkdir(parents=True, exist_ok=True)

            # Always use the SSH default key name so plain `ssh user@host`
            # works without any config — exactly like manual ssh-copy-id.
            key_path = ssh_dir / "id_ed25519"

            console.print(
                f"[dim]   Generating ed25519 key: {key_path}[/dim]"
            )
            if key_path.is_file():
                console.print(
                    "[dim]   Key already exists — reusing.[/dim]"
                )
            else:
                generate_keypair(name, key_path)

            pub_path = key_path.with_suffix(".pub")
            _install_and_verify(user, host, port, key_path, pub_path)

            # Store empty key_path — essh will use SSH defaults,
            # which auto-tries id_ed25519.  Same as plain `ssh`.
            final_key_path = ""

    # ── Save profile ───────────────────────────────────────────────────
    profile: dict = {
        "name": name,
        "user": user,
        "host": host,
        "port": port,
        "key_path": final_key_path,
    }

    profiles = load_profiles()
    # Remove old entry if self-repairing
    profiles = [p for p in profiles if p.get("name") != name]
    profiles.append(profile)
    save_profiles(profiles)

    console.print(
        f"[green]✓ DONE — Profile '{name}' saved.[/green]"
    )
    console.print(
        f"[dim]   Plain SSH works: ssh {user}@{host}[/dim]"
    )
    console.print(
        f"[dim]   Or via essh:   essh {name} echo ok[/dim]"
    )


# ---------------------------------------------------------------------------
# Transfer helpers (scp / rsync)
# ---------------------------------------------------------------------------


def _resolve_transfer_args(args: list[str]) -> tuple[list[str], dict[str, dict]]:
    """Resolve NAME:path patterns in scp/rsync arguments.

    Returns (resolved_args, profiles) where profiles maps name -> profile dict.
    The resolved args replace NAME:path with user@host:path.
    """
    resolved: list[str] = []
    profiles: dict[str, dict] = {}

    for arg in args:
        m = re.match(r'^([a-z0-9_-]+):(.*)$', arg)
        if m:
            name = m.group(1)
            path = m.group(2)
            profile = find_profile(name)
            if profile is None:
                known = list_profile_names()
                hint = f" Known profiles: {', '.join(known)}" if known else ""
                raise click.ClickException(
                    f"Profile '{name}' not found.{hint}"
                )
            profiles[name] = profile
            resolved.append(f"{profile['user']}@{profile['host']}:{path}")
        else:
            resolved.append(arg)

    return resolved, profiles


def _authorize_transfer_profiles(
    profiles: dict[str, dict],
    is_tty: bool,
) -> None:
    """Authorize all profiles for agent-mode transfers."""
    if is_tty:
        return
    for name in profiles:
        console.print(
            f"[yellow]Agent mode: requesting authorization for '{name}'...[/yellow]"
        )
        create_pending_request(name)
        try:
            wait_for_authorization(name)
            console.print(f"[green]Authorization granted for '{name}'.[/green]")
        except click.ClickException:
            console.print(
                f"[red]Authorization denied or timed out for '{name}'.[/red]"
            )
            raise


def _run_scp(
    resolved_args: list[str],
    profiles: dict[str, dict],
    is_tty: bool,
) -> int:
    """Build and run scp with resolved args and profile identities."""
    scp_bin = shutil.which("scp") or shutil.which("scp.exe")
    if not scp_bin:
        raise click.ClickException(
            "scp not found on PATH. Install OpenSSH client:\n"
            "  winget install Microsoft.OpenSSH.Beta    (Windows)\n"
            "  apt install openssh-client               (Debian/Ubuntu)\n"
            "  brew install openssh                     (macOS)"
        )

    # Collect identity/port args from profiles
    identity_args: list[str] = []
    seen_keys: set[str] = set()
    for profile in profiles.values():
        key_path_raw = profile.get("key_path") or ""
        if key_path_raw and key_path_raw not in seen_keys:
            identity_args.extend(["-i", key_path_raw])
            seen_keys.add(key_path_raw)
        port = profile.get("port", DEFAULT_PORT)
        if port != DEFAULT_PORT:
            identity_args.extend(["-P", str(port)])

    # Warn if multiple profiles have different keys (scp limitation)
    if len(seen_keys) > 1:
        console.print(
            "[yellow]Warning: multiple profiles with different keys. "
            "scp -i applies globally; consider using -3 (copy via localhost).[/yellow]"
        )

    cmd = [scp_bin] + identity_args + resolved_args

    if is_tty:
        result = subprocess.run(
            cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr
        )
    else:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout:
            click.echo(result.stdout, nl=False)
        if result.stderr:
            console.print(f"[dim]{result.stderr}[/dim]", end="")

    return result.returncode


def _run_rsync(
    resolved_args: list[str],
    profiles: dict[str, dict],
    is_tty: bool,
) -> int:
    """Build and run rsync with resolved args and profile identities."""
    rsync_bin = shutil.which("rsync") or shutil.which("rsync.exe")
    if not rsync_bin:
        raise click.ClickException(
            "rsync not found on PATH. Install rsync:\n"
            "  apt install rsync               (Debian/Ubuntu)\n"
            "  brew install rsync              (macOS)\n"
            "  winget install rsync            (Windows via winget/scoop/choco)"
        )

    # Build the SSH transport command for -e
    # Use the first profile's key/port settings
    first = next(iter(profiles.values()))
    ssh_parts = ["ssh"]
    key_path_raw = first.get("key_path") or ""
    if key_path_raw:
        ssh_parts.extend(["-i", key_path_raw])
    port = first.get("port", DEFAULT_PORT)
    if port != DEFAULT_PORT:
        ssh_parts.extend(["-p", str(port)])

    # Warn if multiple profiles with different keys
    seen_keys: set[str] = set()
    for p in profiles.values():
        k = p.get("key_path") or ""
        if k:
            seen_keys.add(k)
    if len(seen_keys) > 1:
        console.print(
            "[yellow]Warning: multiple profiles with different keys. "
            "Only the first profile's key is used for the SSH transport.[/yellow]"
        )

    cmd = [rsync_bin, "-e", " ".join(ssh_parts)] + resolved_args

    if is_tty:
        result = subprocess.run(
            cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr
        )
    else:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout:
            click.echo(result.stdout, nl=False)
        if result.stderr:
            console.print(f"[dim]{result.stderr}[/dim]", end="")

    return result.returncode


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

    # ---- Command filter evaluation ----
    if remote_command:
        command_str = " ".join(remote_command)
        global_rules = _load_global_filters()
        profile_rules = _load_profile_filters(profile)
        all_rules = global_rules + profile_rules  # profile overrides (last-match-wins)
        action = _evaluate_filters("bash", command_str, all_rules)

        if action == "deny":
            msg = _action_message("bash", command_str, all_rules)
            msg = msg or "This command is blocked by a filter rule."
            console.print(f"[red]❌ BLOCKED: {msg}[/red]")
            console.print(f"[dim]  Command: {command_str}[/dim]")
            sys.exit(1)

        if action == "ask":
            if is_tty:
                console.print(f"[yellow]Command requires authorization:[/yellow]")
                console.print(f"  [bold]{command_str}[/bold]")
                try:
                    click.confirm("Run this command?", default=True, abort=True)
                except click.Abort:
                    console.print("[red]Command cancelled.[/red]")
                    sys.exit(1)
            else:
                create_pending_request(name, command=command_str)
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

    # Read request details (backward compat: old format is plain text timestamp)
    cmd: str | None = None
    try:
        raw = pending_file.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if isinstance(payload, dict):
            cmd = payload.get("command")
    except (OSError, json.JSONDecodeError):
        cmd = None

    if cmd:
        console.print(f"[yellow]Pending request for '{name}':[/yellow]")
        console.print(f"  [bold]Command:[/bold] {cmd}")
        if sys.stdin.isatty():
            try:
                click.confirm("Authorize?", default=True, abort=True)
            except click.Abort:
                console.print("[red]Authorization cancelled.[/red]")
                return
    else:
        console.print(f"[yellow]Pending request for '{name}'.[/yellow]")

    pending_file.unlink()
    console.print(f"[green]'{name}' authorized.[/green]")


# ---------------------------------------------------------------------------
# filter group — manage command filter rules
# ---------------------------------------------------------------------------
@main.group(name="filter")
def filter_group() -> None:
    """Manage command filter rules for SSH connections.

    Use ``essh filter add``, ``essh filter rm``, ``essh filter list``,
    or ``essh filter clear`` with ``global`` (all profiles) or a profile name.
    """

@filter_group.command(name="add")
@click.argument("target")
@click.argument("pattern")
@click.option("--action", type=click.Choice(["deny", "ask", "allow"]), default="deny", help="Filter action")
@click.option("--message", "-m", default=None, help="Custom message (for deny/ask)")
def filter_add(target: str, pattern: str, action: str, message: str | None) -> None:
    """Add a filter rule.

    TARGET is ``global`` for all profiles, or a saved profile name.
    """
    if target == "global":
        _ensure_filter_storage()
        rules = _load_global_filters()
        rule: dict[str, str] = {"permission": "bash", "pattern": pattern, "action": action}
        if message:
            rule["msg"] = message
        rules.append(rule)
        # Serialize back to config format
        config: dict[str, dict[str, object]] = {}
        for r in rules:
            perm = r.get("permission", "bash")
            pat = r.get("pattern", "")
            act = r.get("action", "deny")
            msg = r.get("msg")
            if msg:
                config.setdefault(perm, {})[pat] = {"action": act, "msg": str(msg)}
            else:
                config.setdefault(perm, {})[pat] = act
        FILTERS_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
        console.print(f"[green]Global filter added: {pattern} → {action}[/green]")
    else:
        validate_name(target)
        profiles_list = load_profiles()
        # Find profile within the loaded list (don't use find_profile which re-reads)
        profile = next((p for p in profiles_list if p.get("name") == target), None)
        if profile is None:
            raise click.ClickException(f"Profile '{target}' not found.")
        raw_filters: object = profile.get("filters", {})
        filters_dict: dict[str, object] = raw_filters if isinstance(raw_filters, dict) else {}
        bash_raw: object = filters_dict.get("bash", {})
        bash_dict: dict[str, object] = bash_raw if isinstance(bash_raw, dict) else {}
        if message:
            bash_dict[pattern] = {"action": action, "msg": str(message)}
        else:
            bash_dict[pattern] = action
        filters_dict["bash"] = bash_dict
        profile["filters"] = filters_dict
        save_profiles(profiles_list)
        console.print(f"[green]Filter added for '{target}': {pattern} → {action}[/green]")

@filter_group.command(name="rm")
@click.argument("target")
@click.argument("pattern")
def filter_rm(target: str, pattern: str) -> None:
    """Remove a filter rule by pattern."""
    if target == "global":
        rules = _load_global_filters()
        before = len(rules)
        rules = [r for r in rules if r.get("pattern") != pattern or r.get("permission") != "bash"]
        removed = before - len(rules)
        if removed == 0:
            console.print(f"[yellow]No matching global rule found: {pattern}[/yellow]")
            return
        config: dict[str, dict[str, object]] = {}
        for r in rules:
            perm = r.get("permission", "bash")
            pat = r.get("pattern", "")
            act = r.get("action", "deny")
            msg = r.get("msg")
            if msg:
                config.setdefault(perm, {})[pat] = {"action": act, "msg": str(msg)}
            else:
                config.setdefault(perm, {})[pat] = act
        FILTERS_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
        console.print(f"[green]Removed {removed} global rule(s): {pattern}[/green]")
    else:
        validate_name(target)
        profiles_list = load_profiles()
        profile = next((p for p in profiles_list if p.get("name") == target), None)
        if profile is None:
            raise click.ClickException(f"Profile '{target}' not found.")
        raw_filters: object = profile.get("filters", {})
        filters_dict: dict[str, object] = raw_filters if isinstance(raw_filters, dict) else {}
        if "bash" not in filters_dict:
            console.print(f"[yellow]No filter rules for '{target}'.[/yellow]")
            return
        bash_raw: object = filters_dict.get("bash", {})
        bash_dict: dict[str, object] = bash_raw if isinstance(bash_raw, dict) else {}
        if pattern not in bash_dict:
            console.print(f"[yellow]No matching rule for '{target}': {pattern}[/yellow]")
            return
        del bash_dict[pattern]
        if bash_dict:
            filters_dict["bash"] = bash_dict
        else:
            del filters_dict["bash"]
        if filters_dict:
            profile["filters"] = filters_dict
        else:
            profile.pop("filters", None)
        save_profiles(profiles_list)
        console.print(f"[green]Removed filter for '{target}': {pattern}[/green]")

@filter_group.command(name="list")
@click.argument("target")
def filter_list(target: str) -> None:
    """List all filter rules for a target."""
    if target == "global":
        rules = _load_global_filters()
        console.print(f"[bold]Global filter rules:[/bold]")
    else:
        validate_name(target)
        profile = find_profile(target)
        if profile is None:
            raise click.ClickException(f"Profile '{target}' not found.")
        rules = _load_profile_filters(profile)
        console.print(f"[bold]Filter rules for '{target}':[/bold]")

    if not rules:
        console.print("[dim]No rules defined.[/dim]")
        return

    for i, rule in enumerate(rules, 1):
        action = rule.get("action", "?")
        pattern = rule.get("pattern", "?")
        msg = rule.get("msg", "")
        action_colored = {
            "allow": "[green]allow[/green]",
            "ask": "[yellow]ask[/yellow]",
            "deny": "[red]deny[/red]",
        }.get(action, action)
        line = f"  {i}. {pattern}  →  {action_colored}"
        if msg:
            line += f"  ({msg})"
        console.print(line)

@filter_group.command(name="clear")
@click.argument("target")
def filter_clear(target: str) -> None:
    """Remove ALL filter rules for a target."""
    if target == "global":
        if not FILTERS_FILE.exists():
            console.print("[yellow]No global filter file found.[/yellow]")
            return
        FILTERS_FILE.unlink()
        console.print("[green]Global filter rules cleared.[/green]")
    else:
        validate_name(target)
        profiles_list = load_profiles()
        profile = next((p for p in profiles_list if p.get("name") == target), None)
        if profile is None:
            raise click.ClickException(f"Profile '{target}' not found.")
        if "filters" not in profile:
            console.print(f"[yellow]No filter rules for '{target}'.[/yellow]")
            return
        profile.pop("filters", None)
        save_profiles(profiles_list)
        console.print(f"[green]Filter rules cleared for '{target}'.[/green]")


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
# edit
# ---------------------------------------------------------------------------

@main.command(name="edit")
@click.argument("name")
@click.option("--host", help="New hostname")
@click.option("--port", type=int, help="New port")
@click.option("--user", help="New username")
@click.option("-i", "--identity", type=click.Path(exists=True, path_type=Path), help="New identity key path")
@click.option("-n", "--new-name", help="Rename the profile")
def edit_profile(name: str, host: str | None, port: int | None, user: str | None,
                 identity: Path | None, new_name: str | None) -> None:
    """Modify an existing SSH host profile.

    Only the provided fields are updated. Unchanged fields keep their values.
    """
    validate_name(name)

    profiles = load_profiles()
    profile = find_profile(name)
    if profile is None:
        raise click.ClickException(f"Profile '{name}' not found.")

    # Remove the old profile
    profiles = [p for p in profiles if p.get("name") != name]

    # Update fields
    if new_name is not None:
        validate_name(new_name)
        profile["name"] = new_name
    if host is not None:
        profile["host"] = host
    if port is not None:
        profile["port"] = port
    if user is not None:
        profile["user"] = user
    if identity is not None:
        profile["key_path"] = str(identity.expanduser().resolve())

    # Re-add with updated values
    profiles.append(profile)
    save_profiles(profiles)

    display_name = new_name or name
    console.print(f"[green]Profile '{display_name}' updated.[/green]")


# ---------------------------------------------------------------------------
# scp
# ---------------------------------------------------------------------------

@main.command(
    name="scp",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.pass_context
def scp_cmd(ctx: click.Context) -> None:
    """Copy files with scp using saved profile names.

    \b
    Usage:  essh scp [SCP_OPTIONS...] SOURCE DEST

    Use NAME:path instead of user@host:path for saved profiles.

    \b
    Examples:
      essh scp my-server:/remote/file.txt ./local/
      essh scp -r ./local/dir/ my-server:/remote/dir/
      essh scp my-server:/remote/log ./
    """
    raw_args = list(ctx.args)
    if not raw_args:
        raise click.ClickException("Usage: essh scp [OPTIONS...] SOURCE DEST")

    resolved_args, profiles = _resolve_transfer_args(raw_args)

    is_tty = sys.stdin.isatty()
    _authorize_transfer_profiles(profiles, is_tty)

    exit_code = _run_scp(resolved_args, profiles, is_tty)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# rsync
# ---------------------------------------------------------------------------

@main.command(
    name="rsync",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.pass_context
def rsync_cmd(ctx: click.Context) -> None:
    """Sync files with rsync using saved profile names.

    \b
    Usage:  essh rsync [RSYNC_OPTIONS...] SOURCE DEST

    Use NAME:path instead of user@host:path for saved profiles.

    \b
    Examples:
      essh rsync -avz my-server:/var/www/ ./www-backup/
      essh rsync --progress ./build/ my-server:/srv/app/
    """
    raw_args = list(ctx.args)
    if not raw_args:
        raise click.ClickException("Usage: essh rsync [OPTIONS...] SOURCE DEST")

    resolved_args, profiles = _resolve_transfer_args(raw_args)

    is_tty = sys.stdin.isatty()
    _authorize_transfer_profiles(profiles, is_tty)

    exit_code = _run_rsync(resolved_args, profiles, is_tty)
    sys.exit(exit_code)


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
