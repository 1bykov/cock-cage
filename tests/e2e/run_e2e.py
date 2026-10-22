"""Real-tools end-to-end check: `cockcage init` on an empty and an existing project, then a full task.

Installs real dev tools from PyPI, so it needs network access and takes a few minutes.
Run: python3 tests/e2e/run_e2e.py [workdir]
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CC = os.path.join(REPO, "plugins", "cock-cage", "scripts", "cc.py")
PY = os.environ.get("E2E_PYTHON", sys.executable)
FAILS = []


def sh(cmd, cwd, check=True, inp=None, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=inp, env=env or ENV, timeout=1800)
    if check and p.returncode != 0:
        raise SystemExit("FAILED %s\n%s\n%s" % (cmd, p.stdout, p.stderr))
    return p


def hook(root, event, payload):
    payload = dict(payload, cwd=root)
    p = sh([PY, CC, "hook", event], root, inp=json.dumps(payload), env=dict(ENV, CLAUDE_PROJECT_DIR=root))
    return json.loads(p.stdout) if p.stdout.strip() else None


def expect(cond, what):
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def state(root):
    return json.load(open(os.path.join(root, ".cock-cage", "state.json")))


def wait_baseline(root):
    for _ in range(300):
        if state(root).get("baseline_status") != "running":
            return state(root)["baseline_status"]
        time.sleep(1)
    return "running"


def git(root, *a):
    return sh(["git", *a], root, check=False).stdout.strip()


def git_raw(root, *a):
    return sh(["git", *a], root, check=False).stdout


# ------------------------------------------------------------------ setup

work = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="cc-e2e-")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
bindir = os.path.join(work, "bin")
os.makedirs(bindir)
os.symlink(shutil.which("uv"), os.path.join(bindir, "uv"))
# PATH without globally installed ruff/pyright/pytest, so only project tools count
ENV = dict(os.environ, PATH=bindir + ":/usr/bin:/bin", COCK_CAGE_HOME=os.path.join(work, "home"),
           GIT_AUTHOR_NAME="e2e", GIT_AUTHOR_EMAIL="e2e@example.com", GIT_COMMITTER_NAME="e2e",
           GIT_COMMITTER_EMAIL="e2e@example.com", HOME=os.path.join(work, "home"))
os.makedirs(ENV["HOME"])
sh(["git", "config", "--global", "init.defaultBranch", "main"], work)
print("python for hooks:", sh([PY, "--version"], work).stdout.strip(), "| workdir:", work)

# ------------------------------------------------------------------ 1. empty project

print("\n[1] empty project")
empty = os.path.join(work, "empty")
os.makedirs(empty)
hook(empty, "user-prompt", {"prompt": "set up cock-cage here"})   # the user's agreement, as the hook records it
out = sh([PY, CC, "init"], empty)
print(textwrap.indent(out.stdout.strip(), "    "))
expect(os.path.isdir(os.path.join(empty, ".git")), "git repository created")
expect(os.path.exists(os.path.join(empty, "pyproject.toml")), "pyproject.toml created")
for tool in ("ruff", "pytest", "pyright", "vulture", "deptry", "pip-audit"):
    expect(os.path.exists(os.path.join(empty, ".venv", "bin", tool)), "%s installed in .venv" % tool)
expect("cock-cage:start" in open(os.path.join(empty, "CLAUDE.md")).read(), "CLAUDE.md section written")
settings = json.load(open(os.path.join(empty, ".claude", "settings.json")))
expect(settings["enabledPlugins"].get("cock-cage@cock-cage") is True, "plugin enabled at project scope")
expect(settings["extraKnownMarketplaces"]["cock-cage"]["source"]["repo"] == "1bykov/cock-cage", "marketplace declared")
expect(git(empty, "log", "--oneline").count("\n") == 0 and "initial commit" in git(empty, "log", "--oneline"), "one initial commit")
expect(git(empty, "status", "--porcelain") == "", "working tree clean after init")
expect("[tool.pyright]" in open(os.path.join(empty, "pyproject.toml")).read(), "pyright configured for .venv")
out = sh([PY, CC, "init"], empty)
expect(git(empty, "log", "--oneline").count("\n") == 0, "re-running init is idempotent (no extra commit)")

# ------------------------------------------------------------------ 2. existing project

print("\n[2] existing project with history, a known failing test and uncommitted work")
ex = os.path.join(work, "existing")
os.makedirs(os.path.join(ex, "pricing"))
os.makedirs(os.path.join(ex, "tests"))
open(os.path.join(ex, "pyproject.toml"), "w").write(textwrap.dedent("""\
    [project]
    name = "pricing"
    version = "0.3.0"
    requires-python = ">=3.9"
    dependencies = []

    [build-system]
    requires = ["hatchling"]
    build-backend = "hatchling.build"
