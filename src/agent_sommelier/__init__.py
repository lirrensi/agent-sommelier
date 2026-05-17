"""Agent Sommelier - A capability sommelier for your coding agent."""

from __future__ import annotations

import re
from pathlib import Path


def _get_version() -> str:
    """Read version from pyproject.toml so it never drifts."""
    try:
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        if pyproject.is_file():
            text = pyproject.read_text(encoding="utf-8")
            match = re.search(
                r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE
            )
            if match:
                return match.group(1)
    except Exception:  # noqa: BLE001 — fallback safe
        pass
    return "0.0.0"


__version__ = _get_version()
