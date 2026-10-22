import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "cock-cage")
CC = os.path.join(PLUGIN, "scripts", "cc.py")
sys.path.insert(0, os.path.join(PLUGIN, "lib"))


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("COCK_CAGE_HOME", str(tmp_path / "cc-home"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def hook(root, event, payload=None, env=None):
    """Run a hook exactly as Claude Code would: JSON on stdin, JSON (or nothing) on stdout."""
    data = dict(payload or {})
    data.setdefault("cwd", str(root))
    full_env = dict(os.environ)
    full_env["CLAUDE_PROJECT_DIR"] = str(root)
    full_env.update(env or {})
    proc = subprocess.run([sys.executable, CC, "hook", event], input=json.dumps(data), capture_output=True,
                          text=True, cwd=str(root), env=full_env, timeout=300)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def decision(out):
    if not out:
        return None
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")
