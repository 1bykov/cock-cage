"""Chat approvals.

Two flows let Claude act on things that need the user's word, without the user touching a terminal:

  folder offers   In a folder without cock-cage, Claude asks once whether to set it up.
                  The user's reply (seen only by the UserPromptSubmit hook) is recorded per folder.
  requests        For sensitive actions (deleting ledger reports, loosening enforcement), Claude files
                  a request with the CLI and asks. Only the UserPromptSubmit hook can mark it approved,
                  and the CLI refuses to apply a request that is not approved and fresh.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from typing import Any, Dict, List, Optional

from . import util

REQUEST_TTL_SECONDS = 60 * 60
NEGATIVE_RE = re.compile(r"^\s*(no|nope|nah|not now|not yet|don't|do not|skip|never|cancel|leave it)\b", re.I)
HEDGE_RE = re.compile(r"\b(but|except|instead|however|change|don't|do not|not yet|wait)\b", re.I)
CC_MENTION_RE = re.compile(r"\bcock[\s-]?cage\b|\bcockcage\b", re.I)
SETUP_RE = re.compile(r"\b(set ?up|install|init(ialise|ialize)?|enable|add|start)\b", re.I)


FILLERS = {"please", "thanks", "thank", "you", "cheers", "mate", "then", "now", "right"}
AFFIRM_ONLY = {"yes", "yep", "yeah", "yup", "sure", "ok", "okay", "aye", "fine", "great", "perfect", "nice", "cool"}
PHRASES = {
    "approve", "approved", "lgtm", "go", "go ahead", "go for it", "proceed", "do it", "build it", "ship it",
    "looks good", "sounds good", "lets do it", "let's do it", "lets go", "let's go", "make it so", "carry on",
    "set it up", "please do", "approve it", "yes do it", "do that",
}


def is_affirmative(prompt: str, words: List[str]) -> bool:
    """True only if the whole message is approval: "yes", "OK, go ahead", "approved!".

    Anything that adds an instruction ("yes but...", "go back and rethink", "yes please add X first")
    is feedback, not approval.
    """
    p = prompt.strip().lower()
    if p.startswith("/cock-cage:approve") or p.startswith("approved. the user has approved the plan"):
        return True
    if not p or len(p) > 60 or "?" in p:
        return False
    phrases = PHRASES | {w.lower() for w in words}
    clauses = [c.strip() for c in re.split(r"[,.!;:\n]+|\s-\s|\band\b", p) if c.strip()]
    if not clauses:
        return False
    for clause in clauses:
        tokens = [t for t in re.findall(r"[a-z']+", clause) if t not in FILLERS]
        rest = [t for t in tokens if t not in AFFIRM_ONLY]
        if not rest:
            continue  # "yes", "ok", or pure filler like "thanks"
        if " ".join(rest) not in phrases and " ".join(tokens) not in phrases:
            return False
    return any(t in AFFIRM_ONLY or t in {w for ph in phrases for w in ph.split()} for t in re.findall(r"[a-z']+", p))


def is_negative(prompt: str) -> bool:
    return bool(NEGATIVE_RE.match(prompt or ""))


SETUP_NEG_RE = re.compile(r"\b(no|not|don't|do not|never|without|stop|remove|uninstall|disable|what|why|how|whether)\b|\?", re.I)


def asks_for_setup(prompt: str) -> bool:
    """An explicit, unnegated instruction to set cock-cage up ("set up cock-cage here")."""
    p = (prompt or "").strip()
    if p.lower().startswith("/cock-cage:init"):
        return True
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", p):
        if CC_MENTION_RE.search(sentence) and SETUP_RE.search(sentence) and not SETUP_NEG_RE.search(sentence):
            return True
    return False


# ---------------------------------------------------------------- folder offers

def _registry_path() -> str:
    return os.path.join(util.user_dir(), "folders.json")


def folder_status(root: str) -> Optional[str]:
    return (util.read_json(_registry_path(), {}) or {}).get(os.path.realpath(root))


def set_folder_status(root: str, status: str) -> None:
    data = util.read_json(_registry_path(), {}) or {}
    data[os.path.realpath(root)] = status
    util.write_json(_registry_path(), data)


PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "uv.lock", "poetry.lock")


def looks_like_project(root: str) -> bool:
    """Worth offering cock-cage: a Python project, a git repo with Python in it, or an empty folder."""
    real = os.path.realpath(root)
    if real in (os.path.realpath(os.path.expanduser("~")), "/") or real.startswith(os.path.realpath(util.user_dir())):
        return False
    try:
        entries = [e for e in os.listdir(root) if e not in (".DS_Store",)]
    except OSError:
        return False
    if not entries or entries == [".git"]:
        return True
    if any(m in entries for m in PROJECT_MARKERS):
        return True
    if any(e.endswith(".py") for e in entries):
        return True
    return False


# ---------------------------------------------------------------- requests

ACTIONS = {
    "ledger-delete": "delete report(s) {entries} from the ledger",
    "ledger-reseal": "accept the current ledger contents and reseal it",
    "ledger-unlock": "lift the ledger's append-only lock (for example before deleting the project folder)",
    "loosen": "change the '{check}' check from {old} to {new}",
}


def describe(req: Dict[str, Any]) -> str:
    args = dict(req.get("args", {}))
    if "entries" in args:
        args["entries"] = ", ".join("#%s" % e for e in args["entries"])
    return ACTIONS.get(req.get("action", ""), req.get("action", "?")).format(**args)


def pending(st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The open request, dropping it first if it has expired."""
    req = st.get("request")
    if not req:
        return None
    if time.time() - float(req.get("created", 0)) > REQUEST_TTL_SECONDS and not req.get("approved_at"):
        st["request"] = None
        return None
    return req


