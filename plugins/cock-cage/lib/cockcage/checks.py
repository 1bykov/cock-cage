"""Run the project's quality tools and normalise their output into comparable keys.

Every result is {"ran": bool, "note": str, ...}. Keys deliberately omit line numbers,
so moving code around does not make an old problem look new.
"""

from __future__ import annotations

import ast
import glob
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import dupes, util

ALL = ("tests", "lint", "types", "dead_code", "complexity", "duplicates", "deps_hygiene", "docstrings")


def _skip(note: str) -> Dict[str, Any]:
    return {"ran": False, "note": note}


def _relkey(root: str, path: str) -> str:
    return util.rel(root, path if os.path.isabs(path) else os.path.join(root, path))


def _has_module(root: str, module: str) -> bool:
    for venv in (".venv", "venv", "env"):
        if glob.glob(os.path.join(root, venv, "lib", "python*", "site-packages", module)):
            return True
    return False


def _src_args(cfg: Dict[str, Any]) -> List[str]:
    return list(cfg.get("src_dirs") or ["."])


# ---------------------------------------------------------------- tests + coverage

def pytest_cmd(root: str) -> Optional[List[str]]:
    """The project's pytest if its venv has one, else a pytest executable on PATH, else None."""
    if _has_module(root, "pytest"):
        return [util.project_python(root), "-m", "pytest"]
    exe = util.find_tool(root, "pytest")
    return [exe] if exe else None