"""))
open(os.path.join(ex, "pricing", "__init__.py"), "w").write("")
open(os.path.join(ex, "pricing", "core.py"), "w").write(textwrap.dedent('''\
    """Pricing helpers."""
    import os


    def vat(amount: float, rate: float = 0.2) -> float:
        """Add VAT."""
        return round(amount * (1 + rate), 2)


    def parse_rows(lines):
        out = []
        for line in lines:
            parts = line.strip().split(",")
            if len(parts) != 2:
                continue
            out.append({"sku": parts[0], "price": float(parts[1])})
        return out
'''))
open(os.path.join(ex, "pricing", "legacy.py"), "w").write("def old( a,b ):\n    return a+b\n\n\nTAX   =   0.2\n")
open(os.path.join(ex, "tests", "test_core.py"), "w").write(textwrap.dedent('''\
    from pricing.core import vat


    def test_vat():
        assert vat(100) == 120.0


    def test_known_broken():
        assert vat(0.1, 0.0) == 0.2
'''))
sh(["git", "init", "-q", "-b", "main"], ex)
sh(["git", "add", "-A"], ex)
sh(["git", "commit", "-q", "-m", "feat: pricing helpers"], ex)
with open(os.path.join(ex, "pricing", "core.py"), "a") as fh:
    fh.write("\n# work in progress by the user\n")
open(os.path.join(ex, "notes.txt"), "w").write("my scratch notes\n")

hook(ex, "user-prompt", {"prompt": "set up cock-cage here"})
out = sh([PY, CC, "init"], ex)
print(textwrap.indent(out.stdout.strip(), "    "))
expect("work in progress" in open(os.path.join(ex, "pricing", "core.py")).read(), "user's uncommitted edit untouched")
st = git_raw(ex, "status", "--porcelain")
expect(" M pricing/core.py" in st and "?? notes.txt" in st, "user's uncommitted work left uncommitted")
expect("chore: set up cock-cage" in git(ex, "log", "-1", "--pretty=%s"), "setup committed separately")
committed = git(ex, "show", "--name-only", "--pretty=", "HEAD").split()
expect("pricing/core.py" not in committed and "notes.txt" not in committed, "setup commit excludes user files")
base = state(ex)["baseline"]
expect(base["tests"]["failing"] == ["tests.test_core::test_known_broken"], "baseline records the pre-existing failure")
expect("os" in json.dumps(base["lint"]["items"]), "baseline records the pre-existing lint issue (unused import)")
sh(["git", "stash", "-u", "-q"], ex)  # park the user's WIP so the task below starts clean

# ------------------------------------------------------------------ 3. a full task in the existing project

print("\n[3] a task: plan gate, approval, checks against baseline, report, commit")
out = hook(ex, "session-start", {"source": "startup"})
ctx = out["hookSpecificOutput"]["additionalContext"]
expect("pricing/core.py" in ctx and "def vat(amount: float, rate: float=…) -> float" in ctx, "code map injected at start")
expect(wait_baseline(ex) == "ready", "background baseline finished")
core = os.path.join(ex, "pricing", "core.py")
d = hook(ex, "pre-tool", {"tool_name": "Edit", "tool_input": {"file_path": core}})
expect(d and d["hookSpecificOutput"]["permissionDecision"] == "deny", "edit blocked before a plan")
d = hook(ex, "pre-tool", {"tool_name": "Bash", "tool_input": {"command": "uv add requests"}})
expect(d is None, "adding a dependency is allowed (logged as a warning)")
plan = os.path.join(ex, ".cock-cage", "plan.md")
open(plan, "w").write("# Add discount helper\n\n## Goal\nA discount() function.\n")
hook(ex, "post-tool", {"tool_name": "Write", "tool_input": {"file_path": plan}})
hook(ex, "user-prompt", {"prompt": "yes, go ahead"})
expect(git(ex, "branch", "--show-current") == "cc/add-discount-helper", "approval moved work to a task branch")

legacy = os.path.join(ex, "pricing", "legacy.py")
tests = os.path.join(ex, "tests", "test_core.py")
for f in (core, tests, legacy):
    hook(ex, "pre-tool", {"tool_name": "Edit", "tool_input": {"file_path": f}})
expect(state(ex)["task"]["fmt"].get("pricing/legacy.py") is False, "legacy file recorded as not formatted")

# deliberately bad first attempt: unfixable lint error, duplicate of parse_rows, failing new test
with open(core, "a") as fh:
    fh.write(textwrap.dedent("""

        def discount(amount, pct):
            unused = 1
            return amount * (1 - pct)


        def parse_rows_again(lines):
            out = []
            for line in lines:
                parts = line.strip().split(",")
                if len(parts) != 2:
                    continue
                out.append({"sku": parts[0], "price": float(parts[1])})
            return out
    """))
with open(tests, "a") as fh:
    fh.write("\n\ndef test_discount():\n    from pricing.core import discount\n    assert discount(100, 0.1) == 91\n")
with open(legacy, "a") as fh:
    fh.write("RATE = 0.1\n")
fb_text = ""
for f in (core, tests, legacy):
    fb = hook(ex, "post-tool", {"tool_name": "Edit", "tool_input": {"file_path": f}})
    fb_text += (fb or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
expect("test_discount" in fb_text or "fail" in fb_text, "targeted tests ran after the edit and reported the failure")

REPORT = textwrap.dedent("""\
    # Add discount helper

    ## What changed
    pricing/core.py gains discount(amount, pct), with a test.

    ## Why
    Checkout needs percentage discounts. A Decimal-based version was rejected for now.

    ## Concept (rounding money)
    Floats cannot represent most decimal fractions exactly, so results are rounded to pence. See core.py discount().
