"""Git helpers: task branches, checkpoint commits, diff stats. All best-effort."""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from . import util


def git(root: str, *args: str, timeout: int = 60) -> Tuple[int, str, str]:
    return util.run(["git"] + list(args), cwd=root, timeout=timeout)


def is_repo(root: str) -> bool:
    code, out, _ = git(root, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


def branch(root: str) -> str:
    code, out, _ = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    return out.strip() if code == 0 else ""


def head(root: str) -> str:
    code, out, _ = git(root, "rev-parse", "HEAD")
    return out.strip() if code == 0 else ""


def untracked(root: str) -> List[str]:
    code, out, _ = git(root, "ls-files", "--others", "--exclude-standard")
    return sorted(l for l in out.splitlines() if l.strip()) if code == 0 else []


def toplevel(root: str) -> str:
    code, out, _ = git(root, "rev-parse", "--show-toplevel")
    return out.strip() if code == 0 and out.strip() else root


def porcelain(root: str) -> Dict[str, str]:
    """{path relative to root: XY status}, NUL-separated so odd file names survive."""
    code, out, _ = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
    if code != 0:
        return {}
    top = toplevel(root)
    res: Dict[str, str] = {}
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            xy, path = entry[:2], entry[3:]
            if xy[0] in "RC":  # renames carry the old path in the next field
                i += 1
            relp = util.rel(root, os.path.join(top, path))
            res[relp] = xy
        i += 1
    return res


def dirty_paths(root: str) -> List[str]:
    return sorted(porcelain(root)) if is_repo(root) else []


def committable(root: str, paths: Sequence[str]) -> List[str]:
    """Keep paths git can stage: inside the repo, not ignored, and existing or tracked (deleted files count)."""
    if not paths or not is_repo(root):
        return []
    keep = []
    for p in paths:
        if p.startswith("..") or os.path.isabs(p):
            continue
        exists = os.path.lexists(os.path.join(root, p))
        tracked = git(root, "ls-files", "--error-unmatch", "--", p)[0] == 0
        if not exists and not tracked:
            continue  # created and deleted again within the task
        if exists and not tracked and git(root, "check-ignore", "-q", "--", p)[0] == 0:
            continue
        keep.append(p)
    return keep


def has_identity(root: str) -> bool:
    return git(root, "config", "user.email")[0] == 0 and git(root, "config", "user.name")[0] == 0


def _id_args(root: str) -> List[str]:
    return [] if has_identity(root) else ["-c", "user.name=cock-cage", "-c", "user.email=cock-cage@localhost"]


def slugify(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:maxlen].rstrip("-") or "task")


def ensure_task_branch(root: str, title: str, cfg: Dict) -> Optional[str]:
    """If on a protected branch, move work to a new task branch. Uncommitted changes travel along."""
    g = cfg.get("git", {})
    if not g.get("auto_branch", True) or not is_repo(root):
        return None
    cur = branch(root)
    if not head(root):  # unborn branch: nothing to branch from yet
        return None
    if cur and cur not in g.get("protected_branches", ["main", "master"]):
        return None  # already on a working branch
    name = g.get("branch_prefix", "cc/") + slugify(title)
    base, i = name, 2
    while git(root, "rev-parse", "--verify", "--quiet", "refs/heads/" + name)[0] == 0:
        name = "%s-%d" % (base, i)
        i += 1
    code, _, _ = git(root, "switch", "-c", name)
    return name if code == 0 else None


COMMIT_TYPES = (
    (r"^(fix|repair|correct|resolve)", "fix"),
    (r"^(add|implement|create|build|introduce|support)", "feat"),
    (r"^(refactor|restructure|simplify|extract|rename|move)", "refactor"),
    (r"^(test|cover)", "test"),
    (r"^(doc|document|explain)", "docs"),
    (r"^(speed|optimi[sz]e|perf)", "perf"),
)