def run_tests(root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    base_cmd = pytest_cmd(root)
    if not base_cmd:
        return _skip("pytest not installed")
    tmp = tempfile.mkdtemp(prefix="cc-pytest-")
    junit = os.path.join(tmp, "junit.xml")
    covjson = os.path.join(tmp, "cov.json")
    cmd = base_cmd + ["-q", "-p", "no:cacheprovider", "--junitxml", junit]
    with_cov = len(base_cmd) == 3 and _has_module(root, "pytest_cov")
    if with_cov:
        for d in _src_args(cfg):
            cmd += ["--cov", d]
        cmd += ["--cov-report", "json:%s" % covjson, "--cov-report", "term:skip-covered"]
    code, out, err = util.run(cmd, cwd=root, timeout=900)
    res: Dict[str, Any] = {"ran": True, "passed": 0, "failed": 0, "skipped": 0, "failing": [], "ids": [], "note": ""}
    if code == 5:
        res["note"] = "no tests collected"
    elif code in (124, 127):
        return _skip((err or out).strip()[:200])
    try:
        tree = ET.parse(junit)
        for case in tree.iter("testcase"):
            tid = "%s::%s" % (case.get("classname", ""), case.get("name", ""))
            res["ids"].append(tid)
            if case.find("failure") is not None or case.find("error") is not None:
                res["failing"].append(tid)
            elif case.find("skipped") is not None:
                res["skipped"] += 1
            else:
                res["passed"] += 1
    except (ET.ParseError, OSError):
        if code not in (0, 5):
            tail = (err or out).strip().splitlines()
            res["note"] = "pytest crashed: " + (tail[-1][:200] if tail else "no output")
            res["failing"].append("<collection error>")
    res["failed"] = len(res["failing"])
    if with_cov:
        cov = util.read_json(covjson, {}) or {}
        files = {}
        for f, data in (cov.get("files") or {}).items():
            files[_relkey(root, f)] = round(float(data.get("summary", {}).get("percent_covered", 0.0)), 1)
        total = cov.get("totals", {}).get("percent_covered")
        res["coverage"] = {"total": round(float(total), 1) if total is not None else None, "files": files}
    return res


# ---------------------------------------------------------------- ruff

def run_lint(root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ruff = util.find_tool(root, "ruff")
    if not ruff:
        return _skip("ruff not installed")
    code, out, err = util.run([ruff, "check", "--output-format", "json", "--exit-zero", "--no-fix"] + _src_args(cfg), cwd=root, timeout=300)
    try:
        items = json.loads(out or "[]")
    except ValueError:
        return _skip("ruff output unreadable: " + (err or "")[:200])
    keys = sorted({"%s:%s:%s" % (_relkey(root, i.get("filename", "")), i.get("code"), i.get("message")) for i in items})
    return {"ran": True, "items": keys, "note": ""}


def run_complexity(root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ruff = util.find_tool(root, "ruff")
    if not ruff:
        return _skip("ruff not installed")
    mx = int(cfg.get("complexity_max", 10))
    code, out, err = util.run(
        [ruff, "check", "--select", "C901", "--output-format", "json", "--exit-zero", "--no-fix",
         "--config", "lint.mccabe.max-complexity = %d" % mx] + _src_args(cfg), cwd=root, timeout=300)
    try:
        items = json.loads(out or "[]")
    except ValueError:
        return _skip("ruff output unreadable")
    keys = sorted({"%s:%s" % (_relkey(root, i.get("filename", "")), i.get("message")) for i in items})
    return {"ran": True, "items": keys, "note": ""}


# ---------------------------------------------------------------- type checking

_GENERIC_DIAG = re.compile(r"^(?P<file>[^:\n]+\.pyi?):(?P<line>\d+)(?::\d+)?:\s*(?P<sev>error|warning)(?:\[(?P<rule>[^\]]+)\])?:?\s*(?P<msg>.*)$")


def run_types(root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    checker = str(cfg.get("type_checker", "pyright")).lower()
    if checker == "none":
        return _skip("type checking disabled")
    exe = util.find_tool(root, checker)
    if not exe:
        return _skip("%s not installed" % checker)
    if checker in ("pyright", "basedpyright"):
        code, out, err = util.run([exe, "--outputjson"] + [d for d in _src_args(cfg) if d != "."], cwd=root, timeout=600)
        try:
            data = json.loads(out or "{}")
        except ValueError:
            return _skip("%s failed: %s" % (checker, (err or out).strip()[:200]))
        keys = sorted({
            "%s:%s:%s" % (_relkey(root, d.get("file", "")), d.get("rule", ""), d.get("message", "").splitlines()[0])
            for d in data.get("generalDiagnostics", []) if d.get("severity") == "error"
        })
        return {"ran": True, "items": keys, "note": ""}
    if checker == "mypy":
        cmd = [exe, "--no-error-summary", "--hide-error-context", "--no-color-output", "--show-error-codes",
               "--no-pretty"] + _src_args(cfg)
    else:  # ty and anything with "file:line:col: error[rule] message" output
        cmd = [exe, "check", "--output-format", "concise"] + [d for d in _src_args(cfg) if d != "."]
    code, out, err = util.run(cmd, cwd=root, timeout=600)
    keys = set()
    for line in (out + "\n" + err).splitlines():
        m = _GENERIC_DIAG.match(line.strip())
        if m and m.group("sev") == "error":
            keys.add("%s:%s:%s" % (_relkey(root, m.group("file")), m.group("rule") or "", m.group("msg").strip()))
    return {"ran": True, "items": sorted(keys), "note": ""}


# ---------------------------------------------------------------- vulture, deptry, docstrings, duplicates

_VULTURE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+): (?P<msg>.+?) \(\d+% confidence")


def run_dead_code(root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    exe = util.find_tool(root, "vulture")
    if not exe:
        return _skip("vulture not installed")
    excl = ",".join(sorted(util.SKIP_DIRS))
    code, out, err = util.run([exe, "--min-confidence", "80", "--exclude", excl] + _src_args(cfg), cwd=root, timeout=300)
    keys = set()
    for line in out.splitlines():
        m = _VULTURE.match(line.strip())
        if m:
            keys.add("%s:%s" % (_relkey(root, m.group("file")), m.group("msg")))
    return {"ran": True, "items": sorted(keys), "note": ""}


def run_deps(root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    exe = util.find_tool(root, "deptry")
    if not exe:
        return _skip("deptry not installed")
    if not os.path.exists(os.path.join(root, "pyproject.toml")):
        return _skip("no pyproject.toml")
    tmp = os.path.join(tempfile.mkdtemp(prefix="cc-deptry-"), "out.json")
    util.run([exe, ".", "--json-output", tmp], cwd=root, timeout=300)
    data = util.read_json(tmp, None)
    if data is None:
        return _skip("deptry produced no output")
    keys = sorted({"%s:%s" % (d.get("error", {}).get("code", ""), d.get("module", "")) for d in data})
    return {"ran": True, "items": keys, "note": ""}


def run_docstrings(root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    keys = set()
    for f in util.iter_py_files(root, _src_args(cfg)):
        relp = util.rel(root, f)
        base = os.path.basename(f)
        if base.startswith("test_") or "/tests/" in "/" + relp or base == "conftest.py":
            continue
        try:
            tree = ast.parse(util.read_text(f))
        except (SyntaxError, ValueError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                if not ast.get_docstring(node):
                    keys.add("%s:%s" % (relp, node.name))
    return {"ran": True, "items": sorted(keys), "note": ""}


def run_duplicates(root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    files = util.iter_py_files(root, _src_args(cfg))
    groups = dupes.find(root, files, min_lines=int(cfg.get("duplicate_min_lines", 6)))
    return {"ran": True, "items": sorted({str(g["key"]) for g in groups}),
            "describe": {str(g["key"]): dupes.describe(g) for g in groups}, "note": ""}


def run_audit(root: str) -> Dict[str, Any]:
    exe = util.find_tool(root, "pip-audit")
    if not exe:
        return _skip("pip-audit not installed")
    code, out, err = util.run([exe, "-f", "json", "--progress-spinner", "off"], cwd=root, timeout=300)
    try:
        data = json.loads(out or "{}")
    except ValueError:
        return _skip("pip-audit failed: " + (err or "").strip()[:200])
    deps = data.get("dependencies", data if isinstance(data, list) else [])
    items = []
    for d in deps:
        for v in d.get("vulns", []):
            items.append("%s==%s: %s (fix: %s)" % (d.get("name"), d.get("version"), v.get("id"), ", ".join(v.get("fix_versions", [])) or "none"))
    return {"ran": True, "items": sorted(items), "note": "", "at": util.now_iso()}


RUNNERS: Dict[str, Callable[[str, Dict[str, Any]], Dict[str, Any]]] = {
    "tests": run_tests,
    "lint": run_lint,
    "types": run_types,
    "dead_code": run_dead_code,
    "complexity": run_complexity,
    "duplicates": run_duplicates,
    "deps_hygiene": run_deps,
    "docstrings": run_docstrings,
}


def run_all(root: str, cfg: Dict[str, Any], which: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    names = [n for n in (which or ALL) if n in RUNNERS]
    out: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {n: pool.submit(RUNNERS[n], root, cfg) for n in names}
        for n, fut in futs.items():
            try:
                out[n] = fut.result()
            except Exception as exc:  # a broken tool must not break the hook
                out[n] = _skip("crashed: %r" % exc)
    if "tests" in out and isinstance(out["tests"].get("coverage"), dict):
        out["coverage"] = dict(out["tests"]["coverage"], ran=True)
    out["at"] = util.now_iso()
    return out


# ---------------------------------------------------------------- comparison

def _file_of(key: str) -> str:
    return key.split(":", 1)[0]


def compare(base: Dict[str, Any], cur: Dict[str, Any], touched: Sequence[str], have_baseline: bool) -> Dict[str, List[str]]:
    """New problems per check. Without a baseline, everything counts as new."""
    touched_set = set(touched)
    new: Dict[str, List[str]] = {}
    for name in ("lint", "types", "dead_code", "deps_hygiene"):
        c = cur.get(name) or {}
        if not c.get("ran"):
            continue
        b = set((base.get(name) or {}).get("items", [])) if have_baseline else set()
        diff = [k for k in c.get("items", []) if k not in b]
        if diff:
            new[name] = diff
    for name in ("complexity", "docstrings"):
        c = cur.get(name) or {}
        if not c.get("ran"):
            continue
        b = set((base.get(name) or {}).get("items", [])) if have_baseline else set()
        diff = [k for k in c.get("items", []) if k not in b and _file_of(k) in touched_set]
        if diff:
            new[name] = diff
    c = cur.get("duplicates") or {}
    if c.get("ran"):
        b = set((base.get("duplicates") or {}).get("items", [])) if have_baseline else set()
        desc = c.get("describe", {})
        diff = [desc.get(k, k) for k in c.get("items", []) if k not in b and any(t in k for t in touched_set)]
        if diff:
            new["duplicates"] = diff
    t = cur.get("tests") or {}
    if t.get("ran"):
        b = set((base.get("tests") or {}).get("failing", [])) if have_baseline else set()
        diff = [x for x in t.get("failing", []) if x not in b]
        if diff:
            new["tests"] = diff
        bt = base.get("tests") or {}
        if have_baseline and bt.get("ran") and "ids" in bt and "ids" in t:
            gone = sorted(set(bt["ids"]) - set(t["ids"]))
            if gone:
                new["removed_tests"] = gone
    cov = cur.get("coverage") or {}
    if cov.get("ran"):
        btotal = (base.get("coverage") or {}).get("total") if have_baseline else None
        if btotal is not None and cov.get("total") is not None and cov["total"] < btotal - 2.0:
            new.setdefault("coverage", []).append("total coverage fell %.1f%% -> %.1f%%" % (btotal, cov["total"]))
        bfiles = (base.get("coverage") or {}).get("files", {}) if have_baseline else {}
        drops = []
        for f in touched:
            if f not in cov.get("files", {}) or f.startswith("tests/") or os.path.basename(f).startswith("test_"):
                continue
            now = cov["files"][f]
            before = bfiles.get(f)
            if before is not None and now < before - 1.0:
                drops.append("%s coverage fell %.0f%% -> %.0f%%" % (f, before, now))
            elif before is None and now < 50.0:
                drops.append("%s is only %.0f%% covered by tests" % (f, now))
        if drops:
            new.setdefault("coverage", []).extend(drops)
    return new


def summary_line(cur: Dict[str, Any], base: Dict[str, Any], have_baseline: bool) -> List[str]:
    """Human evidence lines for the ledger, straight from tool output."""
    lines = []
    t = cur.get("tests") or {}
    if t.get("ran"):
        bp = (base.get("tests") or {}).get("passed") if have_baseline else None
        s = "pytest: %d passed, %d failed" % (t.get("passed", 0), t.get("failed", 0))
        if t.get("skipped"):
            s += ", %d skipped" % t["skipped"]
        if bp is not None:
            s += " (baseline %d passed, %d failed)" % (bp, len((base.get("tests") or {}).get("failing", [])))
        if t.get("note"):
            s += " (" + t["note"] + ")"
        lines.append(s)
    else:
        lines.append("pytest: not run (%s)" % t.get("note", "unknown"))
    for name, label in (("lint", "ruff"), ("types", "types")):
        c = cur.get(name) or {}
        if c.get("ran"):
            b = len((base.get(name) or {}).get("items", [])) if have_baseline else None
            lines.append("%s: %d issues%s" % (label, len(c.get("items", [])), " (baseline %d)" % b if b is not None else ""))
        else:
            lines.append("%s: not run (%s)" % (label, c.get("note", "unknown")))
    cov = cur.get("coverage") or {}
    if cov.get("ran") and cov.get("total") is not None:
        bt = (base.get("coverage") or {}).get("total") if have_baseline else None
        lines.append("coverage: %.1f%%%s" % (cov["total"], " (baseline %.1f%%)" % bt if bt is not None else ""))
    return lines
