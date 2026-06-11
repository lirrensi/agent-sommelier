# FILE: src/agent_sommelier/bg.py
# PURPOSE: Manage detached background jobs with friendly names, stable UIDs, async launch workers, state refresh, and lightweight update events.
# OWNS: bg CLI storage, naming, worker launch, process inspection, output capture, wait loops, and record cleanup.
# EXPORTS: main (CLI entry point), create_job (launch helper), list_jobs (enumeration), load_job_snapshot (lookup), launch helpers, wait helpers
# DOCS: docs/product.md, docs/arch.md, skills/bg-jobs/SKILL.md, .agents/reports/plan_bg_name_redesign_2026-03-27.md, .agents/reports/plan_bg_wait_notifications_2026-03-28.md, .agents/reports/plan_bg_immediate_fire_and_forget_2026-04-07.md

"""Background job manager CLI."""

from __future__ import annotations

import json
import os
import shlex
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import click
import psutil

from agent_sommelier import __version__

HAS_PSUTIL = hasattr(psutil, "Process")

if not hasattr(click, "group"):

    class _MiniClickException(Exception):
        pass

    def _identity_decorator(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator

    class _MiniClickGroup:
        def __init__(self, func):
            self.func = func
            self.commands: dict[str, object] = {}

        def command(self, name: str | None = None, **_kwargs):
            def decorator(func):
                self.commands[name or func.__name__] = func
                return func

            return decorator

        def __call__(self, *args, **kwargs):
            argv = sys.argv[1:]
            if not argv:
                return self.func(*args, **kwargs)

            if argv[0] in {"-h", "--help"}:
                available = ", ".join(sorted(self.commands))
                print(f"Usage: bg <command>\nAvailable: {available}")
                return None

            cmd_name = argv[0]
            command = self.commands.get(cmd_name)
            if command is None:
                raise _MiniClickException(f"Unknown command: {cmd_name}")

            if cmd_name == "list":
                return command(json_output="--json" in argv[1:])

            if cmd_name == "wait-all":
                return command()

            if cmd_name == "wait":
                if len(argv) < 2:
                    raise _MiniClickException(f"Missing argument for {cmd_name}")
                job_ref = argv[1]
                pattern = None
                if "--match" in argv[2:]:
                    match_index = argv[2:].index("--match") + 2
                    if match_index + 1 >= len(argv):
                        raise _MiniClickException("Missing value for --match")
                    pattern = argv[match_index + 1]
                return command(job_ref, pattern)

            if cmd_name == "restart":
                if len(argv) < 2:
                    raise _MiniClickException(f"Missing argument for {cmd_name}")
                return command(argv[1])

            if cmd_name == "prune":
                return command()

            if len(argv) < 2:
                raise _MiniClickException(f"Missing argument for {cmd_name}")
            return command(argv[1])

    class _MiniClickModule:
        ClickException = _MiniClickException

        @staticmethod
        def group(*_args, **_kwargs):
            return lambda func: _MiniClickGroup(func)

        command = staticmethod(_identity_decorator)
        option = staticmethod(_identity_decorator)
        argument = staticmethod(_identity_decorator)

        @staticmethod
        def version_option(*_args, **_kwargs):
            return _identity_decorator

        @staticmethod
        def echo(message: str = "", err: bool = False):
            stream = sys.stderr if err else sys.stdout
            print(message, file=stream)

    click = _MiniClickModule()

JOBS_DIR = Path(tempfile.gettempdir()) / "agentcli_bgjobs"
RECORDS_DIR = JOBS_DIR / "records"
INDEX_FILE = JOBS_DIR / "index.json"
INDEX_VERSION = 1
TERMINAL_JOB_RETENTION_SECONDS = 60 * 60
TERMINAL_JOB_CAP = 32
BG_LAUNCH_TIMEOUT_SECONDS = 10
LAUNCH_PID_PROBE_DELAY_SECONDS = 5
IN_PROGRESS_JOB_STATUSES = {"running", "launching", "starting"}
LAUNCHING_JOB_STATUSES = {"launching", "starting"}

FRIENDLY_WORDS = [
    "amber",
    "brisk",
    "calm",
    "dapper",
    "ember",
    "fuzzy",
    "gentle",
    "hollow",
    "ivory",
    "jolly",
    "keen",
    "lucky",
    "mellow",
    "nimble",
    "opal",
    "plucky",
    "quiet",
    "rosy",
    "sleepy",
    "tidy",
    "umber",
    "velvet",
    "witty",
    "windy",
    "yonder",
    "zesty",
    "atlas",
    "beacon",
    "cinder",
    "drift",
    "echo",
    "glimmer",
    "harbor",
    "lantern",
    "moss",
    "nectar",
    "rivet",
    "sable",
    "thimble",
    "willow",
]

WRAPPER_TOKENS = {
    "bash",
    "cmd",
    "cmd.exe",
    "env",
    "git",
    "nice",
    "npx",
    "npm",
    "node",
    "poetry",
    "powershell",
    "pwsh",
    "py",
    "python",
    "python3",
    "sudo",
    "time",
    "uv",
    "uvx",
}

NAME_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def ensure_jobs_dir() -> None:
    """Ensure the storage root exists."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically to avoid partial records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def dump_json(data: dict | list) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def load_json_file(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def default_index() -> dict:
    return {"version": INDEX_VERSION, "records": {}, "names": {}}


def load_index() -> dict:
    ensure_jobs_dir()
    if not INDEX_FILE.exists():
        return default_index()

    try:
        raw = load_json_file(INDEX_FILE)
    except (OSError, json.JSONDecodeError):
        return default_index()

    if not isinstance(raw, dict):
        return default_index()

    index = default_index()
    index["records"].update(
        raw.get("records", {}) if isinstance(raw.get("records"), dict) else {}
    )
    index["names"].update(
        raw.get("names", {}) if isinstance(raw.get("names"), dict) else {}
    )
    return index


def save_index(index: dict) -> None:
    atomic_write_text(INDEX_FILE, dump_json(index))


def record_dir_for_uid(uid: str) -> Path:
    return RECORDS_DIR / uid


def legacy_record_dir_for(ref: str) -> Path:
    return JOBS_DIR / ref


def meta_file_for_uid(uid: str) -> Path:
    return record_dir_for_uid(uid) / "meta.json"


def stdout_file_for_uid(uid: str) -> Path:
    return record_dir_for_uid(uid) / "stdout.txt"


def stderr_file_for_uid(uid: str) -> Path:
    return record_dir_for_uid(uid) / "stderr.txt"


def exit_code_file_for_uid(uid: str) -> Path:
    return record_dir_for_uid(uid) / "exit_code.txt"


def runner_file_for_uid(uid: str, extension: str) -> Path:
    return record_dir_for_uid(uid) / f"runner.{extension}"


def parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _write_temp_script(content: str) -> Path:
    """Write worker script to a temp file to avoid inline -c deadlocks on Windows."""
    temp_dir = Path(tempfile.gettempdir()) / "agent_sommelier_workers"
    temp_dir.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex[:12]
    script_path = temp_dir / f"worker_{suffix}.py"
    script_path.write_text(content, encoding="utf-8")
    return script_path


def calculate_elapsed_seconds(started_at: str | None) -> float | None:
    started = parse_iso_timestamp(started_at)
    if not started:
        return None
    return max(0.0, (datetime.now(started.tzinfo) - started).total_seconds())


def read_exit_code_for_uid(uid: str) -> int | None:
    exit_file = exit_code_file_for_uid(uid)
    if not exit_file.exists():
        return None

    content = exit_file.read_text(encoding="utf-8").strip()
    if not content:
        return None

    try:
        return int(content)
    except ValueError:
        return None


def inspect_process(pid: int | None) -> dict:
    if not pid:
        return {"process_state": "missing_pid", "is_running": False}

    if not HAS_PSUTIL:
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if str(pid) in result.stdout:
                    return {"process_state": "alive", "is_running": True}
                return {"process_state": "dead", "is_running": False}
            except (OSError, subprocess.SubprocessError):
                return {"process_state": "unknown", "is_running": False}

        try:
            os.kill(pid, 0)
            return {"process_state": "alive", "is_running": True}
        except ProcessLookupError:
            return {"process_state": "dead", "is_running": False}
        except PermissionError:
            return {"process_state": "alive", "is_running": True}
        except OSError:
            return {"process_state": "unknown", "is_running": False}

    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            status = proc.status()
            if status == getattr(psutil, "STATUS_ZOMBIE", None):
                return {"process_state": "zombie", "is_running": False}
            if not proc.is_running():
                return {"process_state": "dead", "is_running": False}

            details: dict[str, object] = {"process_state": "alive", "is_running": True}
            try:
                details["memory_bytes"] = proc.memory_info().rss
            except (psutil.Error, OSError):
                pass
            try:
                details["cpu_percent"] = proc.cpu_percent(interval=None)
            except (psutil.Error, OSError):
                pass
            return details
    except (getattr(psutil, "NoSuchProcess", Exception), ProcessLookupError):
        return {"process_state": "dead", "is_running": False}
    except (
        getattr(psutil, "AccessDenied", Exception),
        getattr(psutil, "Error", Exception),
        OSError,
    ):
        return {"process_state": "unknown", "is_running": False}


def slugify_root(value: str, limit: int = 14) -> str:
    slug = []
    last_dash = False
    for char in value.lower():
        if char.isalnum():
            slug.append(char)
            last_dash = False
        elif slug and not last_dash:
            slug.append("-")
            last_dash = True
    result = "".join(slug).strip("-")
    return result[:limit].strip("-") or "job"


def tokenize_command(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd, posix=sys.platform != "win32")
    except ValueError:
        return cmd.split()


def extract_command_root(cmd: str) -> str:
    tokens = tokenize_command(cmd)
    if not tokens:
        return "job"

    def is_assignment(token: str) -> bool:
        return "=" in token and not token.startswith("-") and token.index("=") > 0

    meaningful = [token for token in tokens if not is_assignment(token)]
    if not meaningful:
        meaningful = tokens

    lowered = [token.lower() for token in meaningful]
    first = lowered[0]

    if first in {"python", "python3", "py", "uv", "uvx"}:
        for idx, token in enumerate(lowered[1:], start=1):
            if token == "-m" and idx + 1 < len(meaningful):
                return slugify_root(meaningful[idx + 1])
            if token in {"-c", "/c"}:
                return slugify_root(meaningful[0])
        return slugify_root(meaningful[0])

    if first in {"npm", "npx", "poetry"}:
        for idx, token in enumerate(lowered[1:], start=1):
            if token in {"run", "exec"} and idx + 1 < len(meaningful):
                return slugify_root(meaningful[idx + 1])
        return slugify_root(meaningful[0])

    if first == "git":
        for token in meaningful[1:]:
            if not token.startswith(("-", "/")):
                return slugify_root(token)

    if first == "docker":
        for token in meaningful[1:]:
            if not token.startswith(("-", "/")):
                return slugify_root(token)

    for token in meaningful:
        lowered_token = token.lower()
        if lowered_token in WRAPPER_TOKENS:
            continue
        if lowered_token.startswith(("-", "/")):
            continue
        return slugify_root(token)

    return slugify_root(meaningful[0])


def command_root_from_cmd(cmd: str) -> str:
    return extract_command_root(cmd)


def generate_uid(existing: set[str] | None = None) -> str:
    existing = existing or set()
    while True:
        uid = uuid.uuid4().hex[:12]
        if (
            uid not in existing
            and not meta_file_for_uid(uid).exists()
            and not legacy_record_dir_for(uid).exists()
        ):
            return uid


def choose_unique_name(base_name: str, taken_names: set[str]) -> str:
    if base_name not in taken_names:
        return base_name

    while True:
        suffix = uuid.uuid4().hex[:2]
        candidate = f"{base_name}-{suffix}"
        if candidate not in taken_names:
            return candidate


def friendly_name_for(cmd: str, taken_names: set[str]) -> str:
    word = FRIENDLY_WORDS[uuid.uuid4().int % len(FRIENDLY_WORDS)]
    root = command_root_from_cmd(cmd)
    base_name = f"{word}-{root}"
    return choose_unique_name(base_name, taken_names)


def normalize_record_meta(meta: dict, uid: str, name: str) -> dict:
    normalized = dict(meta)
    normalized["uid"] = uid
    normalized["id"] = uid
    normalized["name"] = normalized.get("name") or name
    normalized["command_root"] = normalized.get(
        "command_root"
    ) or command_root_from_cmd(normalized.get("cmd", ""))
    return normalized


def write_meta(uid: str, meta: dict) -> dict:
    atomic_write_text(meta_file_for_uid(uid), dump_json(meta))
    return meta


def build_windows_runner_uid(uid: str, cmd: str) -> Path:
    exit_code_path = str(exit_code_file_for_uid(uid)).replace("'", "''")
    runner_file = runner_file_for_uid(uid, "ps1")
    runner_file.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Continue'",
                "$bgExit = 0",
                "try {",
                "    & {",
                *[f"        {line}" for line in cmd.splitlines() or [cmd]],
                "    }",
                "    if ($LASTEXITCODE -is [int]) {",
                "        $bgExit = $LASTEXITCODE",
                "    } elseif (-not $?) {",
                "        $bgExit = 1",
                "    }",
                "} catch {",
                "    $_ | Out-String | Write-Error",
                "    $bgExit = 1",
                "}",
                f"Set-Content -LiteralPath '{exit_code_path}' -Value $bgExit -NoNewline",
                "exit $bgExit",
            ]
        ),
        encoding="utf-8",
    )
    return runner_file


def build_windows_cmd_runner(uid: str, cmd: str) -> Path:
    runner_file = runner_file_for_uid(uid, "cmd")
    runner_file.write_text(
        "\n".join(
            [
                "@echo off",
                cmd,
                "set bg_exit=%errorlevel%",
                f'> "{exit_code_file_for_uid(uid)}" echo %bg_exit%',
                "exit /b %bg_exit%",
            ]
        ),
        encoding="utf-8",
    )
    return runner_file


def windows_ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def select_windows_shell() -> str | None:
    for shell_name in ("pwsh", "powershell"):
        shell_path = shutil.which(shell_name)
        if shell_path:
            return shell_path
    return None


def build_windows_wrapped_command(uid: str, cmd: str) -> tuple[list[str], str | None]:
    shell_path = select_windows_shell()
    if shell_path:
        runner_file = build_windows_runner_uid(uid, cmd)
        return (
            [
                shell_path,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(runner_file),
            ],
            shell_path,
        )

    runner_file = build_windows_cmd_runner(uid, cmd)
    return ["cmd.exe", "/d", "/c", str(runner_file)], None


def build_wrapped_command(uid: str, cmd: str) -> tuple[str | list[str], bool]:
    if sys.platform == "win32":
        wrapped_cmd, _ = build_windows_wrapped_command(uid, cmd)
        return wrapped_cmd, False

    exit_code_file = exit_code_file_for_uid(uid)
    wrapped = f'({cmd}); printf "%s" "$?" > {json.dumps(str(exit_code_file))}'
    return wrapped, True


def write_windows_start_launcher(
    uid: str,
    wrapped_cmd: list[str],
    stdout_path: Path,
    stderr_path: Path,
) -> Path:
    launcher_file = runner_file_for_uid(uid, "launcher.ps1")
    arg_lines = [f"    {windows_ps_literal(arg)}" for arg in wrapped_cmd[1:]]
    launcher_file.write_text(
        "\n".join(
            [
                "$argList = @(",
                *arg_lines,
                ")",
                (
                    f"$proc = Start-Process -FilePath {windows_ps_literal(wrapped_cmd[0])} "
                    f"-ArgumentList $argList -WindowStyle Hidden "
                    f"-RedirectStandardOutput {windows_ps_literal(str(stdout_path))} "
                    f"-RedirectStandardError {windows_ps_literal(str(stderr_path))} "
                    "-PassThru"
                ),
                "$proc.Id",
            ]
        ),
        encoding="utf-8",
    )
    return launcher_file


def create_record_identity(cmd: str) -> tuple[str, str, str]:
    index = load_index()
    taken_names = set(index.get("names", {}).keys())
    existing_uids = set(index.get("records", {}).keys())
    uid = generate_uid(existing_uids)
    name = friendly_name_for(cmd, taken_names)
    return uid, name, command_root_from_cmd(cmd)


def upsert_index_entry(
    index: dict, uid: str, name: str, record_relpath: str, cmd: str, created_at: str
) -> None:
    index.setdefault("version", INDEX_VERSION)
    index.setdefault("records", {})
    index.setdefault("names", {})

    previous = index["records"].get(uid)
    if previous and previous.get("name") and previous["name"] != name:
        index["names"].pop(previous["name"], None)

    index["records"][uid] = {
        "name": name,
        "record_relpath": record_relpath,
        "cmd": cmd,
        "created_at": created_at,
    }
    index["names"][name] = uid


def remove_index_entry(index: dict, uid: str) -> None:
    record = index.get("records", {}).pop(uid, None)
    if record and record.get("name"):
        index.get("names", {}).pop(record["name"], None)


def parse_record_meta(meta: object) -> dict | None:
    if not isinstance(meta, dict):
        return None

    required = ("uid", "cmd", "started_at")
    if not all(meta.get(field) for field in required):
        return None

    return meta


def record_view(
    *,
    uid: str,
    name: str,
    cmd: str | None,
    started_at: str | None,
    pid: int | None,
    record_state: str,
    process_state: str,
    finished_at: str | None = None,
    exit_code: int | None = None,
    last_event_type: str | None = None,
    last_event_at: str | None = None,
    matched_pattern: str | None = None,
    matched_stream: str | None = None,
    update_marker: str | None = None,
    elapsed_seconds: float | None = None,
    memory_bytes: int | None = None,
    cpu_percent: float | None = None,
    record_status: str | None = None,
    record_issue: str | None = None,
    record_path: str | None = None,
) -> dict:
    status = derive_status(
        record_state, process_state, exit_code, finished_at, record_status
    )
    return {
        "uid": uid,
        "id": uid,
        "name": name,
        "cmd": cmd,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "last_event_type": last_event_type,
        "last_event_at": last_event_at,
        "matched_pattern": matched_pattern,
        "matched_stream": matched_stream,
        "update_marker": update_marker,
        "pid": pid,
        "record_state": record_state,
        "process_state": process_state,
        "status": status,
        "elapsed_seconds": elapsed_seconds,
        "memory_bytes": memory_bytes,
        "cpu_percent": cpu_percent,
        "record_issue": record_issue,
        "record_path": record_path,
    }


def derive_status(
    record_state: str,
    process_state: str,
    exit_code: int | None,
    finished_at: str | None,
    record_status: str | None = None,
) -> str:
    if record_state != "ok":
        return record_state
    if record_status in {"completed", "failed"}:
        return record_status
    if exit_code is not None:
        return "completed" if exit_code == 0 else "failed"
    if finished_at:
        return "completed"
    if record_status in LAUNCHING_JOB_STATUSES:
        return "running"
    if process_state == "alive":
        return "running"
    if process_state in {"dead", "zombie"}:
        return "stale"
    if process_state == "missing_pid":
        return "unknown"
    return "unknown"


def derive_update_marker(meta: dict, status: str | None = None) -> str | None:
    event_type = str(meta.get("last_event_type") or "")
    if meta.get("matched_pattern"):
        pattern = str(meta.get("matched_pattern") or "")
        stream = str(meta.get("matched_stream") or "")
        marker = f"matched: {pattern}" if pattern else "matched output"
        if stream:
            marker = f"{marker} ({stream})"
        return marker
    if event_type == "matched_output":
        pattern = str(meta.get("matched_pattern") or "")
        stream = str(meta.get("matched_stream") or "")
        marker = f"matched: {pattern}" if pattern else "matched output"
        if stream:
            marker = f"{marker} ({stream})"
        return marker
    if event_type in {"completed", "failed"}:
        return event_type
    if status in {"completed", "failed"}:
        return status
    return None


def write_job_event(uid: str, meta: dict, event_type: str, **fields: object) -> dict:
    updated = dict(meta)
    updated["last_event_type"] = event_type
    updated["last_event_at"] = str(
        fields.pop("last_event_at", datetime.now().isoformat())
    )
    for key, value in fields.items():
        updated[key] = value
    write_meta(uid, updated)
    return updated


def persist_terminal_state(uid: str, meta: dict, process_state: str) -> dict:
    if process_state == "alive":
        return meta

    exit_file = exit_code_file_for_uid(uid)
    exit_code = read_exit_code_for_uid(uid)
    if exit_code is None:
        return meta

    updated = dict(meta)
    updated["exit_code"] = exit_code
    updated["status"] = "completed" if exit_code == 0 else "failed"
    updated["last_event_type"] = updated["status"]
    updated["last_event_at"] = updated.get("finished_at") or (
        datetime.fromtimestamp(exit_file.stat().st_mtime).isoformat()
        if exit_file.exists()
        else datetime.now().isoformat()
    )
    if not updated.get("finished_at"):
        updated["finished_at"] = (
            datetime.fromtimestamp(exit_file.stat().st_mtime).isoformat()
            if exit_file.exists()
            else datetime.now().isoformat()
        )
    write_meta(uid, updated)
    return updated


def build_view_from_meta(
    meta: dict,
    *,
    record_state: str = "ok",
    record_issue: str | None = None,
    process_state: str | None = None,
    record_path: Path | None = None,
    refresh_process: bool = True,
) -> dict:
    uid = str(meta.get("uid") or meta.get("id") or "")
    name = str(meta.get("name") or uid)
    started_at = meta.get("started_at")
    pid = meta.get("pid")

    if process_state is None:
        process_info = inspect_process(pid)
        process_state = process_info["process_state"]
        memory_bytes = process_info.get("memory_bytes")
        cpu_percent = process_info.get("cpu_percent")
    else:
        memory_bytes = None
        cpu_percent = None

    working_meta = dict(meta)
    if refresh_process and record_state == "ok" and process_state != "alive":
        working_meta = persist_terminal_state(uid, working_meta, process_state)

    elapsed_seconds = calculate_elapsed_seconds(working_meta.get("started_at"))
    status = derive_status(
        record_state,
        process_state,
        working_meta.get("exit_code"),
        working_meta.get("finished_at"),
        str(working_meta.get("status") or "") or None,
    )
    return record_view(
        uid=uid,
        name=name,
        cmd=working_meta.get("cmd"),
        started_at=working_meta.get("started_at"),
        pid=working_meta.get("pid"),
        record_state=record_state,
        process_state=process_state,
        finished_at=working_meta.get("finished_at"),
        exit_code=working_meta.get("exit_code"),
        last_event_type=working_meta.get("last_event_type"),
        last_event_at=working_meta.get("last_event_at"),
        matched_pattern=working_meta.get("matched_pattern"),
        matched_stream=working_meta.get("matched_stream"),
        update_marker=derive_update_marker(working_meta, status=status),
        elapsed_seconds=elapsed_seconds,
        memory_bytes=memory_bytes,
        cpu_percent=cpu_percent,
        record_status=str(working_meta.get("status") or "") or None,
        record_issue=record_issue or working_meta.get("record_issue"),
        record_path=str(record_path) if record_path else None,
    )


def load_record_snapshot(
    uid: str, *, index_entry: dict | None = None, refresh_process: bool = True
) -> dict | None:
    ensure_jobs_dir()
    record_dir: Path | None = None
    if index_entry and index_entry.get("record_relpath"):
        record_dir = JOBS_DIR / index_entry["record_relpath"]
    elif record_dir_for_uid(uid).exists():
        record_dir = record_dir_for_uid(uid)
    elif legacy_record_dir_for(uid).exists():
        record_dir = legacy_record_dir_for(uid)

    if record_dir is None:
        if index_entry:
            return record_view(
                uid=uid,
                name=str(index_entry.get("name") or uid),
                cmd=index_entry.get("cmd"),
                started_at=index_entry.get("created_at"),
                pid=None,
                record_state="missing",
                process_state="unknown",
                record_issue="record directory missing",
                record_path=str(JOBS_DIR / str(index_entry.get("record_relpath", ""))),
            )
        return None

    meta_file = record_dir / "meta.json"
    if not meta_file.exists():
        if index_entry:
            return record_view(
                uid=uid,
                name=str(index_entry.get("name") or uid),
                cmd=index_entry.get("cmd"),
                started_at=index_entry.get("created_at"),
                pid=None,
                record_state="missing",
                process_state="unknown",
                record_issue="meta.json missing",
                record_path=str(record_dir),
            )
        return record_view(
            uid=uid,
            name=uid,
            cmd=None,
            started_at=None,
            pid=None,
            record_state="orphaned",
            process_state="unknown",
            record_issue="directory exists without meta.json or index entry",
            record_path=str(record_dir),
        )

    try:
        meta = load_json_file(meta_file)
    except (OSError, json.JSONDecodeError):
        return record_view(
            uid=uid,
            name=str((index_entry or {}).get("name") or uid),
            cmd=(index_entry or {}).get("cmd"),
            started_at=(index_entry or {}).get("created_at"),
            pid=None,
            record_state="corrupt",
            process_state="unknown",
            record_issue="meta.json is unreadable",
            record_path=str(record_dir),
        )

    parsed = parse_record_meta(meta)
    if parsed is None:
        return record_view(
            uid=str(meta.get("uid") or uid),
            name=str(meta.get("name") or (index_entry or {}).get("name") or uid),
            cmd=str(meta.get("cmd") or (index_entry or {}).get("cmd") or ""),
            started_at=meta.get("started_at") or (index_entry or {}).get("created_at"),
            pid=meta.get("pid") if isinstance(meta.get("pid"), int) else None,
            record_state="corrupt",
            process_state="unknown",
            record_issue="meta.json is incomplete",
            record_path=str(record_dir),
        )

    if index_entry is None:
        index = load_index()
        upsert_index_entry(
            index,
            parsed["uid"],
            str(parsed.get("name") or parsed["uid"]),
            str(record_dir.relative_to(JOBS_DIR).as_posix()),
            str(parsed.get("cmd")),
            str(parsed.get("started_at")),
        )
        save_index(index)

    view = build_view_from_meta(
        parsed,
        record_state="ok",
        process_state=None,
        record_path=record_dir,
        refresh_process=refresh_process,
    )
    if view["name"] == view["uid"] and index_entry and index_entry.get("name"):
        view["name"] = str(index_entry["name"])
    return view


def resolve_job_ref(job_ref: str) -> str | None:
    index = load_index()
    if job_ref in index.get("names", {}):
        return index["names"][job_ref]
    if job_ref in index.get("records", {}):
        return job_ref
    if (
        meta_file_for_uid(job_ref).exists()
        or record_dir_for_uid(job_ref).exists()
        or legacy_record_dir_for(job_ref).exists()
    ):
        return job_ref
    return None


def load_job_snapshot(job_ref: str, *, refresh_process: bool = True) -> dict | None:
    uid = resolve_job_ref(job_ref)
    if uid is None:
        return None

    for snapshot in scan_jobs_from_disk(refresh_process=refresh_process):
        if str(snapshot.get("uid") or "") == uid:
            return snapshot
    return None


def load_job_meta(job_ref: str) -> dict | None:
    uid = resolve_job_ref(job_ref)
    if uid is None:
        return None

    meta_path = meta_file_for_uid(uid)
    if not meta_path.exists():
        return None

    try:
        raw = load_json_file(meta_path)
    except (OSError, json.JSONDecodeError):
        return None

    return raw if isinstance(raw, dict) else None


def read_stream_append(path: Path, offset: int) -> tuple[str, int]:
    if not path.exists():
        return "", offset

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "", offset

    if offset > len(content):
        offset = 0

    return content[offset:], len(content)


def is_terminal_snapshot(job: dict) -> bool:
    if job.get("status") in {"completed", "failed"}:
        return True
    if job.get("process_state") in {"dead", "zombie"}:
        return True
    return job.get("exit_code") is not None


def wait_for_completion(job_ref: str) -> dict:
    while True:
        job = load_job_snapshot(job_ref, refresh_process=True)
        if job is None:
            raise click.ClickException(f"Job not found: {job_ref}")
        if job.get("record_state") != "ok":
            raise click.ClickException(
                f"Job record not available: {job_ref} ({job.get('record_state')})"
            )
        if is_terminal_snapshot(job):
            return job
        time.sleep(0.2)


def wait_for_match(job_ref: str, pattern: str) -> dict:
    if not pattern:
        raise click.ClickException("--match requires a non-empty pattern")

    offsets = {"stdout": 0, "stderr": 0}
    tails = {"stdout": "", "stderr": ""}
    tail_limit = max(0, len(pattern) - 1)

    while True:
        job = load_job_snapshot(job_ref, refresh_process=True)
        if job is None:
            raise click.ClickException(f"Job not found: {job_ref}")
        if job.get("record_state") != "ok":
            raise click.ClickException(
                f"Job record not available: {job_ref} ({job.get('record_state')})"
            )

        record_dir = Path(job["record_path"])
        for stream in ("stdout", "stderr"):
            chunk, new_offset = read_stream_append(
                record_dir / f"{stream}.txt", offsets[stream]
            )
            offsets[stream] = new_offset
            haystack = f"{tails[stream]}{chunk}"
            if pattern in haystack:
                meta = load_job_meta(job_ref)
                if meta is not None:
                    write_job_event(
                        str(job["uid"]),
                        meta,
                        "matched_output",
                        matched_pattern=pattern,
                        matched_stream=stream,
                    )
                return job
            tails[stream] = haystack[-tail_limit:] if tail_limit else ""

        if is_terminal_snapshot(job):
            raise click.ClickException(
                f"Pattern not found before job finished: {job_ref}"
            )
        time.sleep(0.2)


def wait_for_all_jobs() -> list[dict]:
    targets = {
        str(job["uid"])
        for job in scan_jobs_from_disk(refresh_process=True)
        if job.get("record_state") == "ok"
    }
    if not targets:
        return []

    while True:
        active: list[dict] = []
        for job in scan_jobs_from_disk(refresh_process=True):
            if str(job.get("uid")) not in targets:
                continue
            if job.get("record_state") != "ok":
                continue
            if not is_terminal_snapshot(job):
                active.append(job)
        if not active:
            return []
        time.sleep(0.2)


def scan_jobs_from_disk(*, refresh_process: bool = True) -> list[dict]:
    ensure_jobs_dir()
    index = load_index()
    jobs: list[dict] = []
    seen: set[str] = set()

    for uid, entry in list(index.get("records", {}).items()):
        snapshot = load_record_snapshot(
            uid, index_entry=entry, refresh_process=refresh_process
        )
        if snapshot is not None:
            jobs.append(snapshot)
            seen.add(uid)

    for record_dir in RECORDS_DIR.iterdir():
        if not record_dir.is_dir():
            continue
        uid = record_dir.name
        if uid in seen:
            continue
        snapshot = load_record_snapshot(uid, refresh_process=refresh_process)
        if snapshot is not None:
            jobs.append(snapshot)
            seen.add(uid)

    for legacy_dir in JOBS_DIR.iterdir():
        if not legacy_dir.is_dir() or legacy_dir.name in {RECORDS_DIR.name}:
            continue
        if legacy_dir.name in seen:
            continue
        snapshot = load_record_snapshot(
            legacy_dir.name, refresh_process=refresh_process
        )
        if snapshot is not None:
            jobs.append(snapshot)
            seen.add(legacy_dir.name)

    for job in jobs:
        if job.get("record_state") == "ok" and job.get("uid") not in index.get(
            "records", {}
        ):
            upsert_index_entry(
                index,
                str(job["uid"]),
                str(job.get("name") or job["uid"]),
                str(
                    (
                        record_dir_for_uid(str(job["uid"]))
                        if (record_dir_for_uid(str(job["uid"])).exists())
                        else legacy_record_dir_for(str(job["uid"]))
                    )
                    .relative_to(JOBS_DIR)
                    .as_posix()
                ),
                str(job.get("cmd") or ""),
                str(job.get("started_at") or datetime.now().isoformat()),
            )

    save_index(index)
    deleted_uids = cleanup_terminal_jobs(jobs)
    if deleted_uids:
        jobs = [job for job in jobs if str(job.get("uid") or "") not in deleted_uids]
    jobs.sort(
        key=lambda item: item.get("started_at") or item.get("finished_at") or "",
        reverse=True,
    )
    return jobs


def list_jobs() -> list[dict]:
    """Backward-compatible alias for enumerating jobs."""
    return scan_jobs_from_disk()


def format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "-"

    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_memory(memory_bytes: int | None) -> str:
    if memory_bytes is None:
        return "-"
    if memory_bytes >= 1024**3:
        return f"{memory_bytes / (1024**3):.1f} GB"
    if memory_bytes >= 1024**2:
        return f"{memory_bytes / (1024**2):.0f} MB"
    if memory_bytes >= 1024:
        return f"{memory_bytes / 1024:.0f} KB"
    return f"{memory_bytes} B"


def record_dir_for_job(job: dict) -> Path | None:
    record_path = job.get("record_path")
    if record_path:
        return Path(str(record_path))

    uid = str(job.get("uid") or "")
    if not uid:
        return None

    if record_dir_for_uid(uid).exists():
        return record_dir_for_uid(uid)
    if legacy_record_dir_for(uid).exists():
        return legacy_record_dir_for(uid)
    return None


def terminal_job_reference_time(job: dict) -> datetime | None:
    for field in ("finished_at", "last_event_at", "started_at"):
        timestamp = parse_iso_timestamp(job.get(field))
        if timestamp is not None:
            return timestamp

    record_dir = record_dir_for_job(job)
    if record_dir is None:
        return None

    try:
        return datetime.fromtimestamp(record_dir.stat().st_mtime)
    except (OSError, ValueError):
        return None


def terminal_job_age_seconds(job: dict, now: datetime | None = None) -> float | None:
    reference_time = terminal_job_reference_time(job)
    if reference_time is None:
        return None

    if now is None:
        now = (
            datetime.now(reference_time.tzinfo)
            if reference_time.tzinfo
            else datetime.now()
        )

    return max(0.0, (now - reference_time).total_seconds())


def delete_job_records(jobs_to_delete: list[dict]) -> set[str]:
    removed_uids: set[str] = set()
    if not jobs_to_delete:
        return removed_uids

    index = load_index()
    for job in jobs_to_delete:
        uid = str(job.get("uid") or "")
        record_dir = record_dir_for_job(job)
        if record_dir is not None:
            shutil.rmtree(record_dir, ignore_errors=True)
        if uid:
            remove_index_entry(index, uid)
            removed_uids.add(uid)
    save_index(index)
    return removed_uids


def iter_storage_record_dirs() -> list[Path]:
    dirs: list[Path] = []
    for root in (RECORDS_DIR, JOBS_DIR):
        if not root.exists():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            if root == JOBS_DIR and entry.name == RECORDS_DIR.name:
                continue
            dirs.append(entry)
    return dirs


def cleanup_terminal_jobs(jobs: list[dict]) -> set[str]:
    now = datetime.now()
    candidates: list[tuple[float, str]] = []
    jobs_by_uid: dict[str, dict] = {}

    for job in jobs:
        uid = str(job.get("uid") or "")
        if not uid:
            continue
        jobs_by_uid[uid] = job
        if job.get("record_state") != "ok":
            continue
        if (
            job.get("status") in IN_PROGRESS_JOB_STATUSES
            or job.get("process_state") == "alive"
        ):
            continue

        age_seconds = terminal_job_age_seconds(job, now=now)
        if age_seconds is None:
            continue
        candidates.append((age_seconds, uid))

    to_delete_uids = {
        uid
        for age_seconds, uid in candidates
        if age_seconds >= TERMINAL_JOB_RETENTION_SECONDS
    }

    recent_candidates = sorted(
        [
            (age_seconds, uid)
            for age_seconds, uid in candidates
            if age_seconds < TERMINAL_JOB_RETENTION_SECONDS
        ],
        key=lambda item: item[0],
    )
    to_delete_uids.update(uid for _, uid in recent_candidates[TERMINAL_JOB_CAP:])

    jobs_to_delete = [jobs_by_uid[uid] for uid in to_delete_uids if uid in jobs_by_uid]
    return delete_job_records(jobs_to_delete)


def launch_process_for_job_inner(
    uid: str, cmd: str, stdout_path: Path, stderr_path: Path
) -> int:
    """Launch a process for a job and return its PID."""
    if sys.platform == "win32":
        wrapped_cmd, launcher_shell = build_windows_wrapped_command(uid, cmd)
        if launcher_shell:
            launcher_file = write_windows_start_launcher(
                uid, wrapped_cmd, stdout_path, stderr_path
            )
            result = subprocess.run(
                [
                    launcher_shell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(launcher_file),
                ],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return int(result.stdout.strip().splitlines()[-1])

        with (
            open(stdout_path, "a", encoding="utf-8") as stdout_file,
            open(stderr_path, "a", encoding="utf-8") as stderr_file,
        ):
            proc = subprocess.Popen(
                wrapped_cmd,
                shell=False,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW,
            )
    else:
        wrapped_cmd, use_shell = build_wrapped_command(uid, cmd)
        with (
            open(stdout_path, "a", encoding="utf-8") as stdout_file,
            open(stderr_path, "a", encoding="utf-8") as stderr_file,
        ):
            proc = subprocess.Popen(
                wrapped_cmd,
                shell=use_shell,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )

    return proc.pid


def mark_launch_failed(uid: str, meta: dict, issue: str) -> dict:
    updated = dict(meta)
    finished_at = datetime.now().isoformat()
    updated["pid"] = None
    updated["status"] = "failed"
    updated["finished_at"] = finished_at
    updated["exit_code"] = 1
    updated["record_issue"] = issue
    updated["last_event_type"] = "failed"
    updated["last_event_at"] = finished_at
    try:
        write_meta(uid, updated)
    except Exception:
        pass
    return updated


def mark_launch_worker_pid(uid: str, meta: dict, worker_pid: int) -> dict:
    updated = dict(meta)
    updated["launch_worker_pid"] = worker_pid
    try:
        write_meta(uid, updated)
    except Exception:
        pass
    return updated


def mark_launch_running(uid: str, meta: dict, pid: int) -> dict:
    updated = dict(meta)
    updated["pid"] = pid
    updated["status"] = "running"
    updated["finished_at"] = None
    updated["exit_code"] = None
    updated.pop("record_issue", None)
    try:
        write_meta(uid, updated)
    except Exception:
        pass
    return updated


def mark_launch_issue(uid: str, meta: dict, issue: str) -> dict:
    updated = dict(meta)
    updated["record_issue"] = issue
    try:
        write_meta(uid, updated)
    except Exception:
        pass
    return updated


def find_pid_from_launch_worker(worker_pid: int | None) -> int | None:
    if not isinstance(worker_pid, int) or worker_pid <= 0 or not HAS_PSUTIL:
        return None

    try:
        worker = psutil.Process(worker_pid)
        with worker.oneshot():
            if not worker.is_running():
                return None
            children = worker.children(recursive=True)

        alive_children = []
        for child in children:
            try:
                with child.oneshot():
                    if not child.is_running():
                        continue
                    if child.status() == getattr(psutil, "STATUS_ZOMBIE", None):
                        continue
                    alive_children.append(child)
            except (getattr(psutil, "Error", Exception), OSError):
                continue

        if not alive_children:
            return None

        alive_children.sort(
            key=lambda proc: (
                getattr(proc, "create_time", lambda: 0.0)(),
                proc.pid,
            )
        )
        return alive_children[0].pid
    except (getattr(psutil, "NoSuchProcess", Exception), ProcessLookupError):
        return None
    except (
        getattr(psutil, "AccessDenied", Exception),
        getattr(psutil, "Error", Exception),
        OSError,
    ):
        return None


def probe_launch_pid_for_job(
    uid: str, *, delay_seconds: float = LAUNCH_PID_PROBE_DELAY_SECONDS
) -> None:
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    deadline = time.time() + 1.5
    while time.time() <= deadline:
        meta = load_job_meta(uid)
        if meta is None:
            return

        if (
            meta.get("status") in {"completed", "failed"}
            or meta.get("exit_code") is not None
        ):
            return

        pid = meta.get("pid")
        if isinstance(pid, int) and inspect_process(pid).get("is_running"):
            return

        candidate = find_pid_from_launch_worker(meta.get("launch_worker_pid"))
        if candidate is not None:
            updated = dict(meta)
            updated["pid"] = candidate
            if updated.get("status") in LAUNCHING_JOB_STATUSES:
                updated["status"] = "running"
            updated.pop("record_issue", None)
            try:
                write_meta(uid, updated)
            except Exception:
                pass
            return

        time.sleep(0.5)

    meta = load_job_meta(uid)
    if meta is not None and meta.get("status") not in {"completed", "failed"}:
        mark_launch_issue(
            uid,
            meta,
            "pid probe could not confirm the launched process",
        )


def spawn_launch_pid_probe_for_job(
    uid: str, delay_seconds: float = LAUNCH_PID_PROBE_DELAY_SECONDS
) -> None:
    package_root = Path(__file__).resolve().parents[1]
    probe_script = "\n".join(
        [
            "import json",
            "import sys",
            f"sys.path.insert(0, {str(package_root)!r})",
            "from agent_sommelier.bg import probe_launch_pid_for_job",
            "payload = json.loads(sys.stdin.read() or '{}')",
            "probe_launch_pid_for_job(payload['uid'], delay_seconds=payload.get('delay_seconds', 5))",
        ]
    )
    payload = {"uid": uid, "delay_seconds": delay_seconds}

    # Write to temp script to avoid Windows lock on Scripts directory
    script_path = _write_temp_script(probe_script)

    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "text": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        ) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        [sys.executable, "-I", str(script_path)],
        **popen_kwargs,
    )
    try:
        if proc.stdin is not None:
            proc.stdin.write(dump_json(payload))
            proc.stdin.close()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise


def launch_process_for_job_worker(
    uid: str, cmd: str, stdout_path: Path, stderr_path: Path
) -> None:
    """Launch the target command and persist launch results."""
    meta = load_job_meta(uid)
    if meta is None:
        return

    try:
        pid = launch_process_for_job_inner(uid, cmd, stdout_path, stderr_path)
    except Exception as exc:
        mark_launch_failed(uid, meta, str(exc))
        return

    mark_launch_running(uid, meta, pid)


def spawn_launch_worker_for_job(
    uid: str, cmd: str, stdout_path: Path, stderr_path: Path
) -> int:
    package_root = Path(__file__).resolve().parents[1]
    worker_script = "\n".join(
        [
            "import json",
            "import sys",
            f"sys.path.insert(0, {str(package_root)!r})",
            "from pathlib import Path",
            "from agent_sommelier.bg import launch_process_for_job_worker",
            "payload = json.loads(sys.stdin.read() or '{}')",
            "launch_process_for_job_worker(",
            "    payload['uid'],",
            "    payload['cmd'],",
            "    Path(payload['stdout_path']),",
            "    Path(payload['stderr_path']),",
            ")",
        ]
    )
    payload = {
        "uid": uid,
        "cmd": cmd,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }

    # Write to temp script to avoid Windows lock on Scripts directory
    script_path = _write_temp_script(worker_script)

    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "text": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        ) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        [sys.executable, "-I", str(script_path)],
        **popen_kwargs,
    )
    try:
        if proc.stdin is not None:
            proc.stdin.write(dump_json(payload))
            proc.stdin.close()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise

    return proc.pid


def cleanup_partial_job_state(uid: str, record_dir: Path) -> None:
    try:
        index = load_index()
        remove_index_entry(index, uid)
        save_index(index)
        if not index.get("records") and not index.get("names"):
            INDEX_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    shutil.rmtree(record_dir, ignore_errors=True)


def create_job(cmd: str) -> str:
    ensure_jobs_dir()
    scan_jobs_from_disk(refresh_process=True)
    uid, name, root = create_record_identity(cmd)
    record_dir = record_dir_for_uid(uid)
    record_dir.mkdir(parents=True, exist_ok=False)

    try:
        started_at = datetime.now().isoformat()
        metadata = {
            "uid": uid,
            "id": uid,
            "name": name,
            "cmd": cmd,
            "command_root": root,
            "started_at": started_at,
            "status": "launching",
            "pid": None,
            "launch_worker_pid": None,
            "finished_at": None,
            "exit_code": None,
            "last_event_type": None,
            "last_event_at": None,
            "matched_pattern": None,
            "matched_stream": None,
            "record_issue": None,
        }

        index = load_index()
        upsert_index_entry(
            index,
            uid,
            name,
            str(record_dir.relative_to(JOBS_DIR).as_posix()),
            cmd,
            started_at,
        )
        save_index(index)
        write_meta(uid, metadata)

        stdout_path = stdout_file_for_uid(uid)
        stderr_path = stderr_file_for_uid(uid)
        try:
            worker_pid = spawn_launch_worker_for_job(uid, cmd, stdout_path, stderr_path)
        except Exception as exc:
            mark_launch_failed(uid, metadata, f"launch worker failed to start: {exc}")
            return name

        metadata = mark_launch_worker_pid(uid, metadata, worker_pid)
        try:
            spawn_launch_pid_probe_for_job(uid)
        except Exception as exc:
            mark_launch_issue(uid, metadata, f"pid probe failed to start: {exc}")
        return name
    except Exception:
        cleanup_partial_job_state(uid, record_dir)
        raise


def kill_process(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid)], capture_output=True, check=False
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def kill_process_force(pid: int) -> None:
    """Force-kill a process using SIGKILL (Unix) or taskkill /F (Windows)."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False
            )
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    except (OSError, ProcessLookupError):
        pass


