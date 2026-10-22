"""Semantic-version bumps, changelogs from conventional commits, and tagging, for projects."""

from __future__ import annotations

import datetime as _dt
import os
import re
from typing import Dict, List, Optional, Tuple

from . import gitops, util

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def bump(version: str, part: str) -> str:
    m = SEMVER.match(version.strip())
    if not m:
        raise ValueError("not a semantic version: %r" % version)
    major, minor, patch = (int(x) for x in m.groups())
    if part == "major":
        return "%d.0.0" % (major + 1)
    if part == "minor":
        return "%d.%d.0" % (major, minor + 1)
    if part == "patch":
        return "%d.%d.%d" % (major, minor, patch + 1)
    raise ValueError("part must be major, minor or patch")


def _project_table_span(text: str) -> Optional[Tuple[int, int]]:
    m = re.search(r"^\[project\]\s*$", text, re.M)
    if not m:
        return None
    nxt = re.search(r"^\[", text[m.end():], re.M)
    return m.end(), (m.end() + nxt.start()) if nxt else len(text)


def read_project_version(root: str) -> Optional[str]:
    text = util.read_text(os.path.join(root, "pyproject.toml"))
    span = _project_table_span(text)
    if not span:
        return None
    m = re.search(r'^version\s*=\s*"([^"]+)"', text[span[0]:span[1]], re.M)
    return m.group(1) if m else None


def write_project_version(root: str, new: str) -> None:
    p = os.path.join(root, "pyproject.toml")
    text = util.read_text(p)
    span = _project_table_span(text)
    if not span:
        raise ValueError("pyproject.toml has no [project] table")
    seg = text[span[0]:span[1]]
    seg2, n = re.subn(r'^(version\s*=\s*")[^"]+(")', lambda m: m.group(1) + new + m.group(2), seg, count=1, flags=re.M)
    if n == 0:
        raise ValueError("no version field in [project]")
    util.write_text(p, text[:span[0]] + seg2 + text[span[1]:])


GROUPS = (("feat", "Added"), ("fix", "Fixed"), ("perf", "Performance"), ("refactor", "Changed"),
          ("docs", "Documentation"), ("test", "Tests"))


def changelog_section(root: str, version: str, since: Optional[str]) -> str:
    rng = "%s..HEAD" % since if since else "HEAD"
    code, out, _ = gitops.git(root, "log", rng, "--no-merges", "--pretty=%s")
    buckets: Dict[str, List[str]] = {}
    for subj in out.splitlines():
        m = re.match(r"^(\w+)(?:\([^)]*\))?!?:\s*(.+)$", subj)
        typ, msg = (m.group(1), m.group(2)) if m else ("other", subj)
        if typ == "chore" and msg.startswith(("release", "set up cock-cage", "initial commit")):
            continue
        label = dict(GROUPS).get(typ, "Other")
        buckets.setdefault(label, []).append(msg)
    date = _dt.date.today().isoformat()
    lines = ["## [%s] - %s" % (version, date), ""]
    order = [g[1] for g in GROUPS] + ["Other"]
    for label in order:
        if label in buckets:
            lines.append("### %s" % label)
            lines.extend("- %s" % m for m in buckets[label])
            lines.append("")
    if len(lines) == 2:
        lines.extend(["- Maintenance release.", ""])
    return "\n".join(lines)


def prepend_changelog(root: str, section: str) -> None:
    p = os.path.join(root, "CHANGELOG.md")
    text = util.read_text(p)
    header = "# Changelog\n\nAll notable changes to this project. Versions follow semantic versioning.\n\n"
    if not text.strip():
        util.write_text(p, header + section)
        return
    m = re.search(r"^## ", text, re.M)
    if m:
        util.write_text(p, text[: m.start()] + section + "\n" + text[m.start():])
    else:
        util.write_text(p, text.rstrip() + "\n\n" + section)


def last_version_tag(root: str) -> Optional[str]:
    code, out, _ = gitops.git(root, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    return out.strip() if code == 0 and out.strip() else None


def release(root: str, part: str, dry_run: bool = False) -> List[str]:
    dirty = [f for f in ("pyproject.toml", "CHANGELOG.md") if gitops.git(root, "status", "--porcelain", "--", f)[1].strip()]
    if dirty and not dry_run:
        raise ValueError("%s has uncommitted changes; commit or stash them before releasing" % ", ".join(dirty))
    cur = read_project_version(root)
    if cur is None:
        raise ValueError("pyproject.toml has no [project] version to bump")
    new = bump(cur, part)
    since = last_version_tag(root)
    section = changelog_section(root, new, since)
    out = ["%s -> %s (changes since %s)" % (cur, new, since or "the first commit"), section]
    if dry_run:
        return out
    write_project_version(root, new)
    prepend_changelog(root, section)
    files = ["pyproject.toml", "CHANGELOG.md"]
    if os.path.exists(os.path.join(root, "uv.lock")) and util.run(["uv", "lock"], cwd=root, timeout=300)[0] == 0:
        files.append("uv.lock")
    gitops.git(root, "add", "--", *files)
    code, _, err = util.run(["git"] + gitops._id_args(root) + ["commit", "-q", "-m", "chore(release): v%s" % new, "--", *files], cwd=root)
    if code != 0:
        raise RuntimeError("commit failed: " + err.strip())
    gitops.git(root, "tag", "-a", "v%s" % new, "-m", "v%s" % new)
    out.append("committed and tagged v%s. Push with: git push --follow-tags" % new)
    return out
