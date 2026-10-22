"""Pre-tool guards. Pure functions over (tool_name, tool_input, context) -> list of Findings.

Nothing here touches the network or runs tools, so PreToolUse stays fast.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

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

SHELL_WRAPPERS = {"env", "time", "nohup", "command", "builtin", "exec", "nice", "caffeinate", "xargs"}

# Raw ledger surgery has no approval path; Claude uses `cockcage request` + `cockcage apply` instead,
# which the CLI only honours after the user's yes in chat.
USER_ONLY_CLI = re.compile(r"(cc\.py|cockcage)\b[^;&|\n]*\bledger\s+(unlock|reseal|lock)\b")


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
    udir = util.user_dir()
    if util.is_inside(udir, ap):
        relu = os.path.relpath(os.path.realpath(ap), os.path.realpath(udir)).replace(os.sep, "/")
        if relu in ("folders.json", "settings.json") or relu.startswith("ledgers"):
            return "cock-cage's approval records live there"
    home_claude = os.path.expanduser("~/.claude")
    if util.is_inside(home_claude, ap):
        relh = os.path.relpath(os.path.realpath(ap), os.path.realpath(home_claude)).replace(os.sep, "/")
        if relh.startswith("settings") or relh.startswith("plugins/") or relh == "plugins":
            return "~/.claude settings and installed plugins control the cage itself"
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

CONTROL_OPS = {";", "&&", "||", "|", "&", "|&", ";;", "\n", "(", ")"}
REDIRECT_OPS = {">", ">>", "&>", ">|", "&>>"}
SHELLS = {"bash", "sh", "zsh", "dash"}


def _tokens(cmd: str) -> List[str]:
    lx = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>()\n")
    lx.whitespace = " \t\r"
    lx.whitespace_split = True
    return list(lx)


def _strip_wrappers(toks: List[str]) -> List[str]:
    while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in SHELL_WRAPPERS):
        if toks[0] == "env" and len(toks) > 1 and toks[1].startswith("-"):
            toks = toks[2:]
            continue
        toks = toks[1:]
    return toks


def parse_commands(cmd: str, depth: int = 0) -> List[Tuple[List[str], List[str]]]:
    """Split a shell command line into simple commands: (argv, redirect targets).

    Quote-aware, so '>' or '|' inside quotes is data, not an operator. Heredoc bodies are data
    too. `bash -c "..."`, `bash -lc '...'` and `eval '...'` are parsed recursively so wrapping a
    command does not hide it.
    """
    try:
        toks = _tokens(cmd or "")
    except ValueError:  # unbalanced quotes: fall back to a crude split
        toks = (cmd or "").split()
    out: List[Tuple[List[str], List[str]]] = []
    argv_: List[str] = []
    redirs: List[str] = []
    heredocs: List[str] = []
    i = 0
    while i <= len(toks):
        t = toks[i] if i < len(toks) else ";"
        if t in CONTROL_OPS or re.fullmatch(r"[;&|()\n]+", t):
            if argv_ or redirs:
                a = _strip_wrappers(argv_)
                out.append((a, redirs))
                if depth < 3 and a:
                    prog = os.path.basename(a[0])
                    inner = None
                    if prog in SHELLS:
                        for k, arg in enumerate(a[1:], 1):
                            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg) and k + 1 < len(a):
                                inner = a[k + 1]
                                break
                    elif prog == "eval" and len(a) > 1:
                        inner = " ".join(a[1:])
                    if inner:
                        out.extend(parse_commands(inner, depth + 1))
            argv_, redirs = [], []
            if "\n" in t and heredocs:
                delim = heredocs.pop(0)
                i = _skip_body(toks, i, delim)
                heredocs = []
                continue
        elif t in ("<<", "<<-"):
            if i + 1 < len(toks):
                heredocs.append(toks[i + 1])
            i += 1
        elif t == "<<<":
            i += 1  # here-string: the next token is data
        elif t in REDIRECT_OPS or re.fullmatch(r"[<>&]+", t):
            if argv_ and argv_[-1].isdigit():
                argv_.pop()  # an fd number like the 2 in 2>file
            target = toks[i + 1] if i + 1 < len(toks) else ""
            fd_dup = t.endswith("&") and (target.isdigit() or target == "-")
            if ">" in t and target and not fd_dup and target not in ("/dev/null", "&1", "&2") and not target.startswith("&"):
                redirs.append(target)
            i += 1  # skip the target (also for < input redirection)
        else:
            argv_.append(t)
        i += 1
    return out


def _skip_body(toks: List[str], i: int, delim: str) -> int:
    """toks[i] is the newline ending the heredoc's command line; return the index after the body."""
    j = i + 1
    line_start = True
    while j < len(toks):
        if line_start and toks[j] == delim and (j + 1 >= len(toks) or toks[j + 1].startswith("\n")):
            return j + 1
        line_start = toks[j].endswith("\n")
        j += 1
    return len(toks)