def remove_job(job_ref: str) -> bool:
    snapshot = load_job_snapshot(job_ref, refresh_process=False)
    if snapshot is None:
        return False
    if snapshot.get("record_state") != "ok":
        return False

    pid = snapshot.get("pid")
    if snapshot.get("process_state") == "alive" and isinstance(pid, int):
        kill_process(pid)

    uid = str(snapshot["uid"])
    delete_job_records([snapshot])
    return True


def prune_jobs() -> int:
    jobs = scan_jobs_from_disk(refresh_process=True)
    removed_uids = set(
        delete_job_records(
            [
                job
                for job in jobs
                if job.get("status") not in IN_PROGRESS_JOB_STATUSES
                and job.get("process_state") != "alive"
            ]
        )
    )

    for record_dir in iter_storage_record_dirs():
        uid = record_dir.name
        if uid in removed_uids:
            continue

        snapshot = load_record_snapshot(uid, refresh_process=False)
        if snapshot is None:
            shutil.rmtree(record_dir, ignore_errors=True)
            index = load_index()
            remove_index_entry(index, uid)
            save_index(index)
            removed_uids.add(uid)
            continue

        if (
            snapshot.get("status") in IN_PROGRESS_JOB_STATUSES
            or snapshot.get("process_state") == "alive"
        ):
            continue

        removed_uids.update(delete_job_records([snapshot]))
    return len(removed_uids)


