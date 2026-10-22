"""Project setup. bootstrap() is silent and file-only; init() also installs tools and commits."""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

from . import PLUGIN_ROOT, config, gitops, plugin_version, util

MARKETPLACE_REPO = "1bykov/cock-cage"
PLUGIN_ID = "cock-cage@cock-cage"

CC_GITIGNORE = "# Only config.json is shared. Ledger, state, plans and caches stay local.\n*\n!.gitignore\n!config.json\n"

ROOT_GITIGNORE_LINES = [
    ".venv/", "venv/", "__pycache__/", "*.py[cod]", ".pytest_cache/", ".ruff_cache/", ".mypy_cache/",
    ".coverage", "coverage.json", "htmlcov/", "dist/", "build/", "*.egg-info/", ".env", ".env.*",
    "!.env.example", ".DS_Store",
]

CLAUDE_MD_START = "<!-- cock-cage:start -->"
CLAUDE_MD_END = "<!-- cock-cage:end -->"

TOOL_BLOCKS = {
    "tool.ruff": '[tool.ruff]\nline-length = 100\n\n[tool.ruff.lint]\nselect = ["E", "F", "W", "I", "B", "UP", "SIM"]\n',
    "tool.pyright": '[tool.pyright]\nvenvPath = "."\nvenv = ".venv"\ntypeCheckingMode = "standard"\n',
    "tool.coverage.run": '[tool.coverage.run]\nomit = [".venv/*", "venv/*", "tests/*"]\n',
    "tool.pytest.ini_options": '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n',
}

DEV_TOOLS_BASE = ["ruff", "pytest", "pytest-cov", "vulture", "deptry", "pip-audit"]


def bootstrap(root: str) -> bool:
    """Create .cock-cage/ with defaults if missing. Returns True if it created anything."""
    d = util.cc_path(root)
    created = False
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        created = True
    if not os.path.exists(os.path.join(d, ".gitignore")):
        util.write_text(os.path.join(d, ".gitignore"), CC_GITIGNORE)
        created = True
    if not os.path.exists(config.path(root)):
        config.write_default(root)
        created = True
    return created


def write_wrapper(root: str, script: str) -> str:
    """.cock-cage/bin/cockcage always points at the plugin version this session loaded."""
    p = util.cc_path(root, "bin", "cockcage")
    body = '#!/bin/sh\n# Written by cock-cage at session start; points at the active plugin version.\nexec python3 "%s" "$@"\n' % script
    if util.read_text(p) != body:
        util.write_text(p, body)
        os.chmod(p, 0o755)
    return p


# ---------------------------------------------------------------- helpers

def _pm(root: str, wanted: str) -> str:
    if wanted in ("uv", "pip"):
        return wanted
    if shutil.which("uv") and not os.path.exists(os.path.join(root, "poetry.lock")):
        return "uv"
    return "pip"


def _ensure_root_gitignore(root: str) -> bool:
    p = os.path.join(root, ".gitignore")
    existing = util.read_text(p)
    have = {l.strip() for l in existing.splitlines()}
    missing = [l for l in ROOT_GITIGNORE_LINES if l not in have]
    if not missing:
        return False
    text = existing
    if text and not text.endswith("\n"):
        text += "\n"
    text += ("\n# Added by cock-cage\n" if existing else "# Added by cock-cage\n") + "\n".join(missing) + "\n"
    util.write_text(p, text)
    return True


def _ensure_tool_tables(root: str, type_checker: str) -> List[str]:
    p = os.path.join(root, "pyproject.toml")
    text = util.read_text(p)
    added = []
    for table, block in TOOL_BLOCKS.items():
        if table == "tool.pyright" and type_checker not in ("pyright", "basedpyright"):
            continue
        if table == "tool.pytest.ini_options" and (
            os.path.exists(os.path.join(root, "pytest.ini")) or os.path.exists(os.path.join(root, "tox.ini"))
            or "[tool:pytest]" in util.read_text(os.path.join(root, "setup.cfg"))
        ):
            continue
        if re.search(r"^\[%s\]" % re.escape(table), text, re.M):
            continue
        if text and not text.endswith("\n"):
            text += "\n"
        text += ("\n" if text else "") + block
        added.append(table)
    if added:
        util.write_text(p, text)
    return added