def argv(segment: str) -> List[str]:
    cmds = parse_commands(segment)
    return cmds[0][0] if cmds else []


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


GIT_OPTS_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}


def _git_findings(toks: List[str], ctx: Context) -> List[Finding]:
    out: List[Finding] = []
    low = [t.lower() for t in toks]
    k = 1
    while k < len(low) and low[k].startswith("-"):  # global options before the subcommand
        k += 2 if (low[k] in GIT_OPTS_WITH_VALUE and "=" not in low[k]) else 1
    if k >= len(low):
        return out
    sub = low[k]
    rest = low[k + 1:]
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
    elif sub in ("checkout", "restore") and rest and rest[-1] in (".", "--", ":/") and not (
            sub == "restore" and "--staged" in rest and "--worktree" not in rest and "-w" not in rest):
        out.append(Finding("dangerous", "Discarding all working-tree changes is irreversible."))
    elif sub in ("switch", "checkout") and any(t in ("-f", "--force", "--discard-changes") for t in rest):
        out.append(Finding("dangerous", "Force-switching discards local changes."))
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


TAMPER_PATTERNS = (
    (re.compile(r"(cc\.py|cockcage)\b[^\n]*\bhook\b"), "cock-cage hooks are run by Claude Code itself, never by hand."),
    (re.compile(r"\benv\b[^\n]*\s-u\s*CLAUDE|\bunset\s+CLAUDE|(^|[\s;&|])CLAUDE(CODE|_CODE_ENTRYPOINT)="),
     "Hiding that Claude is running a command is not allowed."),
    (re.compile(r"\bscript\b[^\n]*(cc\.py|cockcage)"), "Faking a terminal for cock-cage is not allowed."),
)
PROTECTED_TEXT = re.compile(
    r"\.cock-cage/(reports\.md|ledger\.seal|config\.json|state\.json)|\.cock-cage/(folders\.json|ledgers)\b|"
    r"(^|[\s'\"/])(ledger\.seal|folders\.json)\b")


def bash_findings(cmd: str, ctx: Context) -> List[Finding]:
    out: List[Finding] = []
    if USER_ONLY_CLI.search(cmd or ""):
        out.append(Finding("ledger_protection", "That cock-cage command is reserved for the user's own terminal."))
    for pat, msg in TAMPER_PATTERNS:
        if pat.search(cmd or ""):
            out.append(Finding("ledger_protection", msg))
    if PROTECTED_TEXT.search(cmd or ""):
        cmds = parse_commands(cmd)
        readonly_only = all(
            (_prog(t) in READONLY_PROGRAMS or (_prog(t) == "git" and len(t) > 1 and t[1] in ("log", "show", "diff", "status")))
            and not r for t, r in cmds) and "<<" not in cmd
        if not readonly_only:
            out.append(Finding("ledger_protection", "Commands that touch cock-cage's ledger, config, state or approval "
                                                    "records are blocked; use the cockcage CLI."))
    for toks, redirs in parse_commands(cmd):
        prog = _prog(toks)
        raw = " ".join(toks)
        # dangerous commands
        if prog == "rm" and _dangerous_rm(toks):
            out.append(Finding("dangerous", "Recursive force-delete of a root, home or whole-project path."))
        if prog in ("sudo", "doas"):
            out.append(Finding("dangerous", "sudo is never needed for project work."))
        if prog in ("mkfs", "diskutil", "fdisk") or (prog == "dd" and re.search(r"of=/dev/", raw)):
            out.append(Finding("dangerous", "Disk-level commands are blocked."))
        if prog == "chmod" and re.search(r"-R\b.*\b777\b|\b777\b.*-R\b", raw):
            out.append(Finding("dangerous", "chmod -R 777 is never the fix."))
        if prog == "find" and "-delete" in toks and not any(t in toks for t in ("-name", "-iname", "-path", "-regex", "-newer")):
            out.append(Finding("dangerous", "find -delete without a name or path filter deletes everything it walks."))
        if prog in ("chflags", "chattr", "xattr"):
            out.append(Finding("ledger_protection", "File flag changes are reserved for the user."))
        if prog == "git":
            out.extend(_git_findings(toks, ctx))
        # secrets
        for t in toks[1:] + redirs:
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
        targets = toks[1:] + redirs
        writes = bool(redirs) or prog in WRITE_PROGRAMS or prog in ("sed", "perl") and "-i" in " ".join(toks)
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
    for toks, redirs in parse_commands(cmd):
        prog = _prog(toks)
        cands: List[str] = list(redirs)
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
