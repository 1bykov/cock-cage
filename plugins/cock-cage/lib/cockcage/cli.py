"""Command line: `cockcage <command>` (alias to scripts/cc.py) and the hook entry point."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional

from . import PLUGIN_ROOT, checks, codemap, config, gitops, initproj, ledger, plugin_version, state, util, versioning


def _root(args: argparse.Namespace) -> str:
    return os.path.abspath(getattr(args, "root", None) or os.getcwd())


# ---------------------------------------------------------------- hook

def cmd_hook(args: argparse.Namespace) -> int:
    from . import hooks
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    root = util.project_root(payload)
    try:
        result = hooks.HANDLERS[args.event](payload)
    except Exception as exc:  # fail open: a cock-cage bug must never brick the session
        util.log_error(root, "hook " + args.event, exc)
        return 0
    if result:
        sys.stdout.write(json.dumps(result))
    return 0


# ---------------------------------------------------------------- baseline (spawned by SessionStart)

def cmd_baseline(args: argparse.Namespace) -> int:
    root = _root(args)
    cfg = config.load(root)
    which = [n for n in checks.ALL if config.level(cfg, n) != "off"]
    res = checks.run_all(root, cfg, which)
    audit = None
    st = state.load(root)
    last = st.get("last_audit") or ""
    if config.level(cfg, "vulns") != "off" and (not last or last[:10] != util.now_iso()[:10]):
        audit = checks.run_audit(root)
    with state.locked(root) as s:
        if s.get("baseline_status") == "running":
            s["baseline"] = res
            s["baseline_status"] = "ready"
        if audit is not None:
            s["audit"] = audit
            s["last_audit"] = util.now_iso()
    print("baseline done:", json.dumps({k: (v.get("ran") if isinstance(v, dict) else v) for k, v in res.items()}))
    return 0


# ---------------------------------------------------------------- init / status

def cmd_init(args: argparse.Namespace) -> int:
    root = _root(args)
    print("cock-cage %s: setting up %s" % (plugin_version(), root))
    ok, log = initproj.init(root, pm=args.pm, tools=not args.no_tools, commit=not args.no_commit,
                            type_checker=args.type_checker)
    for line in log:
        print("  - " + line)
    if not args.no_baseline:
        print("Measuring the baseline (tests, lint, types)…")
        with state.locked(root) as s:
            s["baseline_status"] = "running"
        cmd_baseline(argparse.Namespace(root=root))
        b = state.load(root).get("baseline") or {}
        for line in checks.summary_line(b, {}, False):
            print("  - " + line)
    print("Done." if ok else "Finished with problems; see above.")
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    root = _root(args)
    if not os.path.isdir(util.cc_path(root)):
        print("cock-cage is not set up here. Run: cockcage init")
        return 1
    cfg = config.load(root)
    st = state.load(root)
    t = st["task"]
    ok, problems = ledger.verify(root)
    print("cock-cage %s (config schema %s)" % (plugin_version(), cfg.get("schema")))
    print("task: %s%s" % (t["phase"], ": %s" % t["title"] if t.get("title") else ""))
    if t.get("touched"):
        print("touched: %s" % ", ".join(t["touched"]))
    print("baseline: %s" % st.get("baseline_status"))
    print("ledger: %d entries, %s" % (ledger.entry_count(root), "verified" if ok else "BROKEN: " + "; ".join(problems)))
    tools = ["pytest", "ruff", cfg.get("type_checker", "pyright"), "vulture", "deptry", "pip-audit"]
    print("tools: " + ", ".join("%s %s" % (x, "ok" if util.find_tool(root, x) else "missing") for x in tools if x != "none"))
    print("branch: %s" % (gitops.branch(root) or "(no git)"))
    return 0


# ---------------------------------------------------------------- ledger (user only)

def cmd_ledger(args: argparse.Namespace) -> int:
    root = _root(args)
    cfg = config.load(root)
    if args.action == "verify":
        ok, problems = ledger.verify(root)
        print("ledger verified (%d entries)" % ledger.entry_count(root) if ok else "ledger BROKEN: " + "; ".join(problems))
        return 0 if ok else 1
    if args.action == "show":
        print(ledger.last_entry(root) or "no entries yet")
        return 0
    if args.action == "unlock":
        ledger.unlock(root)
        print("Unlocked. Edit .cock-cage/reports.md, then run: cockcage ledger reseal")
        return 0
    if args.action == "lock":
        print("locked" if ledger.lock(root) else "nothing to lock (macOS only)")
        return 0
    if args.action == "reseal":
        n = ledger.reseal(root, bool(cfg.get("ledger", {}).get("os_lock", True)))
        print("Resealed %d entries." % n)
        return 0
    return 2


# ---------------------------------------------------------------- levels (user only)

def cmd_levels(args: argparse.Namespace) -> int:
    root = _root(args)
    raw = util.read_json(config.path(root), {}) or {}
    if args.name:
        if args.name not in config.DEFAULTS["levels"] or args.level not in config.LEVELS:
            print("usage: cockcage levels <check> <block|warn|report|off>; checks: %s" % ", ".join(config.DEFAULTS["levels"]))
            return 2
        raw.setdefault("levels", {})[args.name] = args.level
        util.write_json(config.path(root), raw)
    cfg = config.load(root)
    for k in sorted(cfg["levels"]):
        print("%-20s %s" % (k, cfg["levels"][k]))
    return 0


# ---------------------------------------------------------------- skills

def _source_skills_dir(explicit: Optional[str]) -> Optional[str]:
    src = explicit or util.user_settings().get("source_repo")
    if not src:
        return None
    d = os.path.join(os.path.expanduser(src), "plugins", "cock-cage", "skills")
    return d if os.path.isdir(d) else None


def cmd_skill(args: argparse.Namespace) -> int:
    root = _root(args)
    drafts = util.cc_path(root, "skill-drafts")
    if args.action == "list":
        print("installed skills (v%s):" % plugin_version())
        for d in sorted(os.listdir(os.path.join(PLUGIN_ROOT, "skills"))):
            print("  - " + d)
        if os.path.isdir(drafts) and os.listdir(drafts):
            print("drafts awaiting your review:")
            for d in sorted(os.listdir(drafts)):
                print("  - %s  (cockcage skill promote %s)" % (d, d))
        return 0
    if args.action == "usage":
        counts: Dict[str, int] = {}
        for line in util.read_text(os.path.join(util.user_dir(), "skill-usage.jsonl")).splitlines():
            try:
                counts[json.loads(line)["skill"]] = counts.get(json.loads(line)["skill"], 0) + 1
            except (ValueError, KeyError):
                continue
        for name in sorted(os.listdir(os.path.join(PLUGIN_ROOT, "skills"))):
            counts.setdefault("cock-cage:" + name, 0)
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print("%5d  %s" % (n, name))
        return 0
    if args.action == "promote":
        if not args.name or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", args.name):
            print("usage: cockcage skill promote <kebab-case-name>")
            return 2
        src = os.path.join(drafts, args.name)
        if not os.path.isfile(os.path.join(src, "SKILL.md")):
            print("no draft at %s/SKILL.md" % src)
            return 1
        dest_root = _source_skills_dir(args.source)
        if not dest_root:
            print("Set your local clone first: cockcage config source-repo ~/path/to/cock-cage")
            return 1
        dest = os.path.join(dest_root, args.name)
        old = util.read_text(os.path.join(dest, "SKILL.md")).splitlines(keepends=True)
        new = util.read_text(os.path.join(src, "SKILL.md")).splitlines(keepends=True)
        sys.stdout.writelines(difflib.unified_diff(old, new, "current/SKILL.md", "draft/SKILL.md"))
        if not args.yes and input("\nPromote this skill into %s? [y/N] " % dest).strip().lower() != "y":
            print("Not promoted.")
            return 1
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        repo = os.path.dirname(os.path.dirname(os.path.dirname(dest_root)))
        gitops.git(repo, "add", "--", dest)
        verb = "update" if old else "add"
        util.run(["git", "commit", "-q", "-m", "feat(skills): %s %s" % (verb, args.name), "--", dest], cwd=repo)
        shutil.rmtree(src)
        print("Promoted and committed in %s.\nRelease it to your projects with: python3 scripts/release.py patch --push" % repo)
        return 0
    return 2


def cmd_config(args: argparse.Namespace) -> int:
    path = os.path.join(util.user_dir(), "settings.json")
    data = util.user_settings()
    if args.key == "source-repo":
        p = os.path.abspath(os.path.expanduser(args.value))
        if not os.path.isdir(os.path.join(p, "plugins", "cock-cage")):
            print("%s does not look like a cock-cage clone" % p)
            return 1
        data["source_repo"] = p
        util.write_json(path, data)
        print("source repo set to %s" % p)
        return 0
    print(json.dumps(data, indent=2))
    return 0


# ---------------------------------------------------------------- ship / release

def cmd_ship(args: argparse.Namespace) -> int:
    root = _root(args)
    cfg = config.load(root)
    br = gitops.branch(root)
    prefix = cfg["git"].get("branch_prefix", "cc/")
    if not br.startswith(prefix):
        print("You are on '%s', not a %s task branch." % (br, prefix))
        return 1
    if gitops.git(root, "status", "--porcelain", "--untracked-files=no")[1].strip():
        print("Commit or stash tracked changes first.")
        return 1
    target = next((b for b in cfg["git"]["protected_branches"]
                   if gitops.git(root, "rev-parse", "--verify", "--quiet", "refs/heads/" + b)[0] == 0), None)
    if not target:
        print("No protected branch (main/master) found to merge into.")
        return 1
    gitops.git(root, "switch", target)
    if args.squash:
        code, out, err = gitops.git(root, "merge", "--squash", br)
        if code == 0:
            subj = gitops.git(root, "log", "-1", "--pretty=%s", br)[1].strip()
            code, out, err = util.run(["git"] + gitops._id_args(root) + ["commit", "-q", "-m", subj], cwd=root)
    else:
        code, out, err = util.run(["git"] + gitops._id_args(root) + ["merge", "--no-ff", "-q", "-m", "Merge %s" % br, br], cwd=root)
    if code != 0:
        print("Merge failed, nothing deleted:\n" + (err or out))
        return 1
    gitops.git(root, "branch", "-D" if args.squash else "-d", br)
    print("Merged %s into %s and deleted the branch." % (br, target))
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    root = _root(args)
    try:
        for line in versioning.release(root, args.part, args.dry_run):
            print(line)
    except (ValueError, RuntimeError) as exc:
        print("release failed: %s" % exc)
        return 1
    return 0


def cmd_codemap(args: argparse.Namespace) -> int:
    root = _root(args)
    info = codemap.build(root, config.load(root).get("src_dirs"))
    print("codemap: %d files, %d symbols -> .cock-cage/codemap.md" % (info["files"], info["symbols"]))
    return 0


# ---------------------------------------------------------------- parser

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="cockcage", description="cock-cage %s" % plugin_version())
    p.add_argument("--version", action="version", version=plugin_version())
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("hook", help=argparse.SUPPRESS)
    s.add_argument("event", choices=["session-start", "user-prompt", "pre-tool", "post-tool", "stop"])
    s.set_defaults(fn=cmd_hook)

    s = sub.add_parser("baseline", help=argparse.SUPPRESS)
    s.add_argument("root", nargs="?")
    s.set_defaults(fn=cmd_baseline)

    s = sub.add_parser("init", help="set up cock-cage in a project (empty or existing)")
    s.add_argument("root", nargs="?")
    s.add_argument("--pm", choices=["auto", "uv", "pip"], default="auto")
    s.add_argument("--type-checker", choices=["pyright", "mypy", "ty", "none"])
    s.add_argument("--no-tools", action="store_true", help="skip installing dev tools")
    s.add_argument("--no-commit", action="store_true", help="do not commit the setup")
    s.add_argument("--no-baseline", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("status", help="show task, baseline, ledger and tool status")
    s.add_argument("root", nargs="?")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("ledger", help="verify, show, unlock, lock or reseal the report ledger")
    s.add_argument("action", choices=["verify", "show", "unlock", "lock", "reseal"])
    s.add_argument("--root")
    s.set_defaults(fn=cmd_ledger)

    s = sub.add_parser("levels", help="show or set enforcement levels")
    s.add_argument("name", nargs="?")
    s.add_argument("level", nargs="?")
    s.add_argument("--root")
    s.set_defaults(fn=cmd_levels)

    s = sub.add_parser("skill", help="list skills, review usage, promote drafts into your library")
    s.add_argument("action", choices=["list", "usage", "promote"])
    s.add_argument("name", nargs="?")
    s.add_argument("--source", help="path to your local cock-cage clone")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--root")
    s.set_defaults(fn=cmd_skill)

    s = sub.add_parser("config", help="per-user settings, e.g. config source-repo ~/code/cock-cage")
    s.add_argument("key", nargs="?", choices=["source-repo"])
    s.add_argument("value", nargs="?")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("ship", help="merge the current cc/ task branch into main")
    s.add_argument("--squash", action="store_true")
    s.add_argument("--root")
    s.set_defaults(fn=cmd_ship)

    s = sub.add_parser("release", help="bump the project version, update CHANGELOG.md, commit and tag")
    s.add_argument("part", choices=["patch", "minor", "major"])
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--root")
    s.set_defaults(fn=cmd_release)

    s = sub.add_parser("codemap", help="rebuild .cock-cage/codemap.md")
    s.add_argument("--root")
    s.set_defaults(fn=cmd_codemap)

    args = p.parse_args(argv)
    return int(args.fn(args) or 0)
