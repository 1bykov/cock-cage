import os

import pytest

from cockcage import guards


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / ".cock-cage").mkdir()
    return guards.Context(root=str(tmp_path), phase="approved", branch="cc/x")


def checks(findings):
    return sorted({(f.check, f.kind) for f in findings})


@pytest.mark.parametrize("path,secret", [
    (".env", True), ("config/.env.production", True), (".env.example", False), ("app.env", True),
    ("id_rsa", True), ("id_rsa.pub", False), ("certs/server.pem", True), ("secrets.yaml", True),
    ("/home/u/.ssh/config", True), ("/home/u/.ssh/id_ed25519.pub", False), ("src/env.py", False),
    ("settings.py", False), ("credentials.json", True),
])
def test_secret_paths(path, secret):
    assert guards.is_secret_path(path) is secret


@pytest.mark.parametrize("cmd,expected", [
    ("rm -rf /", ("dangerous", "deny")),
    ("rm -rf ~", ("dangerous", "deny")),
    ("rm -fr .", ("dangerous", "deny")),
    ("cd x && rm -rf *", ("dangerous", "deny")),
    ("sudo apt install x", ("dangerous", "deny")),
    ("git push --force origin cc/x", ("dangerous", "deny")),
    ("git push origin main", ("dangerous", "deny")),
    ("git push", ("dangerous", "ask")),
    ("git reset --hard HEAD~1", ("dangerous", "deny")),
    ("git clean -fd", ("dangerous", "deny")),
    ("git checkout -- .", ("dangerous", "deny")),
    ("cat .env", ("secrets", "deny")),
    ("uv add requests", ("new_dependency", "ask")),
    ("pip install pandas numpy", ("new_dependency", "ask")),
    ("python -m pip install httpx", ("new_dependency", "ask")),
    ("chflags nouappnd .cock-cage/reports.md", ("ledger_protection", "deny")),
    ("echo hi >> .cock-cage/reports.md", ("ledger_protection", "deny")),
    ("python3 tamper.py .cock-cage/state.json", ("ledger_protection", "deny")),
    ("cockcage ledger reseal", ("ledger_protection", "deny")),
    ("python3 /x/cc.py levels tests off", ("ledger_protection", "deny")),
    ("rm -rf .cock-cage", ("ledger_protection", "deny")),
])
def test_bash_blocked(ctx, cmd, expected):
    assert expected in checks(guards.bash_findings(cmd, ctx))


@pytest.mark.parametrize("cmd", [
    "rm -rf build/", "pytest -q", "ls -la", "git status", "git diff HEAD", "git log --oneline",
    "pip install -r requirements.txt", "pip install -e .", "uv sync", "cat .cock-cage/reports.md",
    "tail -n 20 .cock-cage/reports.md", "echo ok > /dev/null", "cp .env.example config.sample",
    "git commit -m 'x'", "python3 script.py", "cockcage status",
])
def test_bash_allowed(ctx, cmd):
    assert guards.bash_findings(cmd, ctx) == []


def test_commit_on_protected_branch_blocked(ctx):
    ctx.branch = "main"
    assert ("dangerous", "deny") in checks(guards.bash_findings("git commit -am wip", ctx))
    assert ("dangerous", "deny") in checks(guards.bash_findings("git merge cc/x", ctx))


def test_plan_gate_for_edits(ctx):
    ctx.phase = "idle"
    f = guards.pre_tool_findings("Write", {"file_path": os.path.join(ctx.root, "app.py")}, ctx)
    assert ("plan_gate", "deny") in checks(f)
    # the plan itself and report drafts are always writable
    for allowed in ("plan.md", "report-draft.md", "skill-drafts/x/SKILL.md"):
        assert guards.pre_tool_findings("Write", {"file_path": os.path.join(ctx.root, ".cock-cage", allowed)}, ctx) == []
    ctx.phase = "quick"
    assert guards.pre_tool_findings("Edit", {"file_path": os.path.join(ctx.root, "app.py")}, ctx) == []


def test_plan_gate_shell_writes(ctx):
    ctx.phase = "planning"
    msgs = [f.message for f in guards.pre_tool_findings("Bash", {"command": "echo x > app.py"}, ctx)]
    assert any("awaiting" in m or "waiting" in m for m in msgs)
    assert guards.pre_tool_findings("Bash", {"command": "pytest -q"}, ctx) == []
    assert guards.pre_tool_findings("Bash", {"command": "echo x > /tmp/scratch.txt"}, ctx) == []


def test_protected_files_never_editable(ctx):
    for name in ("reports.md", "ledger.seal", "config.json", "state.json"):
        f = guards.pre_tool_findings("Edit", {"file_path": os.path.join(ctx.root, ".cock-cage", name)}, ctx)
        assert ("ledger_protection", "deny") in checks(f), name
    f = guards.pre_tool_findings("Write", {"file_path": os.path.join(ctx.root, ".claude", "settings.json")}, ctx)
    assert ("ledger_protection", "deny") in checks(f)


def test_large_read(ctx):
    big = os.path.join(ctx.root, "big.py")
    with open(big, "w") as fh:
        fh.write("x = 1\n" * 500)
    assert ("large_read", "deny") in checks(guards.pre_tool_findings("Read", {"file_path": big}, ctx))
    assert guards.pre_tool_findings("Read", {"file_path": big, "offset": 10, "limit": 50}, ctx) == []
    small = os.path.join(ctx.root, "small.py")
    with open(small, "w") as fh:
        fh.write("x = 1\n")
    assert guards.pre_tool_findings("Read", {"file_path": small}, ctx) == []


def test_worst_honours_levels():
    fs = [guards.Finding("duplicates", "a"), guards.Finding("new_dependency", "b", "ask"), guards.Finding("secrets", "c")]
    assert guards.worst(fs, {"duplicates": "warn", "new_dependency": "block", "secrets": "block"}).check == "secrets"
    assert guards.worst(fs, {"duplicates": "warn", "new_dependency": "block", "secrets": "off"}).check == "new_dependency"
    assert guards.worst(fs, {"duplicates": "warn", "new_dependency": "warn", "secrets": "off"}) is None