def create(st: Dict[str, Any], action: str, args: Dict[str, Any], reason: str) -> Dict[str, Any]:
    cur = pending(st)
    if cur and not cur.get("approved_at"):
        raise ValueError("request %s (%s) is still waiting for the user; resolve it first" % (cur["id"], describe(cur)))
    req = {"id": secrets.token_hex(2), "action": action, "args": args, "reason": reason.strip(),
           "created": time.time(), "approved_at": None}
    st["request"] = req
    return req


def answers_request(prompt: str, req_id: str, words: List[str]) -> Optional[bool]:
    """True/False if the message approves/declines this request by its id, None if it is about something else.

    The id must appear in the user's reply ("yes 3f2a", "no 3f2a"), so a stray "ok" can never approve
    a deletion or a loosening, and approving one request can never approve another.
    """
    if not re.search(r"\b%s\b" % re.escape(req_id), prompt or "", re.I):
        return None
    rest = re.sub(r"\b%s\b" % re.escape(req_id), " ", prompt, flags=re.I)
    if is_negative(rest):
        return False
    return True if is_affirmative(rest, words) else None


def approve_pending(st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    req = pending(st)
    if req and not req.get("approved_at"):
        req["approved_at"] = time.time()
        return req
    return None


def consume(st: Dict[str, Any], req_id: str) -> Dict[str, Any]:
    req = st.get("request") or {}
    if req.get("id") != req_id:
        raise PermissionError("no request %s is pending" % req_id)
    if not req.get("approved_at"):
        raise PermissionError("request %s has not been approved by the user in chat" % req_id)
    if time.time() - float(req["approved_at"]) > REQUEST_TTL_SECONDS:
        raise PermissionError("approval for request %s expired; ask again" % req_id)
    st["request"] = None
    return req


def claude_is_calling() -> bool:
    """True unless the CLI runs in the user's own interactive terminal.

    Claude Code sets CLAUDECODE, and its Bash tool has no terminal attached. Either signal is enough.
    This is a speed bump, not a security boundary: an agent with the user's rights can always find a way.
    """
    import sys
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return True
    try:
        return not (sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return True
