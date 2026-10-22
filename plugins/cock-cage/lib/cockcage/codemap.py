"""Signature map of the codebase built with the stdlib ast module.

Claude gets the layout (modules, classes, functions, signatures, first docstring line)
for a fraction of the tokens it would spend opening files to find things.
"""

from __future__ import annotations

import ast
import os
from typing import Dict, List, Optional, Tuple

from . import util

DOC_CHARS = 72


def _doc(node: ast.AST) -> str:
    try:
        d = ast.get_docstring(node)  # type: ignore[arg-type]
    except TypeError:
        return ""
    if not d:
        return ""
    line = d.strip().splitlines()[0].strip()
    return (line[: DOC_CHARS - 1] + "…") if len(line) > DOC_CHARS else line


def _args(a: ast.arguments) -> str:
    parts: List[str] = []
    posonly = list(getattr(a, "posonlyargs", []))
    regular = posonly + list(a.args)
    defaults = [None] * (len(regular) - len(a.defaults)) + list(a.defaults)
    for i, (arg, d) in enumerate(zip(regular, defaults)):
        s = arg.arg
        if arg.annotation is not None:
            s += ": " + ast.unparse(arg.annotation)
        if d is not None:
            s += "=…"
        parts.append(s)
        if posonly and i == len(posonly) - 1:
            parts.append("/")
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):
        s = arg.arg + ("=…" if d is not None else "")
        parts.append(s)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return ", ".join(parts)


def _func(node: ast.AST, indent: str) -> str:
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    sig = "%s%s(%s)" % (prefix, node.name, _args(node.args))
    if node.returns is not None:
        sig += " -> " + ast.unparse(node.returns)
    doc = _doc(node)
    return "%s- `%s` L%d%s" % (indent, sig, node.lineno, (": " + doc) if doc else "")


def summarise_file(path: str, root: str) -> Tuple[str, int]:
    """Return (markdown block, symbol count) for one file."""
    relp = util.rel(root, path)
    src = util.read_text(path)
    nlines = src.count("\n") + (1 if src and not src.endswith("\n") else 0)
    try:
        tree = ast.parse(src, filename=relp)
    except (SyntaxError, ValueError):
        return "### %s (%d lines, does not parse)\n" % (relp, nlines), 0
    is_test = os.path.basename(path).startswith("test_") or "/tests/" in "/" + relp.replace(os.sep, "/")
    lines = ["### %s (%d lines)" % (relp, nlines)]
    doc = _doc(tree)
    if doc:
        lines.append("_%s_" % doc)
    count = 0
    if is_test:
        tests = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]
        lines.append("- %d tests" % len(tests))
        return "\n".join(lines) + "\n", len(tests)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(_func(node, ""))
            count += 1
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            cdoc = _doc(node)
            lines.append("- `class %s%s` L%d%s" % (node.name, "(%s)" % bases if bases else "", node.lineno,
                                                 (": " + cdoc) if cdoc else ""))
            count += 1
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if sub.name.startswith("__") and sub.name not in ("__init__", "__call__"):
                        continue
                    lines.append(_func(sub, "  "))
                    count += 1
    return "\n".join(lines) + "\n", count


def cache_path(root: str) -> str:
    return util.cc_path(root, "codemap-cache.json")


def map_path(root: str) -> str:
    return util.cc_path(root, "codemap.md")


def build(root: str, src_dirs: Optional[List[str]] = None, changed: Optional[List[str]] = None) -> Dict[str, object]:
    """(Re)build the map, re-parsing only files whose mtime or size changed."""
    cache: Dict[str, Dict[str, object]] = util.read_json(cache_path(root), {}) or {}
    files = util.iter_py_files(root, src_dirs)
    seen = set()
    for f in files:
        relp = util.rel(root, f)
        seen.add(relp)
        try:
            st = os.stat(f)
        except OSError:
            continue
        key = "%d:%d" % (int(st.st_mtime), st.st_size)
        ent = cache.get(relp)
        if ent and ent.get("key") == key and not (changed and relp in changed):
            continue
        block, count = summarise_file(f, root)
        cache[relp] = {"key": key, "block": block, "count": count, "lines": util.count_lines(f)}
    for gone in [k for k in cache if k not in seen]:
        del cache[gone]
    util.write_json(cache_path(root), cache)
    total_syms = sum(int(v.get("count", 0)) for v in cache.values())
    body = "# Code map\n\nGenerated by cock-cage. %d Python files, %d symbols. `L<n>` is the line number.\n\n" % (
        len(cache), total_syms)
    body += "\n".join(str(cache[k]["block"]) for k in sorted(cache)) if cache else "_No Python files yet._\n"
    util.write_text(map_path(root), body)
    return {"files": len(cache), "symbols": total_syms, "text": body}


def context_snippet(root: str, max_chars: int) -> str:
    """What SessionStart injects: the full map if small, otherwise a module index."""
    text = util.read_text(map_path(root))
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    cache = util.read_json(cache_path(root), {}) or {}
    lines = ["# Code map (index; full map in .cock-cage/codemap.md)", ""]
    for k in sorted(cache):
        lines.append("- %s: %s symbols, %s lines" % (k, cache[k].get("count", 0), cache[k].get("lines", "?")))
        if sum(len(x) + 1 for x in lines) > max_chars:
            lines.append("- … %d more files" % (len(cache) - len(lines) + 3))
            break
    return "\n".join(lines) + "\n"
