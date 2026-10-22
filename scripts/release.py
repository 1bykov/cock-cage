#!/usr/bin/env python3
"""Release cock-cage itself: bump the plugin version, roll the changelog, test, validate, commit, tag, push.

    python3 scripts/release.py patch|minor|major [--push] [--dry-run] [--skip-tests]

plugin.json is the single source of truth for the version. Projects stay on the version they
have until this string changes, so every release is deliberate and every project can see what
it runs (the ledger records the plugin version on each report).
Tags follow Claude Code's convention: cock-cage--v<version>.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_JSON = os.path.join(REPO, "plugins", "cock-cage", ".claude-plugin", "plugin.json")
MARKET_JSON = os.path.join(REPO, ".claude-plugin", "marketplace.json")
CHANGELOG = os.path.join(REPO, "CHANGELOG.md")
sys.path.insert(0, os.path.join(REPO, "plugins", "cock-cage", "lib"))

from cockcage import versioning  # noqa: E402


def run(cmd, check=True):
    print("$ " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=REPO, text=True, capture_output=True)
    if p.stdout.strip():
        print(p.stdout.strip())
    if check and p.returncode != 0:
        sys.exit("failed: %s\n%s" % (" ".join(cmd), p.stderr))
    return p


def test_command():
    """pytest from this interpreter if available, else through uv, so a stock macOS python works."""
    if subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode == 0:
        return [sys.executable, "-m", "pytest", "-q", "tests"]
    if shutil.which("uv"):
        return ["uv", "run", "--no-project", "--with", "pytest", "python", "-m", "pytest", "-q", "tests"]
    sys.exit("pytest is not available (install uv or pytest); refusing to release untested code")


def roll_changelog(new: str, since_tag: str) -> None:
    text = open(CHANGELOG).read() if os.path.exists(CHANGELOG) else "# Changelog\n\n## [Unreleased]\n"
    m = re.search(r"^## \[Unreleased\]\s*\n(.*?)(?=^## \[|\Z)", text, re.S | re.M)
    body = (m.group(1).strip() if m else "")
    if not body:
        section = versioning.changelog_section(REPO, new, since_tag or None)
        body = section.split("\n", 2)[2].strip() if section.count("\n") >= 2 else "- Maintenance release."
    new_section = "## [Unreleased]\n\n## [%s] - %s\n\n%s\n\n" % (new, dt.date.today().isoformat(), body)
    if m:
        text = text[: m.start()] + new_section + text[m.end():]
    else:
        text = text.rstrip() + "\n\n" + new_section
    open(CHANGELOG, "w").write(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("part", choices=["patch", "minor", "major"])
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--update-local", action="store_true", help="refresh this machine's install after pushing")
    a = ap.parse_args()

    if run(["git", "status", "--porcelain"], check=False).stdout.strip() and not a.dry_run:
        sys.exit("working tree not clean; commit or stash first")
    manifest = json.load(open(PLUGIN_JSON))
    market = json.load(open(MARKET_JSON))
    entry = next(p for p in market["plugins"] if p["name"] == manifest["name"])
    if "version" in entry:
        sys.exit("marketplace.json must not carry a version; plugin.json is the source of truth")
    cur = manifest["version"]
    new = versioning.bump(cur, a.part)
    last = run(["git", "describe", "--tags", "--abbrev=0", "--match", "cock-cage--v*"], check=False).stdout.strip()
    print("cock-cage %s -> %s (since %s)" % (cur, new, last or "the beginning"))
    if a.dry_run:
        print(versioning.changelog_section(REPO, new, last or None))
        return 0
    if not a.skip_tests:
        run(test_command())
    manifest["version"] = new
    with open(PLUGIN_JSON, "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    roll_changelog(new, last)
    if shutil.which("claude"):
        run(["claude", "plugin", "validate", "--strict", "plugins/cock-cage"])
        run(["claude", "plugin", "validate", "--strict", "."])
    run(["git", "add", PLUGIN_JSON, CHANGELOG])
    run(["git", "commit", "-q", "-m", "chore(release): cock-cage v%s" % new])
    tag = "cock-cage--v%s" % new
    run(["git", "tag", "-a", tag, "-m", "cock-cage v%s" % new])
    if a.push:
        run(["git", "push", "--follow-tags"])
        print("Pushed. Projects pick it up automatically (auto-update) at their next session.")
        if a.update_local and shutil.which("claude"):
            run(["claude", "plugin", "marketplace", "update", "cock-cage"], check=False)
            run(["claude", "plugin", "update", "cock-cage@cock-cage"], check=False)
            print("This machine now has v%s; it loads in new sessions or after /reload-plugins." % new)
    else:
        print("Tagged %s. Push with: git push --follow-tags" % tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
