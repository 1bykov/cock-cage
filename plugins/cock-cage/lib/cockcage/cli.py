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

from . import PLUGIN_ROOT, approvals, checks, codemap, config, gitops, initproj, ledger, plugin_version, state, util, versioning

MARKETPLACE_REPO = initproj.MARKETPLACE_REPO
SRC_DEFAULT = os.path.join(util.user_dir(), "src")


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
    status = approvals.folder_status(root)
    if approvals.claude_is_calling() and not os.path.isdir(util.cc_path(root)) and status not in ("approved", "set-up"):
        print("Not set up: the user has not agreed to cock-cage in this folder. Ask them first.")
        return 3
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
    approvals.set_folder_status(root, "set-up")
    print("Done." if ok else "Finished with problems; see above.")
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    root = _root(args)
    if not os.path.isdir(util.cc_path(root)):
        print("cock-cage is not set up here. Run: cockcage init (after the user agrees)")
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


# ---------------------------------------------------------------- ledger (unlock, lock and reseal need the user)

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
    if args.action in ("unlock", "lock", "reseal") and approvals.claude_is_calling():
        print("Ledger surgery needs the user's yes. File `cockcage request ledger-delete <n>...` or "
              "`cockcage request ledger-reseal`, ask them, then run `cockcage apply <id>`.")
        return 3
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


# ---------------------------------------------------------------- levels (loosening needs the user)

def cmd_levels(args: argparse.Namespace) -> int:
    root = _root(args)
    raw = util.read_json(config.path(root), {}) or {}
    if args.name:
        if args.name not in config.DEFAULTS["levels"] or args.level not in config.LEVELS:
            print("usage: cockcage levels <check> <block|warn|report|off>; checks: %s" % ", ".join(config.DEFAULTS["levels"]))
            return 2
        old = config.level(config.load(root), args.name)
        if config.STRICTNESS[args.level] < config.STRICTNESS[old] and approvals.claude_is_calling():
            print("Loosening '%s' from %s to %s needs the user's yes. File `cockcage request loosen %s %s "
                  "--reason \"...\"`, ask them, then run `cockcage apply <id>`." % (args.name, old, args.level, args.name, args.level))
            return 3
        raw.setdefault("levels", {})[args.name] = args.level
        util.write_json(config.path(root), raw)
    cfg = config.load(root)
    for k in sorted(cfg["levels"]):
        print("%-20s %s" % (k, cfg["levels"][k]))
    return 0


# ---------------------------------------------------------------- skills

def _source_skills_dir(explicit: Optional[str]) -> Optional[str]:
    src = explicit or util.user_settings().get("source_repo") or (SRC_DEFAULT if os.path.isdir(SRC_DEFAULT) else None)
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
            print("No cock-cage source clone found. Run: cockcage bootstrap")
            return 1
        dest = os.path.join(dest_root, args.name)
        old = util.read_text(os.path.join(dest, "SKILL.md")).splitlines(keepends=True)
        new = util.read_text(os.path.join(src, "SKILL.md")).splitlines(keepends=True)
        sys.stdout.writelines(difflib.unified_diff(old, new, "current/SKILL.md", "draft/SKILL.md"))
        if not (args.yes or approvals.claude_is_calling() or not sys.stdin.isatty()) and \
                input("\nPromote this skill into %s? [y/N] " % dest).strip().lower() != "y":
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
        print("Promoted and committed in %s. Publish it to every project with: cockcage publish patch" % repo)
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
        print("You are on '%s', not a %s task branch." % (br or "a detached HEAD", prefix))
        return 1
    if gitops.git(root, "status", "--porcelain", "--untracked-files=no")[1].strip():
        print("Commit or stash tracked changes first.")
        return 1
    ok, msg = gitops.merge_into_protected(root, br, cfg["git"]["protected_branches"])
    print(("Merged %s into %s and deleted the branch." % (br, msg)) if ok else ("Not merged: %s" % msg))
    return 0 if ok else 1


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


# ---------------------------------------------------------------- chat-approved requests

