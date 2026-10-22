"""Pre-tool guards. Pure functions over (tool_name, tool_input, context) -> list of Findings.

Nothing here touches the network or runs tools, so PreToolUse stays fast.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from . import util

EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}

# Files inside .cock-cage that Claude may write. Everything else there is off limits.
CLAUDE_WRITABLE = ("plan.md", "report-draft.md", "skill-drafts")

# Files Claude must never modify: its own leash.
PROTECTED_CC = ("reports.md", "ledger.seal", "config.json", "state.json", "baseline.json")

TEXT_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".txt", ".csv", ".tsv", ".html", ".css", ".sql", ".sh", ".rs", ".go", ".java", ".c", ".h",
    ".cpp", ".hpp", ".rb", ".php", ".ipynb", ".xml", ".log",
}

SECRET_ALLOWED_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".test.example")
SECRET_BASENAMES = {
    ".env", ".netrc", ".pgpass", ".pypirc", ".npmrc", "credentials.json", "service-account.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}
SECRET_EXTS = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
SECRET_DIR_PARTS = (".ssh", ".aws", ".gnupg", ".kube")
SECRET_NAME_RE = re.compile(r"^(secrets?|credentials?)\.(ya?ml|json|toml|ini)$", re.I)

READONLY_PROGRAMS = {
    "cat", "less", "more", "head", "tail", "grep", "rg", "egrep", "fgrep", "wc", "sha256sum",
    "shasum", "md5", "md5sum", "diff", "file", "stat", "ls", "bat", "jq",
}

WRITE_PROGRAMS = {"tee", "cp", "mv", "rm", "touch", "truncate", "patch", "install", "ln", "dd", "rsync"}

SHELL_WRAPPERS = {"sudo", "env", "time", "nohup", "command", "builtin", "exec", "nice", "caffeinate"}

USER_ONLY_CLI = re.compile(
    r"(cc\.py|cockcage)\b[^;&|\n]*\b(ledger\s+(unlock|reseal|lock)|skill\s+promote|ship|"
    r"levels\s+[a-z_]+\s+(block|warn|report|off)|config\s+source-repo)\b"
)


@dataclass
class Finding:
    check: str          # config level name, e.g. "secrets"
    message: str
    kind: str = "deny"  # deny | ask


@dataclass
class Context:
    root: str
    phase: str = "idle"
    branch: str = ""
    protected_branches: List[str] = field(default_factory=lambda: ["main", "master"])
    large_read_lines: int = 400
    extra_tmp: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- path helpers

def _abs(ctx: Context, p: str) -> str:
    p = os.path.expanduser(p or "")
    return os.path.abspath(p if os.path.isabs(p) else os.path.join(ctx.root, p))


def _cc_rel(ctx: Context, ap: str) -> Optional[str]:
    """Path relative to .cock-cage/ if inside it, else None."""
    base = util.cc_path(ctx.root)
    if util.is_inside(base, ap):
        return os.path.relpath(os.path.realpath(ap), os.path.realpath(base))
    return None


def is_claude_writable_cc(ctx: Context, ap: str) -> bool:
    r = _cc_rel(ctx, ap)
    if r is None:
        return False
    first = r.split(os.sep)[0]
    return first in CLAUDE_WRITABLE


def is_protected(ctx: Context, ap: str) -> Optional[str]:
    """Return a reason if ap is part of the cage itself."""
    r = _cc_rel(ctx, ap)
    if r is not None and r.split(os.sep)[0] in PROTECTED_CC:
        return ".cock-cage/%s is managed by hooks and by you, never by Claude" % r
    claude_dir = os.path.join(ctx.root, ".claude")
    if util.is_inside(claude_dir, ap) and os.path.basename(ap).startswith("settings"):
        return "project Claude settings control the cage itself"
    home_claude = os.path.expanduser("~/.claude")
    if util.is_inside(home_claude, ap) and "/skills/" not in ap.replace(os.sep, "/"):
        return "~/.claude holds your global Claude config and installed plugins"
    return None


def _tmp_roots(ctx: Context) -> List[str]:
    roots = [tempfile.gettempdir(), "/tmp", "/private/tmp", "/private/var/folders", "/var/folders"]
    roots.extend(ctx.extra_tmp)
    return [r for r in roots if r]


def is_scratch(ctx: Context, ap: str) -> bool:
    """Temp locations outside the project. A project that itself lives in /tmp is still the project."""
    if util.is_inside(ctx.root, ap):
        return False
    return any(util.is_inside(r, ap) for r in _tmp_roots(ctx) if os.path.isdir(r))


def is_secret_path(p: str) -> bool:
    norm = p.replace("\\", "/")
    base = os.path.basename(norm.rstrip("/"))
    if not base:
        return False
    if base.lower().endswith(SECRET_ALLOWED_SUFFIXES):
        return False
    if base in SECRET_BASENAMES or base.startswith(".env.") or base.endswith(".env"):
        return True
    if any(base.startswith(n + ".") for n in ("id_rsa", "id_ed25519", "id_ecdsa")) and not base.endswith(".pub"):
        return True
    if base.lower().endswith(SECRET_EXTS):
        return True
    if SECRET_NAME_RE.match(base):
        return True
    parts = norm.split("/")
    return any(part in SECRET_DIR_PARTS for part in parts[:-1]) and not base.endswith(".pub")


# ---------------------------------------------------------------- bash parsing

_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\n|\|(?!\|))\s*")
_REDIR_RE = re.compile(r"(?:^|[^0-9&>])(>>?|&>)\s*([^\s;&|<>]+)")


def segments(cmd: str) -> List[str]:
    return [s for s in _SPLIT_RE.split(cmd or "") if s.strip()]


def argv(segment: str) -> List[str]:
    try:
        toks = shlex.split(segment, posix=True)
    except ValueError:
        toks = segment.split()
    # strip env assignments and wrappers
    while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in SHELL_WRAPPERS):
        if toks[0] in ("env",) and len(toks) > 1 and toks[1].startswith("-"):
            toks = toks[2:]
            continue
        toks = toks[1:]
    return toks


def redirect_targets(segment: str) -> List[str]:
    return [m.group(2) for m in _REDIR_RE.finditer(segment) if m.group(2) not in ("/dev/null", "&1", "&2")]


def _prog(toks: List[str]) -> str:
    return os.path.basename(toks[0]) if toks else ""


def _rm_flags(toks: List[str]) -> str:
    flags = ""
    for t in toks[1:]:
        if t == "--":
            break
        if t in ("--recursive",):
            flags += "r"
        elif t in ("--force",):
            flags += "f"
        elif t.startswith("-") and not t.startswith("--"):
            flags += t[1:]
    return flags.lower()


_DANGEROUS_RM_TARGETS = {"/", "/*", "~", "~/", "~/*", ".", "./", "./*", "..", "../", "*", "$HOME", "${HOME}", "$HOME/"}


def _dangerous_rm(toks: List[str]) -> bool:
    flags = _rm_flags(toks)
    if "r" not in flags or "f" not in flags:
        return False
    targets = [t for t in toks[1:] if not t.startswith("-")]
    for t in targets:
        if t in _DANGEROUS_RM_TARGETS:
            return True
        if re.fullmatch(r"/[^/]+/?", t) or re.fullmatch(r"/(Users|home)/[^/]+/?", t):
            return True
    return False


def _pkg_args(toks: List[str], start: int) -> List[str]:
    out, skip = [], False
    for t in toks[start:]:
        if skip:
            skip = False
            continue
        if t in ("-r", "--requirement", "-c", "--constraint", "--group", "--extra", "--index-url",
                 "-i", "--python", "-p", "--optional"):
            skip = True
            continue
        if t.startswith("-"):
            continue
        if t in (".",) or t.startswith(("./", "../", "/")) or t.endswith((".txt", ".whl", ".tar.gz")):
            continue
        out.append(t)
    return out


def new_dependency(toks: List[str]) -> List[str]:
    """Return package names a command would add, or [] if it installs nothing new."""
    p = _prog(toks)
    low = [t.lower() for t in toks]
    if p == "uv":
        if len(low) > 1 and low[1] == "add":
            return _pkg_args(toks, 2)
        if len(low) > 2 and low[1] == "pip" and low[2] == "install":
            return _pkg_args(toks, 3)
        if len(low) > 2 and low[1] == "tool" and low[2] == "install":
            return _pkg_args(toks, 3)
    if p in ("pip", "pip3") and len(low) > 1 and low[1] == "install":
        return _pkg_args(toks, 2)
    if re.fullmatch(r"python[0-9.]*", p) and low[1:4] == ["-m", "pip", "install"]:
        return _pkg_args(toks, 4)
    if p in ("poetry", "pdm", "rye") and len(low) > 1 and low[1] == "add":
        return _pkg_args(toks, 2)
    if p in ("pipx", "conda", "mamba", "brew") and len(low) > 1 and low[1] == "install":
        return _pkg_args(toks, 2)
    if p in ("npm", "pnpm", "yarn") and len(low) > 1 and low[1] in ("i", "install", "add"):
        return _pkg_args(toks, 2)
    return []


def _git_findings(toks: List[str], ctx: Context) -> List[Finding]:
    out: List[Finding] = []
    low = [t.lower() for t in toks]
    if len(low) < 2:
        return out
    sub = low[1]
    rest = low[2:]
    prot = ctx.protected_branches
    if sub == "push":
        if any(t in ("-f", "--force", "--force-if-includes") or t.startswith("+") for t in rest):
            out.append(Finding("dangerous", "Force-pushing rewrites shared history. Ask the user to do it."))
        elif any(t in prot or t.endswith(":" + b) for t in rest for b in prot):
            out.append(Finding("dangerous", "Pushing to a protected branch is the user's call."))
        else:
            out.append(Finding("dangerous", "Pushing publishes work. Confirm with the user.", "ask"))
    elif sub == "reset" and "--hard" in rest:
        out.append(Finding("dangerous", "git reset --hard discards work irreversibly. Use a new commit or ask the user."))
    elif sub == "clean" and any(t.startswith("-") and "f" in t for t in rest):
        out.append(Finding("dangerous", "git clean -f deletes untracked files irreversibly."))
    elif sub in ("checkout", "restore") and rest and rest[-1] in (".", "--", ":/"):
        out.append(Finding("dangerous", "Discarding all working-tree changes is irreversible."))
    elif sub == "stash" and rest[:1] in (["drop"], ["clear"]):
        out.append(Finding("dangerous", "Dropping stashes loses work."))
    elif sub == "branch" and any(t in ("-d", "-D", "--delete") for t in rest) and any(b in rest for b in prot):
        out.append(Finding("dangerous", "Deleting a protected branch is not allowed."))
    elif sub == "commit" and ctx.branch in prot:
        out.append(Finding(
            "dangerous",
            "You are on '%s'. Commit on a task branch (git switch -c cc/<task>) instead." % ctx.branch,
        ))
    elif sub in ("merge", "rebase") and ctx.branch in prot:
        out.append(Finding("dangerous", "Merging into '%s' is the user's call (cockcage ship)." % ctx.branch))
    elif sub == "filter-branch" or (sub == "update-ref" and "-d" in rest):
        out.append(Finding("dangerous", "History rewriting is not allowed."))
    return out


def bash_findings(cmd: str, ctx: Context) -> List[Finding]:
    out: List[Finding] = []
    if USER_ONLY_CLI.search(cmd or ""):
        out.append(Finding("ledger_protection", "That cock-cage command is reserved for the user's own terminal."))
    for seg in segments(cmd):
        toks = argv(seg)
        prog = _prog(toks)
        raw = seg
        # dangerous commands
        if prog == "rm" and _dangerous_rm(toks):
            out.append(Finding("dangerous", "Recursive force-delete of a root, home or whole-project path."))
        if prog in ("sudo", "doas") or re.search(r"(^|\s)sudo\s", raw):
            out.append(Finding("dangerous", "sudo is never needed for project work."))
        if prog in ("mkfs", "diskutil", "fdisk") or (prog == "dd" and re.search(r"of=/dev/", raw)):
            out.append(Finding("dangerous", "Disk-level commands are blocked."))
        if prog == "chmod" and re.search(r"-R\b.*\b777\b|\b777\b.*-R\b", raw):
            out.append(Finding("dangerous", "chmod -R 777 is never the fix."))
        if prog in ("chflags", "chattr", "xattr"):
            out.append(Finding("ledger_protection", "File flag changes are reserved for the user."))
        if prog == "git":
            out.extend(_git_findings(toks, ctx))
        # secrets
        for t in toks[1:] + redirect_targets(raw):
            if is_secret_path(t):
                out.append(Finding("secrets", "'%s' looks like a secrets file. Ask the user to handle it." % t))
                break
        # new dependencies
        pkgs = new_dependency(toks)
        if pkgs:
            out.append(Finding(
                "new_dependency",
                "This adds dependencies (%s). Explain why each is needed and get the user's OK." % ", ".join(pkgs[:6]),
                "ask",
            ))
        # the cage itself
        targets = toks[1:] + redirect_targets(raw)
        writes = bool(redirect_targets(raw)) or prog in WRITE_PROGRAMS or prog in ("sed", "perl") and "-i" in " ".join(toks)
        for t in targets:
            if t.startswith("-"):
                continue
            ap = _abs(ctx, t)
            reason = is_protected(ctx, ap)
            if not reason and os.path.basename(ap.rstrip("/")) == util.CC_DIR and prog in WRITE_PROGRAMS:
                reason = "the .cock-cage folder holds the ledger and your config"
            readonly = prog in READONLY_PROGRAMS or (
                prog == "git" and len(toks) > 1 and toks[1] in ("log", "show", "diff", "status", "blame")
            )
            if reason and (writes or not readonly):
                out.append(Finding("ledger_protection", "Blocked: %s." % reason))
                break
    # dedupe by message
    seen, uniq = set(), []
    for f in out:
        if f.message not in seen:
            seen.add(f.message)
            uniq.append(f)
    return uniq


def bash_writes_project_files(cmd: str, ctx: Context) -> List[str]:
    """Heuristic: files a shell command would create or modify inside the project."""
    hits: List[str] = []
    for seg in segments(cmd):
        toks = argv(seg)
        prog = _prog(toks)
        cands: List[str] = list(redirect_targets(seg))
        if prog in ("tee", "touch", "truncate"):
            cands += [t for t in toks[1:] if not t.startswith("-")]
        elif prog in ("cp", "mv", "install", "ln", "rsync") and len(toks) > 2:
            cands.append([t for t in toks[1:] if not t.startswith("-")][-1])
        elif prog == "rm":
            cands += [t for t in toks[1:] if not t.startswith("-")]
        elif prog in ("sed", "perl") and any(t.startswith("-i") or t == "-pi" for t in toks):
            cands += [t for t in toks[1:] if not t.startswith("-") and os.path.exists(_abs(ctx, t))]
        elif prog in ("patch",) or (prog == "git" and len(toks) > 1 and toks[1] in ("apply", "am")):
            cands.append(".")
        for c in cands:
            ap = _abs(ctx, c)
            if is_scratch(ctx, ap) or is_claude_writable_cc(ctx, ap):
                continue
            if util.is_inside(ctx.root, ap):
                hits.append(util.rel(ctx.root, ap))
    return hits


# ---------------------------------------------------------------- tool-level entry

def pre_tool_findings(tool: str, tin: Dict[str, Any], ctx: Context) -> List[Finding]:
    out: List[Finding] = []
    if tool in EDIT_TOOLS:
        p = tin.get("file_path") or tin.get("notebook_path") or tin.get("path") or ""
        ap = _abs(ctx, p)
        reason = is_protected(ctx, ap)
        if reason:
            out.append(Finding("ledger_protection", "Blocked: %s." % reason))
        if is_secret_path(ap):
            out.append(Finding("secrets", "'%s' looks like a secrets file. Ask the user to edit it." % util.rel(ctx.root, ap)))
        if not reason and not is_claude_writable_cc(ctx, ap) and not is_scratch(ctx, ap):
            if ctx.phase not in ("approved", "quick"):
                out.append(Finding("plan_gate", _plan_gate_message(ctx)))
    elif tool == "Read":
        p = tin.get("file_path") or tin.get("path") or ""
        ap = _abs(ctx, p)
        if is_secret_path(ap):
            out.append(Finding("secrets", "'%s' looks like a secrets file. Ask the user before reading it." % util.rel(ctx.root, ap)))
        elif not tin.get("limit") and _cc_rel(ctx, ap) is None and os.path.splitext(ap)[1].lower() in TEXT_EXTS:
            n = util.count_lines(ap, ctx.large_read_lines + 1)
            if n > ctx.large_read_lines:
                out.append(Finding(
                    "large_read",
                    "%s has over %d lines. Find the symbol first (.cock-cage/codemap.md, the LSP tool, "
                    "or Grep with -n), then Read with offset and limit."
                    % (util.rel(ctx.root, ap), ctx.large_read_lines),
                ))
    elif tool == "Bash":
        cmd = str(tin.get("command") or "")
        out.extend(bash_findings(cmd, ctx))
        if ctx.phase not in ("approved", "quick"):
            hits = bash_writes_project_files(cmd, ctx)
            if hits:
                out.append(Finding("plan_gate", _plan_gate_message(ctx) + " (shell write to %s)" % ", ".join(hits[:3])))
    return out


def _plan_gate_message(ctx: Context) -> str:
    if ctx.phase == "planning":
        return ("The plan in .cock-cage/plan.md is waiting for the user's approval. "
                "Stop and ask them to approve it, or revise it.")
    return ("No approved plan yet. Write one to .cock-cage/plan.md (see the cock-cage planning skill), "
            "then ask the user to approve it. For trivial changes the user can prefix their message with 'quick:'.")


def worst(findings: Iterable[Finding], levels: Dict[str, str]) -> Optional[Finding]:
    """The finding that should decide the outcome, honouring configured levels."""
    deny = [f for f in findings if levels.get(f.check, "block") == "block" and f.kind == "deny"]
    if deny:
        return deny[0]
    ask = [f for f in findings if levels.get(f.check, "block") == "block" and f.kind == "ask"]
    return ask[0] if ask else None
