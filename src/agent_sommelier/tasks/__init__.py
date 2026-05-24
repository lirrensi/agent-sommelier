# FILE: src/agent_sommelier/tasks/__init__.py
# PURPOSE: Preserve the public task-system package API while delegating storage, rendering, and CLI behavior to smaller modules.
# OWNS: Backward-compatible re-exports for task data helpers and the CLI entry point.
# EXPORTS: main (Click entry point), core task helpers/constants used by tests and callers.
# DOCS: README.md, docs/arch.md, skills/task-system/SKILL.md

"""Task system package for Agent Sommelier."""

from __future__ import annotations

from .core import (  # noqa: F401
    CLOSED_FILE_NAME,
    CLOSED_HEADER,
    INBOX_FILE_NAME,
    TASKS_DIR_NAME,
    TASKS_FILE_NAME,
    TASKS_HEADER,
    VALID_DEP_TYPES,
    VALID_PRIORITIES,
    VALID_SOURCES,
    _append_text_field,
    _collect_all_tags,
    _ensure_config,
    _ensure_deps_field,
    _ensure_deps_normalized,
    _find_task_by_id,
    _format_id,
    _get_blockers,
    _get_dep_ids,
    _inbox_line_count,
    _is_task_blocked,
    _migrate_task,
    _migrate_tasks,
    _now_date,
    _now_iso,
    _normalize_evidence,
    _normalize_notes,
    _normalize_priority,
    _normalize_text_list,
    _priority_sort_key,
    _resolve_related,
    _resolve_tasks_dir,
    _strip_none_fields,
    _strip_none_fields_from_list,
    _task_has_dep_id,
    _task_text,
    add_task,
    build_overview_data,
    close_task,
    filter_tasks,
    init_task_files,
    load_closed_yaml,
    load_inbox,
    load_tasks_yaml,
    migrate_to_perfile,
    next_counter_and_id,
    save_closed_yaml,
    save_tasks_yaml,
    search_tasks,
    set_storage,
    update_task,
)
from .storage import (  # noqa: F401
    STORAGE_VERSION,
    TaskStorage,
    MonolithicYamlStorage,
    PerFileYamlStorage,
    detect_storage_version,
)
from .cli import main  # noqa: F401
from .render import _format_priority, console  # noqa: F401