def cmd_request(args: argparse.Namespace) -> int:
    root = _root(args)
    if args.action == "ledger-delete":
        nums = [int(x.lstrip("#")) for x in args.items if x.lstrip("#").isdigit()]
        if not nums:
            print("usage: cockcage request ledger-delete <n> [<n>...] --reason \"...\"")
            return 2
        payload: Dict[str, Any] = {"entries": nums}
    elif args.action == "loosen":
        if len(args.items) != 2 or args.items[0] not in config.DEFAULTS["levels"] or args.items[1] not in config.LEVELS:
            print("usage: cockcage request loosen <check> <warn|report|off> --reason \"...\"")
            return 2
        old = config.level(config.load(root), args.items[0])
        if config.STRICTNESS[args.items[1]] >= config.STRICTNESS[old]:
            print("That is not a loosening; run `cockcage levels %s %s` directly." % tuple(args.items))
            return 2
        payload = {"check": args.items[0], "old": old, "new": args.items[1]}
    else:
        payload = {}
    if not args.reason.strip():
        print("Give a --reason the user can judge.")
        return 2
    try:
        with state.locked(root) as st:
            req = approvals.create(st, args.action, payload, args.reason)
    except ValueError as exc:
        print("Not filed: %s." % exc)
        return 2
    print("Request %s filed: %s. Reason: %s" % (req["id"], approvals.describe(req), req["reason"]))
    print("Now ask the user in plain words whether to %s, and tell them to reply \"yes %s\" or \"no %s\". "
          "Only a reply containing %s counts. When the hook confirms approval, run: cockcage apply %s"
          % (approvals.describe(req), req["id"], req["id"], req["id"], req["id"]))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    root = _root(args)
    cfg = config.load(root)
    lock_files = bool(cfg.get("ledger", {}).get("os_lock", True))
    try:
        with state.locked(root) as st:
            req = approvals.consume(st, args.id)
    except PermissionError as exc:
        print("Refused: %s." % exc)
        return 3
    if req["action"] == "ledger-delete":
        try:
            n = ledger.delete_entries(root, req["args"]["entries"], lock_files)
        except ValueError as exc:
            print("Nothing deleted: %s" % exc)
            return 1
        print("Deleted %s. The ledger now has %d entries and verifies." % (approvals.describe(req).split(" from")[0], n))
    elif req["action"] == "ledger-unlock":
        ledger.unlock(root)
        print("The ledger's append-only lock is lifted. It returns at the next report or reseal.")
    elif req["action"] == "ledger-reseal":
        n = ledger.reseal(root, lock_files)
        print("Resealed the ledger with %d entries." % n)
    elif req["action"] == "loosen":
        raw = util.read_json(config.path(root), {}) or {}
        raw.setdefault("levels", {})[req["args"]["check"]] = req["args"]["new"]
        util.write_json(config.path(root), raw)
        print("Changed '%s' from %s to %s." % (req["args"]["check"], req["args"]["old"], req["args"]["new"]))
    return 0


# ---------------------------------------------------------------- machine setup and publishing

