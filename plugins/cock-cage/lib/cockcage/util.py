"""Small shared helpers. Standard library only, Python 3.9+."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

CC_DIR = ".cock-cage"

# Directories never scanned for source code.
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", "build", "dist",
    ".cock-cage", ".claude", "site-packages", ".idea", ".vscode", ".eggs",
}


def now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def project_root(payload: Optional[Dict[str, Any]] = None) -> str:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    if payload and isinstance(payload.get("cwd"), str) and os.path.isdir(payload["cwd"]):
        return os.path.abspath(payload["cwd"])
    return os.path.abspath(os.getcwd())


def cc_path(root: str, *parts: str) -> str:
    return os.path.join(root, CC_DIR, *parts)


def read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path: str, data: Any) -> None:
    """Atomic JSON write (temp file + rename) so a crash never leaves half a file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return default


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(cmd: Sequence[str], cwd: str, timeout: int = 300, env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    """Run a command, never raising. Returns (code, stdout, stderr). 127 means not found, 124 timeout."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(
            list(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout, env=full_env,
            stdin=subprocess.DEVNULL,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss: %s" % (timeout, " ".join(cmd))
    except OSError as exc:  # permission errors and similar
        return 126, "", str(exc)


def find_tool(root: str, name: str) -> Optional[str]:
    """Locate a dev tool, preferring the project's virtualenv over PATH."""
    for venv in (".venv", "venv", "env"):
        cand = os.path.join(root, venv, "bin", name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return shutil.which(name)


def project_python(root: str) -> str:
    for venv in (".venv", "venv", "env"):
        cand = os.path.join(root, venv, "bin", "python")
        if os.path.isfile(cand):
            return cand
    return sys.executable or "python3"


def rel(root: str, path: str) -> str:
    try:
        return os.path.relpath(os.path.abspath(path), root)
    except ValueError:
        return path


def is_inside(root: str, path: str) -> bool:
    try:
        ap = os.path.realpath(os.path.abspath(path))
        rp = os.path.realpath(os.path.abspath(root))
        return os.path.commonpath([ap, rp]) == rp
    except ValueError:
        return False


def iter_py_files(root: str, dirs: Optional[List[str]] = None) -> List[str]:
    out: List[str] = []
    for base in dirs or ["."]:
        start = os.path.join(root, base)
        if os.path.isfile(start) and start.endswith(".py"):
            out.append(os.path.abspath(start))
            continue
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    out.append(os.path.abspath(os.path.join(dirpath, fn)))
    return sorted(set(out))


def count_lines(path: str, limit: int = 10_000_000) -> int:
    n = 0
    try:
        with open(path, "rb") as fh:
            for n, _ in enumerate(fh, 1):
                if n >= limit:
                    break
    except OSError:
        return 0
    return n


def user_dir() -> str:
    """Per-user cock-cage folder (survives plugin updates and uninstalls)."""
    return os.environ.get("COCK_CAGE_HOME") or os.path.expanduser("~/.cock-cage")


def user_settings() -> Dict[str, Any]:
    return read_json(os.path.join(user_dir(), "settings.json"), {}) or {}


def log_error(root: str, where: str, exc: BaseException) -> None:
    try:
        os.makedirs(cc_path(root), exist_ok=True)
        with open(cc_path(root, "hook-errors.log"), "a", encoding="utf-8") as fh:
            fh.write("%s %s: %r\n" % (now_iso(), where, exc))
    except OSError:
        pass
