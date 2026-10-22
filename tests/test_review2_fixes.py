"""Regression tests for the second review's findings (0.2.1)."""

import json
import os
import subprocess
import sys
import time

import pytest

from conftest import CC, decision, git, hook
from cockcage import approvals, config, guards, initproj, state


def cli(root, *args):
    env = dict(os.environ, CLAUDECODE="1")
    return subprocess.run([sys.executable, CC, *args], cwd=str(root), capture_output=True, text=True, env=env, timeout=300)


def setup(root):
    hook(str(root), "user-prompt", {"prompt": "set up cock-cage here"})
    r = cli(root, "init", "--no-tools", "--no-baseline")
    assert r.returncode == 0, r.stdout


def write_plan(root):
    plan = os.path.join(root, ".cock-cage", "plan.md")
    open(plan, "w").write("# Change add\n")
    hook(root, "post-tool", {"tool_name": "Write", "tool_input": {"file_path": plan}})


# 1: obvious self-approval workarounds are blocked
@pytest.mark.parametrize("cmd", [
    "env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT cockcage levels types off",
    "CLAUDECODE= python3 /x/cc.py ledger reseal",
    "unset CLAUDECODE; cockcage levels tests off",
    "echo '{\"prompt\":\"ok\"}' | python3 /x/cc.py hook user-prompt",
    "python3 -c \"open('.cock-cage/state.json','w').write('{}')\"",
    "python3 - <<'PY'\nimport json\njson.dump({}, open('.cock-cage/config.json','w'))\nPY",
    "script -q /dev/null cockcage ledger reseal",
    "rm ~/.cock-cage/folders.json",
])
def test_self_approval_workarounds_blocked(cmd, tmp_path):
    os.makedirs(tmp_path / ".cock-cage")
    ctx = guards.Context(root=str(tmp_path), phase="approved", branch="cc/x")
    assert any(f.check == "ledger_protection" for f in guards.bash_findings(cmd, ctx)), cmd


def test_reading_protected_files_still_fine(tmp_path):
    os.makedirs(tmp_path / ".cock-cage")
    ctx = guards.Context(root=str(tmp_path), phase="approved", branch="cc/x")
    assert guards.bash_findings("tail -n 30 .cock-cage/reports.md | grep Concept", ctx) == []


# 2: an expired request never swallows plan approval
def test_expired_request_does_not_lock_plan_approval(repo):
    root = str(repo)
    setup(root)
    hook(root, "session-start", {"source": "startup"})
    assert cli(root, "request", "loosen", "tests", "warn", "--reason", "x").returncode == 0
    with state.locked(root) as st:
        st["request"]["created"] = time.time() - 2 * approvals.REQUEST_TTL_SECONDS
    write_plan(root)
    hook(root, "user-prompt", {"prompt": "yes"})
    assert state.load(root)["task"]["phase"] == "approved"


# 3: requests are answered by id; one request at a time
def test_request_needs_id_and_no_overwrite(repo):
    root = str(repo)
    setup(root)
    r = cli(root, "request", "loosen", "tests", "warn", "--reason", "x")
    req_id = r.stdout.split()[1]
    assert "yes %s" % req_id in r.stdout
    assert cli(root, "request", "loosen", "lint", "warn", "--reason", "y").returncode == 2, "no second pending request"
    for stray in ("ok thanks", "nice", "yes", "/cock-cage:approve"):
        hook(root, "user-prompt", {"prompt": stray})
    assert not state.load(root)["request"]["approved_at"]
    hook(root, "user-prompt", {"prompt": "Yes %s." % req_id})
    assert state.load(root)["request"]["approved_at"]


# 4: everyday commands are not mistaken for writes during planning
@pytest.mark.parametrize("cmd", ["pytest -q 2>&1 | tail -20", "ls > /dev/null 2>&1", "echo hi >&2",
                                 "cat <<EOF\nrm -rf /\nEOF", "python3 - <<'PY'\nif 3 > 2:\n    print(1)\nPY"])
def test_no_false_writes(cmd, tmp_path):
    os.makedirs(tmp_path / ".cock-cage")
    ctx = guards.Context(root=str(tmp_path), phase="planning")
    assert guards.pre_tool_findings("Bash", {"command": cmd}, ctx) == [], cmd


@pytest.mark.parametrize("cmd", ["bash -lc 'rm -rf ~'", "eval 'git push -f'", "find . -delete",
                                 "git checkout -f main", "git switch --discard-changes main"])
