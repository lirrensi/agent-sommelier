# FILE: src/agent_sommelier/tasks/core.py
# PURPOSE: Store and mutate repo-local task YAML state plus task/business logic.
# OWNS: YAML storage, migrations, task CRUD, filtering, search, dependency resolution, and queue math.
# EXPORTS: load/save helpers, add/update/close operations, overview/query helpers, filters, search helpers, constants.
# DOCS: README.md, docs/arch.md, skills/task-system/SKILL.md

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .storage import (
    CLOSED_FILE_NAME,
    CLOSED_HEADER,
    STORAGE_VERSION,
    TASKS_DIR_NAME,
    TASKS_FILE_NAME,
    TASKS_HEADER,
    TaskStorage,
    MonolithicYamlStorage,
    PerFileYamlStorage,
    detect_storage_version,
)

# ---------------------------------------------------------------------------
# Module-level storage instance (lazy-initialized)
# ---------------------------------------------------------------------------

_storage: TaskStorage | None = None
_storage_cwd: str = ""


def _resolve_tasks_dir() -> Path:
    return Path.cwd() / TASKS_DIR_NAME


def _ensure_storage() -> TaskStorage:
    global _storage, _storage_cwd
    cwd = Path.cwd().as_posix()
    if _storage is not None and _storage_cwd != cwd:
        _storage = None
    if _storage is None:
        _storage_cwd = cwd
        tasks_dir = _resolve_tasks_dir()
        version = detect_storage_version(tasks_dir)
        if version == 1:
            _storage = MonolithicYamlStorage(tasks_dir)
        else:
            _storage = PerFileYamlStorage(tasks_dir)
    return _storage


def set_storage(storage: TaskStorage) -> None:
    """Replace the current storage backend (used by migration)."""
    global _storage, _storage_cwd
    _storage = storage
    _storage_cwd = Path.cwd().as_posix()


