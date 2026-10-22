"""Project configuration: .cock-cage/config.json, with forward-only schema migrations."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Tuple

from . import util

SCHEMA = 1
LEVELS = ("block", "warn", "report", "off")

DEFAULTS: Dict[str, Any] = {
    "schema": SCHEMA,
    "levels": {
        # block: Claude must fix before carrying on
        "plan_gate": "block",
        "tests": "block",
        "removed_tests": "block",
        "lint": "block",
        "types": "block",
        "report": "block",
        "secrets": "block",
        "dangerous": "block",
        "new_dependency": "block",
        "large_read": "block",
        "review_large_diffs": "block",
        "ledger_protection": "block",
        # warn: Claude is told during work and the hook lists it in the report
        "duplicates": "warn",
        "dead_code": "warn",
        "complexity": "warn",
        "coverage": "warn",
        "deps_hygiene": "warn",
        # report: noted in the ledger only
        "vulns": "report",
        "docstrings": "report",
    },
    "src_dirs": ["."],
    "type_checker": "pyright",          # pyright | mypy | ty | none
    "package_manager": "auto",          # auto | uv | pip
    "large_read_lines": 400,
    "complexity_max": 10,
    "duplicate_min_lines": 6,
    "review_diff_lines": 80,
    "report_max_words": 220,
    "max_stop_blocks": 3,
    "quick_prefix": "quick:",
    "approve_words": ["approve", "approved", "lgtm", "go ahead", "build it", "ship it", "yes", "proceed",
                      "do it", "looks good", "go"],
    "codemap_context_chars": 6000,
    "git": {
        "protected_branches": ["main", "master"],
        "branch_prefix": "cc/",
        "auto_branch": True,
        "auto_checkpoint": True,
    },
    "ledger": {"os_lock": True},
}


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# Each migration takes a raw config at schema N and returns it at schema N+1.
# Append new ones here when the format changes; never edit old ones.
MIGRATIONS: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


def migrate(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    notes: List[str] = []
    cur = int(raw.get("schema", 0) or 0)
    if cur == 0:
        raw["schema"] = 1
        cur = 1
    while cur < SCHEMA:
        raw = MIGRATIONS[cur](raw)
        notes.append("config migrated from schema %d to %d" % (cur, cur + 1))
        cur += 1
        raw["schema"] = cur
    if cur > SCHEMA:
        notes.append(
            "config schema %d is newer than this plugin understands (%d); update cock-cage" % (cur, SCHEMA)
        )
    return raw, notes


def path(root: str) -> str:
    return util.cc_path(root, "config.json")


def load(root: str) -> Dict[str, Any]:
    raw = util.read_json(path(root), {}) or {}
    raw, _ = migrate(raw)
    return _deep_merge(DEFAULTS, raw)


def load_and_migrate_on_disk(root: str) -> List[str]:
    """Run migrations and persist them. Returns human-readable notes."""
    p = path(root)
    raw = util.read_json(p, None)
    if raw is None:
        return []
    before = raw.get("schema")
    raw, notes = migrate(raw)
    if raw.get("schema") != before:
        util.write_json(p, raw)
    return notes


def level(cfg: Dict[str, Any], name: str) -> str:
    lv = str(cfg.get("levels", {}).get(name, DEFAULTS["levels"].get(name, "warn")))
    return lv if lv in LEVELS else "warn"


def write_default(root: str, overrides: Dict[str, Any] = None) -> None:
    """Write only the keys a user is likely to tune; everything else comes from DEFAULTS."""
    data = {
        "schema": SCHEMA,
        "levels": dict(DEFAULTS["levels"]),
        "src_dirs": list(DEFAULTS["src_dirs"]),
        "type_checker": DEFAULTS["type_checker"],
        "package_manager": DEFAULTS["package_manager"],
    }
    data = _deep_merge(data, overrides or {})
    util.write_json(path(root), data)
