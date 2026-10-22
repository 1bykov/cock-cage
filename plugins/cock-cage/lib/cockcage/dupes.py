"""Duplicate-code detection, standard library only.

Two detectors:
  window  copy-paste clones: N consecutive normalised source lines appearing twice
  ast     structural clones: functions whose bodies are identical once names are erased
The second catches renamed copies that text matching misses. Neither catches the same
idea written in a different shape; that is what the reviewer subagent is for.
"""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from . import util

TRIVIAL = {"", ")", "]", "}", "),", "],", "},", "pass", "else:", "try:", "finally:", "return", "continue", "break"}


def _significant_lines(src: str) -> List[Tuple[int, str]]:
    """(lineno, normalised text) with comments, docstrings, imports and trivial lines removed."""
    drop: set = set()
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    end = getattr(body[0], "end_lineno", body[0].lineno)
                    drop.update(range(body[0].lineno, end + 1))
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                end = getattr(node, "end_lineno", node.lineno)
                drop.update(range(node.lineno, end + 1))
    except (SyntaxError, ValueError):
        return []
    comments: Dict[int, int] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                comments[tok.start[0]] = tok.start[1]
    except (tokenize.TokenError, IndentationError):
        pass
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in drop:
            continue
        if i in comments:
            line = line[: comments[i]]
        norm = " ".join(line.split())
        if norm in TRIVIAL or norm.startswith(("@", "def ", "async def ", "class ")):
            continue
        out.append((i, norm))
    return out


class _Eraser(ast.NodeTransformer):
    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = "_"
        node.annotation = None
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)


def _stmt_count(node: ast.AST) -> int:
    return sum(1 for n in ast.walk(node) if isinstance(n, ast.stmt)) - 1


def find(root: str, files: Sequence[str], min_lines: int = 6, min_stmts: int = 4,
         focus: Optional[Sequence[str]] = None) -> List[Dict[str, object]]:
    """Return clone groups. If focus is given, keep only groups touching those files."""
    windows: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    shapes: Dict[str, List[Tuple[str, int, str]]] = defaultdict(list)
    for f in files:
        relp = util.rel(root, f)
        src = util.read_text(f)
        lines = _significant_lines(src)
        for k in range(0, max(0, len(lines) - min_lines + 1)):
            chunk = "\n".join(t for _, t in lines[k: k + min_lines])
            h = hashlib.sha1(chunk.encode()).hexdigest()[:16]
            windows[h].append((relp, lines[k][0]))
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _stmt_count(node) >= min_stmts:
                body = ast.Module(body=[_Eraser().visit(ast.parse(ast.unparse(s))) for s in node.body], type_ignores=[])
                h = hashlib.sha1(ast.dump(body).encode()).hexdigest()[:16]
                shapes[h].append((relp, node.lineno, node.name))
    groups: List[Dict[str, object]] = []
    # Merge overlapping window hits into one group per file pair.
    seen_pairs: Dict[Tuple[str, ...], Dict[str, object]] = {}
    for h, locs in windows.items():
        uniq = sorted(set(locs))
        if len(uniq) < 2:
            continue
        # ignore self-overlapping windows inside one file
        if len({f for f, _ in uniq}) == 1 and max(l for _, l in uniq) - min(l for _, l in uniq) < min_lines:
            continue
        pair = tuple(sorted({f for f, _ in uniq}))
        g = seen_pairs.get(pair)
        if g is None:
            g = {"kind": "copy-paste", "locations": uniq[:4], "key": "window:" + "|".join(pair), "size": min_lines}
            seen_pairs[pair] = g
        else:
            g["size"] = int(g["size"]) + 1  # type: ignore[operator]
    groups.extend(seen_pairs.values())
    for h, locs in shapes.items():
        if len(locs) < 2:
            continue
        files_in = sorted({f for f, _, _ in locs})
        groups.append({
            "kind": "same-shape functions",
            "locations": [(f, l) for f, l, _ in sorted(locs)][:4],
            "names": [n for _, _, n in sorted(locs)][:4],
            "key": "ast:%s:%s" % (h, "|".join(files_in)),
        })
    if focus:
        fset = {util.rel(root, f) if f.startswith("/") else f for f in focus}
        groups = [g for g in groups if any(loc[0] in fset for loc in g["locations"])]  # type: ignore[index]
    return groups


def describe(g: Dict[str, object]) -> str:
    locs = ", ".join("%s:%s" % (f, l) for f, l in g["locations"])  # type: ignore[misc]
    if g["kind"] == "same-shape functions":
        return "same-shape functions %s at %s" % (", ".join(g.get("names", [])), locs)  # type: ignore[arg-type]
    return "copy-paste block (~%s lines) at %s" % (g.get("size"), locs)