def migrate_to_perfile() -> dict[str, Any]:
    """Migrate from monolithic YAML to per-task file storage.

    Reads all data from the current storage (must be MonolithicYamlStorage),
    writes it as per-task files, switches the active backend, and backs up
    the old monolithic files.

    Returns a dict with migration stats.
    """
    s = _ensure_storage()
    if not isinstance(s, MonolithicYamlStorage):
        return {"migrated": 0, "message": f"Already using {s.storage_type()} storage"}

    tasks_dir = _resolve_tasks_dir()

    # Read all data from old backend
    meta = s.load_meta()
    all_tasks = s.load_all_tasks()

    # Ensure config is present
    if "config" not in meta or not isinstance(meta.get("config"), dict):
        meta["config"] = dict(DEFAULT_TASK_CONFIG)

    active_count = sum(1 for t in all_tasks if not t.get("closed", False))
    closed_count = len(all_tasks) - active_count

    # Create new backend
    new_storage = PerFileYamlStorage(tasks_dir)
    new_storage.init_storage(meta)

    # Write each task
    for task in all_tasks:
        new_storage.save_task(task)

    # Backup old monolithic files
    s.deinit_storage()

    # Switch to new backend
    set_storage(new_storage)

    return {
        "migrated": len(all_tasks),
        "active": active_count,
        "closed": closed_count,
        "from": "monolithic",
        "to": "perfile",
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INBOX_FILE_NAME = "inbox.md"

DEFAULT_TASK_CONFIG: dict[str, Any] = {
    "statuses": ["todo", "in-progress", "done", "blocked", "postponed", "cancelled",
                 "review", "waiting", "parked", "deferred", "backlog", "abandoned"],
    "default_status": "todo",
    "ready_status": "todo",
    "active_status": "in-progress",
    "close_status": "done",
}


def _ensure_config(meta: dict[str, Any]) -> dict[str, Any]:
    """Return task config from meta, injecting defaults for any missing keys."""
    cfg = meta.get("config", {})
    if not isinstance(cfg, dict):
        cfg = {}
    merged = dict(DEFAULT_TASK_CONFIG)
    merged.update(cfg)
    return merged


VALID_PRIORITIES = {0, 1, 2, 3, 4}
VALID_SOURCES = {"inbox", "audit", "test", "jira", "agent", "idea"}
VALID_DEP_TYPES = {"blocks", "parent", "child", "discovered", "relates"}

PRIORITY_ORDER = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
PRIORITY_LABELS = {0: "p0", 1: "p1", 2: "p2", 3: "p3", 4: "p4"}
_NAMED_PRIORITY_MAP = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
_PRIORITY_ALIASES = {
    "critical": 0, "urgent": 0, "high": 1, "medium": 2, "low": 3, "backlog": 4,
    "p0": 0, "p1": 1, "p2": 2, "p3": 3, "p4": 4,
}

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _tasks_file() -> Path:
    return _resolve_tasks_dir() / TASKS_FILE_NAME


def _inbox_file() -> Path:
    return _resolve_tasks_dir() / INBOX_FILE_NAME


# ---------------------------------------------------------------------------
# YAML file I/O
# ---------------------------------------------------------------------------


def _migrate_task(task: dict[str, Any]) -> dict[str, Any]:
    return _ensure_deps_normalized(task)


def _migrate_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for t in tasks:
        _migrate_task(t)
    return tasks


def load_tasks_yaml() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _require_tasks_dir()
    s = _ensure_storage()
    try:
        meta = s.load_meta()
    except FileNotFoundError:
        _require_tasks_dir()
    if "config" not in meta or not isinstance(meta.get("config"), dict):
        meta["config"] = dict(DEFAULT_TASK_CONFIG)
    all_tasks = s.load_all_tasks()
    _migrate_tasks(all_tasks)
    active = [t for t in all_tasks if not t.get("closed", False)]
    # Preserve newest-first ordering for backward compat
    active.sort(key=lambda t: t.get("id", ""), reverse=True)
    return meta, active


def save_tasks_yaml(meta: dict[str, Any], tasks: list[dict[str, Any]]) -> None:
    s = _ensure_storage()
    s.save_meta(meta)
    for task in _strip_none_fields_from_list(tasks):
        s.save_task(task)


def load_closed_yaml() -> list[dict[str, Any]]:
    tasks_dir = _resolve_tasks_dir()
    if not tasks_dir.exists():
        return []
    s = _ensure_storage()
    all_tasks = s.load_all_tasks()
    _migrate_tasks(all_tasks)
    return [t for t in all_tasks if t.get("closed", False)]


def save_closed_yaml(closed_list: list[dict[str, Any]]) -> None:
    s = _ensure_storage()
    for task in _strip_none_fields_from_list(closed_list):
        s.save_task(task)


def load_inbox() -> str:
    path = _inbox_file()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_none_fields(task: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for k, v in task.items():
        if v is None or v == "":
            continue
        if isinstance(v, (list, dict)) and not v:
            continue
        if k in ("related",):
            continue
        result[k] = v
    return result


def _strip_none_fields_from_list(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_strip_none_fields(t) for t in tasks]


def _now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now().isoformat()


def _format_id(counter: int) -> str:
    return f"TSK-{counter:04d}"


def _normalize_priority(priority: Any) -> int | None:
    if priority is None:
        return None
    if isinstance(priority, int):
        return priority if priority in PRIORITY_ORDER else None
    if isinstance(priority, str):
        mapped = _PRIORITY_ALIASES.get(priority.lower())
        if mapped is not None:
            return mapped
        try:
            val = int(priority)
            return val if val in PRIORITY_ORDER else None
        except (ValueError, TypeError):
            return None
    return None


def _format_priority(priority: Any) -> str:
    norm = _normalize_priority(priority)
    if norm is None:
        return "-"
    return PRIORITY_LABELS.get(norm, str(norm))


def _priority_sort_key(task: dict[str, Any]) -> int:
    p = _normalize_priority(task.get("priority"))
    return p if p is not None else 99


def _ensure_deps_normalized(task: dict[str, Any]) -> dict[str, Any]:
    old_p = task.get("priority")
    if old_p is not None and isinstance(old_p, str):
        new_p = _normalize_priority(old_p)
        if new_p is not None:
            task["priority"] = new_p

    related = task.get("related")
    if related and not task.get("deps"):
        _ensure_deps_field(task)
        task["deps"].append({"id": str(related), "type": "relates"})
    task.pop("related", None)
    deps = task.get("deps")
    if deps is not None and not deps:
        task.pop("deps", None)
    return task


def _ensure_deps_field(task: dict[str, Any]) -> list[dict[str, str]]:
    if "deps" not in task or task["deps"] is None:
        task["deps"] = []
    elif not isinstance(task["deps"], list):
        task["deps"] = []
    return task["deps"]


def _get_dep_ids(task: dict[str, Any], dep_type: str | None = None) -> list[str]:
    deps = task.get("deps") or []
    if not isinstance(deps, list):
        return []
    if dep_type:
        return [d["id"] for d in deps if isinstance(d, dict) and d.get("type") == dep_type]
    return [d["id"] for d in deps if isinstance(d, dict)]


def _find_task_by_id(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
    for t in tasks:
        if t.get("id") == task_id:
            return t
    return None


def _resolve_related(task_id: str, tasks_yaml: list[dict[str, Any]], closed_yaml: list[dict[str, Any]]) -> dict[str, Any] | None:
    found = _find_task_by_id(tasks_yaml, task_id)
    if found:
        return found
    return _find_task_by_id(closed_yaml, task_id)


def _get_blockers(task: dict[str, Any], tasks_list: list[dict[str, Any]], closed_list: list[dict[str, Any]], close_status: str = "done") -> list[dict[str, Any]]:
    block_ids = _get_dep_ids(task, dep_type="blocks")
    blockers: list[dict[str, Any]] = []
    for bid in block_ids:
        target = _resolve_related(bid, tasks_list, closed_list)
        if target is None or (target.get("status") != close_status and not target.get("closed", False)):
            blockers.append({"id": bid, "task": target})
    return blockers


def _is_task_blocked(task: dict[str, Any], tasks_list: list[dict[str, Any]], closed_list: list[dict[str, Any]], close_status: str = "done") -> bool:
    return len(_get_blockers(task, tasks_list, closed_list, close_status=close_status)) > 0


def _collect_all_tags(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in tasks:
        for tag in t.get("tags", []) or []:
            counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    return [str(value)]


def _normalize_notes(notes: Any) -> list[str]:
    return _normalize_text_list(notes)


def _normalize_evidence(evidence: Any) -> list[str]:
    return _normalize_text_list(evidence)


def _append_text_field(task: dict[str, Any], field: str, value: str | None, replace: bool = False) -> None:
    if value is None:
        return
    existing = _normalize_text_list(task.get(field))
    if replace or not existing:
        task[field] = [value]
    else:
        existing.append(value)
        task[field] = existing


def _inbox_line_count() -> int:
    content = load_inbox()
    return len([line for line in content.splitlines() if line.strip()])


def _overview_recency_value(task: dict[str, Any]) -> str:
    return str(task.get("updated") or task.get("created") or "")


def _sort_overview_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = [dict(task) for task in tasks]
    ordered.sort(key=_overview_recency_value, reverse=True)
    ordered.sort(key=_priority_sort_key)
    return ordered


def _overview_blocker_hint(task: dict[str, Any], tasks_list: list[dict[str, Any]], closed_list: list[dict[str, Any]], close_status: str = "done") -> str | None:
    blockers = _get_blockers(task, tasks_list, closed_list, close_status=close_status)
    if not blockers:
        return None
    blocker_ids = [str(blocker.get("id") or "?") for blocker in blockers]
    if len(blocker_ids) == 1:
        return f"blocked by {blocker_ids[0]}"
    preview = ", ".join(blocker_ids[:2])
    remainder = len(blocker_ids) - 2
    suffix = f" +{remainder}" if remainder > 0 else ""
    return f"blocked by {preview}{suffix}"


def _overview_task_hint(task: dict[str, Any], tasks_list: list[dict[str, Any]], closed_list: list[dict[str, Any]], close_status: str = "done") -> str:
    blocker_hint = _overview_blocker_hint(task, tasks_list, closed_list, close_status=close_status)
    if blocker_hint:
        return blocker_hint
    tags = task.get("tags") or []
    if tags:
        return f"tags: {', '.join(str(tag) for tag in tags)}"
    source = task.get("source")
    if source:
        return f"source: {source}"
    return "-"


def build_overview_data(tasks: list[dict[str, Any]], closed_list: list[dict[str, Any]], ready_status: str = "todo", close_status: str = "done") -> dict[str, Any]:
    active_tasks = [task for task in tasks if not task.get("closed", False)]
    sections: dict[str, list[dict[str, Any]]] = {
        "now": [],
        "ready": [],
        "waiting": [],
        "parked": [],
        "other": [],
    }

    for task in active_tasks:
        task_view = dict(task)
        task_view["hint"] = _overview_task_hint(task, active_tasks, closed_list, close_status=close_status)
        is_claimed = bool(task.get("claimed"))
        is_blocked = _is_task_blocked(task, active_tasks, closed_list, close_status=close_status)
        if is_claimed:
            sections["now"].append(task_view)
        elif is_blocked:
            sections["waiting"].append(task_view)
        elif task.get("status") == ready_status:
            sections["ready"].append(task_view)
        else:
            sections["parked"].append(task_view)

    overview = {name: _sort_overview_tasks(items) for name, items in sections.items()}
    overview["counts"] = {
        "active": len(active_tasks),
        "now": len(overview["now"]),
        "ready": len(overview["ready"]),
        "waiting": len(overview["waiting"]),
        "parked": len(overview["parked"]),
        "other": len(overview["other"]),
    }
    overview["inbox_entries"] = _inbox_line_count()
    return overview


# ---------------------------------------------------------------------------
# Data operations
# ---------------------------------------------------------------------------


def init_task_files() -> dict[str, str]:
    tasks_dir = _resolve_tasks_dir()
    results: dict[str, str] = {}
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # inbox
    inbox = _inbox_file()
    if not inbox.exists():
        inbox.write_text("", encoding="utf-8")
        results[str(inbox)] = "created"
    else:
        results[str(inbox)] = "exists"

    version = detect_storage_version(tasks_dir)

    if version == 1:
        # Validate monolithic
        tasks_path = _tasks_file()
        try:
            raw = yaml.safe_load(tasks_path.read_text(encoding="utf-8"))
            if raw is None:
                raise ValueError("empty")
            results["active"] = "tasks.yaml (exists)"
        except Exception:
            results["active"] = "tasks.yaml (corrupt)"
            return results
        # Ensure closed.yaml
        closed_path = tasks_dir / CLOSED_FILE_NAME
        if not closed_path.exists():
            old_done = tasks_dir / "done.yaml"
            if old_done.exists():
                old_done.rename(closed_path)
                results["closed"] = "migrated from done.yaml"
            else:
                data = {"meta": {"total_closed": 0}, "tasks": []}
                content = CLOSED_HEADER + yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
                closed_path.write_text(content, encoding="utf-8")
                results["closed"] = "closed.yaml (created)"
        else:
            results["closed"] = "closed.yaml (exists)"
    else:
        # Per-file or fresh
        s = _ensure_storage()
        meta_path = tasks_dir / "meta.yaml"
        if not meta_path.exists():
            s.init_storage({"counter": 0, "config": dict(DEFAULT_TASK_CONFIG)})
            results["storage"] = "per-file (created)"
        else:
            try:
                s.load_meta()
                results["storage"] = "per-file (exists)"
            except Exception:
                results["storage"] = "corrupt"

    return results


def next_counter_and_id() -> tuple[int, str]:
    _require_tasks_dir()
    s = _ensure_storage()
    meta = s.load_meta()
    counter = meta.get("counter", 0)
    if not isinstance(counter, int):
        counter = 0
    new_counter = counter + 1
    meta["counter"] = new_counter
    s.save_meta(meta)
    return new_counter, _format_id(new_counter)


def _require_tasks_dir() -> None:
    """Raise FileNotFoundError if the tasks directory does not exist."""
    tasks_dir = _resolve_tasks_dir()
    if not tasks_dir.exists():
        raise FileNotFoundError(
            f"Tasks file not found: {tasks_dir}\nRun 'tasks init' first."
        )


def add_task(title: str, priority: int | str | None = None, tags: list[str] | None = None,
             source: str | None = None, claimed: str | None = None,
             created_by: str | None = None,
             deps: list[dict[str, str]] | None = None,
             related: str | None = None, notes: str | None = None,
             evidence: str | None = None) -> dict[str, Any]:
    _require_tasks_dir()
    s = _ensure_storage()
    meta = s.load_meta()
    counter = meta.get("counter", 0)
    if not isinstance(counter, int):
        counter = 0
    new_counter = counter + 1
    meta["counter"] = new_counter
    task_id = _format_id(new_counter)

    task: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "status": _ensure_config(meta).get("default_status", "todo"),
        "created": _now_date(),
        "closed": False,
    }
    if priority is not None:
        norm_p = _normalize_priority(priority)
        if norm_p is not None:
            task["priority"] = norm_p
    if tags:
        task["tags"] = [t.lower().strip().replace(" ", "-") for t in tags]
    if source:
        task["source"] = source
    if claimed:
        task["claimed"] = claimed
    if created_by:
        task["createdBy"] = created_by
    if deps:
        task["deps"] = deps
    elif related:
        task["deps"] = [{"id": related, "type": "relates"}]
    if notes:
        _append_text_field(task, "notes", notes)
    if evidence:
        _append_text_field(task, "evidence", evidence)

    s.save_meta(meta)
    s.save_task(task)
    return task


def update_task(task_id: str, status: str | None = None, priority: int | str | None = None,
                tags: list[str] | None = None, claimed: str | None = None,
                created_by: str | None = None,
                deps: list[dict[str, str]] | None = None,
                related: str | None = None, notes: str | None = None, replace_notes: bool = False,
                evidence: str | None = None, replace_evidence: bool = False,
                closed: bool | None = None) -> dict[str, Any]:
    _require_tasks_dir()
    s = _ensure_storage()
    task = s.get_task(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")

    if status is not None:
        meta = s.load_meta()
        config_status = _ensure_config(meta)
        if status not in config_status["statuses"]:
            raise ValueError(f"Invalid status '{status}'. Valid statuses: {', '.join(config_status['statuses'])}")
        task["status"] = status
    if priority is not None:
        norm_p = _normalize_priority(priority)
        if norm_p is not None:
            task["priority"] = norm_p
    if tags is not None:
        existing = task.get("tags", []) or []
        new_tags = [t.lower().strip().replace(" ", "-") for t in tags]
        for tag in new_tags:
            if tag not in existing:
                existing.append(tag)
        task["tags"] = existing
    if claimed is not None:
        if claimed:
            task["claimed"] = claimed
        else:
            task.pop("claimed", None)
    if created_by is not None:
        if created_by:
            task["createdBy"] = created_by
        else:
            task.pop("createdBy", None)
    if deps:
        existing_deps = _ensure_deps_field(task)
        for dep in deps:
            dep_id = dep.get("id", "")
            dep_type = dep.get("type", "relates")
            if not any(d.get("id") == dep_id and d.get("type") == dep_type for d in existing_deps):
                existing_deps.append({"id": dep_id, "type": dep_type})
    elif related is not None:
        existing_deps = _ensure_deps_field(task)
        if not any(d.get("id") == related and d.get("type") == "relates" for d in existing_deps):
            existing_deps.append({"id": related, "type": "relates"})
    if notes is not None:
        _append_text_field(task, "notes", notes, replace=replace_notes)
    if evidence is not None:
        _append_text_field(task, "evidence", evidence, replace=replace_evidence)

    if closed is True:
        task["closed"] = True
        task["closed_at"] = _now_iso()
    elif closed is False:
        task["closed"] = False

    task["updated"] = _now_iso()

    s.save_task(task)

    return task


def close_task(task_id: str, note: str | None = None, evidence: str | None = None) -> dict[str, Any]:
    _require_tasks_dir()
    s = _ensure_storage()
    task = s.get_task(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")

    if task.get("closed", False):
        raise ValueError(f"Task already closed: {task_id}")

    task["closed"] = True
    task["closed_at"] = _now_iso()
    task["updated"] = _now_iso()
    if note:
        _append_text_field(task, "notes", note)
    if evidence:
        _append_text_field(task, "evidence", evidence)

    s.save_task(task)
    return task


def _task_has_dep_id(task: dict[str, Any], dep_id: str) -> bool:
    for dep in (task.get("deps") or []):
        if isinstance(dep, dict) and dep.get("id") == dep_id:
            return True
    if task.get("related") == dep_id:
        return True
    return False


def filter_tasks(tasks: list[dict[str, Any]], status: str | None = None, tags: list[str] | None = None,
                 tag: str | None = None, tags_any: list[str] | None = None,
                 priority: int | str | None = None, source: str | None = None,
                 related: str | None = None) -> list[dict[str, Any]]:
    result = tasks
    if status:
        result = [t for t in result if t.get("status") == status]
    if priority is not None:
        norm_p = _normalize_priority(priority)
        if norm_p is not None:
            result = [t for t in result if _normalize_priority(t.get("priority")) == norm_p]
    if source:
        result = [t for t in result if t.get("source") == source]
    if tag:
        tags = (tags or []) + [tag]
    if tags:
        for tag in tags:
            result = [t for t in result if tag in (t.get("tags") or [])]
    if tags_any:
        normalized_any = [t.lower().strip().replace(" ", "-") for t in tags_any]
        result = [t for t in result if any(tg in (t.get("tags") or []) for tg in normalized_any)]
    if related:
        result = [t for t in result if _task_has_dep_id(t, related)]
    return result


def _task_text(task: dict[str, Any]) -> str:
    parts: list[str] = [
        task.get("id", ""),
        task.get("title", ""),
        task.get("status", ""),
        str(task.get("priority", "")),
        task.get("source", ""),
        task.get("claimed", ""),
        task.get("createdBy", ""),
        " ".join(task.get("tags", []) or []),
    ]
    for dep in (task.get("deps") or []):
        if isinstance(dep, dict):
            parts.append(dep.get("id", ""))
            parts.append(dep.get("type", ""))
    related = task.get("related")
    if related:
        parts.append(str(related))
    notes = task.get("notes")
    if notes:
        if isinstance(notes, list):
            parts.extend(str(n) for n in notes)
        else:
            parts.append(str(notes))
    evidence = task.get("evidence")
    if evidence:
        if isinstance(evidence, list):
            parts.extend(str(e) for e in evidence)
        else:
            parts.append(str(evidence))
    return " ".join(parts).lower()


def search_tasks(tasks: list[dict], text: str) -> list[dict]:
    needle = text.lower()
    return [t for t in tasks if needle in _task_text(t)]
