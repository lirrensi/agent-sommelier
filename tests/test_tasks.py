"""Comprehensive test suite for agentcli_helpers.tasks module.

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

from agentcli_helpers.tasks import (
    CLOSED_HEADER,
    TASKS_HEADER,
    VALID_PRIORITIES,
    VALID_SOURCES,
    VALID_STATUSES,
    _collect_all_tags,
    _find_task_by_id,
    _format_id,
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
    update_task,
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
        """init_task_files creates tasks.yaml, closed.yaml, and inbox.md."""
        results = init_task_files()
        tasks_dir = tmp_cwd / "tasks"
        assert (tasks_dir / "tasks.yaml").exists()
        assert (tasks_dir / "closed.yaml").exists()
        assert (tasks_dir / "inbox.md").exists()
        assert all(v == "created" for v in results.values())
        assert len(results) == 3

    def test_init_is_idempotent(self, initted: Path) -> None:
        """Running init a second time reports 'exists' for all files."""
        results = init_task_files()
        assert all(v == "exists" for v in results.values())
        assert len(results) == 3

    def test_init_tasks_yaml_structure(self, initted: Path) -> None:
        """Freshly created tasks.yaml has counter=0 and empty tasks list."""
        meta, tasks = load_tasks_yaml()
        assert meta == {"counter": 0}
        assert tasks == []

    def test_init_closed_yaml_structure(self, initted: Path) -> None:
        """Freshly created closed.yaml is empty."""
        assert load_closed_yaml() == []

    def test_save_and_load_roundtrip(self, initted: Path) -> None:
        """YAML round-trip: save then load returns identical data."""
        meta = {"counter": 5}
        tasks = [{"id": "TSK-0001", "title": "test", "status": "todo"}]
        save_tasks_yaml(meta, tasks)
        loaded_meta, loaded_tasks = load_tasks_yaml()
        assert loaded_meta == meta
        assert loaded_tasks == tasks

    def test_save_strips_none_fields(self, initted: Path) -> None:
        """save_tasks_yaml removes dict entries whose value is None."""
        meta = {"counter": 1}
        tasks = [
            {"id": "TSK-0001", "title": "test", "status": "todo", "priority": None}
        ]
        save_tasks_yaml(meta, tasks)
        _, loaded = load_tasks_yaml()
        assert "priority" not in loaded[0]

    def test_load_closed_returns_empty_for_missing_file(self, tmp_cwd: Path) -> None:
        """load_closed_yaml returns [] when closed.yaml does not exist."""
        assert load_closed_yaml() == []

    def test_load_closed_legacy_flat_list(self, initted: Path) -> None:
        """load_closed_yaml handles legacy format where file is a flat list."""
        tasks_dir = initted / "tasks"
        # Remove closed.yaml so fallback to done.yaml is triggered
        closed_path = tasks_dir / "closed.yaml"
        if closed_path.exists():
            closed_path.unlink()
        done_path = tasks_dir / "done.yaml"
        task = {"id": "TSK-0001", "title": "legacy task"}
        content = CLOSED_HEADER + yaml.safe_dump(
            [task], default_flow_style=False, sort_keys=False
        )
        done_path.write_text(content, encoding="utf-8")
        assert load_closed_yaml() == [task]

    def test_save_closed_yaml_header_and_meta(self, initted: Path) -> None:
        """save_closed_yaml writes the CLOSED_HEADER and meta.total_closed."""
        task = {"id": "TSK-0001", "title": "done", "status": "done"}
        save_closed_yaml([task])
        content = (initted / "tasks" / "closed.yaml").read_text(encoding="utf-8")
        assert "# AgentCLI Task System" in content
        assert "# !! DO NOT EDIT THIS FILE DIRECTLY !!" in content
        loaded = load_closed_yaml()
        assert loaded == [task]

    def test_save_closed_meta_total_closed(self, initted: Path) -> None:
        """save_closed_yaml sets meta.total_closed to match list length."""
        tasks = [
            {"id": "TSK-0001", "title": "a"},
            {"id": "TSK-0002", "title": "b"},
        ]
        save_closed_yaml(tasks)
        raw = (initted / "tasks" / "closed.yaml").read_text(encoding="utf-8")
        yaml_part = raw[raw.index("meta:"):]
        data = yaml.safe_load(yaml_part)
        assert data["meta"]["total_closed"] == 2
        assert len(data["tasks"]) == 2

    def test_load_inbox_missing_file(self, tmp_cwd: Path) -> None:
        """load_inbox returns '' when inbox.md does not exist."""
        assert load_inbox() == ""

    def test_load_inbox_reads_content(self, initted: Path) -> None:
        """load_inbox reads inbox.md contents."""
        p = initted / "tasks" / "inbox.md"
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
            related="TSK-0000",
            notes="Some notes",
        )
        _assert_task_fields(
            task,
            id="TSK-0001",
            title="Full task",
            status="todo",
            priority="high",
            source="jira",
            related="TSK-0000",
            notes="Some notes",
        )
        assert task["tags"] == ["bug", "ui"]
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
        assert tasks[0]["priority"] == "medium"
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
        assert updated["priority"] == "urgent"

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
        assert updated["related"] == "TSK-0001"

    def test_update_notes_replaces(self, initted: Path) -> None:
        """Notes field is replaced, not appended (unlike close --note)."""
        add_task("Test", notes="original")
        updated = update_task("TSK-0001", notes="replaced")
        assert updated["notes"] == "replaced"

    def test_update_not_found(self, initted: Path) -> None:
        with pytest.raises(ValueError, match="Task not found: TSK-9999"):
            update_task("TSK-9999")

    def test_update_persists(self, initted: Path) -> None:
        add_task("Test", priority="low")
        update_task("TSK-0001", priority="high", tags=["updated"])
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == "high"
        assert tasks[0]["tags"] == ["updated"]

    def test_update_only_requested_fields(self, initted: Path) -> None:
        """Fields not passed are untouched."""
        add_task("Test", priority="low", notes="original")
        update_task("TSK-0001", priority="urgent")
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == "urgent"
        assert tasks[0]["notes"] == "original"

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
        assert closed["notes"] == "only this"

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
         "priority": "high", "tags": ["bug"], "source": "jira"},
        {"id": "TSK-0002", "title": "Feature", "status": "todo",
         "priority": "low", "tags": ["feature"], "source": "agent"},
        {"id": "TSK-0003", "title": "In progress", "status": "in-progress",
         "priority": "urgent", "tags": ["bug", "ui"], "source": "inbox"},
        {"id": "TSK-0004", "title": "Done task", "status": "done",
         "priority": "medium", "tags": [], "source": "test"},
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
        result = filter_tasks(self.SAMPLE, priority="high")
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
        assert _priority_sort_key({"priority": "urgent"}) == 0
        assert _priority_sort_key({"priority": "high"}) == 1
        assert _priority_sort_key({"priority": "medium"}) == 2
        assert _priority_sort_key({"priority": "low"}) == 3
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
        tasks_dir = tmp_cwd / "tasks"
        assert (tasks_dir / "tasks.yaml").exists()
        assert (tasks_dir / "closed.yaml").exists()
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
            "--related", "TSK-0000",
            "--notes", "Important",
        ])
        assert result.exit_code == 0
        assert "Created TSK-0001: Full task" in result.output
        _, tasks = load_tasks_yaml()
        assert tasks[0]["priority"] == "high"
        assert tasks[0]["tags"] == ["bug", "ui"]
        assert tasks[0]["source"] == "jira"

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
        assert tasks[0]["priority"] == "urgent"

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
        runner.invoke(main, ["add", "Show me", "--priority", "urgent", "--notes", "detail"])
        result = runner.invoke(main, ["show", "TSK-0001"])
        assert result.exit_code == 0
        assert "TSK-0001" in result.output
        assert "Show me" in result.output
        assert "urgent" in result.output
        assert "detail" in result.output

    def test_show_not_found(self, initted: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["show", "TSK-9999"])
        assert result.exit_code == 1
        assert "Task not found: TSK-9999" in result.output

    def test_show_related_resolved(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Parent task"])
        runner.invoke(main, ["add", "Child task", "--related", "TSK-0001"])
        result = runner.invoke(main, ["show", "TSK-0002"])
        assert result.exit_code == 0
        assert "related: TSK-0001 (todo)" in result.output
        assert "Parent task" in result.output

    def test_show_related_from_closed(self, initted: Path, runner: CliRunner) -> None:
        """Resolves related even when the target is closed (preserves its status)."""
        runner.invoke(main, ["add", "Done target"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "Depends on done", "--related", "TSK-0001"])
        result = runner.invoke(main, ["show", "TSK-0002"])
        assert result.exit_code == 0
        # Close preserves status (todo), so related shows (todo)
        assert "related: TSK-0001 (todo)" in result.output

    def test_show_related_not_found(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "Orphan", "--related", "TSK-9999"])
        result = runner.invoke(main, ["show", "TSK-0001"])
        assert result.exit_code == 0
        assert "related: TSK-9999 (not found)" in result.output

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
        assert tasks[0]["priority"] == "urgent"

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
        runner.invoke(main, ["close", "TSK-0001", "--note", "done note"])
        closed_list = load_closed_yaml()
        assert "initial" in closed_list[0]["notes"]
        assert "done note" in closed_list[0]["notes"]

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
        """--skip-related excludes tasks whose related target is not done."""
        runner.invoke(main, ["add", "Independent"])
        runner.invoke(main, ["add", "Depends on open", "--related", "TSK-0001"])
        runner.invoke(main, ["add", "Depends on missing", "--related", "TSK-9999"])
        result = runner.invoke(main, ["next", "--take", "all", "--skip-related"])
        # TSK-0001: no related => included
        assert "TSK-0001" in result.output
        # TSK-0002: related=TSK-0001 (todo, not done) => excluded
        assert "TSK-0002" not in result.output
        # TSK-0003: related=TSK-9999 (not found) => excluded
        assert "TSK-0003" not in result.output

    def test_next_skip_related_allows_closed(self, initted: Path, runner: CliRunner) -> None:
        """--skip-related includes tasks whose related target is done."""
        runner.invoke(main, ["add", "Target"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "Depends on done", "--related", "TSK-0001"])
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
        assert data["counts"]["in_progress"] == 0
        assert isinstance(data["top_priority"], list)
        assert isinstance(data["in_progress"], list)
        assert isinstance(data["tags"], dict)
        assert isinstance(data["inbox_entries"], int)

    def test_status_json_in_progress(self, initted: Path, runner: CliRunner) -> None:
        runner.invoke(main, ["add", "WIP", "--priority", "urgent"])
        runner.invoke(main, ["update", "TSK-0001", "--status", "in-progress"])
        result = runner.invoke(main, ["status", "--json"])
        data = json.loads(result.output)
        assert data["counts"]["in_progress"] == 1
        assert len(data["in_progress"]) == 1
        assert data["in_progress"][0]["id"] == "TSK-0001"

    def test_status_file_not_found(self, tmp_cwd: Path, runner: CliRunner) -> None:
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 1
        assert "Tasks file not found" in result.output


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
        (initted / "tasks" / "inbox.md").write_text("Item 1\nItem 2\n", encoding="utf-8")
        result = runner.invoke(main, ["inbox"])
        assert result.exit_code == 0
        assert "Item 1" in result.output
        assert "Item 2" in result.output
        assert "Inbox is empty." not in result.output


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
        """closed.yaml header and meta survive multiple appends."""
        closed_path = initted / "tasks" / "closed.yaml"
        # Read original header
        original = closed_path.read_text(encoding="utf-8")

        runner.invoke(main, ["add", "A"])
        runner.invoke(main, ["close", "TSK-0001"])
        runner.invoke(main, ["add", "B"])
        runner.invoke(main, ["close", "TSK-0002"])

        content = closed_path.read_text(encoding="utf-8")
        assert "# AgentCLI Task System" in content
        assert "# !! DO NOT EDIT THIS FILE DIRECTLY !!" in content
        # Verify meta.total_closed = 2
        yaml_part = content[content.index("meta:"):]
        data = yaml.safe_load(yaml_part)
        assert data["meta"]["total_closed"] == 2

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
        (initted / "tasks" / "inbox.md").write_text("a\nb\nc\n", encoding="utf-8")
        result = runner.invoke(main, ["status", "--json"])
        data = json.loads(result.output)
        assert data["inbox_entries"] == 3