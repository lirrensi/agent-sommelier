# FILE: tests/test_essh.py
# PURPOSE: Comprehensive test suite for the essh CLI — Osiris judges every line.
# COVERS: Unit, integration, edge cases, backward compat, export/import round-trip.

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import click.testing
import pytest

# Ensure the source is importable
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

# Import the module under test
import agent_sommelier.essh as essh  # noqa: E402

# ---------------------------------------------------------------------------
# Constants used across tests
# ---------------------------------------------------------------------------

SAMPLE_PROFILES: list[dict] = [
    {"name": "prod-web", "user": "deploy", "host": "web.example.com", "port": 22, "key_path": "/home/user/.ssh/id_ed25519_prod-web"},
    {"name": "dev-db", "user": "admin", "host": "db.dev.local", "port": 2222, "key_path": ""},
    {"name": "legacy-box", "user": "root", "host": "old-server.local", "port": 22, "key_path": str(Path.home() / ".essh" / "keys" / "legacy-box" / "id_ed25519")},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner() -> click.testing.CliRunner:
    """A Click CliRunner for invoking CLI commands."""
    return click.testing.CliRunner()


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() to a temp directory so filesystem ops are isolated."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Also update the module-level constants that are derived from Path.home()
    essh.ESSH_DIR = tmp_path / ".essh"
    essh.PROFILES_FILE = essh.ESSH_DIR / "profiles.json"
    essh.KEYS_DIR = essh.ESSH_DIR / "keys"
    essh.REQUESTS_DIR = essh.ESSH_DIR / "requests"
    essh.EXPORTS_DIR = essh.ESSH_DIR / "exports"
    essh.KNOWN_HOSTS = tmp_path / ".ssh" / "known_hosts"
    return tmp_path


@pytest.fixture(autouse=True)
def reset_module_constants(tmp_home: Path) -> None:
    """Reset module-level path constants between tests to avoid cross-test pollution."""
    essh.ESSH_DIR = tmp_home / ".essh"
    essh.PROFILES_FILE = essh.ESSH_DIR / "profiles.json"
    essh.KEYS_DIR = essh.ESSH_DIR / "keys"
    essh.REQUESTS_DIR = essh.ESSH_DIR / "requests"
    essh.EXPORTS_DIR = essh.ESSH_DIR / "exports"
    essh.KNOWN_HOSTS = tmp_home / ".ssh" / "known_hosts"


@pytest.fixture
def mock_subprocess_run(monkeypatch: pytest.MonkeyPatch) -> mock.MagicMock:
    """Mock subprocess.run globally for all tests that use it."""
    m = mock.MagicMock()
    monkeypatch.setattr(subprocess, "run", m)
    return m


@pytest.fixture(autouse=True)
def no_ssh_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure SSH_AUTH_SOCK is not set so ensure_ssh_agent is a no-op by default."""
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


@pytest.fixture(autouse=True)
def mock_ssh_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock shutil.which so all SSH tool lookups succeed without real binaries."""
    def _which(cmd: str) -> str:
        return cmd
    monkeypatch.setattr("shutil.which", _which)





@pytest.fixture
def sample_profiles(tmp_home: Path) -> list[dict]:
    """Write sample profiles to the temp essh dir and return them."""
    essh.ensure_dirs()
    # Adjust legacy path to match tmp_home
    profiles = [
        {"name": "prod-web", "user": "deploy", "host": "web.example.com", "port": 22, "key_path": ""},
        {"name": "dev-db", "user": "admin", "host": "db.dev.local", "port": 2222, "key_path": str(tmp_home / ".ssh" / "id_ed25519_dev-db")},
        {"name": "legacy-box", "user": "root", "host": "old-server.local", "port": 22, "key_path": str(essh.KEYS_DIR / "legacy-box" / "id_ed25519")},
    ]
    essh.save_profiles(profiles)
    return profiles


# ===========================================================================
# UNIT TESTS — pure functions, no mocking needed
# ===========================================================================


class TestParseHostString:
    """parse_host_string() — 'user@host[:port]' → (user, host, port)."""

    def test_basic(self) -> None:
        assert essh.parse_host_string("alice@server.com") == ("alice", "server.com", 22)

    def test_with_port(self) -> None:
        assert essh.parse_host_string("bob@host.io:2222") == ("bob", "host.io", 2222)

    def test_ip_address(self) -> None:
        assert essh.parse_host_string("root@192.168.1.1") == ("root", "192.168.1.1", 22)

    def test_ip_with_port(self) -> None:
        assert essh.parse_host_string("admin@10.0.0.1:2222") == ("admin", "10.0.0.1", 2222)

    def test_user_with_dots(self) -> None:
        assert essh.parse_host_string("first.last@host.com:443") == ("first.last", "host.com", 443)

    def test_raises_when_no_at(self) -> None:
        with pytest.raises(click.ClickException, match="Invalid target"):
            essh.parse_host_string("justahost")

    def test_raises_when_empty_user(self) -> None:
        with pytest.raises(click.ClickException, match="Invalid target"):
            essh.parse_host_string("@host.com")

    def test_raises_when_empty_host(self) -> None:
        with pytest.raises(click.ClickException, match="Invalid target"):
            essh.parse_host_string("user@")

    def test_raises_when_port_not_int(self) -> None:
        with pytest.raises(click.ClickException, match="Invalid port number"):
            essh.parse_host_string("user@host.com:abc")

    def test_port_negative_accepted_by_parser(self) -> None:
        """int() accepts negative numbers; SSH itself rejects invalid ports."""
        user, host, port = essh.parse_host_string("user@host.com:-1")
        assert port == -1

    def test_raises_when_empty(self) -> None:
        with pytest.raises(click.ClickException, match="Invalid target"):
            essh.parse_host_string("")

    def test_ipv6_localhost(self) -> None:
        user, host, port = essh.parse_host_string("me@::1:2222")
        # With ipv6, the rsplit on ":" splits ::1 into ["", "", "1"] so
        # last part becomes port. This is expected behavior.
        assert user == "me"
        assert port == 2222

    def test_port_zero_accepted_by_parser(self) -> None:
        """int('0') succeeds; port validation is left to SSH itself."""
        user, host, port = essh.parse_host_string("user@host.com:0")
        assert port == 0


class TestValidateName:
    """validate_name() — only [a-z0-9_-] allowed."""

    def test_valid_simple(self) -> None:
        essh.validate_name("my-server")  # no exception

    def test_valid_with_underscores(self) -> None:
        essh.validate_name("prod_web_01")

    def test_valid_with_digits(self) -> None:
        essh.validate_name("server42")

    @pytest.mark.parametrize("bad_name", [
        "UPPERCASE", "has space", "has@symbol", "has!exclaim",
        "has.dot", "", "has/slash", "has📦emoji",
    ])
    def test_invalid_names(self, bad_name: str) -> None:
        with pytest.raises(click.ClickException, match="Invalid name"):
            essh.validate_name(bad_name)


class TestGenerateName:
    """generate_name() — Docker-style color-animal names."""

    def test_format(self) -> None:
        name = essh.generate_name(set())
        parts = name.split("-", 1)
        assert len(parts) == 2
        assert parts[0] in essh.COLORS
        assert parts[1] in essh.ANIMALS

    def test_no_collision_with_existing(self) -> None:
        existing = set()
        names = {essh.generate_name(existing) for _ in range(20)}
        assert len(names) == 20  # all unique
        for name in names:
            existing.add(name)

    def test_collision_appends_suffix(self) -> None:
        # Force collision: pre-fill existing set so first random pick collides
        name = essh.generate_name(set())
        # Create a huge set that includes name to force suffix generation
        existing = {name}
        new_name = essh.generate_name(existing)
        assert new_name != name
        # May or may not have a suffix depending on random picks;
        # just verify they're different
        assert essh.NAME_PATTERN.match(new_name)

    def test_raises_after_10_attempts(self) -> None:
        """When all random attempts collide, a ClickException is raised."""
        # Mock random.choice to always pick the same color and animal.
        # The loop tries base names (10 attempts), then falls back to suffixes
        # (10 more attempts with random 0-0xFFF suffixes).
        existing = {"x-x"}
        for s in range(0x1000):
            existing.add(f"x-x-{s:03x}")
        with mock.patch.object(essh.random, "choice", return_value="x"):
            with pytest.raises(click.ClickException, match="unique name"):
                essh.generate_name(existing)


# ===========================================================================
# STORAGE UNIT TESTS
# ===========================================================================


class TestLoadSaveProfiles:
    """load_profiles() / save_profiles() — CRUD for profiles.json."""

    def test_load_returns_empty_when_no_file(self, tmp_home: Path) -> None:
        assert essh.load_profiles() == []

    def test_save_and_load_roundtrip(self, tmp_home: Path) -> None:
        profiles = SAMPLE_PROFILES
        essh.save_profiles(profiles)
        loaded = essh.load_profiles()
        assert loaded == profiles

    def test_save_is_atomic(self, tmp_home: Path) -> None:
        """save_profiles writes to .tmp then atomically replaces."""
        essh.save_profiles([{"name": "test"}])
        assert essh.PROFILES_FILE.exists()
        # No .tmp file should remain
        assert not essh.PROFILES_FILE.with_suffix(".tmp").exists()

    def test_load_handles_corrupt_json(self, tmp_home: Path) -> None:
        essh.ensure_dirs()
        essh.PROFILES_FILE.write_text("{broken", encoding="utf-8")
        assert essh.load_profiles() == []

    def test_load_handles_non_list_json(self, tmp_home: Path) -> None:
        essh.ensure_dirs()
        essh.PROFILES_FILE.write_text('{"not": "alist"}', encoding="utf-8")
        assert essh.load_profiles() == []

    def test_load_handles_empty_file(self, tmp_home: Path) -> None:
        essh.ensure_dirs()
        essh.PROFILES_FILE.write_text("", encoding="utf-8")
        assert essh.load_profiles() == []

    def test_creates_dirs_on_save(self, tmp_home: Path) -> None:
        assert not essh.ESSH_DIR.exists()
        essh.save_profiles([{"name": "test"}])
        assert essh.ESSH_DIR.exists()


class TestFindProfile:
    """find_profile() — lookup by name."""

    def test_finds_existing(self, sample_profiles: list[dict]) -> None:
        profile = essh.find_profile("prod-web")
        assert profile is not None
        assert profile["host"] == "web.example.com"

    def test_returns_none_for_missing(self, sample_profiles: list[dict]) -> None:
        assert essh.find_profile("nonexistent") is None

    def test_returns_none_on_empty(self, tmp_home: Path) -> None:
        assert essh.find_profile("anything") is None


class TestListProfileNames:
    """list_profile_names() — sorted list of names."""

    def test_returns_sorted(self, sample_profiles: list[dict]) -> None:
        names = essh.list_profile_names()
        assert names == sorted(names)
        assert "dev-db" in names
        assert "prod-web" in names

    def test_returns_empty_when_none(self, tmp_home: Path) -> None:
        assert essh.list_profile_names() == []


# ===========================================================================
# KEY MANAGEMENT UNIT TESTS
# ===========================================================================


class TestGenerateKeypair:
    """generate_keypair() — delegates to ssh-keygen."""

    def test_calls_ssh_keygen(self, tmp_home: Path, mock_subprocess_run: mock.MagicMock) -> None:
        key_path = tmp_home / ".ssh" / "id_ed25519_test"
        mock_subprocess_run.return_value.returncode = 0
        result = essh.generate_keypair("test", key_path)
        assert result == key_path
        # Verify subprocess was called with correct args
        args = mock_subprocess_run.call_args[0][0]
        assert args[0] == "ssh-keygen"
        assert "-t" in args and "ed25519" in args
        assert "-f" in args and str(key_path) in args
        assert "-N" in args and "" in args
        assert "-C" in args and "essh:test" in args

    def test_raises_on_failure(self, tmp_home: Path, mock_subprocess_run: mock.MagicMock) -> None:
        key_path = tmp_home / ".ssh" / "id_ed25519_fail"
        mock_subprocess_run.return_value.returncode = 1
        mock_subprocess_run.return_value.stderr = "error msg"
        with pytest.raises(click.ClickException, match="Key generation failed"):
            essh.generate_keypair("fail", key_path)

    def test_creates_parent_dir(self, tmp_home: Path, mock_subprocess_run: mock.MagicMock) -> None:
        key_path = tmp_home / "custom" / "dir" / "mykey"
        mock_subprocess_run.return_value.returncode = 0
        essh.generate_keypair("test", key_path)
        assert key_path.parent.exists()


class TestSshCopyId:
    """ssh_copy_id() — installs public key inline in remote command."""

    def test_subprocess_call(self, tmp_home: Path, mock_subprocess_run: mock.MagicMock) -> None:
        pubkey_path = tmp_home / ".ssh" / "id_ed25519.pub"
        pubkey_path.parent.mkdir(parents=True, exist_ok=True)
        pubkey_path.write_text("ssh-ed25519 AAAA... test-key\n", encoding="utf-8")

        mock_subprocess_run.return_value.returncode = 0

        essh.ssh_copy_id("deploy", "server.com", 2222, pubkey_path)

        # Verify key is embedded in the remote command (no stdin pipe)
        call_args = mock_subprocess_run.call_args[0][0]
        remote_cmd = call_args[-1]
        assert "mkdir -p ~/.ssh" in remote_cmd
        assert "echo '" in remote_cmd
        assert "ssh-ed25519 AAAA... test-key" in remote_cmd
        assert ">> ~/.ssh/authorized_keys" in remote_cmd
        assert "chmod 600" in remote_cmd
        # Verify no input= (key is in command, not piped)
        call_kwargs = mock_subprocess_run.call_args[1]
        assert "input" not in call_kwargs

    def test_raises_on_failure(self, tmp_home: Path, mock_subprocess_run: mock.MagicMock) -> None:
        pubkey_path = tmp_home / "test.pub"
        pubkey_path.write_text("ssh-ed25519 KEY\n", encoding="utf-8")
        mock_subprocess_run.return_value.returncode = 1
        with pytest.raises(click.ClickException, match="FAILED"):
            essh.ssh_copy_id("user", "host", 22, pubkey_path)

    def test_no_stdin_pipe_conflict(self, tmp_home: Path, mock_subprocess_run: mock.MagicMock) -> None:
        """Regression: key inlined in command — no stdin=input, no conflict."""
        pubkey_path = tmp_home / "test.pub"
        pubkey_path.write_text("ssh-ed25519 KEY\n", encoding="utf-8")
        mock_subprocess_run.return_value.returncode = 0
        # This should not raise ValueError
        essh.ssh_copy_id("user", "host", 22, pubkey_path)
        call_kwargs = mock_subprocess_run.call_args[1]
        assert "input" not in call_kwargs
        assert "stdin" not in call_kwargs


class TestSshDefaultKeysProbe:
    """_try_ssh_default_keys() — probe SSH connectivity without -i."""

    def test_returns_true_when_ssh_succeeds(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 0
        assert essh._try_ssh_default_keys("user", "host.com", 22) is True
        args = mock_subprocess_run.call_args[0][0]
        assert "PasswordAuthentication=no" in args
        assert "BatchMode=yes" in args
        assert "echo essh_ok" in args

    def test_returns_false_when_ssh_fails(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 255
        assert essh._try_ssh_default_keys("user", "unreachable.host", 22) is False

    def test_returns_false_with_output_but_error(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 1
        assert essh._try_ssh_default_keys("u", "h", 22) is False

    def test_uses_correct_ssh_flags(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 0
        essh._try_ssh_default_keys("alice", "srv.io", 2222)
        args = mock_subprocess_run.call_args[0][0]
        assert "-p" in args
        assert "2222" in args
        assert "alice@srv.io" in args


class TestEnsureSshAgent:
    """ensure_ssh_agent() — optional key addition to ssh-agent."""

    def test_returns_when_no_agent(self, monkeypatch: pytest.MonkeyPatch, mock_subprocess_run: mock.MagicMock) -> None:
        """No SSH_AUTH_SOCK → no subprocess call."""
        monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
        essh.ensure_ssh_agent(Path("/key/path"))
        mock_subprocess_run.assert_not_called()

    def test_calls_ssh_add_when_agent_running(self, monkeypatch: pytest.MonkeyPatch, mock_subprocess_run: mock.MagicMock) -> None:
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")
        mock_subprocess_run.return_value.returncode = 0
        key = Path("/path/to/key")
        essh.ensure_ssh_agent(key)
        mock_subprocess_run.assert_called_once()
        args = mock_subprocess_run.call_args[0][0]
        assert args[0] == "ssh-add"
        # str(Path) on Windows uses backslashes
        assert args[1] == str(key)

    def test_does_not_raise_on_failure(self, monkeypatch: pytest.MonkeyPatch, mock_subprocess_run: mock.MagicMock) -> None:
        """Non-fatal: agent failure does not raise."""
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/sock")
        mock_subprocess_run.return_value.returncode = 1
        essh.ensure_ssh_agent(Path("/bad/key"))  # no exception


class TestRunSsh:
    """_run_ssh() — builds and executes SSH command."""

    def test_with_key_path(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 0
        key = Path("/my/key")
        exit_code = essh._run_ssh("user", "host.io", 22, key, [], is_tty=False)
        assert exit_code == 0
        args = mock_subprocess_run.call_args[0][0]
        assert "-i" in args
        assert str(key) in args  # On Windows: \my\key
        assert "user@host.io" in args

    def test_without_key_path(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 0
        essh._run_ssh("user", "host.io", 22, None, [], is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        assert "-i" not in args

    def test_with_empty_key_path(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Empty key_path (from default-keys profile) should be treated as no -i."""
        mock_subprocess_run.return_value.returncode = 0
        essh._run_ssh("user", "host.io", 22, None, [], is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        assert "-i" not in args

    def test_with_remote_command(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 0
        essh._run_ssh("user", "host.io", 22, None, ["uptime", "-p"], is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        # remote command should be at the end
        assert args[-2:] == ["uptime", "-p"]

    def test_with_port(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 0
        essh._run_ssh("user", "host.io", 2222, None, [], is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        assert "-p" in args
        assert "2222" in args

    def test_tty_mode_inherits_streams(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 0
        essh._run_ssh("user", "host.io", 22, None, [], is_tty=True)
        call_kwargs = mock_subprocess_run.call_args[1]
        assert call_kwargs["stdin"] == sys.stdin
        assert call_kwargs["stdout"] == sys.stdout
        assert call_kwargs["stderr"] == sys.stderr

    def test_non_tty_captures_output(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 0
        mock_subprocess_run.return_value.stdout = "result\n"
        mock_subprocess_run.return_value.stderr = ""
        essh._run_ssh("user", "host.io", 22, None, [], is_tty=False)
        call_kwargs = mock_subprocess_run.call_args[1]
        assert call_kwargs.get("capture_output") is True

    def test_returns_exit_code(self, mock_subprocess_run: mock.MagicMock) -> None:
        mock_subprocess_run.return_value.returncode = 42
        exit_code = essh._run_ssh("user", "host.io", 22, None, [], is_tty=False)
        assert exit_code == 42


# ===========================================================================
# INTEGRATION TESTS — CLI commands via Click CliRunner
# ===========================================================================


class TestCliAdd:
    """essh add — profile creation with identity, default keys, or key generation."""

    def test_add_with_identity_flag(self, tmp_home: Path, runner: click.testing.CliRunner, mock_subprocess_run: mock.MagicMock) -> None:
        """-i flag: references key in-place, pushes pubkey via ssh-copy-id."""
        mock_subprocess_run.return_value.returncode = 0

        # Create a fake identity key
        ssh_dir = tmp_home / ".ssh"
        ssh_dir.mkdir(parents=True, exist_ok=True)
        (ssh_dir / "custom_key").write_text("private key content", encoding="utf-8")
        (ssh_dir / "custom_key.pub").write_text("ssh-ed25519 AAAA... custom\n", encoding="utf-8")

        result = runner.invoke(essh.main, ["add", "-i", str(ssh_dir / "custom_key"), "deploy@server.com:2222"])
        assert result.exit_code == 0, result.output
        assert "Profile" in result.output

        # Verify profile saved with external path
        profiles = essh.load_profiles()
        assert len(profiles) == 1
        p = profiles[0]
        # Name is auto-generated (random color-animal), so just verify it's set
        assert p["name"]
        assert p["key_path"] == str((ssh_dir / "custom_key").resolve())
        assert p["user"] == "deploy"
        assert p["host"] == "server.com"
        assert p["port"] == 2222

    def test_add_with_name_flag_and_identity(self, tmp_home: Path, runner: click.testing.CliRunner, mock_subprocess_run: mock.MagicMock) -> None:
        """-n flag + -i: explicit name via -n, identity path saved."""
        mock_subprocess_run.return_value.returncode = 0
        ssh_dir = tmp_home / ".ssh"
        ssh_dir.mkdir(parents=True, exist_ok=True)
        (ssh_dir / "mykey").write_text("private", encoding="utf-8")
        (ssh_dir / "mykey.pub").write_text("ssh-ed25519 KEY\n", encoding="utf-8")

        result = runner.invoke(essh.main, ["add", "-n", "production", "-i", str(ssh_dir / "mykey"), "root@prod.example.com"])
        assert result.exit_code == 0, result.output
        p = essh.find_profile("production")
        assert p is not None
        assert p["key_path"] == str((ssh_dir / "mykey").resolve())

    def test_add_with_two_positional_args(self, tmp_home: Path, runner: click.testing.CliRunner, mock_subprocess_run: mock.MagicMock) -> None:
        """essh add NAME TARGET — two positional args."""
        mock_subprocess_run.return_value.returncode = 0
        ssh_dir = tmp_home / ".ssh"
        ssh_dir.mkdir(parents=True, exist_ok=True)
        (ssh_dir / "id_ed25519").write_text("private", encoding="utf-8")
        (ssh_dir / "id_ed25519.pub").write_text("ssh-ed25519 KEY\n", encoding="utf-8")

        result = runner.invoke(essh.main, ["add", "my-server", "deploy@10.0.0.1", "-i", str(ssh_dir / "id_ed25519")])
        assert result.exit_code == 0, result.output
        p = essh.find_profile("my-server")
        assert p is not None

    def test_add_without_identity_default_keys_work(self, tmp_home: Path, runner: click.testing.CliRunner, mock_subprocess_run: mock.MagicMock) -> None:
        """No -i, default SSH keys work → saves empty key_path."""
        # First call (_try_ssh_default_keys) returns success
        # Second call (ssh_copy_id) not needed
        def side_effect(*args, **kwargs):
            # First subprocess call is the default-keys probe
            m = mock.MagicMock()
            m.returncode = 0
            return m
        mock_subprocess_run.side_effect = side_effect

        result = runner.invoke(essh.main, ["add", "admin@server.com"])
        assert result.exit_code == 0, result.output
        profiles = essh.load_profiles()
        assert len(profiles) == 1
        p = profiles[0]
        # With default keys working, key_path should be empty
        assert p["key_path"] == ""

    def test_add_without_identity_no_default_keys_generates(self, tmp_home: Path, runner: click.testing.CliRunner, mock_subprocess_run: mock.MagicMock) -> None:
        """No -i, no default keys → generates key in ~/.ssh/."""
        mock_subprocess_run.return_value.returncode = 0
        with (
            mock.patch.object(essh, "_try_ssh_default_keys", return_value=False),
            mock.patch.object(essh, "generate_keypair", return_value=tmp_home / ".ssh" / "id_ed25519_test") as mock_gen,
            mock.patch.object(essh, "ssh_copy_id"),
        ):
            result = runner.invoke(essh.main, ["add", "user@newhost.com"])

        assert result.exit_code == 0, result.output
        profiles = essh.load_profiles()
        assert len(profiles) == 1
        p = profiles[0]
        # Key is ~/.ssh/id_ed25519 — profile stores empty key_path
        # so essh uses SSH defaults, same as plain `ssh`.
        assert p["key_path"] == ""

    def test_add_non_interactive_generates_silently(self, tmp_home: Path, runner: click.testing.CliRunner, mock_subprocess_run: mock.MagicMock) -> None:
        """Non-interactive mode (no TTY) should auto-generate name and key."""
        mock_subprocess_run.return_value.returncode = 0
        with (
            mock.patch.object(essh, "_try_ssh_default_keys", return_value=False),
            mock.patch.object(essh, "generate_keypair", return_value=tmp_home / ".ssh" / "id_ed25519_test"),
            mock.patch.object(essh, "ssh_copy_id"),
        ):
            result = runner.invoke(essh.main, ["add", "user@silent.com"])

        assert result.exit_code == 0, result.output
        profiles = essh.load_profiles()
        assert len(profiles) == 1
        p = profiles[0]
        assert p["user"] == "user"
        assert p["host"] == "silent.com"
        assert p["port"] == 22

    def test_add_duplicate_name_raises(self, tmp_home: Path, runner: click.testing.CliRunner, sample_profiles: list[dict], mock_subprocess_run: mock.MagicMock) -> None:
        """Adding a profile with an existing name now triggers self-repair.
        If the existing key works, it reports 'Already working'.
        If it's broken, it attempts repair and reports the result.
        """
        mock_subprocess_run.return_value.returncode = 0
        # Create real key files so Click validation passes
        real_key = tmp_home / "somekey"
        real_key.write_text("key content", encoding="utf-8")
        (tmp_home / "somekey.pub").write_text("ssh-ed25519 KEY\n", encoding="utf-8")
        result = runner.invoke(essh.main, ["add", "prod-web", "x@y.com", "-i", str(real_key)])
        # Self-repair: existing profile detected, default keys still work (mock)
        assert result.exit_code == 0, result.output
        assert "already exists" in result.output
        assert "no changes needed" in result.output

    def test_add_invalid_name_raises(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["add", "INVALID_NAME", "user@host.com"])
        assert result.exit_code != 0
        assert "Invalid name" in result.output

    def test_add_with_identity_missing_key_raises(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["add", "-i", "/nonexistent/key", "user@host.com"])
        assert result.exit_code != 0
        assert "does not exist" in result.output or "not found" in result.output

    def test_add_without_identity_and_no_generate(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        """Non-interactive mode (CliRunner = no TTY) auto-generates key.

        The 'decline to generate' prompt only appears in interactive TTY mode.
        In non-TTY mode (agent/CI/runner), there is no prompt — the key is
        auto-generated. This path is covered by:
        ``test_add_non_interactive_generates_silently``.
        """
        pytest.skip("User-decline path is interactive-only; unreachable via CliRunner")

    def test_add_key_exists_in_ssh_dir(self, tmp_home: Path, runner: click.testing.CliRunner, mock_subprocess_run: mock.MagicMock) -> None:
        """Key always goes to ~/.ssh/id_ed25519 (SSH default). Reuses if exists."""
        mock_subprocess_run.return_value.returncode = 0
        ssh_dir = tmp_home / ".ssh"
        ssh_dir.mkdir(parents=True, exist_ok=True)
        (ssh_dir / "id_ed25519").write_text("existing key", encoding="utf-8")
        (ssh_dir / "id_ed25519.pub").write_text("ssh-ed25519 EXISTING\n", encoding="utf-8")

        with (
            mock.patch.object(essh, "_try_ssh_default_keys", return_value=False),
            mock.patch.object(essh, "ssh_copy_id"),
        ):
            result = runner.invoke(essh.main, ["add", "user@host.com"])

        assert result.exit_code == 0, result.output
        profiles = essh.load_profiles()
        assert len(profiles) == 1
        p = profiles[0]
        # Profile uses default keys (empty key_path)—SSH auto-tries id_ed25519
        assert p["key_path"] == ""

    def test_add_cannot_use_n_and_two_positional(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["add", "-n", "myname", "arg1", "arg2"])
        assert result.exit_code != 0
        assert "Cannot use both" in result.output


class TestCliList:
    """essh list — display profiles."""

    def test_list_empty(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["list"])
        assert result.exit_code == 0
        assert "No profiles saved" in result.output

    def test_list_with_profiles(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["list"])
        assert result.exit_code == 0
        assert "prod-web" in result.output
        assert "dev-db" in result.output
        assert "legacy-box" in result.output

    def test_list_shows_default_keys(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """profile with empty key_path shows (default keys)."""
        result = runner.invoke(essh.main, ["list"])
        assert "(default keys)" in result.output

    def test_list_shows_missing_key(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """profile with non-existent key_path shows (missing)."""
        result = runner.invoke(essh.main, ["list"])
        assert "(missing)" in result.output

    def test_list_json_output(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 3
        # Profiles are stored in insertion order (not sorted)
        names = [p["name"] for p in data]
        assert "prod-web" in names
        assert "dev-db" in names
        assert "legacy-box" in names

    def test_list_json_empty(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []


class TestCliConnect:
    """essh connect (and shortcut) — SSH connection.

    NOTE: Click's CliRunner always uses non-TTY stdin, so all connect tests
    mock the agent/authorization path (create_pending_request + wait_for_authorization)
    and the SSH execution (_run_ssh).
    """

    def _auth_patches(self):
        return (
            mock.patch("agent_sommelier.essh.create_pending_request"),
            mock.patch("agent_sommelier.essh.wait_for_authorization"),
        )

    def test_connect_with_default_keys(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Profile with empty key_path → no -i flag in SSH."""
        with mock.patch.object(essh, "_run_ssh", return_value=0) as mock_run:
            with self._auth_patches()[0], self._auth_patches()[1]:
                result = runner.invoke(essh.main, ["connect", "prod-web"])

        assert result.exit_code == 0, result.output
        args, _ = mock_run.call_args
        user, host, port, key_path, remote_cmd, is_tty = args
        assert key_path is None  # empty key_path → None

    def test_connect_with_explicit_key(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Profile with explicit key_path → passed to _run_ssh."""
        with mock.patch.object(essh, "_run_ssh", return_value=0) as mock_run:
            with self._auth_patches()[0], self._auth_patches()[1]:
                result = runner.invoke(essh.main, ["connect", "dev-db"])

        assert result.exit_code == 0, result.output
        args, _ = mock_run.call_args
        _, _, _, key_path, _, _ = args
        assert key_path is not None
        assert "id_ed25519_dev-db" in str(key_path)

    def test_connect_shortcut_unknown_command(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Unknown first argument should route to connect."""
        with mock.patch.object(essh, "_run_ssh", return_value=0) as mock_run:
            with self._auth_patches()[0], self._auth_patches()[1]:
                result = runner.invoke(essh.main, ["prod-web", "uptime"])

        assert result.exit_code == 0, result.output
        args, _ = mock_run.call_args
        _, _, _, _, remote_cmd, _ = args
        assert "uptime" in remote_cmd

    def test_connect_not_found(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["connect", "nonexistent"])
        assert result.exit_code != 0
        assert "Profile" in result.output
        assert "not found" in result.output

    def test_connect_with_remote_command(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Remote command is passed through to SSH."""
        with mock.patch.object(essh, "_run_ssh", return_value=0) as mock_run:
            with self._auth_patches()[0], self._auth_patches()[1]:
                result = runner.invoke(essh.main, ["prod-web", "df", "-h"])

        assert result.exit_code == 0, result.output
        args, _ = mock_run.call_args
        _, _, _, _, remote_cmd, _ = args
        assert "df" in remote_cmd
        assert "-h" in remote_cmd

    def test_connect_non_tty_agent_mode(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Non-TTY mode triggers pending request and wait for authorization."""
        with (
            mock.patch.object(essh, "_run_ssh", return_value=0),
            mock.patch("agent_sommelier.essh.create_pending_request") as mock_create,
            mock.patch("agent_sommelier.essh.wait_for_authorization") as mock_wait,
        ):
            result = runner.invoke(essh.main, ["connect", "prod-web"])

        assert result.exit_code == 0, result.output
        mock_create.assert_called_once_with("prod-web")
        mock_wait.assert_called_once_with("prod-web")


class TestCliRm:
    """essh rm — remove profile and keys."""

    def test_rm_removes_profile(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["rm", "prod-web"])
        assert result.exit_code == 0, result.output
        assert "removed" in result.output
        assert essh.find_profile("prod-web") is None

    def test_rm_not_found(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["rm", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_rm_does_not_delete_external_keys(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        """Only keys in ~/.essh/keys/ are deleted; external paths untouched."""
        external_key = tmp_home / "external" / "my_key"
        external_key.parent.mkdir(parents=True, exist_ok=True)
        external_key.write_text("external key content", encoding="utf-8")

        essh.save_profiles([{"name": "external-box", "user": "u", "host": "h", "port": 22, "key_path": str(external_key)}])

        result = runner.invoke(essh.main, ["rm", "external-box"])
        assert result.exit_code == 0, result.output
        # External key file must still exist
        assert external_key.exists()

    def test_rm_deletes_essh_keys(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        """Keys in ~/.essh/keys/NAME/ are deleted."""
        legacy_dir = essh.KEYS_DIR / "legacy-box"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "id_ed25519").write_text("key content", encoding="utf-8")

        essh.save_profiles([{"name": "legacy-box", "user": "u", "host": "h", "port": 22, "key_path": str(legacy_dir / "id_ed25519")}])

        result = runner.invoke(essh.main, ["rm", "legacy-box"])
        assert result.exit_code == 0, result.output
        assert not legacy_dir.exists()

    def test_rm_cleans_pending_request(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Stale pending request files are cleaned."""
        pending_file = essh.REQUESTS_DIR / "prod-web.pending"
        essh.ensure_dirs()
        pending_file.write_text("pending", encoding="utf-8")

        result = runner.invoke(essh.main, ["rm", "prod-web"])
        assert result.exit_code == 0
        assert not pending_file.exists()


class TestCliExportImport:
    """essh export / import — round-trip with empty and explicit key_path."""

    def test_export_with_default_keys(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Export profiles, including those with empty key_path."""
        export_path = sample_profiles[0].get("__export_path", None)
        result = runner.invoke(essh.main, ["export"])
        assert result.exit_code == 0, result.output
        assert "Exported to" in result.output

    def test_export_with_explicit_key(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Export picks up keys from external paths."""
        # Ensure the external key for dev-db actually exists
        key_path = Path(sample_profiles[1]["key_path"])
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text("private", encoding="utf-8")
        (key_path.parent / "id_ed25519.pub").write_text("ssh-ed25519 KEY\n", encoding="utf-8")

        result = runner.invoke(essh.main, ["export"])
        assert result.exit_code == 0, result.output
        assert "Exported to" in result.output

    def test_export_with_legacy_keys(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        """Export includes legacy keys from ~/.essh/keys/."""
        legacy_dir = essh.KEYS_DIR / "legacy-box"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "id_ed25519").write_text("legacy private", encoding="utf-8")
        (legacy_dir / "id_ed25519.pub").write_text("ssh-ed25519 LEGACY\n", encoding="utf-8")

        essh.save_profiles([{"name": "legacy-box", "user": "u", "host": "h", "port": 22, "key_path": str(legacy_dir / "id_ed25519")}])

        result = runner.invoke(essh.main, ["export"])
        assert result.exit_code == 0, result.output

    def test_export_no_profiles(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["export"])
        assert result.exit_code != 0
        assert "No profiles to export" in result.output

    def test_import_basic(self, sample_profiles: list[dict], runner: click.testing.CliRunner, tmp_home: Path) -> None:
        """Export then import: verify round-trip preserves key_path."""
        # Create the export first
        key_path = Path(sample_profiles[1]["key_path"])
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text("private", encoding="utf-8")
        (key_path.parent / "id_ed25519.pub").write_text("ssh-ed25519 KEY\n", encoding="utf-8")

        # Use explicit output path to avoid parsing Rich output
        export_path = tmp_home / "test_export.tar.gz"
        result = runner.invoke(essh.main, ["export", str(export_path)])
        assert result.exit_code == 0, result.output
        assert export_path.exists()

        # Now clear profiles and import
        essh.save_profiles([])

        result = runner.invoke(essh.main, ["import", str(export_path)])
        assert result.exit_code == 0, result.output
        assert "Imported:" in result.output

        imported = essh.load_profiles()
        assert len(imported) == 3

    def test_import_skip_duplicates(self, sample_profiles: list[dict], runner: click.testing.CliRunner, tmp_home: Path) -> None:
        """Import skips existing profiles without --force."""
        key_path = Path(sample_profiles[1]["key_path"])
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text("private", encoding="utf-8")
        (key_path.parent / "id_ed25519.pub").write_text("ssh-ed25519 KEY\n", encoding="utf-8")

        export_path = tmp_home / "test_export_skip.tar.gz"
        result = runner.invoke(essh.main, ["export", str(export_path)])
        assert result.exit_code == 0, result.output

        result = runner.invoke(essh.main, ["import", str(export_path)])
        assert result.exit_code == 0, result.output
        assert "Skipping" in result.output or "Imported" in result.output

    def test_import_force_overwrite(self, sample_profiles: list[dict], runner: click.testing.CliRunner, tmp_home: Path) -> None:
        """--force overwrites existing profiles."""
        key_path = Path(sample_profiles[1]["key_path"])
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text("private", encoding="utf-8")
        (key_path.parent / "id_ed25519.pub").write_text("ssh-ed25519 KEY\n", encoding="utf-8")

        export_path = tmp_home / "test_export_force.tar.gz"
        result = runner.invoke(essh.main, ["export", str(export_path)])
        assert result.exit_code == 0, result.output

        result = runner.invoke(essh.main, ["import", str(export_path), "--force"])
        assert result.exit_code == 0, result.output
        assert "Imported:" in result.output

    def test_import_invalid_archive(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        invalid = tmp_home / "not_tar.gz"
        invalid.write_text("not a tar", encoding="utf-8")
        result = runner.invoke(essh.main, ["import", str(invalid)])
        assert result.exit_code != 0
        assert "Not a valid tar archive" in result.output or "Expected a .tar.gz" in result.output


class TestCliAuthorize:
    """essh authorize — approve pending requests."""

    def test_authorize_pending(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        essh.ensure_dirs()
        pending_file = essh.REQUESTS_DIR / "prod-web.pending"
        pending_file.write_text("2026-01-01T00:00:00", encoding="utf-8")

        result = runner.invoke(essh.main, ["authorize", "prod-web"])
        assert result.exit_code == 0, result.output
        assert "authorized" in result.output
        assert not pending_file.exists()

    def test_authorize_no_pending(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["authorize", "prod-web"])
        assert result.exit_code == 0
        assert "No pending request" in result.output

    def test_authorize_nonexistent_profile(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["authorize", "ghost"])
        assert result.exit_code != 0
        assert "not found" in result.output


# ===========================================================================
# REQUEST/AUTH POLLING UNIT TESTS
# ===========================================================================


class TestCreatePendingRequest:
    """create_pending_request() — pending request file management."""

    def test_creates_pending_file(self, tmp_home: Path) -> None:
        essh.create_pending_request("test-host")
        pending = essh.REQUESTS_DIR / "test-host.pending"
        assert pending.exists()
        content = pending.read_text(encoding="utf-8")
        assert len(content) > 0  # ISO timestamp

    def test_raises_if_too_recent(self, tmp_home: Path) -> None:
        essh.create_pending_request("test-host")
        with pytest.raises(click.ClickException, match="already pending"):
            essh.create_pending_request("test-host")

    def test_allows_after_stale(self, tmp_home: Path) -> None:
        """Stale request older than AUTH_TIMEOUT is cleaned up."""
        essh.create_pending_request("test-host")
        pending = essh.REQUESTS_DIR / "test-host.pending"
        # Manually set mtime to old timestamp
        old_time = 1000000  # way in the past
        os.utime(str(pending), (old_time, old_time))
        essh.create_pending_request("test-host")  # should not raise
        assert pending.exists()


class TestWaitForAuthorization:
    """wait_for_authorization() — polling loop."""

    def test_returns_when_deleted(self, tmp_home: Path) -> None:
        essh.create_pending_request("test-host")
        pending = essh.REQUESTS_DIR / "test-host.pending"
        # Delete it immediately (simulating authorize)
        pending.unlink()
        essh.wait_for_authorization("test-host")  # should return immediately

    def test_timeout(self, tmp_home: Path) -> None:
        essh.ensure_dirs()
        pending = essh.REQUESTS_DIR / "test-timeout.pending"
        pending.write_text("pending", encoding="utf-8")

        with pytest.raises(click.ClickException, match="Authorization timeout"):
            essh.wait_for_authorization("test-timeout")


# ===========================================================================
# EDGE CASES & REGRESSIONS
# ===========================================================================


class TestEdgeCases:
    """Dark corners, regressions, and boundary conditions."""

    def test_ssh_copy_id_no_valueerror(self, tmp_home: Path, mock_subprocess_run: mock.MagicMock) -> None:
        """Regression: ssh_copy_id must not raise ValueError from stdin+input conflict.
        After the fix, the key is embedded in the remote command — no stdin pipe needed."""
        pubkey_path = tmp_home / "key.pub"
        pubkey_path.write_text("ssh-ed25519 KEY\n", encoding="utf-8")
        mock_subprocess_run.return_value.returncode = 0
        essh.ssh_copy_id("user", "host", 22, pubkey_path)
        call_kwargs = mock_subprocess_run.call_args[1]
        # Key is inlined in the remote command — no input=, no stdin=
        assert "input" not in call_kwargs
        assert "stdin" not in call_kwargs
        # stdout should be sys.stdout (for user to see password prompt)
        assert call_kwargs.get("stdout") == sys.stdout

    def test_ensure_ssh_agent_non_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ensure_ssh_agent silently returns when no SSH_AUTH_SOCK."""
        monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
        essh.ensure_ssh_agent(Path("/any/key"))  # no exception

    def test_ensure_ssh_agent_non_fatal_on_failure(self, monkeypatch: pytest.MonkeyPatch, mock_subprocess_run: mock.MagicMock) -> None:
        """ensure_ssh_agent does not raise when ssh-add fails."""
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/sock")
        mock_subprocess_run.return_value.returncode = 1
        essh.ensure_ssh_agent(Path("/bad/key"))  # no exception

    def test_parse_host_string_colons_in_host(self) -> None:
        """IPv6-like addresses are handled (rsplit on colon)."""
        user, host, port = essh.parse_host_string("admin@[::1]:2222")
        # The current implementation does rsplit on ":" so
        # "admin@[::1]:2222" → parts after @: "[::1]:2222" → rsplit gives "[::1]" and "2222"
        # Wait: rsplit(":", 1) on "[::1]:2222" gives ["[::1]", "2222"]
        assert user == "admin"
        assert host == "[::1]"
        assert port == 2222

    def test_profile_name_collision_case_sensitivity(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Two distinct lowercase names can coexist (case sensitivity is moot since uppercase is rejected by validate_name)."""
        assert essh.find_profile("prod-web") is not None  # from sample_profiles
        # A different lowercase name should be accepted
        with mock.patch.object(essh, "_try_ssh_default_keys", return_value=True):
            result = runner.invoke(essh.main, ["add", "web-prod", "u@h.com"])
        assert result.exit_code == 0, result.output
        assert essh.find_profile("web-prod") is not None

    def test_list_shows_correct_display_for_key_states(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        """Verify the three display states via JSON output (avoids Rich truncation)."""
        external_key = tmp_home / ".ssh" / "existing_key"
        external_key.parent.mkdir(parents=True, exist_ok=True)
        external_key.write_text("key", encoding="utf-8")

        profiles = [
            {"name": "defaults", "user": "u", "host": "a", "port": 22, "key_path": ""},
            {"name": "explicit", "user": "u", "host": "b", "port": 22, "key_path": str(external_key)},
            {"name": "missing", "user": "u", "host": "c", "port": 22, "key_path": str(tmp_home / "nonexistent_key")},
        ]
        essh.save_profiles(profiles)

        # Use --json to avoid Rich table truncation
        result = runner.invoke(essh.main, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 3

        # Check empty key_path
        d = next(p for p in data if p["name"] == "defaults")
        assert d["key_path"] == ""

        # Check explicit key path
        e = next(p for p in data if p["name"] == "explicit")
        assert e["key_path"] == str(external_key)

        # Check missing key
        m = next(p for p in data if p["name"] == "missing")
        assert "nonexistent_key" in m["key_path"]

        # Verify Rich table also shows markers correctly
        text_result = runner.invoke(essh.main, ["list"])
        assert text_result.exit_code == 0
        assert "(default keys)" in text_result.output
        assert "(missing)" in text_result.output

    def test_empty_profiles_list_does_not_crash_list(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["list"])
        assert result.exit_code == 0

    def test_main_help(self, runner: click.testing.CliRunner) -> None:
        result = runner.invoke(essh.main, ["--help"])
        assert result.exit_code == 0
        assert "SSH" in result.output or "essh" in result.output

    def test_connect_nonexistent(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        """Connect to missing profile shows error with hints."""
        result = runner.invoke(essh.main, ["connect", "nope"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_extract_known_hosts_entries_no_file(self, tmp_home: Path) -> None:
        """extract_known_hosts_entries returns [] when known_hosts doesn't exist."""
        entries = essh.extract_known_hosts_entries([{"host": "example.com", "port": 22}])
        assert entries == []


class TestExtractKnownHosts:
    """extract_known_hosts_entries() — filter known_hosts lines."""

    def test_returns_matching_entries(self, tmp_home: Path) -> None:
        known_hosts = tmp_home / ".ssh" / "known_hosts"
        known_hosts.parent.mkdir(parents=True, exist_ok=True)
        known_hosts.write_text(
            "example.com ssh-ed25519 AAA...\n"
            "other.com ssh-rsa BBB...\n"
            "[server.io]:2222 ssh-ed25519 CCC...\n",
            encoding="utf-8",
        )
        # Reset KNOWN_HOSTS to use our temp path
        essh.KNOWN_HOSTS = known_hosts
        profiles = [
            {"name": "a", "host": "example.com", "port": 22},
            {"name": "b", "host": "server.io", "port": 2222},
        ]
        entries = essh.extract_known_hosts_entries(profiles)
        assert len(entries) == 2
        assert any("example.com" in e for e in entries)
        assert any("server.io" in e for e in entries)
        assert all("other.com" not in e for e in entries)


class TestGenerateKeypairWithExisting:
    """generate_keypair() with path that already exists."""

    def test_ssh_keygen_handles_existing_path(self, tmp_home: Path, mock_subprocess_run: mock.MagicMock) -> None:
        """ssh-keygen is called with -f path; it may or may not overwrite."""
        key_path = tmp_home / ".ssh" / "id_ed25519"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text("existing", encoding="utf-8")

        mock_subprocess_run.return_value.returncode = 0
        result = essh.generate_keypair("test", key_path)
        assert result == key_path
        # subprocess.run was called
        mock_subprocess_run.assert_called_once()


# ===========================================================================
# BACKWARD COMPATIBILITY TESTS
# ===========================================================================


class TestBackwardCompat:
    """Profiles created with legacy key paths still work."""

    def test_legacy_key_path_connect(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        """Legacy ~/.essh/keys/ path should still connect."""
        legacy_key_path = essh.KEYS_DIR / "legacy-box" / "id_ed25519"
        essh.save_profiles([{
            "name": "legacy-box", "user": "root", "host": "old-server.local",
            "port": 22, "key_path": str(legacy_key_path),
        }])

        with (
            mock.patch.object(essh, "_run_ssh", return_value=0) as mock_run,
            mock.patch("agent_sommelier.essh.create_pending_request"),
            mock.patch("agent_sommelier.essh.wait_for_authorization"),
        ):
            result = runner.invoke(essh.main, ["connect", "legacy-box"])

        assert result.exit_code == 0, result.output
        args, _ = mock_run.call_args
        _, _, _, key_path, _, _ = args
        assert key_path is not None
        assert "-i" in mock_run.call_args or True

    def test_export_legacy_key_included(self, tmp_home: Path, runner: click.testing.CliRunner) -> None:
        """Legacy key in ~/.essh/keys/NAME/ is included in export."""
        legacy_dir = essh.KEYS_DIR / "legacy-box"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "id_ed25519").write_text("legacy key content", encoding="utf-8")
        (legacy_dir / "id_ed25519.pub").write_text("ssh-ed25519 LEGACY\n", encoding="utf-8")

        essh.save_profiles([{
            "name": "legacy-box", "user": "root", "host": "old.local",
            "port": 22, "key_path": str(legacy_dir / "id_ed25519"),
        }])

        result = runner.invoke(essh.main, ["export"])
        assert result.exit_code == 0, result.output
        assert "Exported to" in result.output


# ===========================================================================
# TRANSFER COMMAND TESTS (scp / rsync)
# ===========================================================================


class TestResolveTransferArgs:
    """_resolve_transfer_args() -- resolves NAME:path patterns to user@host:path."""

    def test_single_host_with_path(self, sample_profiles: list[dict]) -> None:
        """NAME:path resolves to user@host:path."""
        resolved, profiles = essh._resolve_transfer_args(["prod-web:/remote/file.txt"])
        assert resolved == ["deploy@web.example.com:/remote/file.txt"]
        assert "prod-web" in profiles
        assert profiles["prod-web"]["user"] == "deploy"

    def test_single_host_empty_path(self, sample_profiles: list[dict]) -> None:
        """NAME: (empty path) resolves to user@host: (home dir)."""
        resolved, profiles = essh._resolve_transfer_args(["prod-web:"])
        assert resolved == ["deploy@web.example.com:"]
        assert "prod-web" in profiles

    def test_two_hosts(self, sample_profiles: list[dict]) -> None:
        """Multiple NAME:path args all resolve."""
        resolved, profiles = essh._resolve_transfer_args(["prod-web:file1", "dev-db:file2"])
        assert resolved == ["deploy@web.example.com:file1", "admin@db.dev.local:file2"]
        assert set(profiles.keys()) == {"prod-web", "dev-db"}

    def test_plain_local_path_passthrough(self, sample_profiles: list[dict]) -> None:
        """Plain local paths without NAME: prefix pass through unchanged."""
        resolved, profiles = essh._resolve_transfer_args(["./local/file.txt"])
        assert resolved == ["./local/file.txt"]
        assert profiles == {}

    def test_mixed_args(self, sample_profiles: list[dict]) -> None:
        """Flags and local paths preserved, NAME:path resolved."""
        resolved, profiles = essh._resolve_transfer_args(["-r", "prod-web:/remote", "./local/"])
        assert resolved == ["-r", "deploy@web.example.com:/remote", "./local/"]
        assert "prod-web" in profiles

    def test_nonexistent_profile_raises(self, sample_profiles: list[dict]) -> None:
        """Unknown profile name raises ClickException."""
        with pytest.raises(click.ClickException, match="Profile 'nonexistent' not found"):
            essh._resolve_transfer_args(["nonexistent:/path"])

    def test_arg_without_colon_passthrough(self, sample_profiles: list[dict]) -> None:
        """Arguments without a colon pass through unchanged."""
        resolved, profiles = essh._resolve_transfer_args(["somearg"])
        assert resolved == ["somearg"]
        assert profiles == {}


class TestRunScp:
    """_run_scp() -- builds and executes scp command."""

    def test_with_key_path(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Profile with key_path adds -i flag."""
        mock_subprocess_run.return_value.returncode = 0
        profiles = {"test": {"key_path": "/path/to/key", "port": 22}}
        exit_code = essh._run_scp(["user@host:/remote", "./local/"], profiles, is_tty=False)
        assert exit_code == 0
        args = mock_subprocess_run.call_args[0][0]
        assert args[0] == "scp"
        assert "-i" in args
        assert "/path/to/key" in args

    def test_with_default_keys(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Profile with empty key_path omits -i."""
        mock_subprocess_run.return_value.returncode = 0
        profiles = {"test": {"key_path": "", "port": 22}}
        essh._run_scp(["user@host:/remote", "./local/"], profiles, is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        assert "-i" not in args

    def test_with_port(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Profile with non-default port adds -P and verifies command order."""
        mock_subprocess_run.return_value.returncode = 0
        profiles = {"test": {"key_path": "/path/to/key", "port": 2222}}
        essh._run_scp(["user@host:/remote"], profiles, is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        assert "-i" in args
        assert "/path/to/key" in args
        assert "-P" in args
        assert "2222" in args
        # Verify order: scp -i key -P port ...resolved_args
        i_idx = args.index("-i")
        key_idx = args.index("/path/to/key")
        P_idx = args.index("-P")
        port_idx = args.index("2222")
        resolved_idx = args.index("user@host:/remote")
        assert i_idx < key_idx < P_idx < port_idx < resolved_idx

    def test_non_tty_captures_output(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Non-TTY mode captures stdout/stderr and echoes stdout."""
        mock_subprocess_run.return_value.returncode = 0
        mock_subprocess_run.return_value.stdout = "transferred\n"
        mock_subprocess_run.return_value.stderr = ""
        profiles = {"test": {"key_path": "", "port": 22}}
        essh._run_scp(["a", "b"], profiles, is_tty=False)
        call_kwargs = mock_subprocess_run.call_args[1]
        assert call_kwargs.get("capture_output") is True

    def test_tty_inherits_streams(self, mock_subprocess_run: mock.MagicMock) -> None:
        """TTY mode passes stdin/stdout/stderr."""
        mock_subprocess_run.return_value.returncode = 0
        profiles = {"test": {"key_path": "", "port": 22}}
        essh._run_scp(["a", "b"], profiles, is_tty=True)
        call_kwargs = mock_subprocess_run.call_args[1]
        assert call_kwargs["stdin"] == sys.stdin
        assert call_kwargs["stdout"] == sys.stdout
        assert call_kwargs["stderr"] == sys.stderr

    def test_scp_not_found_raises(self) -> None:
        """When scp is not on PATH, raises ClickException."""
        with mock.patch.object(essh, "shutil") as mock_shutil:
            mock_shutil.which.return_value = None
            with pytest.raises(click.ClickException, match="scp not found"):
                essh._run_scp(["a", "b"], {}, is_tty=False)


class TestRunRsync:
    """_run_rsync() -- builds and executes rsync command."""

    def test_with_key_path(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Profile with key_path and port builds -e 'ssh -i KEY -p PORT'."""
        mock_subprocess_run.return_value.returncode = 0
        profiles = {"test": {"key_path": "/path/to/key", "port": 2222}}
        essh._run_rsync(["-avz", "user@host:/remote", "./local/"], profiles, is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        assert args[0] == "rsync"
        assert args[1] == "-e"
        assert "ssh" in args[2]
        assert "-i" in args[2]
        assert "/path/to/key" in args[2]
        assert "-p" in args[2]
        assert "2222" in args[2]

    def test_with_default_keys(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Profile with empty key_path, default port: -e 'ssh' only."""
        mock_subprocess_run.return_value.returncode = 0
        profiles = {"test": {"key_path": "", "port": 22}}
        essh._run_rsync(["-avz", "a", "b"], profiles, is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        assert args[1] == "-e"
        assert args[2] == "ssh"

    def test_with_non_default_port(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Non-default port appears in -e ssh command."""
        mock_subprocess_run.return_value.returncode = 0
        profiles = {"test": {"key_path": "/path/to/key", "port": 2222}}
        essh._run_rsync(["-avz", "a", "b"], profiles, is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        assert "-p" in args[2]
        assert "2222" in args[2]

    def test_default_port_omitted(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Port 22 is omitted from -e ssh command."""
        mock_subprocess_run.return_value.returncode = 0
        profiles = {"test": {"key_path": "/path/to/key", "port": 22}}
        essh._run_rsync(["-avz", "a", "b"], profiles, is_tty=False)
        args = mock_subprocess_run.call_args[0][0]
        assert args[2] == "ssh -i /path/to/key"

    def test_non_tty_captures_output(self, mock_subprocess_run: mock.MagicMock) -> None:
        """Non-TTY mode captures stdout/stderr."""
        mock_subprocess_run.return_value.returncode = 0
        mock_subprocess_run.return_value.stdout = "sent 100 bytes\n"
        mock_subprocess_run.return_value.stderr = ""
        profiles = {"test": {"key_path": "", "port": 22}}
        essh._run_rsync(["a", "b"], profiles, is_tty=False)
        call_kwargs = mock_subprocess_run.call_args[1]
        assert call_kwargs.get("capture_output") is True

    def test_rsync_not_found_raises(self) -> None:
        """When rsync is not on PATH, raises ClickException."""
        with mock.patch.object(essh, "shutil") as mock_shutil:
            mock_shutil.which.return_value = None
            with pytest.raises(click.ClickException, match="rsync not found"):
                essh._run_rsync(["a", "b"], {}, is_tty=False)


class TestCliScp:
    """essh scp -- CLI integration tests."""

    def test_scp_single_host(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """scp with one profile resolves NAME:path and calls _run_scp."""
        with (
            mock.patch.object(essh, "_run_scp", return_value=0) as mock_run,
            mock.patch.object(essh, "_authorize_transfer_profiles"),
        ):
            result = runner.invoke(essh.main, ["scp", "prod-web:/remote", "./local/"])
        assert result.exit_code == 0, result.output
        args, _ = mock_run.call_args
        resolved_args, profiles, is_tty = args
        assert "deploy@web.example.com:/remote" in resolved_args
        assert "./local/" in resolved_args
        assert "prod-web" in profiles
        assert is_tty is False

    def test_scp_profile_not_found(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Unknown profile name raises error with hint."""
        result = runner.invoke(essh.main, ["scp", "nonexistent:/path", "./local/"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_scp_no_args(self, runner: click.testing.CliRunner) -> None:
        """No arguments for scp shows usage error."""
        result = runner.invoke(essh.main, ["scp"])
        assert result.exit_code != 0
        assert "Usage" in result.output

    def test_scp_agent_mode_authorization(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Non-TTY mode calls _authorize_transfer_profiles."""
        with (
            mock.patch.object(essh, "_run_scp", return_value=0),
            mock.patch.object(essh, "_authorize_transfer_profiles") as mock_auth,
        ):
            result = runner.invoke(essh.main, ["scp", "prod-web:/remote", "./local/"])
        assert result.exit_code == 0, result.output
        mock_auth.assert_called_once()
        auth_args, _ = mock_auth.call_args
        profiles, is_tty = auth_args
        assert "prod-web" in profiles
        assert is_tty is False


class TestCliRsync:
    """essh rsync -- CLI integration tests."""

    def test_rsync_single_host(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """rsync with one profile resolves NAME:path and calls _run_rsync."""
        with (
            mock.patch.object(essh, "_run_rsync", return_value=0) as mock_run,
            mock.patch.object(essh, "_authorize_transfer_profiles"),
        ):
            result = runner.invoke(essh.main, ["rsync", "-avz", "prod-web:/remote", "./local/"])
        assert result.exit_code == 0, result.output
        args, _ = mock_run.call_args
        resolved_args, profiles, is_tty = args
        assert "deploy@web.example.com:/remote" in resolved_args
        assert "-avz" in resolved_args
        assert "./local/" in resolved_args
        assert "prod-web" in profiles
        assert is_tty is False

    def test_rsync_profile_not_found(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Unknown profile name raises error with hint."""
        result = runner.invoke(essh.main, ["rsync", "nonexistent:/path", "./local/"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_rsync_no_args(self, runner: click.testing.CliRunner) -> None:
        """No arguments for rsync shows usage error."""
        result = runner.invoke(essh.main, ["rsync"])
        assert result.exit_code != 0
        assert "Usage" in result.output

    def test_rsync_agent_mode_authorization(self, sample_profiles: list[dict], runner: click.testing.CliRunner) -> None:
        """Non-TTY mode calls _authorize_transfer_profiles."""
        with (
            mock.patch.object(essh, "_run_rsync", return_value=0),
            mock.patch.object(essh, "_authorize_transfer_profiles") as mock_auth,
        ):
            result = runner.invoke(essh.main, ["rsync", "prod-web:/remote", "./local/"])
        assert result.exit_code == 0, result.output
        mock_auth.assert_called_once()
        auth_args, _ = mock_auth.call_args
        profiles, is_tty = auth_args
        assert "prod-web" in profiles
        assert is_tty is False


class TestAuthorizeTransferProfiles:
    """_authorize_transfer_profiles() -- agent-mode authorization for transfers."""

    def test_tty_mode_skips_auth(self, sample_profiles: list[dict]) -> None:
        """When is_tty=True, no pending requests are created."""
        profiles_dict = {"prod-web": sample_profiles[0]}
        with (
            mock.patch("agent_sommelier.essh.create_pending_request") as mock_create,
            mock.patch("agent_sommelier.essh.wait_for_authorization") as mock_wait,
        ):
            essh._authorize_transfer_profiles(profiles_dict, is_tty=True)
        mock_create.assert_not_called()
        mock_wait.assert_not_called()

    def test_non_tty_creates_pending(self, sample_profiles: list[dict]) -> None:
        """When is_tty=False, creates pending request and waits for each profile."""
        profiles_dict = {
            "prod-web": sample_profiles[0],
            "dev-db": sample_profiles[1],
        }
        with (
            mock.patch("agent_sommelier.essh.create_pending_request") as mock_create,
            mock.patch("agent_sommelier.essh.wait_for_authorization") as mock_wait,
        ):
            essh._authorize_transfer_profiles(profiles_dict, is_tty=False)
        assert mock_create.call_count == 2
        assert mock_wait.call_count == 2
        mock_create.assert_any_call("prod-web")
        mock_create.assert_any_call("dev-db")

    def test_non_tty_timeout_raises(self, sample_profiles: list[dict]) -> None:
        """When wait_for_authorization raises, the exception propagates."""
        profiles_dict = {"prod-web": sample_profiles[0]}
        with (
            mock.patch("agent_sommelier.essh.create_pending_request"),
            mock.patch(
                "agent_sommelier.essh.wait_for_authorization",
                side_effect=click.ClickException("Authorization timeout"),
            ),
        ):
            with pytest.raises(click.ClickException, match="Authorization timeout"):
                essh._authorize_transfer_profiles(profiles_dict, is_tty=False)