def restart_job(job_ref: str) -> str:
    """Restart a job by killing the process and starting a new one."""
    snapshot = load_job_snapshot(job_ref, refresh_process=False)
    if snapshot is None:
        raise click.ClickException(f"Job not found: {job_ref}")

    if snapshot.get("record_state") != "ok":
        raise click.ClickException(
            f"Job record not available: {job_ref} ({snapshot.get('record_state')})"
        )

    cmd = snapshot.get("cmd")
    if not cmd:
        raise click.ClickException(f"Job has no command: {job_ref}")

    uid = str(snapshot["uid"])
    name = str(snapshot.get("name") or uid)

    pid = snapshot.get("pid")
    if snapshot.get("process_state") == "alive" and isinstance(pid, int):
        kill_process(pid)

    stdout_path = stdout_file_for_uid(uid)
    stderr_path = stderr_file_for_uid(uid)

    new_pid = launch_process_for_job_inner(uid, cmd, stdout_path, stderr_path)

    meta = load_job_meta(uid)
    if meta is None:
        raise click.ClickException(f"Job metadata not found: {job_ref}")

    updated = dict(meta)
    updated["pid"] = new_pid
    updated["started_at"] = datetime.now().isoformat()
    updated["status"] = "running"
    updated["finished_at"] = None
    updated["exit_code"] = None
    write_meta(uid, updated)

    return name


