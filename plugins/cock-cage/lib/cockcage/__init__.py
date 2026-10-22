"""cock-cage: guardrails, reporting and versioning for Claude Code in Python projects."""

from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(_HERE))


def plugin_version() -> str:
    """Single source of truth for the version: .claude-plugin/plugin.json."""
    try:
        with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("version", "0.0.0"))
    except (OSError, ValueError):
        return "0.0.0"


__version__ = plugin_version()
