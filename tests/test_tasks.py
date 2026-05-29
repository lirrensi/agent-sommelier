"""Comprehensive test suite for agent_sommelier.tasks module.

Tests both the data layer (functions) and CLI layer (Click commands).
Each test uses tmp_path isolation via os.chdir() so no files leak between tests.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml
from click.testing import CliRunner

# Ensure the source is importable
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from agent_sommelier.tasks import (
    CLOSED_HEADER,
    TASKS_HEADER,
    VALID_PRIORITIES,
    VALID_SOURCES,
    _collect_all_tags,
    _find_task_by_id,
    _format_id,
    _task_text,
    _priority_sort_key,
    _resolve_related,
    _strip_none_fields,
    add_task,
    close_task,
    filter_tasks,
    init_task_files,
    load_closed_yaml,
    load_inbox,
    load_tasks_yaml,
    main,
    next_counter_and_id,
    save_closed_yaml,
    save_tasks_yaml,
    search_tasks,
    update_task,
)
from agent_sommelier.tasks.storage import (
    STORAGE_VERSION,
    PerFileYamlStorage,
    detect_storage_version,
)
from agent_sommelier.tasks.core import (
    migrate_to_perfile,
    set_storage,
    _ensure_storage,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_cwd(tmp_path: Path) -> Path:
    """Change cwd to a temp directory so all file I/O is scoped per test."""
    os.chdir(str(tmp_path))
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    """A Click CliRunner for invoking CLI commands."""
    return CliRunner()


@pytest.fixture
def initted(tmp_cwd: Path) -> Path:
    """Initialize task files in the temp cwd and return the path."""
    init_task_files()
    return tmp_cwd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_task_fields(task: dict[str, Any], **expected: Any) -> None:
    """Assert that *task* contains every key-value pair in *expected*."""
    for key, value in expected.items():
        actual = task.get(key)
        assert actual == value, (
            f"task.{key} expected {value!r}, got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    """Config loading, defaults, and injection."""

    def test_default_config_injected_when_missing(self, tmp_cwd):
        """Loading a tasks.yaml without meta.config injects defaults."""
        (tmp_cwd / ".agents" / "tasks").mkdir(parents=True, exist_ok=True)
        data = {"meta": {"counter": 0}, "tasks": []}
        content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
        (tmp_cwd / ".agents" / "tasks" / "tasks.yaml").write_text(content, encoding="utf-8")
        meta, tasks = load_tasks_yaml()
        assert "config" in meta
        assert meta["config"]["default_status"] == "todo"
        assert meta["config"]["ready_status"] == "todo"
        assert meta["config"]["active_status"] == "in-progress"
        assert meta["config"]["close_status"] == "done"
        assert isinstance(meta["config"]["statuses"], list)

    def test_config_preserved_when_present(self, tmp_cwd):
        """An existing config is kept as-is."""
        (tmp_cwd / ".agents" / "tasks").mkdir(parents=True, exist_ok=True)
        custom = {"statuses": ["icebox", "next", "active", "done"], "default_status": "icebox", "ready_status": "next", "active_status": "active", "close_status": "done"}
        data = {"meta": {"counter": 0, "config": custom}, "tasks": []}
        content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
        (tmp_cwd / ".agents" / "tasks" / "tasks.yaml").write_text(content, encoding="utf-8")
        meta, tasks = load_tasks_yaml()
        assert meta["config"]["default_status"] == "icebox"

    def test_init_writes_default_config(self, initted):
        """tasks init writes config with defaults."""
        raw = yaml.safe_load((initted / ".agents" / "tasks" / "meta.yaml").read_text(encoding="utf-8"))
        assert "config" in raw
        assert raw["config"]["default_status"] == "todo"

    def test_add_uses_config_default_status(self, initted):
        """add_task uses config.default_status."""
        add_task("test")
        _, tasks = load_tasks_yaml()
        assert tasks[0]["status"] == "todo"

    def test_add_uses_custom_default_status(self, tmp_cwd):
        """With a custom default_status, add_task uses that."""
        (tmp_cwd / ".agents" / "tasks").mkdir(parents=True, exist_ok=True)
        custom = {"statuses": ["backlog", "todo", "in-progress", "done"], "default_status": "backlog", "ready_status": "todo", "active_status": "in-progress", "close_status": "done"}
        data = {"meta": {"counter": 0, "config": custom}, "tasks": []}
        content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
        (tmp_cwd / ".agents" / "tasks" / "tasks.yaml").write_text(content, encoding="utf-8")
        add_task("custom test")
        _, tasks = load_tasks_yaml()
        assert tasks[0]["status"] == "backlog"

    def test_update_rejects_invalid_status(self, initted):
        """update_task raises ValueError for status not in config.statuses."""
        add_task("test")
        with pytest.raises(ValueError, match="Invalid status"):
            update_task("TSK-0001", status="nonsense")

    def test_take_uses_config_active_status(self, tmp_cwd, runner):
        """take moves to config.active_status instead of hardcoded 'in-progress'."""
        (tmp_cwd / ".agents" / "tasks").mkdir(parents=True, exist_ok=True)
        custom = {"statuses": ["todo", "active", "done"], "default_status": "todo", "ready_status": "todo", "active_status": "active", "close_status": "done"}
        data = {"meta": {"counter": 0, "config": custom}, "tasks": []}
        content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
        (tmp_cwd / ".agents" / "tasks" / "tasks.yaml").write_text(content, encoding="utf-8")
        init_task_files()  # writes config
        # add_task uses default_status="todo" from config
        runner.invoke(main, ["add", "test"])
        result = runner.invoke(main, ["take", "TSK-0001"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["status"] == "active"

    def test_next_filters_by_config_ready_status(self, tmp_cwd, runner):
        """next pulls from config.ready_status."""
        (tmp_cwd / ".agents" / "tasks").mkdir(parents=True, exist_ok=True)
        custom = {"statuses": ["backlog", "ready", "active", "done"], "default_status": "backlog", "ready_status": "ready", "active_status": "active", "close_status": "done"}
        data = {"meta": {"counter": 0, "config": custom}, "tasks": []}
        content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
        (tmp_cwd / ".agents" / "tasks" / "tasks.yaml").write_text(content, encoding="utf-8")
        init_task_files()
        runner.invoke(main, ["add", "task-in-backlog"])       # default_status=backlog
        runner.invoke(main, ["add", "task-in-ready"])         # default_status=backlog
        runner.invoke(main, ["update", "TSK-0002", "--status", "ready"])
        result = runner.invoke(main, ["next"])
        assert result.exit_code == 0
        assert "task-in-ready" in result.output
        # backlog task should NOT appear in next output
        task_ids_in_output = [line for line in result.output.splitlines() if "TSK-" in line]
        assert all("backlog" not in line for line in task_ids_in_output) or "task-in-backlog" not in result.output


# ===========================================================================
# DATA LAYER TESTS
# ===========================================================================


class TestFileIO:
    """YAML file I/O: load, save, init."""

    def test_load_tasks_yaml_file_not_found(self, tmp_cwd: Path) -> None:
        """Raises FileNotFoundError when no tasks.yaml exists."""
        with pytest.raises(FileNotFoundError, match="Tasks file not found"):
            load_tasks_yaml()

    def test_init_creates_all_files(self, tmp_cwd: Path) -> None:
        """init_task_files creates meta.yaml and inbox.md."""
        results = init_task_files()
        tasks_dir = tmp_cwd / ".agents" / "tasks"
        assert (tasks_dir / "meta.yaml").exists()
        assert (tasks_dir / "inbox.md").exists()
        assert results.get("storage", "").startswith("per-file")
        assert any("created" in v for v in results.values())

    def test_init_is_idempotent(self, initted: Path) -> None:
        """Running init a second time reports 'exists' for all files."""
        results = init_task_files()
        assert any(v == "exists" for v in results.values())
        assert "per-file" in results.get("storage", "")

    def test_init_tasks_yaml_structure(self, initted: Path) -> None:
        """Freshly created meta.yaml has counter=0, config defaults."""
        meta, tasks = load_tasks_yaml()
        assert meta["counter"] == 0
        assert "config" in meta
        assert meta["config"]["default_status"] == "todo"
        assert isinstance(meta["config"]["statuses"], list)
        assert tasks == []

    def test_init_closed_yaml_structure(self, initted: Path) -> None:
        """Freshly created closed.yaml is empty."""
        assert load_closed_yaml() == []

    def test_save_and_load_roundtrip(self, initted: Path) -> None:
        """YAML round-trip: save then load returns identical data."""
        meta = {"counter": 5, "config": {"statuses": ["todo", "done"], "default_status": "todo", "ready_status": "todo", "active_status": "in-progress", "close_status": "done"}}
        tasks = [{"id": "TSK-0001", "title": "test", "status": "todo", "evidence": ["file: src/example.py"]}]
        save_tasks_yaml(meta, tasks)
        loaded_meta, loaded_tasks = load_tasks_yaml()
        assert loaded_meta == meta
        assert loaded_tasks == tasks

    def test_save_strips_none_fields(self, initted: Path) -> None:
        """save_tasks_yaml removes dict entries whose value is None."""
        meta = {"counter": 1}
        tasks = [
            {"id": "TSK-0001", "title": "test", "status": "todo", "priority": None, "evidence": None}
        ]
        save_tasks_yaml(meta, tasks)
        _, loaded = load_tasks_yaml()
        assert "priority" not in loaded[0]
        assert "evidence" not in loaded[0]

    def test_load_closed_returns_empty_for_missing_file(self, tmp_cwd: Path) -> None:
        """load_closed_yaml returns [] when closed.yaml does not exist."""
        assert load_closed_yaml() == []

    def test_load_closed_empty_after_init(self, initted: Path) -> None:
        """load_closed_yaml returns [] on fresh init."""
        assert load_closed_yaml() == []

    def test_save_closed_persists_task(self, initted: Path) -> None:
        """save_closed_yaml persists the task as an individual file."""
        task = {"id": "TSK-0001", "title": "done", "status": "done", "closed": True}
        save_closed_yaml([task])
        tasks_dir = initted / ".agents" / "tasks"
        task_path = tasks_dir / "TSK-0001.yaml"
        assert task_path.exists()
        loaded = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        assert loaded["title"] == "done"
        assert loaded["closed"] is True
        closed_list = load_closed_yaml()
        assert len(closed_list) == 1
        assert closed_list[0]["title"] == "done"

    def test_save_closed_multiple_tasks(self, initted: Path) -> None:
        """save_closed_yaml with multiple tasks creates individual files."""
        tasks = [
            {"id": "TSK-0001", "title": "a", "closed": True},
            {"id": "TSK-0002", "title": "b", "closed": True},
        ]
        save_closed_yaml(tasks)
        tasks_dir = initted / ".agents" / "tasks"
        assert (tasks_dir / "TSK-0001.yaml").exists()
        assert (tasks_dir / "TSK-0002.yaml").exists()
        closed_list = load_closed_yaml()
        assert len(closed_list) == 2

    def test_load_inbox_missing_file(self, tmp_cwd: Path) -> None:
        """load_inbox returns '' when inbox.md does not exist."""
        assert load_inbox() == ""

    def test_load_inbox_reads_content(self, initted: Path) -> None:
        """load_inbox reads inbox.md contents."""
        p = initted / ".agents" / "tasks" / "inbox.md"
        p.write_text("item 1\nitem 2\n", encoding="utf-8")
        assert load_inbox() == "item 1\nitem 2\n"


class TestAddTask:
    """add_task() — the core task-creation function."""

    def test_minimal_task(self, initted: Path) -> None:
        """A minimal task has only id, title, status, created.
        (source is set by the CLI default, not the data function.)"""
        task = add_task("My task")
        _assert_task_fields(
            task, id="TSK-0001", title="My task", status="todo"
        )
        assert "created" in task
        assert "priority" not in task
        assert "tags" not in task
        assert "related" not in task
        assert "notes" not in task
        assert "source" not in task

    def test_counter_increments(self, initted: Path) -> None:
        """Each call increments the counter."""
        assert add_task("First")["id"] == "TSK-0001"
        assert add_task("Second")["id"] == "TSK-0002"
        assert add_task("Third")["id"] == "TSK-0003"

    def test_source_not_set_when_none(self, initted: Path) -> None:
        """add_task with source=None does not set a source field.
        (The CLI default of 'agent' is handled by the Click layer.)"""
        task = add_task("Default source")
        assert "source" not in task

    def test_with_all_fields(self, initted: Path) -> None:
        """All optional fields are stored correctly."""
        task = add_task(
            title="Full task",
            priority="high",
            tags=["bug", "ui"],
            source="jira",
            deps=[{"id": "TSK-0000", "type": "blocks"}],
            notes="Some notes",
            evidence="Proof here",
        )
        _assert_task_fields(
            task,
            id="TSK-0001",
            title="Full task",
            status="todo",
            priority=1,
            source="jira",
            notes=["Some notes"],
            evidence=["Proof here"],
        )
        assert task["tags"] == ["bug", "ui"]
        assert task["deps"] == [{"id": "TSK-0000", "type": "blocks"}]
        assert "created" in task

    def test_tags_normalized(self, initted: Path) -> None:
        """Tags are lowercased, stripped, spaces→hyphens."""
        task = add_task("Tag norm", tags=["  BUG ", "UI Bug"])
        assert task["tags"] == ["bug", "ui-bug"]

    def test_prepends_newest_first(self, initted: Path) -> None:
        """Newest task appears first in the YAML file."""
        add_task("Older")
        add_task("Newer")
        _, tasks = load_tasks_yaml()
        assert tasks[0]["id"] == "TSK-0002"
        assert tasks[1]["id"] == "TSK-0001"

    def test_persists_to_yaml(self, initted: Path) -> None:
        """Task is actually saved into tasks.yaml."""
        add_task("Persistence test", priority="medium", tags=["test"])
        _, tasks = load_tasks_yaml()
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Persistence test"
        assert tasks[0]["priority"] == 2
        assert tasks[0]["tags"] == ["test"]

    def test_unicode_title(self, initted: Path) -> None:
        """Unicode characters in the title are preserved."""
        task = add_task("🔥 Tâche avec émoji 中文")
        assert task["title"] == "🔥 Tâche avec émoji 中文"

    def test_very_long_title(self, initted: Path) -> None:
        """Very long titles are stored without truncation."""
        long_title = "A" * 10_000
        task = add_task(long_title)
        assert len(task["title"]) == 10_000


class TestUpdateTask:
    """update_task() — modify an existing task's fields."""

    def test_update_status(self, initted: Path) -> None:
        add_task("Test")
        updated = update_task("TSK-0001", status="in-progress")
        assert updated["status"] == "in-progress"
        assert "updated" in updated

    def test_update_priority(self, initted: Path) -> None:
        add_task("Test")
        updated = update_task("TSK-0001", priority="urgent")
        assert updated["priority"] == 0

    def test_update_tags_appended(self, initted: Path) -> None:
        """Tags are appended, not replaced."""
        add_task("Test", tags=["ui"])
        updated = update_task("TSK-0001", tags=["bug"])
        assert updated["tags"] == ["ui", "bug"]

    def test_update_tags_no_duplicates(self, initted: Path) -> None:
        """Duplicate tags are not added."""
        add_task("Test", tags=["bug"])
        updated = update_task("TSK-0001", tags=["bug"])
        assert updated["tags"] == ["bug"]

    def test_update_tags_normalized(self, initted: Path) -> None:
        """New tags are normalized during update."""
        add_task("Test", tags=["ui"])
        updated = update_task("TSK-0001", tags=["  BUG "])
        assert updated["tags"] == ["ui", "bug"]

    def test_update_related(self, initted: Path) -> None:
        add_task("A")
        add_task("B")
        updated = update_task("TSK-0002", related="TSK-0001")
        assert updated["deps"] == [{"id": "TSK-0001", "type": "relates"}]

    def test_update_notes_appends(self, initted: Path) -> None:
        """Notes append by default, matching the CLI help text."""
        add_task("Test", notes="original")
        updated = update_task("TSK-0001", notes="replaced")
        assert updated["notes"] == ["original", "replaced"]

    def test_update_evidence_appends(self, initted: Path) -> None:
        add_task("Test", evidence="initial proof")
        updated = update_task("TSK-0001", evidence="later proof")
        assert updated["evidence"] == ["initial proof", "later proof"]

    def test_update_evidence_replaces(self, initted: Path) -> None:
        add_task("Test", evidence="initial proof")
        updated = update_task("TSK-0001", evidence="fresh proof", replace_evidence=True)
        assert updated["evidence"] == ["fresh proof"]

    def test_update_not_found(self, initted: Path) -> None:
        with pytest.raises(ValueError, match="Task not found: TSK-9999"):
            update_task("TSK-9999")

    def test_update_persists(self, initted: Path) -> None:
        add_task("Test", priority="low")
        update_task("TSK-0001", priority="high", tags=["updated"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == 1
        assert tasks[0]["tags"] == ["updated"]

    def test_update_only_requested_fields(self, initted: Path) -> None:
        """Fields not passed are untouched."""
        add_task("Test", priority="low", notes="original")
        update_task("TSK-0001", priority="urgent")
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == 0
        assert tasks[0]["notes"] == ["original"]

    @pytest.mark.parametrize("status_name", [
        "blocked", "postponed", "cancelled", "review", "waiting",
        "parked", "deferred", "backlog", "abandoned",
    ])
    def test_update_to_any_new_status(self, initted: Path, status_name: str) -> None:
        """All 9 new statuses are accepted by update_task()."""
        add_task("Test")
        updated = update_task("TSK-0001", status=status_name)
        assert updated["status"] == status_name
        assert "updated" in updated
        # Confirm persistence
        _, tasks = load_tasks_yaml()
        assert tasks[0]["status"] == status_name


class TestCloseTask:
    """close_task() — move a task from active to closed."""

    def test_moves_to_closed_yaml(self, initted: Path) -> None:
        add_task("To complete")
        closed = close_task("TSK-0001")
        assert closed["closed"] is True
        assert "closed_at" in closed
        # Gone from active
        _, tasks = load_tasks_yaml()
        assert all(t["id"] != "TSK-0001" for t in tasks)
        # Present in closed
        closed_list = load_closed_yaml()
        assert len(closed_list) == 1
        assert closed_list[0]["id"] == "TSK-0001"
        assert closed_list[0]["closed"] is True

    def test_close_not_found(self, initted: Path) -> None:
        with pytest.raises(ValueError, match="Task not found: TSK-9999"):
            close_task("TSK-9999")

    def test_close_already_closed(self, initted: Path) -> None:
        add_task("To complete")
        close_task("TSK-0001")
        with pytest.raises(ValueError, match="already closed"):
            close_task("TSK-0001")

    def test_close_with_note_appended(self, initted: Path) -> None:
        add_task("With notes", notes="initial")
        closed = close_task("TSK-0001", note="closing")
        assert "initial" in closed["notes"]
        assert "closing" in closed["notes"]

    def test_close_without_existing_notes(self, initted: Path) -> None:
        add_task("No notes")
        closed = close_task("TSK-0001", note="only this")
        assert closed["notes"] == ["only this"]

    def test_close_with_evidence(self, initted: Path) -> None:
        add_task("No evidence")
        closed = close_task("TSK-0001", evidence="file: src/agent_sommelier/tasks.py")
        assert closed["evidence"] == ["file: src/agent_sommelier/tasks.py"]

    def test_close_preserves_other_tasks(self, initted: Path) -> None:
        add_task("Keep me")
        add_task("Complete me")
        close_task("TSK-0002")
        _, tasks = load_tasks_yaml()
        assert len(tasks) == 1
        assert tasks[0]["id"] == "TSK-0001"

    def test_close_strips_from_active(self, initted: Path) -> None:
        """The task is physically removed from tasks.yaml."""
        add_task("To complete")
        close_task("TSK-0001")
        _, tasks = load_tasks_yaml()
        ids = [t["id"] for t in tasks]
        assert "TSK-0001" not in ids


class TestNextCounterAndId:
    """next_counter_and_id() operates on the YAML counter directly."""

    def test_increments_from_zero(self, initted: Path) -> None:
        counter, tid = next_counter_and_id()
        assert counter == 1
        assert tid == "TSK-0001"

    def test_second_call(self, initted: Path) -> None:
        next_counter_and_id()
        counter, tid = next_counter_and_id()
        assert counter == 2
        assert tid == "TSK-0002"

    def test_file_not_found(self, tmp_cwd: Path) -> None:
        with pytest.raises(FileNotFoundError):
            next_counter_and_id()


class TestFilterTasks:
    """filter_tasks() — pure function, all filters ANDed."""

    SAMPLE: list[dict[str, Any]] = [
        {"id": "TSK-0001", "title": "Bug fix", "status": "todo",
         "priority": 1, "tags": ["bug"], "source": "jira"},
        {"id": "TSK-0002", "title": "Feature", "status": "todo",
         "priority": 3, "tags": ["feature"], "source": "agent"},
        {"id": "TSK-0003", "title": "In progress", "status": "in-progress",
         "priority": 0, "tags": ["bug", "ui"], "source": "inbox"},
        {"id": "TSK-0004", "title": "Done task", "status": "done",
         "priority": 2, "tags": [], "source": "test"},
    ]

    def test_no_filters(self) -> None:
        assert len(filter_tasks(self.SAMPLE)) == 4

    def test_by_status(self) -> None:
        result = filter_tasks(self.SAMPLE, status="todo")
        assert len(result) == 2
        assert all(t["status"] == "todo" for t in result)

    def test_by_tag(self) -> None:
        result = filter_tasks(self.SAMPLE, tag="bug")
        assert len(result) == 2
        assert all("bug" in (t.get("tags") or []) for t in result)

    def test_by_priority(self) -> None:
        result = filter_tasks(self.SAMPLE, priority=1)
        assert len(result) == 1
        assert result[0]["id"] == "TSK-0001"

    def test_by_source(self) -> None:
        result = filter_tasks(self.SAMPLE, source="inbox")
        assert len(result) == 1
        assert result[0]["id"] == "TSK-0003"

    def test_combined_and(self) -> None:
        result = filter_tasks(self.SAMPLE, status="todo", tag="bug")
        assert len(result) == 1
        assert result[0]["id"] == "TSK-0001"

    def test_no_match(self) -> None:
        assert filter_tasks(self.SAMPLE, tag="nonexistent") == []

    def test_tag_on_task_without_tags(self) -> None:
        tasks = [{"id": "TSK-0001", "title": "No tags", "status": "todo"}]
        assert filter_tasks(tasks, tag="bug") == []


class TestHelpers:
    """Standalone helper functions."""

    def test_format_id(self) -> None:
        assert _format_id(1) == "TSK-0001"
        assert _format_id(42) == "TSK-0042"
        assert _format_id(9999) == "TSK-9999"

    def test_strip_none(self) -> None:
        result = _strip_none_fields(
            {"a": 1, "b": None, "c": "hello", "d": None}
        )
        assert result == {"a": 1, "c": "hello"}

    def test_strip_none_empty(self) -> None:
        assert _strip_none_fields({}) == {}

    def test_find_task_by_id(self) -> None:
        tasks = [
            {"id": "TSK-0001", "title": "A"},
            {"id": "TSK-0002", "title": "B"},
        ]
        assert _find_task_by_id(tasks, "TSK-0002")["title"] == "B"

    def test_find_task_by_id_missing(self) -> None:
        assert _find_task_by_id([{"id": "TSK-0001"}], "TSK-9999") is None

    def test_find_task_by_id_empty(self) -> None:
        assert _find_task_by_id([], "TSK-0001") is None

    def test_resolve_related_from_active(self) -> None:
        active = [{"id": "TSK-0001", "title": "Active", "status": "todo"}]
        found = _resolve_related("TSK-0001", active, [])
        assert found is not None
        assert found["title"] == "Active"

    def test_resolve_related_from_done(self) -> None:
        done = [{"id": "TSK-0001", "title": "Done", "status": "done"}]
        found = _resolve_related("TSK-0001", [], done)
        assert found is not None
        assert found["title"] == "Done"

    def test_resolve_related_prefers_active(self) -> None:
        active = [
            {"id": "TSK-0001", "title": "Active", "status": "in-progress"}
        ]
        done = [
            {"id": "TSK-0001", "title": "Done copy", "status": "done"}
        ]
        found = _resolve_related("TSK-0001", active, done)
        assert found is not None
        assert found["title"] == "Active"

    def test_resolve_related_not_found(self) -> None:
        assert _resolve_related("TSK-9999", [], []) is None

    def test_priority_sort_key(self) -> None:
        assert _priority_sort_key({"priority": 0}) == 0
        assert _priority_sort_key({"priority": 1}) == 1
        assert _priority_sort_key({"priority": 2}) == 2
        assert _priority_sort_key({"priority": 3}) == 3
        assert _priority_sort_key({"priority": 4}) == 4
        assert _priority_sort_key({}) == 99
        assert _priority_sort_key({"priority": "bogus"}) == 99

    def test_collect_all_tags(self) -> None:
        tasks = [
            {"tags": ["bug", "ui"]},
            {"tags": ["bug"]},
            {"tags": ["feature"]},
            {},
        ]
        assert _collect_all_tags(tasks) == {"bug": 2, "ui": 1, "feature": 1}

    def test_collect_all_tags_empty(self) -> None:
        assert _collect_all_tags([]) == {}

    def test_collect_sorted_by_frequency(self) -> None:
        tasks = [
            {"tags": ["rare"]},
            {"tags": ["common", "common"]},
            {"tags": ["common"]},
        ]
        counts = _collect_all_tags(tasks)
        items = list(counts.items())
        assert items[0][0] == "common"  # highest count first

    def test_task_text_includes_evidence(self) -> None:
        task = {"id": "TSK-0001", "title": "Test", "evidence": ["Email sent to finance@example.com"]}
        assert "finance@example.com" in _task_text(task)

    def test_search_matches_evidence(self) -> None:
        tasks = [{"id": "TSK-0001", "title": "Test", "evidence": ["Evidence trail"]}]
        result = search_tasks(tasks, "trail")
        assert result == tasks


# ===========================================================================
# CLI LAYER TESTS  (via Click CliRunner)
# ===========================================================================


class TestCliInit:
    """tasks init"""

    def test_init_creates_files(self, tmp_cwd: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        assert "Created" in result.output
        assert "Task system initialized." in result.output
        tasks_dir = tmp_cwd / ".agents" / "tasks"
        assert (tasks_dir / "meta.yaml").exists()
        assert (tasks_dir / "inbox.md").exists()

    def test_init_idempotent(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        assert "All files already exist." in result.output


class TestCliAdd:
    """tasks add <title>"""

    def test_add_minimal(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["add", "My task"])
        assert result.exit_code == 0
        assert "Created TSK-0001: My task" in result.output

    def test_add_with_options(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, [
            "add", "Full task",
            "--tag", "bug",
            "--tag", "ui",
            "--priority", "high",
            "--source", "jira",
            "--dep", "TSK-0000:blocks",
            "--notes", "Important",
            "--evidence", "Proof",
        ])
        assert result.exit_code == 0
        assert "Created TSK-0001: Full task" in result.output
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == 1
        assert tasks[0]["tags"] == ["bug", "ui"]
        assert tasks[0]["source"] == "jira"
        assert tasks[0]["deps"] == [{"id": "TSK-0000", "type": "blocks"}]
        assert tasks[0]["evidence"] == ["Proof"]

    def test_add_auto_increments(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "First"])
        runner.invoke(main, ["add", "Second"])
        result = runner.invoke(main, ["add", "Third"])
        assert "Created TSK-0003" in result.output

    def test_add_default_source(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Default"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["source"] == "agent"


class TestCliList:
    """tasks list"""

    def test_list_empty(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "No tasks found." in result.output

    def test_list_with_tasks(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Task A", "--tag", "bug"])
        runner.invoke(main, ["add", "Task B", "--priority", "high"])
        result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "TSK-0002" in result.output
        assert "TSK-0001" in result.output

    def test_list_filter_tag(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Bug task", "--tag", "bug"])
        runner.invoke(main, ["add", "Feature", "--tag", "feature"])
        result = runner.invoke(main, ["list", "--tag", "bug"])
        assert result.exit_code == 0
        assert "Bug task" in result.output or "No tasks" not in result.output  # noqa: SIM300
        # Harder to verify Rich table output precisely; use JSON path below

    def test_list_json(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "JSON task", "--tag", "json", "--priority", "urgent"])
        result = runner.invoke(main, ["list", "--json"])
        assert result.exit_code == 0
        tasks = json.loads(result.output)
        assert isinstance(tasks, list)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "JSON task"
        assert tasks[0]["tags"] == ["json"]
        assert tasks[0]["priority"] == 0

    def test_list_json_filtered(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Bug high", "--tag", "bug", "--priority", "high"])
        runner.invoke(main, ["add", "Bug low", "--tag", "bug", "--priority", "low"])
        runner.invoke(main, ["add", "Feature high", "--tag", "feature", "--priority", "high"])
        result = runner.invoke(main, ["list", "--tag", "bug", "--priority", "high", "--json"])
        assert result.exit_code == 0
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Bug high"

    def test_list_excludes_closed(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Active"])
        runner.invoke(main, ["add", "To complete"])
        runner.invoke(main, ["close", "TSK-0002"])
        result = runner.invoke(main, ["list", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "TSK-0001"

    def test_list_filter_status_blocked(self, initted: Path, runner: CliRunner) -> None:
        """Filtering by --status blocked works via the CLI."""
        runner.invoke(main, ["add", "Active task"])
        runner.invoke(main, ["add", "Blocked task"])
        runner.invoke(main, ["update", "TSK-0002", "--status", "blocked"])
        result = runner.invoke(main, ["list", "--status", "blocked", "--json"])
        assert result.exit_code == 0
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "TSK-0002"
        assert tasks[0]["status"] == "blocked"

    def test_list_filter_status_waiting(self, initted: Path, runner: CliRunner) -> None:
        """Filtering by --status waiting works via the CLI."""
        runner.invoke(main, ["add", "Active task"])
        runner.invoke(main, ["add", "Waiting task"])
        runner.invoke(main, ["update", "TSK-0002", "--status", "waiting"])
        result = runner.invoke(main, ["list", "--status", "waiting", "--json"])
        assert result.exit_code == 0
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "TSK-0002"
        assert tasks[0]["status"] == "waiting"


class TestCliShow:
    """tasks show <ID>"""

    def test_show_task(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Show me", "--priority", "urgent", "--notes", "detail", "--evidence", "file: src/agent_sommelier/tasks.py"])
        result = runner.invoke(main, ["show", "TSK-0001"])
        assert result.exit_code == 0
        assert "TSK-0001" in result.output
        assert "Show me" in result.output
        assert "p0" in result.output
        assert "detail" in result.output
        assert "evidence" in result.output

    def test_show_not_found(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["show", "TSK-9999"])
        assert result.exit_code == 1
        assert "Task not found: TSK-9999" in result.output

    def test_show_related_resolved(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Parent task"])
        runner.invoke(main, ["add", "Child task", "--related", "TSK-0001"])
        result = runner.invoke(main, ["show", "TSK-0002"])
        assert result.exit_code == 0
        assert "dep (relates): TSK-0001 (todo)" in result.output
        assert "Parent task" in result.output

    def test_show_related_from_closed(self, initted: Path, runner: CliRunner) -> None:
        """Resolves related even when the target is closed (preserves its status)."""
        runner.invoke(main, ["add", "Done target"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "Depends on done", "--related", "TSK-0001"])
        result = runner.invoke(main, ["show", "TSK-0002"])
        assert result.exit_code == 0
        # Close preserves status (todo), so related shows (todo)
        assert "dep (relates): TSK-0001 (todo)" in result.output

    def test_show_related_not_found(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Orphan", "--related", "TSK-9999"])
        result = runner.invoke(main, ["show", "TSK-0001"])
        assert result.exit_code == 0
        assert "dep (relates): TSK-9999 (not found)" in result.output

    def test_show_closed_task(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "To complete"])
        runner.invoke(main, ["close", "TSK-0001"])
        result = runner.invoke(main, ["show", "TSK-0001"])
        assert result.exit_code == 0
        assert "closed_at" in result.output


class TestCliUpdate:
    """tasks update <ID>"""

    def test_update_status_in_progress(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Test task"])
        result = runner.invoke(main, ["update", "TSK-0001", "--status", "in-progress"])
        assert result.exit_code == 0
        assert "Updated TSK-0001" in result.output

    def test_update_status_blocked(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Blocker task"])
        result = runner.invoke(main, ["update", "TSK-0001", "--status", "blocked"])
        assert result.exit_code == 0
        assert "Updated TSK-0001" in result.output
        _, tasks = load_tasks_yaml()
        assert tasks[0]["status"] == "blocked"

    def test_update_status_postponed(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Postponed task"])
        result = runner.invoke(main, ["update", "TSK-0001", "--status", "postponed"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["status"] == "postponed"

    def test_update_status_review(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Review task"])
        result = runner.invoke(main, ["update", "TSK-0001", "--status", "review"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["status"] == "review"

    def test_update_tags_appended(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Test task", "--tag", "ui"])
        runner.invoke(main, ["update", "TSK-0001", "--tag", "bug"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["tags"] == ["ui", "bug"]

    def test_update_priority(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Test task", "--priority", "low"])
        runner.invoke(main, ["update", "TSK-0001", "--priority", "urgent"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == 0

    def test_update_evidence(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Test task"])
        result = runner.invoke(main, ["update", "TSK-0001", "--evidence", "proof"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["evidence"] == ["proof"]

    def test_update_not_found(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["update", "TSK-9999"])
        assert result.exit_code == 1
        assert "Task not found: TSK-9999" in result.output

    def test_update_closed(self, initted: Path, runner: CliRunner) -> None:
        """--closed flag on update moves task to closed.yaml, preserves status."""
        runner.invoke(main, ["add", "Close via update"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "blocked"])
        result = runner.invoke(main, ["update", "TSK-0001", "--closed"])
        assert result.exit_code == 0
        assert "Updated TSK-0001" in result.output
        # Verify in closed.yaml with preserved status
        closed_list = load_closed_yaml()
        assert len(closed_list) == 1
        assert closed_list[0]["id"] == "TSK-0001"
        assert closed_list[0]["status"] == "blocked"
        assert closed_list[0]["closed"] is True
        assert "closed_at" in closed_list[0]


class TestTakeCommand:
    """tasks take <id> — shorthand for marking in-progress."""

    def test_take_sets_in_progress(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "My task"])
        result = runner.invoke(main, ["take", "TSK-0001"])
        assert result.exit_code == 0
        assert "is now in-progress" in result.output
        _, tasks = load_tasks_yaml()
        assert tasks[0]["status"] == "in-progress"

    def test_take_idempotent(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "My task"])
        runner.invoke(main, ["take", "TSK-0001"])
        result = runner.invoke(main, ["take", "TSK-0001"])
        assert result.exit_code == 0
        assert "is now in-progress" in result.output

    def test_take_not_found(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["take", "TSK-9999"])
        assert result.exit_code == 1
        assert "Task not found" in result.output

    def test_take_with_claimed(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "My task"])
        result = runner.invoke(main, ["take", "TSK-0001", "--claimed", "rx"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["claimed"] == "rx"
        assert tasks[0]["status"] == "in-progress"

    def test_take_defaults_claimed_agent(self, initted: Path, runner: CliRunner) -> None:
        """take with no --claimed defaults to 'agent'."""
        runner.invoke(main, ["add", "My task"])
        result = runner.invoke(main, ["take", "TSK-0001"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["claimed"] == "agent"
        assert tasks[0]["status"] == "in-progress"

    def test_claim_command_alias(self, initted: Path, runner: CliRunner) -> None:
        """claim command is an alias for take."""
        runner.invoke(main, ["add", "My task"])
        result = runner.invoke(main, ["claim", "TSK-0001", "--claimed", "bob"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["claimed"] == "bob"
        assert tasks[0]["status"] == "in-progress"

    def test_claim_defaults_claimed_agent(self, initted: Path, runner: CliRunner) -> None:
        """claim with no --claimed defaults to 'agent'."""
        runner.invoke(main, ["add", "My task"])
        result = runner.invoke(main, ["claim", "TSK-0001"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["claimed"] == "agent"


class TestClaimedAndCreatedBy:
    """Optional claimed and createdBy fields on tasks."""

    def test_add_with_claimed(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["add", "Task with claim", "--claimed", "rx"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["claimed"] == "rx"

    def test_add_with_created_by(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["add", "Task with creator", "--created-by", "gmail-import"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["createdBy"] == "gmail-import"

    def test_add_with_both_fields(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["add", "Full task", "--claimed", "rx", "--created-by", "agent"])
        assert result.exit_code == 0
        _, tasks = load_tasks_yaml()
        assert tasks[0]["claimed"] == "rx"
        assert tasks[0]["createdBy"] == "agent"

    def test_add_without_fields(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Task without fields"])
        _, tasks = load_tasks_yaml()
        assert "claimed" not in tasks[0]
        assert "createdBy" not in tasks[0]

    def test_update_set_claimed(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "My task"])
        runner.invoke(main, ["update", "TSK-0001", "--claimed", "alice"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["claimed"] == "alice"

    def test_update_set_created_by(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "My task"])
        runner.invoke(main, ["update", "TSK-0001", "--created-by", "rx"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["createdBy"] == "rx"

    def test_update_clear_claimed(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "My task", "--claimed", "bob"])
        runner.invoke(main, ["update", "TSK-0001", "--claimed", ""])
        _, tasks = load_tasks_yaml()
        assert "claimed" not in tasks[0]

    def test_update_clear_created_by(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "My task", "--created-by", "agent"])
        runner.invoke(main, ["update", "TSK-0001", "--created-by", ""])
        _, tasks = load_tasks_yaml()
        assert "createdBy" not in tasks[0]

    def test_claimed_in_show_output(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "My task", "--claimed", "rx"])
        result = runner.invoke(main, ["show", "TSK-0001"])
        assert result.exit_code == 0
        assert "claimed: rx" in result.output

    def test_created_by_in_show_output(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "My task", "--created-by", "agent"])
        result = runner.invoke(main, ["show", "TSK-0001"])
        assert result.exit_code == 0
        assert "createdBy: agent" in result.output

    def test_claimed_task_excluded_from_next(self, initted: Path, runner: CliRunner) -> None:
        """Claimed tasks are excluded from next."""
        runner.invoke(main, ["add", "Free task", "--priority", "high"])
        runner.invoke(main, ["add", "Claimed task", "--claimed", "rx", "--priority", "urgent"])
        result = runner.invoke(main, ["next", "--take", "all"])
        assert result.exit_code == 0
        assert "Free task" in result.output
        assert "Claimed task" not in result.output

    def test_claimed_task_excluded_from_ready(self, initted: Path, runner: CliRunner) -> None:
        """Claimed tasks are excluded from ready."""
        runner.invoke(main, ["add", "Free task", "--priority", "high"])
        runner.invoke(main, ["add", "Claimed task", "--claimed", "rx", "--priority", "urgent"])
        result = runner.invoke(main, ["ready", "--take", "all"])
        assert result.exit_code == 0
        assert "Free task" in result.output
        assert "Claimed task" not in result.output


class TestCliClose:
    """tasks close <ID>"""

    def test_close_moves_task(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Complete me"])
        result = runner.invoke(main, ["close", "TSK-0001"])
        assert result.exit_code == 0
        assert "Closed TSK-0001" in result.output
        # Confirm via data layer
        closed_list = load_closed_yaml()
        assert len(closed_list) == 1
        assert closed_list[0]["id"] == "TSK-0001"

    def test_close_with_note(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "With notes", "--notes", "initial"])
        runner.invoke(main, ["close", "TSK-0001", "--note", "done note", "--evidence", "file: docs/result.md"])
        closed_list = load_closed_yaml()
        assert "initial" in closed_list[0]["notes"]
        assert "done note" in closed_list[0]["notes"]
        assert closed_list[0]["evidence"] == ["file: docs/result.md"]

    def test_close_already_closed(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Already done"])
        runner.invoke(main, ["close", "TSK-0001"])
        result = runner.invoke(main, ["close", "TSK-0001"])
        assert result.exit_code == 1
        assert "already closed" in result.output

    def test_close_not_found(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["close", "TSK-9999"])
        assert result.exit_code == 1
        assert "Task not found" in result.output

    def test_close_preserves_status(self, initted: Path, runner: CliRunner) -> None:
        """tasks close does NOT change the task's status."""
        runner.invoke(main, ["add", "Postponed task"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "postponed"])
        result = runner.invoke(main, ["close", "TSK-0001"])
        assert result.exit_code == 0
        assert "status: postponed" in result.output
        closed_list = load_closed_yaml()
        assert closed_list[0]["status"] == "postponed"
        assert closed_list[0]["closed"] is True


class TestCliNext:
    """tasks next"""

    def test_next_no_tasks(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["next"])
        assert result.exit_code == 0
        assert "No tasks found." in result.output

    def test_next_shows_highest_priority(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Low priority", "--priority", "low"])
        runner.invoke(main, ["add", "Urgent task", "--priority", "urgent"])
        result = runner.invoke(main, ["next"])
        assert result.exit_code == 0
        assert "Urgent task" in result.output
        assert "Low priority" not in result.output

    def test_next_filters_by_tag(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Bug task", "--tag", "bug"])
        runner.invoke(main, ["add", "Docs task", "--tag", "docs"])
        result = runner.invoke(main, ["next", "--tag", "bug", "--take", "all"])
        assert result.exit_code == 0
        assert "Bug task" in result.output
        assert "Docs task" not in result.output

    def test_next_priority_order(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Low", "--priority", "low"])
        runner.invoke(main, ["add", "Medium", "--priority", "medium"])
        runner.invoke(main, ["add", "High", "--priority", "high"])
        runner.invoke(main, ["add", "Urgent", "--priority", "urgent"])
        result = runner.invoke(main, ["next", "--take", "all"])
        assert result.exit_code == 0
        output = result.output
        urgent_pos = output.index("Urgent")
        high_pos = output.index("High")
        medium_pos = output.index("Medium")
        low_pos = output.index("Low")
        assert urgent_pos < high_pos < medium_pos < low_pos

    def test_next_take_all(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "One"])
        runner.invoke(main, ["add", "Two"])
        runner.invoke(main, ["add", "Three"])
        result = runner.invoke(main, ["next", "--take", "all"])
        assert result.exit_code == 0
        assert "TSK-0003" in result.output
        assert "TSK-0002" in result.output
        assert "TSK-0001" in result.output

    def test_next_take_two(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "One"])
        runner.invoke(main, ["add", "Two"])
        runner.invoke(main, ["add", "Three"])
        result = runner.invoke(main, ["next", "--take", "2"])
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if "TSK-" in l]
        assert len(lines) == 2

    def test_next_skip_related(self, initted: Path, runner: CliRunner) -> None:
        """--skip-related excludes tasks with unresolved blocks-type deps."""
        runner.invoke(main, ["add", "Independent"])
        runner.invoke(main, ["add", "Depends on open", "--dep", "TSK-0001:blocks"])
        runner.invoke(main, ["add", "Depends on missing", "--dep", "TSK-9999:blocks"])
        result = runner.invoke(main, ["next", "--take", "all", "--skip-related"])
        # TSK-0001: no deps => included
        assert "TSK-0001" in result.output
        # TSK-0002: blocks=TSK-0001 (todo, not done) => excluded
        assert "TSK-0002" not in result.output
        # TSK-0003: blocks=TSK-9999 (not found) => excluded
        assert "TSK-0003" not in result.output

    def test_next_skip_related_allows_closed(self, initted: Path, runner: CliRunner) -> None:
        """--skip-related includes tasks whose blocks target is done/closed."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "Depends on done", "--dep", "TSK-0001:blocks"])
        result = runner.invoke(main, ["next", "--take", "all", "--skip-related"])
        assert "TSK-0002" in result.output

    def test_next_invalid_take(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Test"])
        result = runner.invoke(main, ["next", "--take", "not-a-number"])
        assert result.exit_code == 1
        assert "Invalid --take value" in result.output

    def test_next_file_not_found(self, tmp_cwd: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["next"])
        assert result.exit_code == 1
        assert "Tasks file not found" in result.output


class TestCliStatus:
    """tasks status"""

    def test_status_empty(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "0 todo" in result.output or "0 in-progress" in result.output

    def test_status_with_tasks(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Bug fix", "--priority", "urgent", "--tag", "bug"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "in-progress"])
        runner.invoke(main, ["add", "Feature", "--priority", "high", "--tag", "feature"])
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "Bug fix" in result.output
        assert "Feature" in result.output

    def test_status_json(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "A", "--priority", "urgent"])
        runner.invoke(main, ["add", "B", "--tag", "feature"])
        result = runner.invoke(main, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["counts"]["todo"] == 2
        assert data["counts"]["in-progress"] == 0
        assert isinstance(data["top_priority"], list)
        assert isinstance(data["in-progress"], list)
        assert isinstance(data["tags"], dict)
        assert isinstance(data["inbox_entries"], int)

    def test_status_json_in_progress(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "WIP", "--priority", "urgent"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "in-progress"])
        result = runner.invoke(main, ["status", "--json"])
        data = json.loads(result.output)
        assert data["counts"]["in-progress"] == 1
        assert len(data["in-progress"]) == 1
        assert data["in-progress"][0]["id"] == "TSK-0001"

    def test_status_file_not_found(self, tmp_cwd: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 1
        assert "Tasks file not found" in result.output


class TestCliOverview:
    """tasks overview"""

    def test_overview_empty_initialized_repo(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["overview"])
        assert result.exit_code == 0
        assert "NOW" in result.output
        assert "READY" in result.output
        assert "WAITING" in result.output
        assert "PARKED" in result.output

    def test_overview_now_includes_in_progress(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Ship feature"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "in-progress"])
        result = runner.invoke(main, ["overview"])
        assert result.exit_code == 0
        assert "Ship feature" in result.output

    def test_overview_ready_includes_unblocked_todo(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Ready task", "--priority", "high"])
        result = runner.invoke(main, ["overview"])
        assert result.exit_code == 0
        assert "READY" in result.output
        assert "Ready task" in result.output

    def test_overview_waiting_includes_blocked_and_waiting_tasks(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Blocker"])
        runner.invoke(main, ["add", "Blocked todo", "--dep", "TSK-0001:blocks"])
        runner.invoke(main, ["add", "Waiting task"])
        runner.invoke(main, ["update", "TSK-0003", "--status", "waiting"])
        result = runner.invoke(main, ["overview"])
        assert result.exit_code == 0
        assert "WAITING" in result.output
        assert "Blocked todo" in result.output
        assert "Waiting task" in result.output

    def test_overview_parked_includes_postponed_and_parked(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Later maybe"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "postponed"])
        result = runner.invoke(main, ["overview"])
        assert result.exit_code == 0
        assert "PARKED" in result.output
        assert "Later maybe" in result.output

    def test_overview_json_returns_expected_sections(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "JSON task"])
        result = runner.invoke(main, ["overview", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "counts" in data
        assert "now" in data
        assert "ready" in data
        assert "waiting" in data
        assert "parked" in data

    def test_overview_file_not_found(self, tmp_cwd: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["overview"])
        assert result.exit_code == 1
        assert "Tasks file not found" in result.output

    def test_overview_places_task_in_only_one_section(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Active blocker"])
        runner.invoke(main, ["add", "Only waiting", "--dep", "TSK-0001:blocks"])
        result = runner.invoke(main, ["overview", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        section_hits = sum(
            1
            for section in ("now", "ready", "waiting", "parked", "other")
            if any(task["id"] == "TSK-0002" for task in data[section])
        )
        assert section_hits == 1
        assert any(task["id"] == "TSK-0002" for task in data["waiting"])


class TestCliHistory:
    """tasks history"""

    def test_history_empty(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["history"])
        assert result.exit_code == 0
        assert "No completed tasks." in result.output

    def test_history_json(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "First"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "Second"])
        runner.invoke(main, ["close", "TSK-0002"])
        result = runner.invoke(main, ["history", "--json"])
        assert result.exit_code == 0
        tasks = json.loads(result.output)
        assert len(tasks) == 2
        assert tasks[0]["id"] == "TSK-0002"  # newest first

    def test_history_filter_tag_json(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Bug", "--tag", "bug"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "Feature", "--tag", "feature"])
        runner.invoke(main, ["close", "TSK-0002"])
        result = runner.invoke(main, ["history", "--tag", "bug", "--json"])
        assert result.exit_code == 0
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Bug"

    def test_history_limit(self, initted: Path, runner: CliRunner) -> None:
        """--limit controls how many tasks are shown."""
        for i in range(5):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--limit", "2", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 2
        assert tasks[0]["id"] == "TSK-0005"  # newest first

    def test_history_limit_all(self, initted: Path, runner: CliRunner) -> None:
        """--limit all shows every closed task."""
        for i in range(5):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--limit", "all", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 5

    def test_history_offset(self, initted: Path, runner: CliRunner) -> None:
        """--offset skips N entries from newest."""
        for i in range(5):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--offset", "2", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 3
        assert tasks[0]["id"] == "TSK-0003"  # skipping TSK-0005 and TSK-0004

    def test_history_offset_limit(self, initted: Path, runner: CliRunner) -> None:
        """--offset and --limit combine for pagination."""
        for i in range(10):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--offset", "3", "--limit", "4", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 4
        assert tasks[0]["id"] == "TSK-0007"  # newest: 10, skip 3 → starts at 7
        assert tasks[-1]["id"] == "TSK-0004"

    def test_history_limit_more_than_available(self, initted: Path, runner: CliRunner) -> None:
        """--limit higher than available total returns all."""
        runner.invoke(main, ["add", "Only one"])
        runner.invoke(main, ["close", "TSK-0001"])
        result = runner.invoke(main, ["history", "--limit", "100", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1

    def test_history_invalid_limit(self, initted: Path, runner: CliRunner) -> None:
        """Invalid --limit value shows an error."""
        runner.invoke(main, ["add", "Test"])
        runner.invoke(main, ["close", "TSK-0001"])
        result = runner.invoke(main, ["history", "--limit", "not-a-number"])
        assert result.exit_code == 1
        assert "Invalid --limit value" in result.output

    def test_history_pagination_hint(self, initted: Path, runner: CliRunner) -> None:
        """Rich table output shows a hint when results exceed limit."""
        for i in range(40):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--limit", "30"])
        assert result.exit_code == 0
        assert "Showing 30 of 40" in result.output
        assert "Use --offset 30" in result.output

    def test_history_pagination_hint_no_hint_when_all_shown(self, initted: Path, runner: CliRunner) -> None:
        """No pagination hint when all results fit in the limit."""
        for i in range(5):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--limit", "30"])
        assert result.exit_code == 0
        assert "Showing" not in result.output  # no hint needed
        assert "Use --offset" not in result.output

    def test_history_limit(self, initted: Path, runner: CliRunner) -> None:
        """--limit controls how many tasks are shown."""
        for i in range(5):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--limit", "2", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 2
        assert tasks[0]["id"] == "TSK-0005"  # newest first

    def test_history_limit_all(self, initted: Path, runner: CliRunner) -> None:
        """--limit all shows every closed task."""
        for i in range(5):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--limit", "all", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 5

    def test_history_offset(self, initted: Path, runner: CliRunner) -> None:
        """--offset skips N entries from newest."""
        for i in range(5):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--offset", "2", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 3
        assert tasks[0]["id"] == "TSK-0003"  # skipping TSK-0005 and TSK-0004

    def test_history_offset_limit(self, initted: Path, runner: CliRunner) -> None:
        """--offset and --limit combine for pagination."""
        for i in range(10):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--offset", "3", "--limit", "4", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 4
        assert tasks[0]["id"] == "TSK-0007"  # newest: 10, skip 3 → starts at 7
        assert tasks[-1]["id"] == "TSK-0004"

    def test_history_limit_more_than_available(self, initted: Path, runner: CliRunner) -> None:
        """--limit higher than available total returns all."""
        runner.invoke(main, ["add", "Only one"])
        runner.invoke(main, ["close", "TSK-0001"])
        result = runner.invoke(main, ["history", "--limit", "100", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1

    def test_history_invalid_limit(self, initted: Path, runner: CliRunner) -> None:
        """Invalid --limit value shows an error."""
        runner.invoke(main, ["add", "Test"])
        runner.invoke(main, ["close", "TSK-0001"])
        result = runner.invoke(main, ["history", "--limit", "not-a-number"])
        assert result.exit_code == 1
        assert "Invalid --limit value" in result.output

    def test_history_pagination_hint(self, initted: Path, runner: CliRunner) -> None:
        """Rich table output shows a hint when results exceed limit."""
        for i in range(40):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--limit", "30"])
        assert result.exit_code == 0
        assert "Showing 30 of 40" in result.output
        assert "Use --offset 30" in result.output

    def test_history_pagination_hint_no_hint_when_all_shown(self, initted: Path, runner: CliRunner) -> None:
        """No pagination hint when all results fit in the limit."""
        for i in range(5):
            runner.invoke(main, ["add", f"Task {i}"])
            runner.invoke(main, ["close", f"TSK-{i+1:04d}"])
        result = runner.invoke(main, ["history", "--limit", "30"])
        assert result.exit_code == 0
        assert "Showing" not in result.output  # no hint needed
        assert "Use --offset" not in result.output


class TestCliInbox:
    """tasks inbox"""

    def test_inbox_empty(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["inbox"])
        assert result.exit_code == 0
        assert "Inbox is empty." in result.output

    def test_inbox_with_content(self, initted: Path, runner: CliRunner) -> None:
        (initted / ".agents" / "tasks" / "inbox.md").write_text("Item 1\nItem 2\n", encoding="utf-8")
        result = runner.invoke(main, ["inbox"])
        assert result.exit_code == 0
        assert "Item 1" in result.output
        assert "Item 2" in result.output
        assert "Inbox is empty." not in result.output


# ===========================================================================
# NEW FEATURE TESTS — deps, ready, blocked, priorities
# ===========================================================================


class TestDeps:
    """Dependency system — deps list replaces related scalar."""

    def test_add_with_dep(self, initted: Path, runner: CliRunner) -> None:
        """Adding a task with --dep stores it correctly."""
        runner.invoke(main, ["add", "Depends on A", "--dep", "TSK-0001:blocks"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["deps"] == [{"id": "TSK-0001", "type": "blocks"}]

    def test_add_with_related_shorthand(self, initted: Path, runner: CliRunner) -> None:
        """--related creates a relates-type dep."""
        runner.invoke(main, ["add", "Related to A", "--related", "TSK-0001"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["deps"] == [{"id": "TSK-0001", "type": "relates"}]

    def test_add_with_multiple_deps(self, initted: Path, runner: CliRunner) -> None:
        """Multiple --dep flags create multiple deps."""
        runner.invoke(main, ["add", "Multi dep", "--dep", "TSK-0001:blocks", "--dep", "TSK-0002:relates"])
        _, tasks = load_tasks_yaml()
        assert len(tasks[0]["deps"]) == 2
        assert tasks[0]["deps"] == [
            {"id": "TSK-0001", "type": "blocks"},
            {"id": "TSK-0002", "type": "relates"},
        ]

    def test_update_appends_dep(self, initted: Path, runner: CliRunner) -> None:
        """Updating with --dep appends to existing deps."""
        runner.invoke(main, ["add", "Task", "--dep", "TSK-0001:relates"])
        runner.invoke(main, ["update", "TSK-0001", "--dep", "TSK-0002:blocks"])
        _, tasks = load_tasks_yaml()
        assert len(tasks[0]["deps"]) == 2
        assert tasks[0]["deps"] == [
            {"id": "TSK-0001", "type": "relates"},
            {"id": "TSK-0002", "type": "blocks"},
        ]

    def test_update_dep_no_duplicates(self, initted: Path, runner: CliRunner) -> None:
        """Same id+type dep is not added twice."""
        runner.invoke(main, ["add", "Task", "--dep", "TSK-0001:blocks"])
        runner.invoke(main, ["update", "TSK-0002", "--dep", "TSK-0001:blocks"])
        _, tasks = load_tasks_yaml()
        assert len(tasks[0]["deps"]) == 1

    def test_show_deps_inline(self, initted: Path, runner: CliRunner) -> None:
        """tasks show displays deps with type and status."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["add", "Blocker", "--dep", "TSK-0001:blocks"])
        result = runner.invoke(main, ["show", "TSK-0002"])
        assert result.exit_code == 0
        assert "dep (blocks): TSK-0001 (todo)" in result.output
        assert "Target" in result.output

    def test_show_deps_not_found(self, initted: Path, runner: CliRunner) -> None:
        """Dep target not found shows (not found)."""
        runner.invoke(main, ["add", "Orphan", "--dep", "TSK-9999:blocks"])
        result = runner.invoke(main, ["show", "TSK-0001"])
        assert result.exit_code == 0
        assert "dep (blocks): TSK-9999 (not found)" in result.output

    def test_deps_command_outgoing(self, initted: Path, runner: CliRunner) -> None:
        """tasks deps shows outgoing dependencies."""
        runner.invoke(main, ["add", "Target A"])
        runner.invoke(main, ["add", "Depends on A", "--dep", "TSK-0001:blocks"])
        result = runner.invoke(main, ["deps", "TSK-0002"])
        assert result.exit_code == 0
        assert "DEPENDS ON" in result.output
        assert "TSK-0001" in result.output

    def test_deps_command_incoming(self, initted: Path, runner: CliRunner) -> None:
        """tasks deps shows incoming dependencies."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["add", "Depends on target", "--dep", "TSK-0001:blocks"])
        result = runner.invoke(main, ["deps", "TSK-0001"])
        assert result.exit_code == 0
        assert "DEPENDED BY" in result.output
        assert "TSK-0002" in result.output

    def test_list_filter_related_works_with_deps(self, initted: Path, runner: CliRunner) -> None:
        """--related filter matches deps by ID."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["add", "Linked", "--dep", "TSK-0001:relates"])
        result = runner.invoke(main, ["list", "--related", "TSK-0001", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "TSK-0002"

    def test_deps_command_not_found(self, initted: Path, runner: CliRunner) -> None:
        """tasks deps with nonexistent ID shows error."""
        result = runner.invoke(main, ["deps", "TSK-9999"])
        assert result.exit_code == 1
        assert "Task not found" in result.output

    def test_deps_command_on_closed(self, initted: Path, runner: CliRunner) -> None:
        """tasks deps works on closed tasks too."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["close", "TSK-0001"])
        result = runner.invoke(main, ["deps", "TSK-0001"])
        assert result.exit_code == 0

    def test_dep_without_colon_defaults_to_blocks(self, initted: Path, runner: CliRunner) -> None:
        """--dep TSK-0001 (no type) defaults to blocks type."""
        runner.invoke(main, ["add", "Default blocks", "--dep", "TSK-0001"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["deps"] == [{"id": "TSK-0001", "type": "blocks"}]

    def test_deps_error_before_init(self, tmp_cwd: Path, runner: CliRunner) -> None:
        """tasks deps shows helpful error before init."""
        result = runner.invoke(main, ["deps", "TSK-0001"])
        assert result.exit_code == 1
        assert "Tasks file not found" in result.output

    def test_old_related_auto_migrated(self, initted: Path, runner: CliRunner) -> None:
        """Legacy 'related' field in YAML is auto-converted to deps on load."""
        tasks_dir = initted / ".agents" / "tasks"
        # Manually write an old-format per-task file
        old_task = {"id": "TSK-0001", "title": "Old format", "status": "todo",
                    "created": "2026-01-01", "closed": False, "related": "TSK-0005",
                    "priority": "urgent"}
        (tasks_dir / "TSK-0001.yaml").write_text(
            yaml.safe_dump(old_task, default_flow_style=False),
            encoding="utf-8"
        )
        _, tasks = load_tasks_yaml()
        # Should have migrated
        assert "related" not in tasks[0]
        assert tasks[0]["deps"] == [{"id": "TSK-0005", "type": "relates"}]
        assert tasks[0]["priority"] == 0


class TestReady:
    """tasks ready — top-priority unblocked todos."""

    def test_ready_shows_unblocked_only(self, initted: Path, runner: CliRunner) -> None:
        """ready excludes tasks with unresolved blocks-type deps."""
        runner.invoke(main, ["add", "Independent", "--priority", "high"])
        runner.invoke(main, ["add", "Blocked task", "--dep", "TSK-0001:blocks", "--priority", "urgent"])
        result = runner.invoke(main, ["ready", "--take", "all"])
        assert result.exit_code == 0
        # TSK-0002 is blocked by TSK-0001 (todo) => excluded
        assert "TSK-0002" not in result.output
        assert "TSK-0001" in result.output

    def test_ready_includes_resolved_blocker(self, initted: Path, runner: CliRunner) -> None:
        """ready includes tasks whose blocks dep is done/closed."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "Unblocked", "--dep", "TSK-0001:blocks"])
        result = runner.invoke(main, ["ready", "--take", "all"])
        assert result.exit_code == 0
        assert "TSK-0002" in result.output

    def test_ready_does_not_exclude_relates(self, initted: Path, runner: CliRunner) -> None:
        """relates-type deps do NOT affect the ready queue."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["add", "Soft link", "--dep", "TSK-0001:relates"])
        result = runner.invoke(main, ["ready", "--take", "all"])
        assert result.exit_code == 0
        assert "TSK-0002" in result.output

    def test_ready_empty(self, initted: Path, runner: CliRunner) -> None:
        """ready shows helpful message when nothing is ready."""
        result = runner.invoke(main, ["ready"])
        assert result.exit_code == 0
        assert "No ready tasks" in result.output

    def test_ready_json(self, initted: Path, runner: CliRunner) -> None:
        """ready --json outputs valid JSON."""
        runner.invoke(main, ["add", "Ready task", "--priority", "high"])
        result = runner.invoke(main, ["ready", "--json"])
        assert result.exit_code == 0
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "TSK-0001"

    def test_ready_obeys_take(self, initted: Path, runner: CliRunner) -> None:
        """--take limits the number of results."""
        for i in range(5):
            runner.invoke(main, ["add", f"Task {i}"])
        result = runner.invoke(main, ["ready", "--take", "2", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 2

    def test_ready_filters_by_tag(self, initted: Path, runner: CliRunner) -> None:
        """ready --tag filters results."""
        runner.invoke(main, ["add", "Bug task", "--tag", "bug"])
        runner.invoke(main, ["add", "Docs task", "--tag", "docs"])
        result = runner.invoke(main, ["ready", "--take", "all", "--tag", "bug", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Bug task"

    def test_ready_invalid_take(self, initted: Path, runner: CliRunner) -> None:
        """ready --take with non-numeric value shows error."""
        result = runner.invoke(main, ["ready", "--take", "abc"])
        assert result.exit_code == 1
        assert "Invalid --take value" in result.output

    def test_ready_error_before_init(self, tmp_cwd: Path, runner: CliRunner) -> None:
        """tasks ready shows helpful error before init."""
        result = runner.invoke(main, ["ready"])
        assert result.exit_code == 1
        assert "Tasks file not found" in result.output


class TestBlocked:
    """tasks blocked — show blocked tasks with blockers."""

    def test_blocked_shows_blocked_tasks(self, initted: Path, runner: CliRunner) -> None:
        """blocked shows tasks with unresolved blocks deps."""
        runner.invoke(main, ["add", "Blocking", "--dep", "TSK-9999:blocks"])
        result = runner.invoke(main, ["blocked"])
        assert result.exit_code == 0
        assert "TSK-0001" in result.output
        assert "blocked by" in result.output.lower()

    def test_blocked_empty(self, initted: Path, runner: CliRunner) -> None:
        """blocked shows celebratory message with no blockers."""
        runner.invoke(main, ["add", "All clear"])
        result = runner.invoke(main, ["blocked"])
        assert result.exit_code == 0
        assert "No blocked tasks" in result.output

    def test_blocked_json(self, initted: Path, runner: CliRunner) -> None:
        """blocked --json outputs valid JSON with blocker info."""
        runner.invoke(main, ["add", "Blocked", "--dep", "TSK-9999:blocks"])
        result = runner.invoke(main, ["blocked", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "TSK-0001"
        assert "blockers" in data[0]

    def test_blocked_ignores_relates(self, initted: Path, runner: CliRunner) -> None:
        """Only blocks-type deps show as blocked."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["add", "Soft linked", "--dep", "TSK-0001:relates"])
        result = runner.invoke(main, ["blocked"])
        assert result.exit_code == 0
        assert "No blocked tasks" in result.output

    def test_blocked_not_shown_when_blocker_resolved(self, initted: Path, runner: CliRunner) -> None:
        """Task not shown as blocked when its blocks dep is done/closed."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "Unblocked now", "--dep", "TSK-0001:blocks"])
        result = runner.invoke(main, ["blocked"])
        assert result.exit_code == 0
        assert "No blocked tasks" in result.output

    def test_blocked_with_mixed_deps(self, initted: Path, runner: CliRunner) -> None:
        """Only blocks-type deps matter — relates don't cause blocked."""
        runner.invoke(main, ["add", "Blocker"])
        runner.invoke(main, ["add", "Mixed", "--dep", "TSK-0001:relates", "--dep", "TSK-9999:blocks"])
        result = runner.invoke(main, ["blocked", "--json"])
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "TSK-0002"

    def test_blocked_error_before_init(self, tmp_cwd: Path, runner: CliRunner) -> None:
        """tasks blocked shows helpful error before init."""
        result = runner.invoke(main, ["blocked"])
        assert result.exit_code == 1
        assert "Tasks file not found" in result.output


class TestNumericPriorities:
    """Numeric priority system — p0 to p4."""

    def test_add_with_numeric_priority(self, initted: Path, runner: CliRunner) -> None:
        """Adding with --priority 1 stores as int 1."""
        runner.invoke(main, ["add", "Numeric p1", "--priority", "1"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == 1

    def test_add_with_named_alias(self, initted: Path, runner: CliRunner) -> None:
        """Named aliases (urgent, high, etc.) map to correct ints."""
        runner.invoke(main, ["add", "Critical", "--priority", "critical"])
        runner.invoke(main, ["add", "Urgent", "--priority", "urgent"])
        runner.invoke(main, ["add", "High", "--priority", "high"])
        runner.invoke(main, ["add", "Medium", "--priority", "medium"])
        runner.invoke(main, ["add", "Low", "--priority", "low"])
        runner.invoke(main, ["add", "Backlog", "--priority", "backlog"])
        _, tasks = load_tasks_yaml()
        priorities = {t["title"]: t["priority"] for t in tasks}
        assert priorities["Critical"] == 0
        assert priorities["Urgent"] == 0
        assert priorities["High"] == 1
        assert priorities["Medium"] == 2
        assert priorities["Low"] == 3
        assert priorities["Backlog"] == 4

    def test_update_priority_numeric(self, initted: Path, runner: CliRunner) -> None:
        """Updating with --priority 3 stores int."""
        runner.invoke(main, ["add", "Task"])
        runner.invoke(main, ["update", "TSK-0001", "--priority", "3"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == 3

    def test_display_shows_p0_p4(self, initted: Path, runner: CliRunner) -> None:
        """Priority displays as p0, p1, p2, p3, p4."""
        runner.invoke(main, ["add", "P0 task", "--priority", "0"])
        runner.invoke(main, ["add", "P4 task", "--priority", "4"])
        runner.invoke(main, ["add", "No prio"])
        result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "p0" in result.output
        assert "p4" in result.output
        assert "-" in result.output or "-" in result.output  # no-prio shows as dash

    def test_priority_sort_order(self, initted: Path, runner: CliRunner) -> None:
        """Tasks are sorted by priority (0 first, 4 last)."""
        runner.invoke(main, ["add", "P4", "--priority", "4"])
        runner.invoke(main, ["add", "P2", "--priority", "2"])
        runner.invoke(main, ["add", "P0", "--priority", "0"])
        runner.invoke(main, ["add", "P3", "--priority", "3"])
        runner.invoke(main, ["add", "P1", "--priority", "1"])
        result = runner.invoke(main, ["next", "--take", "all"])
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if "P" in l]
        # The order should be P0, P1, P2, P3, P4
        priorities_in_order = [l.split()[1] for l in lines]
        assert priorities_in_order == ["p0", "p1", "p2", "p3", "p4"], priorities_in_order

    def test_list_filter_by_numeric_priority(self, initted: Path, runner: CliRunner) -> None:
        """--priority 1 filters correctly."""
        runner.invoke(main, ["add", "High task", "--priority", "1"])
        runner.invoke(main, ["add", "Low task", "--priority", "4"])
        result = runner.invoke(main, ["list", "--priority", "1", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "High task"

    def test_update_with_named_priority_alias(self, initted: Path, runner: CliRunner) -> None:
        """Update --priority with named alias maps correctly."""
        runner.invoke(main, ["add", "Task"])
        runner.invoke(main, ["update", "TSK-0001", "--priority", "urgent"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == 0

    def test_invalid_priority_silently_ignored(self, initted: Path, runner: CliRunner) -> None:
        """Invalid priority value doesn't crash — stores nothing."""
        runner.invoke(main, ["add", "Task"])
        runner.invoke(main, ["update", "TSK-0001", "--priority", "999"])
        _, tasks = load_tasks_yaml()
        assert "priority" not in tasks[0] or tasks[0]["priority"] is None

    def test_next_filter_priority_numeric(self, initted: Path, runner: CliRunner) -> None:
        """tasks next --priority 1 filters by numeric priority."""
        runner.invoke(main, ["add", "P1 task", "--priority", "1"])
        runner.invoke(main, ["add", "P4 task", "--priority", "4"])
        result = runner.invoke(main, ["next", "--take", "all", "--priority", "1"])
        assert result.exit_code == 0
        assert "P1 task" in result.output
        assert "P4 task" not in result.output


class TestSearch:
    """Search improvements — --tag-any and --in field filter."""

    def test_list_tag_any_or_logic(self, initted: Path, runner: CliRunner) -> None:
        """--tag-any matches tasks with ANY of the given tags."""
        runner.invoke(main, ["add", "Bug task", "--tag", "bug"])
        runner.invoke(main, ["add", "Feature task", "--tag", "feature"])
        runner.invoke(main, ["add", "Docs task", "--tag", "docs"])
        result = runner.invoke(main, ["list", "--tag-any", "bug", "--tag-any", "feature", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 2
        assert tasks[0]["title"] in ("Bug task", "Feature task")

    def test_list_tag_and_tag_any_combined(self, initted: Path, runner: CliRunner) -> None:
        """--tag (AND) and --tag-any (OR) can be combined."""
        runner.invoke(main, ["add", "A", "--tag", "bug", "--tag", "urgent"])
        runner.invoke(main, ["add", "B", "--tag", "bug"])
        runner.invoke(main, ["add", "C", "--tag", "feature"])
        # Both --tag bug AND (--tag-any urgent OR feature)
        result = runner.invoke(main, ["list", "--tag", "bug",
                                       "--tag-any", "urgent", "--tag-any", "feature", "--json"])
        tasks = json.loads(result.output)
        # A has bug+urgent => matches bug AND urgent
        # B has bug only => matches bug but not urgent/feature
        assert len(tasks) == 1
        assert tasks[0]["title"] == "A"

    def test_search_in_title(self, initted: Path, runner: CliRunner) -> None:
        """--in title scopes search to title field only."""
        runner.invoke(main, ["add", "Login feature"])
        runner.invoke(main, ["add", "Fix login bug"])
        result = runner.invoke(main, ["search", "login", "--in", "title", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) >= 2  # both have "login" in title

    def test_search_in_notes(self, initted: Path, runner: CliRunner) -> None:
        """--in notes scopes search to notes field only."""
        runner.invoke(main, ["add", "Task with note", "--notes", "secret info"])
        runner.invoke(main, ["add", "Other task"])
        result = runner.invoke(main, ["search", "secret", "--in", "notes", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Task with note"

    def test_search_in_id(self, initted: Path, runner: CliRunner) -> None:
        """--in id scopes search to task ID."""
        runner.invoke(main, ["add", "Some task"])
        result = runner.invoke(main, ["search", "TSK-0001", "--in", "id", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "TSK-0001"

    def test_search_in_unknown_field(self, initted: Path, runner: CliRunner) -> None:
        """--in with unknown field returns empty results."""
        runner.invoke(main, ["add", "Test task"])
        result = runner.invoke(main, ["search", "test", "--in", "nonexistent", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 0

    def test_search_in_tags_array_field(self, initted: Path, runner: CliRunner) -> None:
        """--in tags searches within the tags array."""
        runner.invoke(main, ["add", "Bug task", "--tag", "bug"])
        runner.invoke(main, ["add", "Feature", "--tag", "feature"])
        result = runner.invoke(main, ["search", "bug", "--in", "tags", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Bug task"

    def test_tag_any_case_insensitive(self, initted: Path, runner: CliRunner) -> None:
        """--tag-any is case-insensitive (tags are stored normalized)."""
        runner.invoke(main, ["add", "Bug task", "--tag", "Bug"])
        # Tags get normalized to "bug" on add
        result = runner.invoke(main, ["list", "--tag-any", "BUG", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1, f"--tag-any should be case-insensitive, got {tasks}"

    def test_tag_any_nonexistent(self, initted: Path, runner: CliRunner) -> None:
        """--tag-any with nonexistent tag returns empty."""
        runner.invoke(main, ["add", "Task A", "--tag", "bug"])
        result = runner.invoke(main, ["list", "--tag-any", "nonexistent", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 0

    def test_search_in_status(self, initted: Path, runner: CliRunner) -> None:
        """--in status searches the status field."""
        runner.invoke(main, ["add", "Active task"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "in-progress"])
        result = runner.invoke(main, ["search", "in-progress", "--in", "status", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) >= 1  # at least the one we set

    def test_search_in_priority(self, initted: Path, runner: CliRunner) -> None:
        """--in priority searches the priority field."""
        runner.invoke(main, ["add", "Urgent task", "--priority", "0"])
        result = runner.invoke(main, ["search", "0", "--in", "priority", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Urgent task"

    def test_search_error_before_init(self, tmp_cwd: Path, runner: CliRunner) -> None:
        """tasks search shows helpful error before init."""
        result = runner.invoke(main, ["search", "test"])
        assert result.exit_code == 1
        assert "Tasks file not found" in result.output


# ===========================================================================
# EDGE CASE TESTS
# ===========================================================================


class TestEdgeCases:
    """Cross-cutting edge cases."""

    def test_all_commands_error_before_init(self, tmp_cwd: Path, runner: CliRunner) -> None:
        """Every data command shows a helpful message when files are missing."""
        for cmd in [
            ["list"],
            ["show", "TSK-0001"],
            ["update", "TSK-0001"],
            ["close", "TSK-0001"],
            ["next"],
            ["status"],
            ["overview"],
            ["deps", "TSK-0001"],
            ["ready"],
            ["blocked"],
            ["search", "test"],
        ]:
            result = runner.invoke(main, cmd)
            assert result.exit_code == 1, f"{cmd} should exit 1"
            assert "Tasks file not found" in result.output or "Run 'tasks init' first" in result.output

    def test_close_workflow(self, initted: Path, runner: CliRunner) -> None:
        """End-to-end: init → add → list → update → show → close → history."""
        runner.invoke(main, ["add", "My task", "--priority", "high", "--tag", "bug"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "in-progress"])
        show = runner.invoke(main, ["show", "TSK-0001"])
        assert show.exit_code == 0
        assert "in-progress" in show.output

        closed = runner.invoke(main, ["close", "TSK-0001", "--note", "finished"])
        assert closed.exit_code == 0

        hist = runner.invoke(main, ["history", "--json"])
        assert hist.exit_code == 0
        tasks = json.loads(hist.output)
        assert len(tasks) == 1
        assert tasks[0]["closed"] is True

    def test_multiple_updates(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Test"])
        runner.invoke(main, ["update", "TSK-0001", "--tag", "a"])
        runner.invoke(main, ["update", "TSK-0001", "--tag", "b"])
        runner.invoke(main, ["update", "TSK-0001", "--tag", "c"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["tags"] == ["a", "b", "c"]

    def test_closed_header_preserved_across_multiple_closes(
        self, initted: Path, runner: CliRunner
    ) -> None:
        """Closed tasks are persisted as individual files in per-file mode."""
        tasks_dir = initted / ".agents" / "tasks"
        assert not (tasks_dir / "closed.yaml").exists()

        runner.invoke(main, ["add", "A"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "B"])
        runner.invoke(main, ["close", "TSK-0002"])

        # Verify individual closed task files exist
        t1 = tasks_dir / "TSK-0001.yaml"
        t2 = tasks_dir / "TSK-0002.yaml"
        assert t1.exists()
        assert t2.exists()
        d1 = yaml.safe_load(t1.read_text(encoding="utf-8"))
        d2 = yaml.safe_load(t2.read_text(encoding="utf-8"))
        assert d1["closed"] is True
        assert d2["closed"] is True

    def test_list_json_excludes_closed(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Keep"])
        runner.invoke(main, ["add", "Remove"])
        runner.invoke(main, ["close", "TSK-0002"])
        result = runner.invoke(main, ["list", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "TSK-0001"

    def test_status_with_inbox_count(self, initted: Path, runner: CliRunner) -> None:
        """status output mentions inbox entries count."""
        (initted / ".agents" / "tasks" / "inbox.md").write_text("a\nb\nc\n", encoding="utf-8")
        result = runner.invoke(main, ["status", "--json"])
        data = json.loads(result.output)
        assert data["inbox_entries"] == 3


# ===========================================================================
# STORAGE MIGRATION TESTS
# ===========================================================================


class TestStorageMigration:
    """Tests for storage migration from monolithic to per-file."""

    def test_detect_version_fresh_init_returns_2(self, tmp_cwd: Path) -> None:
        """A directory with no task files returns version 2 (fresh init -> per-file)."""
        tasks_dir = tmp_cwd / ".agents" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        assert detect_storage_version(tasks_dir) == 2

    def test_detect_version_monolithic_returns_1(self, tmp_cwd: Path) -> None:
        """A directory with tasks.yaml but no meta.yaml returns version 1."""
        tasks_dir = tmp_cwd / ".agents" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (tasks_dir / "tasks.yaml").write_text("meta: {counter: 0}\ntasks: []\n", encoding="utf-8")
        assert detect_storage_version(tasks_dir) == 1

    def test_detect_version_perfile_returns_2(self, tmp_cwd: Path) -> None:
        """A directory with meta.yaml returns version 2."""
        tasks_dir = tmp_cwd / ".agents" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        meta = {"version": 2, "counter": 0, "config": {}}
        (tasks_dir / "meta.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
        assert detect_storage_version(tasks_dir) == 2

    def test_migration_from_monolithic(self, tmp_cwd: Path, runner: CliRunner) -> None:
        """Migrate from monolithic to per-file preserves all tasks."""
        tasks_dir = tmp_cwd / ".agents" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)

        # Create monolithic files manually (simulate version 1 storage)
        meta_data = {"meta": {"counter": 3, "config": {"statuses": ["todo", "in-progress", "done", "blocked", "postponed", "cancelled", "review", "waiting", "parked", "deferred", "backlog", "abandoned"], "default_status": "todo", "ready_status": "todo", "active_status": "in-progress", "close_status": "done"}}, "tasks": [
            {"id": "TSK-0003", "title": "Task 2", "status": "todo", "created": "2026-05-24", "closed": False, "source": "agent"},
            {"id": "TSK-0002", "title": "Task 1", "status": "todo", "created": "2026-05-24", "closed": False, "source": "agent"},
        ]}
        closed_data = {"meta": {"total_closed": 1}, "tasks": [
            {"id": "TSK-0001", "title": "Task 0", "status": "todo", "created": "2026-05-24", "closed": True, "closed_at": "2026-05-24T00:00:00", "source": "agent"},
        ]}
        (tasks_dir / "tasks.yaml").write_text(TASKS_HEADER + yaml.safe_dump(meta_data, default_flow_style=False, sort_keys=False, allow_unicode=True), encoding="utf-8")
        (tasks_dir / "closed.yaml").write_text(CLOSED_HEADER + yaml.safe_dump(closed_data, default_flow_style=False, sort_keys=False, allow_unicode=True), encoding="utf-8")

        # Verify monolithic files exist
        assert (tasks_dir / "tasks.yaml").exists()
        assert (tasks_dir / "closed.yaml").exists()
        assert not (tasks_dir / "meta.yaml").exists()

        # Migrate
        result = migrate_to_perfile()
        assert result["migrated"] == 3
        assert result["active"] == 2
        assert result["closed"] == 1

        # Verify per-file structure
        assert (tasks_dir / "meta.yaml").exists()
        assert (tasks_dir / "TSK-0001.yaml").exists()  # closed
        assert (tasks_dir / "TSK-0002.yaml").exists()  # active
        assert (tasks_dir / "TSK-0003.yaml").exists()  # active

        # Verify backup
        assert (tasks_dir / "tasks.yaml.bak").exists()
        assert (tasks_dir / "closed.yaml.bak").exists()
        assert not (tasks_dir / "tasks.yaml").exists()  # original renamed
        assert not (tasks_dir / "closed.yaml").exists()

        # Verify data preserved
        meta = load_tasks_yaml()[0]
        assert meta.get("counter") == 3

    def test_migration_idempotent(self, initted: Path, runner: CliRunner) -> None:
        """After migration, calling migrate again reports already migrated."""
        runner.invoke(main, ["add", "Test"])
        migrate_to_perfile()
        result = migrate_to_perfile()
        assert result["migrated"] == 0
        assert "Already" in result.get("message", "")

    def test_perfile_init_task_files(self, tmp_cwd: Path) -> None:
        """init_task_files on a fresh directory creates per-file structure."""
        init_task_files()
        tasks_dir = tmp_cwd / ".agents" / "tasks"
        assert (tasks_dir / "meta.yaml").exists()
        assert not (tasks_dir / "tasks.yaml").exists()

        # Verify meta has version
        meta = yaml.safe_load((tasks_dir / "meta.yaml").read_text(encoding="utf-8"))
        assert meta.get("version") == 2
        assert "counter" in meta
        assert "config" in meta

    def test_perfile_add_and_list(self, initted: Path, runner: CliRunner) -> None:
        """In per-file mode, add creates individual task files."""
        runner.invoke(main, ["add", "Hello"])
        tasks_dir = initted / ".agents" / "tasks"
        assert (tasks_dir / "TSK-0001.yaml").exists()

        result = runner.invoke(main, ["list", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Hello"

    def test_perfile_close(self, initted: Path, runner: CliRunner) -> None:
        """In per-file mode, close just sets closed flag on the same file."""
        runner.invoke(main, ["add", "Close me"])
        runner.invoke(main, ["close", "TSK-0001"])
        tasks_dir = initted / ".agents" / "tasks"

        # Task file should still exist (not moved)
        assert (tasks_dir / "TSK-0001.yaml").exists()

        # Closed tasks should not appear in list
        result = runner.invoke(main, ["list", "--json"])
        tasks = json.loads(result.output)
        assert len(tasks) == 0

    def test_perfile_update(self, initted: Path, runner: CliRunner) -> None:
        """In per-file mode, update modifies the single task file."""
        runner.invoke(main, ["add", "Update me"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "in-progress"])

        tasks_dir = initted / ".agents" / "tasks"
        task = yaml.safe_load((tasks_dir / "TSK-0001.yaml").read_text(encoding="utf-8"))
        assert task["status"] == "in-progress"
        assert task.get("updated") is not None

    def test_storage_type_after_migration(self, initted: Path, runner: CliRunner) -> None:
        """After migration, the active storage is PerFileYamlStorage."""
        runner.invoke(main, ["add", "Test"])
        migrate_to_perfile()
        s = _ensure_storage()
        assert s.storage_type() == "perfile"