def _claude_md(root: str) -> bool:
    p = os.path.join(root, "CLAUDE.md")
    block = util.read_text(os.path.join(PLUGIN_ROOT, "templates", "CLAUDE.md.block")).strip()
    block = "%s\n%s\n%s" % (CLAUDE_MD_START, block, CLAUDE_MD_END)
    text = util.read_text(p)
    if CLAUDE_MD_START in text and CLAUDE_MD_END in text:
        new = re.sub(re.escape(CLAUDE_MD_START) + r".*?" + re.escape(CLAUDE_MD_END), lambda _m: block, text, flags=re.S)
    else:
        new = (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"
    if new != text:
        util.write_text(p, new)
        return True
    return False


def _project_settings(root: str) -> bool:
    """Declare the marketplace, enable the plugin and add deny rules as a second layer."""
    p = os.path.join(root, ".claude", "settings.json")
    data: Dict[str, Any] = util.read_json(p, {}) or {}
    before = json.dumps(data, sort_keys=True)
    mk = data.setdefault("extraKnownMarketplaces", {})
    mk.setdefault("cock-cage", {"source": {"source": "github", "repo": MARKETPLACE_REPO}, "autoUpdate": True})
    data.setdefault("enabledPlugins", {})
    if isinstance(data["enabledPlugins"], dict):
        data["enabledPlugins"].setdefault(PLUGIN_ID, True)
    perms = data.setdefault("permissions", {})
    deny = perms.setdefault("deny", [])
    for rule in (
        "Edit(./.cock-cage/reports.md)", "Write(./.cock-cage/reports.md)",
        "Edit(./.cock-cage/ledger.seal)", "Write(./.cock-cage/ledger.seal)",
        "Edit(./.cock-cage/config.json)", "Write(./.cock-cage/config.json)",
        "Edit(./.cock-cage/state.json)", "Write(./.cock-cage/state.json)",
        "Bash(chflags:*)",
    ):
        if rule not in deny:
            deny.append(rule)
    if json.dumps(data, sort_keys=True) != before:
        util.write_json(p, data)
        return True
    return False


def _install_tools(root: str, pm: str, type_checker: str, log: List[str]) -> bool:
    tools = list(DEV_TOOLS_BASE)
    if type_checker in ("pyright", "mypy", "ty", "basedpyright"):
        tools.append(type_checker)
    if pm == "uv":
        if not os.path.exists(os.path.join(root, "pyproject.toml")):
            code, out, err = util.run(["uv", "init", "--bare", "--no-workspace", "--vcs", "none"], cwd=root, timeout=120)
            log.append("uv init --bare: %s" % ("ok" if code == 0 else (err or out).strip()[:200]))
        elif not re.search(r"^\[project\]", util.read_text(os.path.join(root, "pyproject.toml")), re.M):
            log.append("pyproject.toml has no [project] table; using pip mode for tools")
            return _install_tools(root, "pip", type_checker, log)
        code, out, err = util.run(["uv", "add", "--dev"] + tools, cwd=root, timeout=900)
        log.append("uv add --dev %s: %s" % (" ".join(tools), "ok" if code == 0 else (err or out).strip()[-400:]))
        return code == 0
    py = os.path.join(root, ".venv", "bin", "python")
    if not os.path.exists(py):
        code, out, err = util.run(["python3", "-m", "venv", ".venv"], cwd=root, timeout=300)
        log.append("python3 -m venv .venv: %s" % ("ok" if code == 0 else (err or out).strip()[:200]))
        if code != 0:
            return False
    code, out, err = util.run([py, "-m", "pip", "install", "-q", "-U"] + tools, cwd=root, timeout=900)
    log.append("pip install %s: %s" % (" ".join(tools), "ok" if code == 0 else (err or out).strip()[-400:]))
    req = os.path.join(root, "requirements-dev.txt")
    have = {re.split(r"[<>=\[ ]", l.strip())[0].lower() for l in util.read_text(req).splitlines() if l.strip() and not l.startswith("#")}
    extra = [t for t in tools if t.lower() not in have]
    if extra:
        txt = util.read_text(req)
        util.write_text(req, (txt.rstrip() + "\n" if txt.strip() else "") + "\n".join(extra) + "\n")
    return code == 0


def _porcelain(root: str) -> Dict[str, str]:
    code, out, _ = gitops.git(root, "status", "--porcelain", "--untracked-files=all")
    res = {}
    for l in out.splitlines():
        if len(l) > 3:
            res[l[3:].strip().strip('"')] = l[:2]
    return res


# ---------------------------------------------------------------- init

def init(root: str, pm: str = "auto", tools: bool = True, commit: bool = True,
         type_checker: Optional[str] = None, large_file_mb: int = 5) -> Tuple[bool, List[str]]:
    log: List[str] = []
    root = os.path.abspath(root)
    new_repo = False
    if not gitops.is_repo(root):
        code, _, err = gitops.git(root, "init", "-q", "-b", "main")
        if code != 0:
            code, _, err = gitops.git(root, "init", "-q")
        new_repo = code == 0
        log.append("git init: %s" % ("ok" if code == 0 else err.strip()))
    dirty_before = _porcelain(root) if not new_repo else {}
    unborn = not gitops.head(root)

    bootstrap(root)
    if type_checker:
        raw = util.read_json(config.path(root), {}) or {}
        raw["type_checker"] = type_checker
        util.write_json(config.path(root), raw)
    cfg = config.load(root)
    tc = str(cfg.get("type_checker", "pyright"))
    pm = _pm(root, pm if pm != "auto" else str(cfg.get("package_manager", "auto")))
    log.append("package manager: %s" % pm)

    if _ensure_root_gitignore(root):
        log.append(".gitignore: added Python defaults")
    ok = True
    if tools:
        ok = _install_tools(root, pm, tc, log)
    added = _ensure_tool_tables(root, tc) if os.path.exists(os.path.join(root, "pyproject.toml")) or pm == "pip" else []
    if added:
        log.append("pyproject.toml: added %s" % ", ".join("[%s]" % a for a in added))
    if _claude_md(root):
        log.append("CLAUDE.md: cock-cage section written")
    if _project_settings(root):
        log.append(".claude/settings.json: marketplace declared, plugin enabled, deny rules added")
    raw = util.read_json(config.path(root), {}) or {}
    raw["initialised_with"] = plugin_version()
    util.write_json(config.path(root), raw)

    if commit and gitops.is_repo(root):
        if unborn:
            gitops.git(root, "add", "-A")
            big = []
            for f in gitops.git(root, "diff", "--cached", "--name-only")[1].splitlines():
                fp = os.path.join(root, f)
                if os.path.isfile(fp) and os.path.getsize(fp) > large_file_mb * 1024 * 1024:
                    big.append(f)
            if big:
                gitops.git(root, "reset", "-q", "--", *big)
                log.append("left out of the first commit (over %d MB): %s" % (large_file_mb, ", ".join(big)))
            code, _, err = util.run(["git"] + gitops._id_args(root) + ["commit", "-q", "-m", "chore: initial commit with cock-cage"], cwd=root)
            log.append("first commit: %s" % ("ok" if code == 0 else err.strip()[:200]))
        else:
            after = _porcelain(root)
            ours = [f for f in after if f not in dirty_before]
            skipped = [f for f in after if f in dirty_before]
            if ours:
                gitops.git(root, "add", "-A", "--", *ours)
                code, _, err = util.run(["git"] + gitops._id_args(root) + ["commit", "-q", "-m", "chore: set up cock-cage", "--", *ours], cwd=root)
                log.append("committed cock-cage setup (%d files): %s" % (len(ours), "ok" if code == 0 else err.strip()[:200]))
            if skipped:
                log.append("left your uncommitted changes alone: %s" % ", ".join(sorted(skipped)[:10]))
    return ok, log
