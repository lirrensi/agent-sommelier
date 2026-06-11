"""Agent Sommelier - A capability sommelier for your coding agent."""

from __future__ import annotations


def _get_version() -> str:
    """Read version from package metadata via importlib."""
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("agent-sommelier-cli")
    except Exception:  # noqa: BLE001 — fallback during development
        return "0.0.0"


__version__ = _get_version()