def _settings_json() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """One-time machine setup, safe to re-run: source clone, marketplace, user-scope install, auto-update, LSP."""
    log: List[str] = []
    src = os.path.expanduser(args.src or SRC_DEFAULT)
    if os.path.isdir(os.path.join(src, ".git")):
        code, out, err = util.run(["git", "-C", src, "pull", "--ff-only", "--quiet"], cwd=src, timeout=120)
        log.append("source clone %s: %s" % (src, "updated" if code == 0 else "pull failed: " + (err or out).strip()[:200]))
    else:
        os.makedirs(os.path.dirname(src), exist_ok=True)
        code, out, err = util.run(["git", "clone", "--quiet", "https://github.com/%s" % MARKETPLACE_REPO, src], cwd=os.path.dirname(src), timeout=300)
        log.append("source clone %s: %s" % (src, "cloned" if code == 0 else "clone failed: " + (err or out).strip()[:200]))
    data = util.user_settings()
    data["source_repo"] = src
    util.write_json(os.path.join(util.user_dir(), "settings.json"), data)

    claude = shutil.which("claude")
    if not claude:
        log.append("claude CLI not on PATH: skipped marketplace and plugin install")
    else:
        code, out, _ = util.run([claude, "plugin", "marketplace", "list"], cwd=src, timeout=60)
        if "cock-cage" not in out:
            code, out, err = util.run([claude, "plugin", "marketplace", "add", MARKETPLACE_REPO], cwd=src, timeout=180)
            log.append("marketplace add: %s" % ("ok" if code == 0 else (err or out).strip()[-200:]))
        else:
            util.run([claude, "plugin", "marketplace", "update", "cock-cage"], cwd=src, timeout=180)
            log.append("marketplace: already registered, refreshed")
        code, out, err = util.run([claude, "plugin", "install", "cock-cage@cock-cage", "--scope", "user"], cwd=src, timeout=180)
        log.append("plugin install (user scope): %s" % ("ok" if code == 0 else (err or out).strip()[-200:]))
    sp = _settings_json()
    settings = util.read_json(sp, {}) or {}
    mk = settings.setdefault("extraKnownMarketplaces", {}).setdefault("cock-cage", {})
    mk.setdefault("source", {"source": "github", "repo": MARKETPLACE_REPO})
    if mk.get("autoUpdate") is not True:
        mk["autoUpdate"] = True
        util.write_json(sp, settings)
        log.append("auto-update: enabled for the cock-cage marketplace")
    if not args.no_lsp:
        if not shutil.which("pyright-langserver") and shutil.which("uv"):
            code, _, err = util.run(["uv", "tool", "install", "pyright"], cwd=src, timeout=300)
            log.append("pyright language server: %s" % ("installed" if code == 0 else err.strip()[-200:]))
        if claude:
            code, _, _ = util.run([claude, "plugin", "marketplace", "update", "claude-plugins-official"], cwd=src, timeout=180)
            if code != 0:
                util.run([claude, "plugin", "marketplace", "add", "anthropics/claude-plugins-official"], cwd=src, timeout=180)
            code, out, err = util.run([claude, "plugin", "install", "pyright-lsp@claude-plugins-official", "--scope", "user"], cwd=src, timeout=180)
            log.append("pyright-lsp plugin: %s" % ("ok" if code == 0 else (err or out).strip()[-200:]))
    for line in log:
        print("  - " + line)
    print("cock-cage is installed for every project on this machine. It loads in new Claude sessions "
          "(or after /reload-plugins), and offers itself once in each project folder.")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    src = _source_skills_dir(None)
    if not src:
        print("No cock-cage source clone found. Run: cockcage bootstrap")
        return 1
    repo = os.path.dirname(os.path.dirname(os.path.dirname(src)))
    util.run(["git", "-C", repo, "pull", "--ff-only", "--quiet"], cwd=repo, timeout=120)
    cmd = [sys.executable, os.path.join(repo, "scripts", "release.py"), args.part, "--push", "--update-local"]
    if args.dry_run:
        cmd = [sys.executable, os.path.join(repo, "scripts", "release.py"), args.part, "--dry-run"]
    code, out, err = util.run(cmd, cwd=repo, timeout=1800)
    print(out.strip())
    if code != 0:
        print(err.strip()[-1500:])
    return code


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

    s = sub.add_parser("request", help="file a request that needs the user's yes in chat")
    s.add_argument("action", choices=["ledger-delete", "ledger-reseal", "ledger-unlock", "loosen"])
    s.add_argument("items", nargs="*")
    s.add_argument("--reason", default="")
    s.add_argument("--root")
    s.set_defaults(fn=cmd_request)

    s = sub.add_parser("apply", help="carry out a request the user approved in chat")
    s.add_argument("id")
    s.add_argument("--root")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("bootstrap", help="one-time machine setup (safe to re-run)")
    s.add_argument("--src", help="where to keep the cock-cage source clone (default ~/.cock-cage/src)")
    s.add_argument("--no-lsp", action="store_true")
    s.set_defaults(fn=cmd_bootstrap)

    s = sub.add_parser("publish", help="release a new cock-cage version to all projects")
    s.add_argument("part", choices=["patch", "minor", "major"])
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("codemap", help="rebuild .cock-cage/codemap.md")
    s.add_argument("--root")
    s.set_defaults(fn=cmd_codemap)

    args = p.parse_args(argv)
    return int(args.fn(args) or 0)
