"""0.2.0 autonomy: ask-once folder setup, chat-approved requests, config migration."""

import json
import os
import subprocess
import sys

from conftest import CC, git, hook
from cockcage import approvals, config, ledger


def cli(root, *args, claude=True):
    env = dict(os.environ)
    if claude:
        env["CLAUDECODE"] = "1"
    else:
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    return subprocess.run([sys.executable, CC, *args], cwd=str(root), capture_output=True, text=True, env=env, timeout=300)


def test_offer_once_then_setup_on_yes(repo):
    root = str(repo)
    out = hook(root, "session-start", {"source": "startup"})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "not set up in this folder" in ctx and "cc.py" in ctx
    assert not os.path.isdir(os.path.join(root, ".cock-cage")), "offering must not create files"
    assert hook(root, "session-start", {"source": "startup"}) is None, "ask once only"
    hook(root, "user-prompt", {"prompt": "what does app.py do"})   # the user's first message is their task
    hook(root, "stop", {"last_assistant_message": "It adds. Shall I set up cock-cage here?"})
    out = hook(root, "user-prompt", {"prompt": "yes please"})
    assert "agreed" in out["hookSpecificOutput"]["additionalContext"]
    assert approvals.folder_status(root) == "approved"
    r = cli(root, "init", "--no-tools", "--no-baseline")
    assert r.returncode == 0, r.stdout + r.stderr
    assert os.path.isdir(os.path.join(root, ".cock-cage"))
    assert approvals.folder_status(root) == "set-up"


def test_declined_folder_is_left_alone(repo):
    root = str(repo)
    hook(root, "session-start", {"source": "startup"})
    hook(root, "stop", {"last_assistant_message": "Shall I set up cock-cage here?"})
    hook(root, "user-prompt", {"prompt": "no thanks"})
    assert approvals.folder_status(root) == "declined"
    r = cli(root, "init", "--no-tools", "--no-baseline")
    assert r.returncode == 3 and "has not agreed" in r.stdout
    assert not os.path.isdir(os.path.join(root, ".cock-cage"))
    # asking explicitly later still works
    out = hook(root, "user-prompt", {"prompt": "actually, set up cock-cage here"})
    assert "agreed" in out["hookSpecificOutput"]["additionalContext"]
    assert cli(root, "init", "--no-tools", "--no-baseline").returncode == 0


def test_no_offer_in_non_projects(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "todo.md").write_text("x")
    assert hook(str(notes), "session-start", {"source": "startup"}) is None


def _setup(root):
    hook(str(root), "user-prompt", {"prompt": "set up cock-cage here"})
    assert cli(root, "init", "--no-tools", "--no-baseline").returncode == 0


def test_ledger_delete_needs_chat_approval(repo):
    root = str(repo)
    _setup(root)
    for i in range(3):
        ledger.append(root, "T%d" % i, "body %d" % i, os_lock=False)
    assert cli(root, "ledger", "reseal").returncode == 3, "raw reseal is refused for Claude"
    r = cli(root, "request", "ledger-delete", "2", "--reason", "duplicate")
    assert r.returncode == 0
    req_id = r.stdout.split()[1]
    r = cli(root, "apply", req_id)
    assert r.returncode == 3 and "not been approved" in r.stdout
    assert hook(root, "user-prompt", {"prompt": "yes"}) is None or "approved request" not in str(
        hook(root, "user-prompt", {"prompt": "ok"})), "a bare yes never approves a request"
    out = hook(root, "stop", {"last_assistant_message": "Shall I?"})
    assert 'yes %s' % req_id in out["systemMessage"], "the pending request is shown to the user"
    out = hook(root, "user-prompt", {"prompt": "yes %s" % req_id})
    assert "approved request %s" % req_id in out["hookSpecificOutput"]["additionalContext"]
    r = cli(root, "apply", req_id)
    assert r.returncode == 0, r.stdout
    assert ledger.entry_count(root) == 2 and ledger.verify(root)[0]
    text = open(os.path.join(root, ".cock-cage", "reports.md")).read()
    assert "body 1" not in text and "## #2 · " in text
    assert cli(root, "apply", req_id).returncode == 3, "a request is used once"


