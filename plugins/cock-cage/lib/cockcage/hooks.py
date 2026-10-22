"""Hook event handlers. Each takes the stdin payload and returns a JSON-able dict (or None)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from . import approvals, checks, codemap, config, gitops, guards, initproj, ledger, plugin_version, report, state, util

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "cc.py")


def _ctx_out(event: str, text: str, system: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    if system:
        out["systemMessage"] = system
    return out


def _active(root: str) -> bool:
    return os.path.isdir(util.cc_path(root))


def _plan_title(root: str) -> str:
    for line in util.read_text(util.cc_path(root, "plan.md")).splitlines():
        m = re.match(r"^\s*#+\s+(?:Plan:\s*)?(.+?)\s*$", line)
        if m:
            return m.group(1)
    return ""


RULES = """cock-cage is active in this project. Follow this loop for any change to files:
1. Understand: start from the code map, the LSP tool, or the cock-cage:explorer subagent. Search for existing helpers before writing new ones. Large files cannot be read whole; locate the symbol, then Read with offset and limit.
2. Plan: write .cock-cage/plan.md (goal, approach, rejected alternative, files, tests, steps) using the cock-cage planning skill, then ask the user to approve it. Edits are blocked until they do. A message starting with "{quick}" skips planning for trivial work.
3. Build in small steps. Reuse before you write. Add or update tests with the code.
4. If the diff exceeds {review} changed lines, run the cock-cage:reviewer subagent before finishing.
5. Finish: write .cock-cage/report-draft.md (sections: What changed, Why, Concept, optional Watch out; at most {words} words; see the cock-cage reporting skill) and show it to the user. The Stop hook then runs tests, ruff and the type checker against the session baseline, adds machine-generated evidence, appends the report to the ledger and commits a checkpoint.
You run every cock-cage command yourself with `.cock-cage/bin/cockcage` (status, release, skill promote, publish, request, apply); the user never needs a terminal. Finished tasks are merged into main automatically. Deleting ledger reports and loosening checks need the user's yes: file `cockcage request ...` and ask (see the cock-cage operating skill).
Never modify .cock-cage/config.json, state.json, reports.md or ledger.seal, or project Claude settings directly."""


# ---------------------------------------------------------------- SessionStart

def spawn_baseline(root: str) -> None:
    try:
        log = open(util.cc_path(root, "baseline.log"), "w")
        subprocess.Popen(
            [sys.executable, SCRIPT, "baseline", root], cwd=root, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True, close_fds=True,
        )
    except OSError as exc:
        util.log_error(root, "spawn_baseline", exc)


OFFER = (
    "cock-cage (the user's guardrails plugin for Claude Code) is installed but not set up in this folder. "
    "In your first reply, before anything else, ask the user in one short sentence whether to set it up here "
    "(it installs dev tools, configures checks and commits only its own files). If they agree, run "
    "`python3 \"{script}\" init` from the project root and summarise the result in a few lines. "
    "If they decline or ignore it, carry on normally and do not ask again."
)


def session_start(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root = util.project_root(payload)
    source = str(payload.get("source", "startup"))
    if not _active(root):
        if source == "startup" and approvals.folder_status(root) is None and approvals.looks_like_project(root):
            approvals.set_folder_status(root, "offered")
            return _ctx_out("SessionStart", OFFER.format(script=SCRIPT))
        return None
    created = initproj.bootstrap(root)
    initproj.write_wrapper(root, SCRIPT)
    notes = config.load_and_migrate_on_disk(root)
    cfg = config.load(root)
    system: List[str] = []
    ctx: List[str] = [RULES.format(quick=cfg["quick_prefix"], review=cfg["review_diff_lines"], words=cfg["report_max_words"])]
    if created:
        ctx.append("cock-cage files were just created here. Run `python3 \"%s\" init` to install the dev tools." % SCRIPT)
    for n in notes:
        system.append("cock-cage: " + n)

    ok, problems = ledger.verify(root)
    if not ok:
        system.append("cock-cage: the report ledger failed verification (%s). If you edited it yourself, tell "
                      "Claude and approve the reseal it asks for." % "; ".join(problems))
        ctx.append("The report ledger failed verification (%s). Do not edit it. Tell the user, and if they say they "
                   "edited it themselves, file `cockcage request ledger-reseal` and ask for their approval." % "; ".join(problems))

    missing = [t for t in ("pytest", "ruff", str(cfg.get("type_checker", "pyright"))) if t != "none"
               and not util.find_tool(root, t)]
    if missing:
        ctx.append("Dev tools missing: %s. Run `python3 \"%s\" init` to install them before building." % (", ".join(missing), SCRIPT))

    try:
        info = codemap.build(root, cfg.get("src_dirs"))
        snippet = codemap.context_snippet(root, int(cfg.get("codemap_context_chars", 6000)))
        if snippet:
            ctx.append(snippet)
        if info["files"] == 0:
            ctx.append("This project has no Python files yet.")
    except Exception as exc:  # map is a convenience, never a blocker
        util.log_error(root, "codemap", exc)

    with state.locked(root) as st:
        task = st["task"]
        if task["phase"] != "idle":
            ctx.append("Current task: '%s' (phase: %s). Touched so far: %s." % (
                task.get("title") or _plan_title(root), task["phase"], ", ".join(task["touched"][:10]) or "nothing"))
            plan = util.read_text(util.cc_path(root, "plan.md"))
            if plan and source in ("compact", "resume", "clear"):
                ctx.append("Plan (.cock-cage/plan.md):\n" + plan[:3000])
        start_baseline = source in ("startup", "clear") or st.get("baseline_status") in ("none", "contaminated")
        if start_baseline and task["phase"] == "idle":
            st["baseline_status"] = "running"
            st["baseline_started"] = util.now_iso()
    if start_baseline and task["phase"] == "idle":
        spawn_baseline(root)
    return _ctx_out("SessionStart", "\n\n".join(ctx), " ".join(system) or None)


# ---------------------------------------------------------------- UserPromptSubmit

def _start_task(root: str, st: Dict[str, Any], phase: str, title: str) -> None:
    task = st["task"]
    task["phase"] = phase
    task["title"] = title or task.get("title", "")
    if not task.get("started_at"):
        task["started_at"] = util.now_iso()
        task["start_commit"] = gitops.head(root)
        task["start_untracked"] = gitops.untracked(root)
        task["dirty_at_start"] = gitops.dirty_paths(root)


def _folder_reply(root: str, prompt: str) -> Optional[Dict[str, Any]]:
    status = approvals.folder_status(root)
    words = list(config.DEFAULTS["approve_words"]) + ["sure", "ok", "okay", "please do", "set it up"]
    pending = status in ("offered", "asked")
    if approvals.asks_for_setup(prompt) or (pending and approvals.is_affirmative(prompt, words)):
        approvals.set_folder_status(root, "approved")
        return _ctx_out("UserPromptSubmit", "cock-cage: the user agreed to set it up. Run `python3 \"%s\" init` "
                        "from the project root now, summarise the result, then continue with their request." % SCRIPT)
    if pending and approvals.is_negative(prompt):
        approvals.set_folder_status(root, "declined")
    return None


def user_prompt(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root = util.project_root(payload)
    prompt = str(payload.get("prompt") or "")
    if not _active(root):
        return _folder_reply(root, prompt)
    cfg = config.load(root)
    words = list(cfg.get("approve_words", []))
    quick = str(cfg.get("quick_prefix", "quick:")).lower()
    plan_exists = os.path.exists(util.cc_path(root, "plan.md"))
    with state.locked(root) as st:
        phase = st["task"]["phase"]
        req = st.get("request")
        if req and not req.get("approved_at"):
            if approvals.is_affirmative(prompt, words):
                approvals.approve_pending(st)
                return _ctx_out("UserPromptSubmit", "cock-cage: the user approved request %s (%s). Run "
                                "`.cock-cage/bin/cockcage apply %s` now." % (req["id"], approvals.describe(req), req["id"]))
            if approvals.is_negative(prompt):
                st["request"] = None
                return _ctx_out("UserPromptSubmit", "cock-cage: the user declined request %s. Drop it." % req["id"])
        if prompt.strip().lower().startswith(quick):
            title = prompt.strip()[len(quick):].strip().splitlines()[0][:72] if prompt.strip()[len(quick):].strip() else "quick change"
            _start_task(root, st, "quick", title)
            br = gitops.ensure_task_branch(root, title, cfg)
            return _ctx_out("UserPromptSubmit", "cock-cage: quick mode for this task. The plan gate is off; "
                            "tests, checks and the report still apply when you finish."
                            + (" Work moved to branch %s." % br if br else ""))
        if plan_exists and phase in ("idle", "planning") and approvals.is_affirmative(prompt, words):
            title = _plan_title(root) or "task"
            _start_task(root, st, "approved", title)
            br = gitops.ensure_task_branch(root, title, cfg)
            msg = "cock-cage: plan approved. Edits are unblocked."
            if br:
                msg += " Work moved to branch %s." % br
            return _ctx_out("UserPromptSubmit", msg)
        if phase == "planning":
            return _ctx_out("UserPromptSubmit", "cock-cage: the plan is awaiting approval. If the user is giving "
                            "feedback, revise .cock-cage/plan.md and ask again.")
        touched = st["task"].get("touched", [])
        if phase in ("approved", "quick") and touched and not os.path.exists(report.draft_path(root)):
            return _ctx_out("UserPromptSubmit", "cock-cage: task '%s' is in progress (%d file%s changed). When it is "
                            "finished, write .cock-cage/report-draft.md so it is checked, committed and merged." % (
                                st["task"].get("title") or "current task", len(touched), "" if len(touched) == 1 else "s"))
    return None


# ---------------------------------------------------------------- PreToolUse

def pre_tool(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root = util.project_root(payload)
    if not _active(root):
        return None
    cfg = config.load(root)
    st = state.load(root)
    tool = str(payload.get("tool_name", ""))
    tin = payload.get("tool_input") or {}
    cmd = str(tin.get("command", "")) if tool == "Bash" else ""
    ctx = guards.Context(
        root=root,
        phase=st["task"]["phase"],
        branch=gitops.branch(root) if "git" in cmd else "",
        protected_branches=list(cfg["git"].get("protected_branches", ["main", "master"])),
        large_read_lines=int(cfg.get("large_read_lines", 400)),
        extra_tmp=[payload["scratchpad_dir"]] if payload.get("scratchpad_dir") else [],
    )
    findings = guards.pre_tool_findings(tool, tin, ctx)
    if tool in guards.EDIT_TOOLS and ctx.phase in ("approved", "quick") and not findings:
        _remember_format_state(root, tin, st)
    if not findings:
        return None
    levels = {f.check: config.level(cfg, f.check) for f in findings}
    decisive = guards.worst(findings, levels)
    soft = [f for f in findings if levels[f.check] == "warn"]
    if soft:
        with state.locked(root) as s2:
            for f in soft:
                note = "%s: %s" % (f.check, f.message)
                if note not in s2["task"]["warnings"]:
                    s2["task"]["warnings"].append(note)
    if decisive is None:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decisive.kind,
        "permissionDecisionReason": "cock-cage (%s): %s" % (decisive.check, decisive.message),
    }}


def _remember_format_state(root: str, tin: Dict[str, Any], st: Dict[str, Any]) -> None:
    """Before the first edit of a .py file, note whether it was already ruff-formatted.

    The Stop hook formats only those files (and new ones), so touching one line of a legacy
    file never reformats the whole thing.
    """
    p = str(tin.get("file_path") or tin.get("notebook_path") or "")
    if not p:
        return
    ap = os.path.abspath(p if os.path.isabs(p) else os.path.join(root, p))
    relp = util.rel(root, ap)
    if relp in st["task"].get("fmt", {}) or not util.is_inside(root, ap):
        return
    if not os.path.exists(ap):
        with state.locked(root) as s2:
            if relp not in s2["task"].setdefault("created", []):
                s2["task"]["created"].append(relp)
    if not relp.endswith(".py"):
        return
    if not os.path.exists(ap):
        formatted = True  # a new file: Claude's own code, format and fix freely
    else:
        ruff = util.find_tool(root, "ruff")
        formatted = bool(ruff) and util.run([ruff, "format", "--check", "--quiet", relp], cwd=root, timeout=20)[0] == 0
    with state.locked(root) as s2:
        s2["task"].setdefault("fmt", {})[relp] = formatted


# ---------------------------------------------------------------- PostToolUse

def _related_test(root: str, relp: str) -> Optional[str]:
    stem = os.path.splitext(os.path.basename(relp))[0]
    if stem.startswith("test_"):
        return relp
    for cand in (os.path.join("tests", "test_%s.py" % stem),
                 os.path.join(os.path.dirname(relp), "test_%s.py" % stem),
                 os.path.join(os.path.dirname(relp), "tests", "test_%s.py" % stem)):
        if os.path.exists(os.path.join(root, cand)):
            return cand
    return None


def post_tool(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root = util.project_root(payload)
    if not _active(root):
        return None
    tool = str(payload.get("tool_name", ""))
    tin = payload.get("tool_input") or {}
    if tool == "Skill":
        _log_skill(root, tin, payload)
        return None
    if tool in ("Agent", "Task"):
        sub = str(tin.get("subagent_type", ""))
        if "reviewer" in sub:
            with state.locked(root) as st:
                st["task"]["reviewed"] = True
        return None
    if tool not in guards.EDIT_TOOLS:
        return None
    p = tin.get("file_path") or tin.get("notebook_path") or ""
    ap = os.path.abspath(p if os.path.isabs(p) else os.path.join(root, p))
    relp = util.rel(root, ap)
    if relp == os.path.join(util.CC_DIR, "plan.md"):
        with state.locked(root) as st:
            if st["task"]["phase"] == "idle":
                st["task"]["phase"] = "planning"
                st["task"]["title"] = _plan_title(root)
        return _ctx_out("PostToolUse", "cock-cage: plan saved. Present it to the user and ask for approval.")
    if not util.is_inside(root, ap) or relp.startswith(util.CC_DIR + os.sep):
        return None
    with state.locked(root) as st:
        if relp not in st["task"]["touched"]:
            st["task"]["touched"].append(relp)
        if st.get("baseline_status") == "running":
            st["baseline_status"] = "contaminated"
    if not relp.endswith(".py"):
        return None
    cfg = config.load(root)
    msgs: List[str] = []
    try:
        codemap.build(root, cfg.get("src_dirs"), changed=[relp])
    except Exception as exc:
        util.log_error(root, "codemap-post", exc)
    ruff = util.find_tool(root, "ruff")
    if ruff and config.level(cfg, "lint") != "off":
        code, out, _ = util.run([ruff, "check", "--no-fix", "--output-format", "concise", "--quiet", relp], cwd=root, timeout=30)
        issues = [l for l in out.splitlines() if l.strip()][:5]
        if issues:
            msgs.append("ruff on %s:\n%s" % (relp, "\n".join(issues)))
    test = _related_test(root, relp)
    pt = checks.pytest_cmd(root)
    if test and config.level(cfg, "tests") != "off" and pt:
        code, out, err = util.run(pt + ["-q", "-x", "-p", "no:cacheprovider", "--no-header", test], cwd=root, timeout=90)
        if code not in (0, 5):
            tail = [l for l in (out or err).splitlines() if l.strip()][-6:]
            msgs.append("tests in %s fail:\n%s" % (test, "\n".join(tail)))
    if msgs:
        return _ctx_out("PostToolUse", "cock-cage feedback (fix now or before finishing):\n" + "\n\n".join(msgs))
    return None


def _log_skill(root: str, tin: Dict[str, Any], payload: Dict[str, Any]) -> None:
    name = str(tin.get("skill") or tin.get("name") or tin.get("command") or "?")
    line = '{"at": "%s", "skill": "%s", "project": "%s"}\n' % (util.now_iso(), name.replace('"', ""), os.path.basename(root))
    for target in (util.cc_path(root, "skill-usage.jsonl"), os.path.join(util.user_dir(), "skill-usage.jsonl")):
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass


# ---------------------------------------------------------------- Stop

def _wait_for_baseline(root: str, limit: int = 180) -> str:
    deadline = time.time() + limit
    while time.time() < deadline:
        s = state.load(root).get("baseline_status", "none")
        if s != "running":
            return s
        time.sleep(2)
    return "running"


def _format_block(blocking: Dict[str, List[str]]) -> str:
    parts = ["cock-cage will not let this task finish yet. Fix these, then finish again:"]
    for k, items in blocking.items():
        parts.append("[%s]" % k)
        parts.extend("  - %s" % i for i in items[:8])
        if len(items) > 8:
            parts.append("  - … %d more" % (len(items) - 8))
    return "\n".join(parts)


def stop(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root = util.project_root(payload)
    if not _active(root):
        # Enforce the one-time offer: if Claude's first reply skipped it, send it back once to ask.
        if approvals.folder_status(root) == "offered":
            approvals.set_folder_status(root, "asked")
            last = str(payload.get("last_assistant_message") or "")
            if not approvals.CC_MENTION_RE.search(last):
                return {"decision": "block", "reason": (
                    "Before finishing, ask the user in one short sentence whether to set up cock-cage (their "
                    "guardrails plugin) in this folder. It installs dev tools, configures checks and commits only "
                    "its own files. Ask once; if they decline, never ask again.")}
        return None
    cfg = config.load(root)
    st = state.load(root)
    task = st["task"]
    touched = [t for t in task.get("touched", [])]
    draft_text = util.read_text(report.draft_path(root))
    if not touched and not draft_text:
        if task.get("phase") == "quick":  # a quick question that changed nothing: the quick task is over
            with state.locked(root) as s2:
                state.reset_task(s2)
        return None
    if not draft_text:
        # Work in progress (multi-step tasks, questions to the user). The task is finalised, checked
        # and committed only once Claude writes the report; until then nothing is blocked.
        n = len(touched)
        return {"systemMessage": "cock-cage: task in progress, %d file%s changed, not reported yet." % (n, "" if n == 1 else "s")}

    giving_up = bool(payload.get("stop_hook_active")) and int(task.get("stop_blocks", 0)) >= int(cfg.get("max_stop_blocks", 3))

    # Tidy before measuring. Safe autofixes only in files Claude created; formatting only where the
    # file was already formatted (so only Claude's lines can change) or is new. Code the task did not
    # write is never rewritten.
    tidy: List[str] = []
    ruff = util.find_tool(root, "ruff")
    created = set(task.get("created", []))
    py_new = [t for t in touched if t.endswith(".py") and t in created and os.path.exists(os.path.join(root, t))]
    py_touched = [t for t in touched if t.endswith(".py") and os.path.exists(os.path.join(root, t))]
    if ruff and py_touched and config.level(cfg, "lint") != "off":
        if py_new:
            def _count() -> int:
                code, out, _ = util.run([ruff, "check", "--output-format", "json", "--exit-zero", "--no-fix"] + py_new, cwd=root, timeout=60)
                try:
                    return len(json.loads(out or "[]"))
                except ValueError:
                    return 0
            before = _count()
            util.run([ruff, "check", "--fix", "--quiet", "--exit-zero"] + py_new, cwd=root, timeout=60)
            fixed = before - _count()
            if fixed > 0:
                tidy.append("ruff auto-fixed %d issue%s in new files" % (fixed, "" if fixed == 1 else "s"))
        fmt_ok = [t for t in py_touched if task.get("fmt", {}).get(t, False)]
        if fmt_ok:
            code, out, _ = util.run([ruff, "format", "--check", "--quiet"] + fmt_ok, cwd=root, timeout=60)
            if code != 0:
                util.run([ruff, "format", "--quiet"] + fmt_ok, cwd=root, timeout=60)
                tidy.append("formatted %s" % ", ".join(fmt_ok[:5]))

    bstatus = _wait_for_baseline(root)
    have_baseline = bstatus == "ready"
    base = st.get("baseline") or {}
    which = [n for n in checks.ALL if config.level(cfg, n) != "off" or (n == "tests" and config.level(cfg, "coverage") != "off")]
    cur = checks.run_all(root, cfg, which)
    new = checks.compare(base, cur, touched, have_baseline)

    blocking: Dict[str, List[str]] = {}
    warnings: Dict[str, List[str]] = {}
    noted: Dict[str, List[str]] = {}
    for name, items in new.items():
        lv = config.level(cfg, name)
        if lv == "block" and not have_baseline and name == "tests":
            lv = "warn"  # cannot attribute failures without a baseline
        if name in ("lint", "types", "dead_code"):
            items = [i for i in items if i.split(":", 1)[0] in touched]  # never blame the task for other files
            if not items:
                continue
        {"block": blocking, "warn": warnings, "report": noted}.get(lv, {}).setdefault(name, []).extend(items)

    if "removed_tests" in blocking:
        gone = blocking.pop("removed_tests")
        if re.search(r"(?im)^\s*[-*]?\s*removed tests?\s*:", draft_text):
            warnings.setdefault("removed tests (documented in report)", []).extend(gone)
        else:
            blocking["removed_tests"] = [
                "%d test%s that existed at the baseline no longer run%s. Restore %s, or if removing %s is "
                "intended, add a line 'Removed tests: <reason>' under Watch out in the report."
                % (len(gone), "" if len(gone) == 1 else "s", "s" if len(gone) == 1 else "",
                   "it" if len(gone) == 1 else "them", "it" if len(gone) == 1 else "them")
            ] + gone

    changed = gitops.changed_line_total(root, task.get("start_commit", ""), touched)
    if config.level(cfg, "review_large_diffs") == "block" and changed > int(cfg.get("review_diff_lines", 80)) and not task.get("reviewed"):
        blocking["review_large_diffs"] = ["The diff is %d changed lines. Run the cock-cage:reviewer subagent on it, act on its findings, then finish." % changed]

    if config.level(cfg, "report") != "off":
        problems = report.validate(draft_text, int(cfg.get("report_max_words", 220))) if draft_text else [
            "No report. Write .cock-cage/report-draft.md using this template, show it to the user, then finish:\n" + report.TEMPLATE]
        if problems:
            (blocking if config.level(cfg, "report") == "block" else warnings).setdefault("report", []).extend(problems)

    tidy = list(task.get("tidy", [])) + tidy  # keep notes from earlier blocked attempts
    if blocking and not giving_up:
        with state.locked(root) as s2:
            s2["task"]["stop_blocks"] = int(s2["task"].get("stop_blocks", 0)) + 1
            s2["task"]["tidy"] = tidy
        return {"decision": "block", "reason": _format_block(blocking)}

    if blocking and giving_up:
        for k, v in blocking.items():
            warnings.setdefault("UNRESOLVED " + k, []).extend(v)
        if not draft_text:
            return {"systemMessage": "cock-cage gave up after %d attempts. Unresolved: %s. No report was logged." % (
                task.get("stop_blocks"), ", ".join(blocking))}

    # Guard-level warnings collected during the task
    if task.get("warnings"):
        warnings.setdefault("guards", []).extend(task["warnings"])
    audit = st.get("audit") or {}
    if audit.get("ran") and audit.get("items") and config.level(cfg, "vulns") != "off":
        noted["vulnerable dependencies"] = list(audit["items"])

    # Checkpoint commit (only files Claude wrote, never ones the user had uncommitted work in), then ledger.
    dirty_before = set(task.get("dirty_at_start", []))
    held_back = sorted(t for t in touched if t in dirty_before and t not in created)
    if held_back:
        warnings.setdefault("left uncommitted", []).append(
            "%s had your uncommitted changes before this task, so it was not committed; Claude's edits sit alongside yours"
            % ", ".join(held_back))
    commit_paths = gitops.committable(root, [t for t in touched if t not in held_back])
    title_guess = report.parse(draft_text)[0] or task.get("title") or _plan_title(root) or "task"
    sha = None
    if cfg["git"].get("auto_checkpoint", True):
        sha = gitops.checkpoint(root, commit_paths, title_guess,
                                "Report #%d in .cock-cage/reports.md" % (ledger.entry_count(root) + 1),
                                cfg["git"].get("protected_branches", ["main", "master"]))
    evidence = ["files: " + ("; ".join(gitops.diff_stat(root, task.get("start_commit", ""), commit_paths)) or ", ".join(commit_paths))]
    evidence.extend(checks.summary_line(cur, base, have_baseline))
    evidence.extend(tidy)
    if not have_baseline:
        evidence.append("baseline: %s, so findings could not all be attributed to this task" % bstatus)
    task_branch = gitops.branch(root)
    merged = ""
    if sha and not giving_up and cfg["git"].get("auto_ship", True) and task_branch.startswith(cfg["git"].get("branch_prefix", "cc/")):
        ok, merged = gitops.merge_into_protected(root, task_branch, cfg["git"].get("protected_branches", ["main", "master"]))
        if not ok:
            warnings.setdefault("merge", []).append(merged)
            merged = "no (kept %s)" % task_branch
    meta = {"branch": task_branch, "commit": sha or "", "merged": merged, "mode": task.get("phase", ""),
            "cock-cage": plugin_version()}
    title, body = report.compose(draft_text or "# %s\n" % title_guess, title_guess, meta, evidence, warnings, noted)
    if giving_up:
        title = "[unresolved] " + title
    n = ledger.append(root, title, body, bool(cfg.get("ledger", {}).get("os_lock", True)))

    # Archive the plan, clear the draft, and roll the baseline forward to the state just measured.
    plan = util.cc_path(root, "plan.md")
    if os.path.exists(plan):
        os.makedirs(util.cc_path(root, "plans"), exist_ok=True)
        os.replace(plan, util.cc_path(root, "plans", "%03d-%s.md" % (n, gitops.slugify(title))))
    if os.path.exists(report.draft_path(root)):
        os.unlink(report.draft_path(root))
    with state.locked(root) as s2:
        state.reset_task(s2)
        s2["baseline"] = {k: v for k, v in cur.items() if k != "at"}
        s2["baseline"]["at"] = cur.get("at")
        s2["baseline_status"] = "ready"
    n_warn = sum(len(v) for v in warnings.values())
    msg = "cock-cage: report #%d logged%s%s. %s%s" % (
        n, " · commit %s" % sha if sha else "", " · merged into %s" % merged if merged and not merged.startswith("no") else "",
        "; ".join(checks.summary_line(cur, base, have_baseline)[:2]),
        " · %d warning%s" % (n_warn, "" if n_warn == 1 else "s") if n_warn else "")
    return {"systemMessage": msg}


HANDLERS = {
    "session-start": session_start,
    "user-prompt": user_prompt,
    "pre-tool": pre_tool,
    "post-tool": post_tool,
    "stop": stop,
}
