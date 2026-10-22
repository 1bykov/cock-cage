"""Session and task state in .cock-cage/state.json (gitignored), guarded by a file lock."""

from __future__ import annotations

import contextlib
import os
from typing import Any, Dict, Iterator

from . import util

try:  # POSIX only; on other platforms we fall back to no locking.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

TASK_DEFAULT: Dict[str, Any] = {
    "phase": "idle",          # idle | planning | approved | quick
    "title": "",
    "touched": [],
    "start_commit": "",
    "start_untracked": [],
    "reviewed": False,
    "stop_blocks": 0,
    "warnings": [],
    "started_at": "",
    "fmt": {},                # file -> was it already ruff-formatted before Claude touched it
    "tidy": [],               # autofix/format notes carried across blocked Stop attempts
}

STATE_DEFAULT: Dict[str, Any] = {
    "task": dict(TASK_DEFAULT),
    "baseline": {},           # results of the last trusted check run
    "baseline_status": "none",  # none | running | ready | contaminated
    "codemap_mtimes": {},
    "last_audit": "",
    "audit": {},
    "notices": [],
}


def path(root: str) -> str:
    return util.cc_path(root, "state.json")


def load(root: str) -> Dict[str, Any]:
    data = util.read_json(path(root), {}) or {}
    out = dict(STATE_DEFAULT)
    out.update(data)
    task = dict(TASK_DEFAULT)
    task.update(out.get("task") or {})
    out["task"] = task
    return out


def save(root: str, data: Dict[str, Any]) -> None:
    util.write_json(path(root), data)


@contextlib.contextmanager
def locked(root: str) -> Iterator[Dict[str, Any]]:
    """Load, yield for mutation, then save, holding an exclusive lock throughout."""
    os.makedirs(util.cc_path(root), exist_ok=True)
    lock_path = util.cc_path(root, ".state.lock")
    with open(lock_path, "a+") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            data = load(root)
            yield data
            save(root, data)
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def reset_task(data: Dict[str, Any]) -> None:
    data["task"] = dict(TASK_DEFAULT)
    data["task"]["touched"] = []
    data["task"]["warnings"] = []
    data["task"]["start_untracked"] = []
    data["task"]["fmt"] = {}
    data["task"]["tidy"] = []