def test_declined_request_is_dropped(repo):
    root = str(repo)
    _setup(root)
    ledger.append(root, "T", "b", os_lock=False)
    req_id = cli(root, "request", "ledger-delete", "1", "--reason", "x").stdout.split()[1]
    hook(root, "user-prompt", {"prompt": "no %s, keep it" % req_id})
    assert cli(root, "apply", req_id).returncode == 3
    assert ledger.entry_count(root) == 1


def test_loosening_needs_approval_tightening_does_not(repo):
    root = str(repo)
    _setup(root)
    r = cli(root, "levels", "tests", "warn")
    assert r.returncode == 3 and "needs the user's yes" in r.stdout
    assert config.level(config.load(root), "tests") == "block"
    assert cli(root, "levels", "duplicates", "block").returncode == 0
    assert config.level(config.load(root), "duplicates") == "block"
    req_id = cli(root, "request", "loosen", "tests", "warn", "--reason", "no db here").stdout.split()[1]
    hook(root, "user-prompt", {"prompt": "go ahead %s" % req_id})
    assert cli(root, "apply", req_id).returncode == 0
    assert config.level(config.load(root), "tests") == "warn"


def test_users_own_terminal_is_not_gated(repo, monkeypatch):
    from cockcage import cli as cc_cli
    root = str(repo)
    _setup(root)
    monkeypatch.setattr(approvals, "claude_is_calling", lambda: False)
    assert cc_cli.main(["levels", "lint", "off", "--root", root]) == 0
    assert config.level(config.load(root), "lint") == "off"


def test_no_terminal_counts_as_claude(repo):
    root = str(repo)
    _setup(root)
    r = cli(root, "levels", "tests", "off", claude=False)   # CLAUDECODE removed, but no TTY attached
    assert r.returncode == 3


def test_config_migration_v1_to_v2(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, ".cock-cage"))
    with open(os.path.join(root, ".cock-cage", "config.json"), "w") as fh:
        json.dump({"schema": 1, "levels": {"new_dependency": "block", "tests": "warn"}}, fh)
    notes = config.load_and_migrate_on_disk(root)
    raw = json.load(open(os.path.join(root, ".cock-cage", "config.json")))
    assert raw["schema"] == 2 and raw["levels"]["new_dependency"] == "warn" and raw["levels"]["tests"] == "warn"
    assert raw["git"]["auto_ship"] is True
    assert notes == ["config migrated from schema 1 to 2"]


def test_merge_conflict_keeps_branch(repo):
    from cockcage import gitops
    root = str(repo)
    git(root, "switch", "-q", "-c", "cc/x")
    (repo / "app.py").write_text("def add(a, b):\n    return b + a\n")
    git(root, "commit", "-qam", "branch change")
    git(root, "switch", "-q", "main")
    (repo / "app.py").write_text("def add(a, b):\n    return a+b  # main\n")
    git(root, "commit", "-qam", "main change")
    git(root, "switch", "-q", "cc/x")
    ok, msg = gitops.merge_into_protected(root, "cc/x", ["main"])
    assert not ok and "conflicted" in msg
    assert git(root, "branch", "--show-current").stdout.strip() == "cc/x"
    assert git(root, "status", "--porcelain").stdout.strip() == ""


def test_stop_enforces_the_offer_once(repo):
    root = str(repo)
    hook(root, "session-start", {"source": "startup"})
    out = hook(root, "stop", {"last_assistant_message": "app.py adds two numbers."})
    assert out["decision"] == "block" and "cock-cage" in out["reason"]
    assert approvals.folder_status(root) == "asked"
    assert hook(root, "stop", {"last_assistant_message": "Want me to set up cock-cage here?"}) is None
    out = hook(root, "user-prompt", {"prompt": "sure"})
    assert "agreed" in out["hookSpecificOutput"]["additionalContext"]


def test_stop_does_not_nag_when_claude_already_asked(repo):
    root = str(repo)
    hook(root, "session-start", {"source": "startup"})
    assert hook(root, "stop", {"last_assistant_message": "Shall I set up cock-cage in this folder?"}) is None
    assert hook(root, "stop", {"last_assistant_message": "anything"}) is None
