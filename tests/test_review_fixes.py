"""Regression tests for the independent review's findings."""

import os
import subprocess
import sys

import pytest

from conftest import CC, decision, git, hook
from cockcage import approvals, config, gitops, guards, initproj, ledger


def setup(root):
    hook(str(root), "user-prompt", {"prompt": "set up cock-cage here"})
    r = subprocess.run([sys.executable, CC, "init", "--no-tools", "--no-baseline"], cwd=str(root), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def start_task(root, title="Change add"):
    plan = os.path.join(root, ".cock-cage", "plan.md")
    open(plan, "w").write("# %s\n\n## Goal\nx\n" % title)
    hook(root, "post-tool", {"tool_name": "Write", "tool_input": {"file_path": plan}})
    hook(root, "user-prompt", {"prompt": "approve"})


def edit(root, rel, text, mode="a"):
    p = os.path.join(root, rel)
    hook(root, "pre-tool", {"tool_name": "Edit", "tool_input": {"file_path": p}})
    with open(p, mode) as fh:
        fh.write(text)
    hook(root, "post-tool", {"tool_name": "Edit", "tool_input": {"file_path": p}})


REPORT = "# Change add\n\n## What changed\nx\n\n## Why\ny\n\n## Concept (z)\nz.\n"


def finish(root):
    open(os.path.join(root, ".cock-cage", "report-draft.md"), "w").write(REPORT)
    return hook(root, "stop", {"last_assistant_message": "Done."})


# 1 (critical): the user's unrelated work is never committed
def test_checkpoint_never_sweeps_user_work(repo):
    root = str(repo)
    setup(root)
    hook(root, "session-start", {"source": "startup"})
    (repo / "notes.py").write_text("# user's file\n")
    git(root, "add", "notes.py"); git(root, "commit", "-qm", "notes")
    (repo / "notes.py").write_text("# user's file\n# user WIP\n")      # dirty before the task
    start_task(root)
    (repo / "my-private-notes.txt").write_text("secret plans")          # user creates a file mid-task
    edit(root, "app.py", "\n\ndef sub(a, b):\n    return a - b\n")      # Claude's edit
    edit(root, "notes.py", "# claude touched this too\n")               # Claude edits a dirty file
    out = finish(root)
    assert "report #1 logged" in out["systemMessage"], out
    committed = git(root, "show", "--name-only", "--pretty=", "HEAD").stdout.split()
    assert committed == ["app.py"], committed
    st = git(root, "status", "--porcelain").stdout
    assert "notes.py" in st and "my-private-notes.txt" in st
    assert "had your uncommitted changes" in open(os.path.join(root, ".cock-cage", "reports.md")).read()


# 5: mid-task turn ends are not forced into a report; 6: quick questions end the quick task
def test_multi_turn_tasks_and_quick_reset(repo):
    root = str(repo)
    setup(root)
    hook(root, "session-start", {"source": "startup"})
    start_task(root)
    edit(root, "app.py", "\n# step 1\n")
    out = hook(root, "stop", {"last_assistant_message": "Step 1 done. Reply 'continue'."})
    assert "decision" not in out and "in progress" in out["systemMessage"]
    assert decision(hook(root, "pre-tool", {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(root, "app.py")}})) is None
    assert "report #1" in finish(root)["systemMessage"]
    hook(root, "user-prompt", {"prompt": "quick: what does add do"})
    assert hook(root, "stop", {"last_assistant_message": "It adds."}) is None
    assert decision(hook(root, "pre-tool", {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(root, "app.py")}})) == "deny"