@click.group()
@click.version_option(__version__, prog_name="bg")
def main() -> None:
    """Background job manager."""


@main.command()
@click.argument("cmd", nargs=-1, required=True)
def run(cmd: tuple[str, ...]) -> None:
    """Run a command in the background. Arguments are joined with spaces."""
    cmd_str = " ".join(cmd)
    try:
        click.echo(create_job(cmd_str))
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        raise click.ClickException(str(exc)) from exc


@main.command("list")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.option("--wide", is_flag=True, help="Wider command column")
@click.option("--format", "output_format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def list_cmd(json_output: bool, wide: bool, output_format: str) -> None:
    """List all background jobs.

    Use --wide to show longer commands, or --format json for machine-readable output.
    """
    jobs = scan_jobs_from_disk()

    if json_output or output_format == "json":
        click.echo(dump_json(jobs))
        return

    if not jobs:
        click.echo("No jobs found.")
        return

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Background Jobs")
    table.add_column("Name", style="cyan")
    table.add_column("UID", style="dim")
    table.add_column("Record", style="white")
    table.add_column("Process", style="white")
    table.add_column("Status", style="green")
    table.add_column("Update", style="cyan")
    table.add_column("PID", style="magenta")
    table.add_column("Started", style="dim")
    table.add_column("Elapsed", style="blue")
    cmd_max_width = 120 if wide else 60
    table.add_column("Command", style="white", max_width=cmd_max_width)

    for job in jobs:
        status = job["status"]
        status_style = {
            "running": "yellow",
            "launching": "yellow",
            "starting": "yellow",
            "completed": "green",
            "failed": "red",
            "stale": "magenta",
            "stopped": "blue",
            "missing": "red",
            "corrupt": "red",
            "orphaned": "red",
            "unknown": "white",
        }.get(status, "white")
        record_style = {
            "ok": "green",
            "missing": "red",
            "corrupt": "red",
            "orphaned": "red",
        }.get(job["record_state"], "white")
        process_style = {
            "alive": "yellow",
            "dead": "dim",
            "zombie": "red",
            "missing_pid": "red",
            "unknown": "white",
        }.get(job["process_state"], "white")
        update_marker = job.get("update_marker") or "-"
        update_type = job.get("last_event_type") or (
            status if status in {"completed", "failed"} else None
        )
        update_style = {
            "matched_output": "cyan",
            "completed": "green",
            "failed": "red",
        }.get(update_type, "white")
        table.add_row(
            job.get("name", "?"),
            job.get("uid", "?"),
            f"[{record_style}]{job['record_state']}[/{record_style}]",
            f"[{process_style}]{job['process_state']}[/{process_style}]",
            f"[{status_style}]{status}[/{status_style}]",
            f"[{update_style}]{update_marker}[/{update_style}]"
            if update_marker != "-"
            else "-",
            str(job.get("pid") or "-"),
            (job.get("started_at") or "?")[:19],
            format_elapsed(job.get("elapsed_seconds")),
            (job.get("cmd") or "?")[:cmd_max_width],
        )

    console.print(table)


@main.command()
@click.argument("job_ref")
def status(job_ref: str) -> None:
    """Check job status."""
    job = load_job_snapshot(job_ref)
    if not job:
        click.echo(f"Job not found: {job_ref}", err=True)
        sys.exit(1)

    click.echo(dump_json(job))
    if job.get("record_state") != "ok":
        sys.exit(1)


@main.command("wait")
@click.argument("job_ref")
@click.option("--match", "pattern", default=None, help="Wait for output pattern")
def wait(job_ref: str, pattern: str | None = None) -> None:
    """Wait for a job to complete or match output."""
    try:
        if pattern is not None:
            wait_for_match(job_ref, pattern)
        else:
            wait_for_completion(job_ref)
    except click.ClickException:
        raise
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        raise click.ClickException(str(exc)) from exc


@main.command("wait-all")
def wait_all() -> None:
    """Wait for all known jobs to finish."""
    try:
        wait_for_all_jobs()
    except click.ClickException:
        raise
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.argument("job_ref")
def read(job_ref: str) -> None:
    """Read job stdout."""
    job = load_job_snapshot(job_ref, refresh_process=False)
    if not job or job.get("record_state") != "ok":
        click.echo(f"Job record not available: {job_ref}", err=True)
        sys.exit(1)

    stdout_file = Path(job["record_path"]) / "stdout.txt"
    if not stdout_file.exists():
        click.echo(f"Job output not found: {job_ref}", err=True)
        sys.exit(1)

    click.echo(stdout_file.read_text(encoding="utf-8"))


@main.command()
@click.argument("job_ref")
def logs(job_ref: str) -> None:
    """Read job stdout and stderr."""
    job = load_job_snapshot(job_ref, refresh_process=False)
    if not job or job.get("record_state") != "ok":
        click.echo(f"Job record not available: {job_ref}", err=True)
        sys.exit(1)

    record_dir = Path(job["record_path"])
    stdout_file = record_dir / "stdout.txt"
    stderr_file = record_dir / "stderr.txt"

    if stdout_file.exists():
        click.echo("=== STDOUT ===")
        click.echo(stdout_file.read_text(encoding="utf-8"))

    if stderr_file.exists():
        stderr_text = stderr_file.read_text(encoding="utf-8")
        if stderr_text.strip():
            click.echo("\n=== STDERR ===")
            click.echo(stderr_text)


@main.command()
@click.argument("job_ref")
def rm(job_ref: str) -> None:
    """Remove a job record."""
    if remove_job(job_ref):
        click.echo(f"Removed job: {job_ref}")
        return
    click.echo(f"Job record not available: {job_ref}", err=True)
    sys.exit(1)


@main.command()
@click.argument("job_ref")
def restart(job_ref: str) -> None:
    """Restart a job."""
    try:
        name = restart_job(job_ref)
        # Get the updated job snapshot to show the new PID
        snapshot = load_job_snapshot(job_ref, refresh_process=False)
        pid = snapshot.get("pid", "?") if snapshot else "?"
        click.echo(f"Restarted {name} (PID: {pid})")
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.argument("job_ref")
def stop(job_ref: str) -> None:
    """Stop a background job. Preserves the record for read/logs."""
    snapshot = load_job_snapshot(job_ref, refresh_process=False)
    if snapshot is None:
        click.echo(f"Job not found: {job_ref}", err=True)
        sys.exit(1)
    if snapshot.get("record_state") != "ok":
        click.echo(f"Job record not available: {job_ref}", err=True)
        sys.exit(1)

    pid = snapshot.get("pid")
    name = snapshot.get("name", job_ref)

    if snapshot.get("process_state") == "alive" and isinstance(pid, int):
        kill_process(pid)

    # Update record status to "stopped" but keep all files
    uid = str(snapshot["uid"])
    meta = load_job_meta(uid)
    if meta is not None:
        write_job_event(uid, meta, "stopped", status="stopped")

    click.echo(f"Stopped {name}")


@main.command()
@click.argument("job_ref")
def kill(job_ref: str) -> None:
    """Force-stop a background job. Preserves the record for read/logs."""
    snapshot = load_job_snapshot(job_ref, refresh_process=False)
    if snapshot is None:
        click.echo(f"Job not found: {job_ref}", err=True)
        sys.exit(1)
    if snapshot.get("record_state") != "ok":
        click.echo(f"Job record not available: {job_ref}", err=True)
        sys.exit(1)

    pid = snapshot.get("pid")
    name = snapshot.get("name", job_ref)

    if snapshot.get("process_state") == "alive" and isinstance(pid, int):
        kill_process_force(pid)

    # Update record status to "stopped" but keep all files
    uid = str(snapshot["uid"])
    meta = load_job_meta(uid)
    if meta is not None:
        write_job_event(uid, meta, "stopped", status="stopped")

    click.echo(f"Killed {name}")


@main.command()
def prune() -> None:
    """Remove every job that is not currently running."""
    try:
        removed = prune_jobs()
        click.echo(f"Pruned {removed} job(s)")
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


@main.command(hidden=True)
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish", "powershell"]), default="bash")
@click.pass_context
def completions(ctx: click.Context, shell: str) -> None:
    """Print shell completion setup instructions.

    Use this to enable tab-completion for bg.

    Examples:

        bg completions bash   eval in .bashrc

        bg completions zsh   eval in .zshrc

        bg completions fish   source in config.fish

        bg completions powershell   add to $PROFILE
    """
    tool: str = ctx.parent.info_name if ctx.parent is not None and ctx.parent.info_name is not None else "bg"
    click.echo(f"# Enable shell completion for {tool}:")
    click.echo(f"# Add the following to your shell profile:")
    click.echo(f"eval $(_{tool.upper()}_COMPLETE={shell}_source {tool})")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - CLI surface
        if isinstance(exc, getattr(click, "ClickException", Exception)):
            click.echo(str(exc), err=True)
            sys.exit(1)
        raise