""")
draft = os.path.join(ex, ".cock-cage", "report-draft.md")
out = hook(ex, "stop", {"last_assistant_message": "Step 1 done, continuing next turn."})
expect(out and "decision" not in out and "in progress" in out.get("systemMessage", ""), "mid-task turn end does not block")
open(draft, "w").write(REPORT)
out = hook(ex, "stop", {"last_assistant_message": "Done."})
reason = (out or {}).get("reason", "")
print(textwrap.indent(reason[:900], "    "))
expect(out and out.get("decision") == "block", "finish blocked")
expect("test_discount" in reason, "new failing test blocks")
expect("test_known_broken" not in reason, "pre-existing failure is not blamed on this task")
expect("F841" in reason, "new unfixable lint error blocks")

# second attempt: fixes everything, but 'cheats' by deleting the pre-existing broken test
src = open(core).read().replace("    unused = 1\n", "").replace("return amount * (1 - pct)", "return round(amount * (1 - pct), 2)")
open(core, "w").write(src)
tsrc = open(tests).read().replace("== 91", "== 90.0")
tsrc = tsrc.replace("\n\ndef test_known_broken():\n    assert vat(0.1, 0.0) == 0.2\n", "\n")
open(tests, "w").write(tsrc)

out = hook(ex, "stop", {"last_assistant_message": "Done.", "stop_hook_active": True})
reason = (out or {}).get("reason", "")
print(textwrap.indent(reason, "    "))
expect(out and out.get("decision") == "block" and "removed_tests" in reason and "test_known_broken" in reason,
       "deleting a baseline test blocks")

# third attempt: the removal is documented
open(draft, "w").write(REPORT + "\n## Watch out\nRemoved tests: test_known_broken asserted a wrong VAT value.\n")
out = hook(ex, "stop", {"last_assistant_message": "Done.", "stop_hook_active": True})
msg = (out or {}).get("systemMessage", "")
print("    " + msg)
expect("report #1 logged" in msg and "commit" in msg, "report logged and checkpoint committed")
expect("merged into main" in msg and git(ex, "branch", "--show-current") == "main", "finished work merged into main automatically")
expect("cc/add-discount-helper" not in git(ex, "branch"), "task branch deleted after merge")
ledger_text = open(os.path.join(ex, ".cock-cage", "reports.md")).read()
print(textwrap.indent(ledger_text.split("<!-- cc:entry")[1][:2000], "    | "))
expect("baseline 1 passed, 1 failed" in ledger_text, "evidence compares against the baseline")
expect("removed tests (documented in report)" in ledger_text, "documented test removal recorded")
expect("same-shape functions" in ledger_text or "copy-paste" in ledger_text, "duplicate of parse_rows reported as a warning")
expect("def old( a,b ):" in open(legacy).read(), "legacy file not reformatted wholesale")
expect(sh([PY, CC, "ledger", "verify"], ex, check=False).returncode == 0, "ledger verifies")
expect(git(ex, "log", "-1", "--pretty=%s") == "feat: Add discount helper", "conventional commit message on main (fast-forward)")

# ------------------------------------------------------------------ 4. versioning

print("\n[4] release")
out = sh([PY, CC, "release", "minor"], ex, check=False)
print(textwrap.indent(out.stdout.strip(), "    "))
expect('version = "0.4.0"' in open(os.path.join(ex, "pyproject.toml")).read(), "version bumped 0.3.0 -> 0.4.0")
expect("v0.4.0" in git(ex, "tag"), "tag v0.4.0 created")
cl = open(os.path.join(ex, "CHANGELOG.md")).read()
expect("### Added" in cl and "Add discount helper" in cl, "changelog built from conventional commits")

print("\n%s" % ("ALL E2E CHECKS PASSED" if not FAILS else "%d FAILED: %s" % (len(FAILS), FAILS)))
sys.exit(1 if FAILS else 0)