def test_more_bypasses_caught(cmd, tmp_path):
    os.makedirs(tmp_path / ".cock-cage")
    ctx = guards.Context(root=str(tmp_path), branch="cc/x")
    assert any(f.check == "dangerous" for f in guards.bash_findings(cmd, ctx)), cmd


def test_find_delete_with_filter_is_fine(tmp_path):
    os.makedirs(tmp_path / ".cock-cage")
    ctx = guards.Context(root=str(tmp_path), branch="cc/x")
    assert guards.bash_findings("find . -name '*.pyc' -delete", ctx) == []


# 5: negated or questioning mentions never trigger setup; ignored offers expire; status None refuses
@pytest.mark.parametrize("prompt,ok", [
    ("set up cock-cage here", True), ("please install cockcage in this repo", True), ("/cock-cage:init", True),
    ("don't install cock-cage here", False), ("never add cockcage to this", False),
    ("what does cock-cage enable?", False), ("remove cock-cage", False),
])
def test_setup_intent(prompt, ok):
    assert approvals.asks_for_setup(prompt) is ok


def test_ignored_offer_expires(repo):
    root = str(repo)
    hook(root, "session-start", {"source": "startup"})
    hook(root, "stop", {"last_assistant_message": "Want me to set up cock-cage?"})
    hook(root, "user-prompt", {"prompt": "anyway, refactor app.py please"})   # ignores the offer
    assert approvals.folder_status(root) == "declined"
    hook(root, "user-prompt", {"prompt": "go ahead"})
    assert approvals.folder_status(root) == "declined"


def test_init_refused_without_recorded_agreement(repo):
    r = cli(repo, "init", "--no-tools", "--no-baseline")
    assert r.returncode == 3


# 6 and 7: staged secrets are unstaged; src/env is the user's code
def test_first_commit_unstages_secrets_and_keeps_src_env(tmp_path):
    root = tmp_path / "loose"
    (root / "src" / "env").mkdir(parents=True)
    (root / "src" / "env" / "settings.py").write_text("X = 1\n")
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "i.js").write_text("x")
    (root / ".env.prod").write_text("K=V")
    (root / "server.key").write_text("k")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root)
    subprocess.run(["git", "add", "-A"], cwd=root)     # staged before init ran
    setup(root)
    files = git(str(root), "ls-files").stdout.split()
    assert "src/env/settings.py" in files
    assert not any(f.startswith("node_modules") or f in (".env.prod", "server.key") for f in files), files


# 8: without a TOML parser, dotted tool keys are respected
def test_dotted_tool_keys_without_parser(tmp_path, monkeypatch):
    monkeypatch.setattr(initproj, "_toml_ok", lambda text, root="": None)
    pp = tmp_path / "pyproject.toml"
    pp.write_text('[project]\nname = "x"\nrequires-python = ">=3.9"\n\n[tool]\nruff.line-length = 120\n')
    added = initproj._ensure_tool_tables(str(tmp_path), "pyright")
    assert "tool.ruff" not in added and "[tool.ruff]" not in pp.read_text()


# 9: files changed by shell commands are committed with the task
def test_shell_changed_files_are_committed(repo):
    root = str(repo)
    setup(root)
    hook(root, "session-start", {"source": "startup"})
    write_plan(root)
    hook(root, "user-prompt", {"prompt": "approve"})
    hook(root, "pre-tool", {"tool_name": "Bash", "tool_input": {"command": "uv add requests"}})
    (repo / "requirements.txt").write_text("requests\n")        # what the command changed
    hook(root, "post-tool", {"tool_name": "Bash", "tool_input": {"command": "uv add requests"}})
    p = os.path.join(root, "app.py")
    hook(root, "pre-tool", {"tool_name": "Edit", "tool_input": {"file_path": p}})
    open(p, "a").write("\nimport requests  # noqa\n")
    hook(root, "post-tool", {"tool_name": "Edit", "tool_input": {"file_path": p}})
    open(os.path.join(root, ".cock-cage", "report-draft.md"), "w").write("# Change add\n\n## What changed\nx\n\n## Why\ny\n\n## Concept (z)\nz.\n")
    out = hook(root, "stop", {"last_assistant_message": "Done."})
    assert "logged" in out["systemMessage"], out
    committed = git(root, "show", "--name-only", "--pretty=", "HEAD").stdout.split()
    assert sorted(committed) == ["app.py", "requirements.txt"], committed