def commit_type(title: str) -> str:
    t = title.strip().lower()
    for pat, typ in COMMIT_TYPES:
        if re.match(pat, t):
            return typ
    return "chore"


def checkpoint(root: str, paths: Sequence[str], title: str, body: str, protected: Sequence[str]) -> Optional[str]:
    """Commit exactly these paths (other staged work is left alone). Returns the short sha."""
    if not paths or not is_repo(root):
        return None
    cur = branch(root)
    if not cur or cur in protected:  # never commit on main, and never onto a detached HEAD
        return None
    existing = [p for p in paths if p]
    code, _, err = git(root, "add", "-A", "--", *existing)
    if code != 0:
        return None
    # Only commit if something is staged for these paths.
    if git(root, "diff", "--cached", "--quiet", "--", *existing)[0] == 0:
        return None
    msg = "%s: %s\n\n%s" % (commit_type(title), title.strip()[:72], body.strip())
    code, _, err = util.run(["git"] + _id_args(root) + ["commit", "-q", "-m", msg, "--", *existing], cwd=root, timeout=60)
    if code != 0:
        return None
    return head(root)[:8]


def merge_into_protected(root: str, task_branch: str, protected: Sequence[str]) -> Tuple[bool, str]:
    """Merge a finished task branch into main (fast-forward when possible), delete it, stay on main.

    Uncommitted changes in the working tree travel with the switch, as git normally does. On any
    conflict the merge is aborted and the task branch is left checked out, untouched.
    """
    target = next((b for b in protected if git(root, "rev-parse", "--verify", "--quiet", "refs/heads/" + b)[0] == 0), None)
    if not target:
        return False, "no main or master branch to merge into"
    code, _, err = git(root, "switch", "--quiet", target)
    if code != 0:
        return False, "could not switch to %s: %s" % (target, err.strip()[:200])
    code, _, _ = git(root, "merge", "--ff-only", "--quiet", task_branch)
    if code != 0:
        code, _, err = util.run(["git"] + _id_args(root) + ["merge", "--no-ff", "--quiet", "-m",
                                "Merge %s" % task_branch, task_branch], cwd=root, timeout=60)
        if code != 0:
            git(root, "merge", "--abort")
            git(root, "switch", "--quiet", task_branch)
            return False, "merge into %s conflicted; %s kept for review" % (target, task_branch)
    git(root, "branch", "-d", task_branch)
    return True, target


def diff_stat(root: str, start: str, paths: Sequence[str]) -> List[str]:
    """Per-file +/- counts versus the task's start commit, including new untracked files."""
    if not is_repo(root):
        return []
    lines: List[str] = []
    seen = set()
    if start:
        code, out, _ = git(root, "diff", "--numstat", start, "--", *paths) if paths else git(root, "diff", "--numstat", start)
        for l in out.splitlines():
            parts = l.split("\t")
            if len(parts) == 3:
                lines.append("%s (+%s −%s)" % (parts[2], parts[0], parts[1]))
                seen.add(parts[2])
    for p in paths:
        if p not in seen and p in untracked(root):
            lines.append("%s (new, %d lines)" % (p, util.count_lines(root + "/" + p)))
    return lines


def changed_line_total(root: str, start: str, paths: Sequence[str]) -> int:
    total = 0
    if not is_repo(root):
        return sum(util.count_lines(root + "/" + p) for p in paths)
    if start:
        code, out, _ = git(root, "diff", "--numstat", start, "--", *paths) if paths else (0, "", "")
        for l in out.splitlines():
            parts = l.split("\t")
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                total += int(parts[0]) + int(parts[1])
    ut = set(untracked(root))
    for p in paths:
        if p in ut:
            total += util.count_lines(root + "/" + p)
    return total


def tags(root: str) -> List[str]:
    code, out, _ = git(root, "tag", "--sort=-creatordate")
    return [t for t in out.splitlines() if t.strip()]