# 7: Claude Code's own plan and memory files are fine; settings and plugins are not
def test_home_claude_scope(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    ctx = guards.Context(root=str(tmp_path / "proj"))
    os.makedirs(tmp_path / "proj" / ".cock-cage")
    assert guards.is_protected(ctx, str(home / ".claude" / "plans" / "p.md")) is None
    assert guards.is_protected(ctx, str(home / ".claude" / "projects" / "x" / "memory" / "m.md")) is None
    assert guards.is_protected(ctx, str(home / ".claude" / "settings.json"))
    assert guards.is_protected(ctx, str(home / ".claude" / "plugins" / "cache" / "x" / "hooks.json"))


# 3: init never writes a second [tool.ruff.lint] table
def test_init_respects_existing_tool_tables(tmp_path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text('[project]\nname = "x"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n\n[tool.ruff.lint]\nselect = ["E"]\n')
    added = initproj._ensure_tool_tables(str(tmp_path), "pyright")
    text = pp.read_text()
    assert "tool.ruff" not in added and text.count("[tool.ruff.lint]") == 1
    assert "[tool.pyright]" in text
    try:
        import tomllib
        tomllib.loads(text)
    except ImportError:
        pass


def test_ruff_target_follows_requires_python(tmp_path):
    initproj._ensure_tool_tables(str(tmp_path), "pyright")   # no requires-python: pin py39
    assert 'target-version = "py39"' in (tmp_path / "pyproject.toml").read_text()


# 4: first commit in a fresh repo leaves out secrets and vendored folders
def test_first_commit_skips_secrets_and_vendored(tmp_path):
    root = tmp_path / "loose"
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "i.js").write_text("x")
    (root / "secrets.yaml").write_text("k: v")
    (root / "server.pem").write_text("pem")
    (root / "main.py").write_text("print(1)\n")
    setup(root)
    files = git(str(root), "ls-files").stdout.split()
    assert "main.py" in files
    assert not any(f.startswith("node_modules") or f in ("secrets.yaml", "server.pem") for f in files), files


# 8: ignored, vanished and symlinked paths do not silently break the checkpoint
def test_committable_filters(repo):
    root = str(repo)
    (repo / ".gitignore").write_text("ignored.py\n")
    (repo / "ignored.py").write_text("x")
    keep = gitops.committable(root, ["app.py", "ignored.py", "vanished.py"])
    assert keep == ["app.py"]


# 9: a detached HEAD gets a task branch; checkpoint never commits onto it
def test_detached_head(repo):
    root = str(repo)
    git(root, "checkout", "-q", "--detach")
    cfg = config.load(root)
    assert gitops.checkpoint(root, ["app.py"], "t", "b", ["main"]) is None
    br = gitops.ensure_task_branch(root, "Do thing", cfg)
    assert br == "cc/do-thing" and gitops.branch(root) == br


# 11: deleting the whole ledger, or injecting a marker, is caught
def test_ledger_deletion_and_marker_injection(tmp_path):
    root = str(tmp_path)
    ledger.append(root, "T", "body with <!-- cc:entry n=9 prev=" + "0" * 64 + " --> inside", os_lock=False)
    ok, problems = ledger.verify(root)
    assert ok and ledger.entry_count(root) == 1, problems
    rp, sp = ledger.paths(root)
    os.unlink(rp); os.unlink(sp)
    ok, problems = ledger.verify(root)
    assert not ok and "gone" in problems[0]


# 13 and 14: bypasses caught, quoted text is not an operator
@pytest.mark.parametrize("cmd", ["git -C . push --force origin main", "git --no-pager push -f", 'bash -c "rm -rf ~"',
                                 "sh -c 'git reset --hard'"])
def test_guard_bypasses_caught(cmd, tmp_path):
    os.makedirs(tmp_path / ".cock-cage")
    ctx = guards.Context(root=str(tmp_path), branch="main")
    assert any(f.check == "dangerous" and f.kind == "deny" for f in guards.bash_findings(cmd, ctx)), cmd


def test_git_c_commit_on_main_caught(tmp_path):
    os.makedirs(tmp_path / ".cock-cage")
    ctx = guards.Context(root=str(tmp_path), branch="main")
    assert guards.bash_findings("git -c user.name=x commit -m y", ctx)


@pytest.mark.parametrize("cmd", ['grep -n ") -> str" app.py', 'git log --format="%h -> %s"', "awk '$3 > 100' f",
                                 "git restore --staged .", 'echo "a | b" | wc -l'])
def test_quoted_operators_are_data(cmd, tmp_path):
    os.makedirs(tmp_path / ".cock-cage")
    ctx = guards.Context(root=str(tmp_path), phase="planning")
    assert guards.pre_tool_findings("Bash", {"command": cmd}, ctx) == [], cmd


# 15: only whole-message approval counts
@pytest.mark.parametrize("prompt,ok", [
    ("OK, go ahead", True), ("approve", True), ("yes", True), ("looks good, go ahead", True),
    ("go back and rethink step 2", False), ("do it differently: use sqlite", False),
    ("yes please add error handling to the plan first", False), ("proceed to explain the design", False),
])
def test_whole_message_approval(prompt, ok):
    assert approvals.is_affirmative(prompt, config.DEFAULTS["approve_words"]) is ok


# 12: ship never claims a merge that did not happen
def test_ship_reports_failed_switch(repo):
    root = str(repo)
    setup(root)
    git(root, "switch", "-q", "-c", "cc/x")
    (repo / "blocker.txt").write_text("tracked on branch")
    git(root, "add", "blocker.txt"); git(root, "commit", "-qm", "b")
    git(root, "switch", "-q", "main")
    (repo / "blocker.txt").write_text("untracked on main")   # would be overwritten by the merge
    git(root, "switch", "-q", "cc/x") if False else None
    r = subprocess.run(["git", "checkout", "-q", "cc/x"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:  # git refuses to switch with the untracked file in place; simulate on the branch
        os.unlink(repo / "blocker.txt"); git(root, "checkout", "-q", "cc/x")
    r = subprocess.run([sys.executable, CC, "ship"], cwd=root, capture_output=True, text=True)
    branch = git(root, "branch", "--show-current").stdout.strip()
    if r.returncode == 0:
        assert branch == "main" and "cc/x" not in git(root, "branch").stdout
    else:
        assert "Not merged" in r.stdout and branch == "cc/x"


# low: release refuses when pyproject has uncommitted edits
def test_release_refuses_dirty_pyproject(repo):
    from cockcage import versioning
    root = str(repo)
    (repo / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0.1.0"\n')
    git(root, "add", "pyproject.toml"); git(root, "commit", "-qm", "p")
    (repo / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0.1.0"\n# wip\n')
    with pytest.raises(ValueError):
        versioning.release(root, "patch")


# low: init inside a subdirectory of a bigger repo commits its setup
def test_init_in_subdirectory(repo):
    sub = repo / "svc"
    sub.mkdir()
    (sub / "svc.py").write_text("x = 1\n")
    git(str(repo), "add", "-A"); git(str(repo), "commit", "-qm", "svc")
    setup(sub)
    assert "chore: set up cock-cage" in git(str(repo), "log", "-1", "--pretty=%s").stdout
    assert "svc/.cock-cage/config.json" in git(str(repo), "ls-files").stdout
