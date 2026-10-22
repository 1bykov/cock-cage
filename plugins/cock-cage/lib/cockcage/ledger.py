"""Append-only, hash-chained report ledger.

reports.md   every entry starts with a marker recording the SHA-256 of all bytes above it
ledger.seal  append-only lines "n=<entries> sha=<sha256 of whole file> at=<time>"

The chain detects edits to earlier entries. The seal detects truncation of the last ones.
On macOS the files also carry the user append-only flag (chflags uappnd), which blocks
overwrites, edits, renames and deletion at the OS level until the owner lifts it.
"""

from __future__ import annotations

import os
import re
import sys
from typing import List, Optional, Tuple

from . import util

HEADER = (
    "# cock-cage report ledger\n\n"
    "Append-only. The cock-cage Stop hook adds one entry per finished task, and each entry\n"
    "records the SHA-256 of everything above it. To remove entries yourself, run\n"
    "`cockcage ledger unlock`, edit this file, then `cockcage ledger reseal`.\n"
)
MARKER_RE = re.compile(rb"<!-- cc:entry n=(\d+) prev=([0-9a-f]{64}) -->\n")
SEAL_RE = re.compile(r"n=(\d+) sha=([0-9a-f]{64})")


def paths(root: str) -> Tuple[str, str]:
    return util.cc_path(root, "reports.md"), util.cc_path(root, "ledger.seal")


def _record_path(root: str) -> str:
    """A copy of the latest seal kept outside the project, so deleting the whole ledger is noticed."""
    key = util.sha256_bytes(os.path.realpath(root).encode())[:16]
    return os.path.join(util.user_dir(), "ledgers", key + ".json")


def _write_record(root: str, n: int, sha: str) -> None:
    try:
        util.write_json(_record_path(root), {"project": os.path.realpath(root), "n": n, "sha": sha, "at": util.now_iso()})
    except OSError:
        pass


def _escape(text: str) -> str:
    return text.replace("<!-- cc:entry", "<!-- cc-entry")


def _read_bytes(p: str) -> bytes:
    try:
        with open(p, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


# ---------------------------------------------------------------- OS flag (macOS)

def _chflags(flag: str, *files: str) -> bool:
    if sys.platform != "darwin":
        return False
    existing = [f for f in files if os.path.exists(f)]
    if not existing:
        return False
    code, _, _ = util.run(["chflags", flag] + existing, cwd=os.path.dirname(existing[0]), timeout=10)
    return code == 0


def lock(root: str) -> bool:
    return _chflags("uappnd", *paths(root))


def unlock(root: str) -> bool:
    return _chflags("nouappnd", *paths(root))


# ---------------------------------------------------------------- read / verify

def entry_count(root: str) -> int:
    return len(MARKER_RE.findall(_read_bytes(paths(root)[0])))


def verify(root: str) -> Tuple[bool, List[str]]:
    rp, sp = paths(root)
    data = _read_bytes(rp)
    record = util.read_json(_record_path(root), None)
    if not data and not util.read_text(sp).strip():
        if record and int(record.get("n", 0)) > 0:
            return False, ["the ledger and its seal are gone; %s report(s) were recorded" % record["n"]]
        return True, []  # nothing logged yet
    problems: List[str] = []
    markers = list(MARKER_RE.finditer(data))
    for i, m in enumerate(markers, 1):
        n, prev = int(m.group(1)), m.group(2).decode()
        if n != i:
            problems.append("entry numbering jumps at #%d (expected #%d)" % (n, i))
        if util.sha256_bytes(data[: m.start()]) != prev:
            problems.append("content above entry #%d was changed" % n)
            break
    seal_lines = [ln for ln in util.read_text(sp).splitlines() if ln.strip()]
    if not seal_lines:
        problems.append("ledger.seal is missing or empty")
    else:
        m = SEAL_RE.search(seal_lines[-1])
        if not m:
            problems.append("ledger.seal last line is malformed")
        else:
            if int(m.group(1)) != len(markers):
                problems.append("seal expects %s entries, file has %d" % (m.group(1), len(markers)))
            if m.group(2) != util.sha256_bytes(data):
                problems.append("file hash differs from the seal (entries edited or removed)")
    if record and (int(record.get("n", 0)) != len(markers) or record.get("sha") != util.sha256_bytes(data)) and not problems:
        problems.append("the ledger no longer matches the copy of its seal kept outside the project")
    return (not problems), problems


# ---------------------------------------------------------------- append

def append(root: str, title: str, body: str, os_lock: bool = True) -> int:
    """Append one entry and extend the seal. Returns the new entry number."""
    rp, sp = paths(root)
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    if not os.path.exists(rp):
        with open(rp, "w", encoding="utf-8") as fh:
            fh.write(HEADER)
    n = entry_count(root) + 1
    # A blank separator line belongs to the region above the marker, so write it first,
    # then hash everything up to the marker position.
    with open(rp, "ab") as fh:
        fh.write(b"\n")
    data = _read_bytes(rp)
    marker = "<!-- cc:entry n=%d prev=%s -->\n" % (n, util.sha256_bytes(data))
    stamp = util.now_iso()[:16].replace("T", " ")
    text = marker + "## #%d · %s · %s\n\n%s\n" % (n, stamp, _escape(title.strip()), _escape(body.strip()))
    with open(rp, "ab") as fh:
        fh.write(text.encode("utf-8"))
    full = _read_bytes(rp)
    with open(sp, "a", encoding="utf-8") as fh:
        fh.write("n=%d sha=%s at=%s\n" % (n, util.sha256_bytes(full), util.now_iso()))
    _write_record(root, n, util.sha256_bytes(full))
    if os_lock:
        lock(root)
    return n


# ---------------------------------------------------------------- reseal (user only)

def reseal(root: str, os_lock: bool = True) -> int:
    """Rebuild numbering, chain and seal after the user's own edits. Returns entry count."""
    rp, sp = paths(root)
    unlock(root)
    data = _read_bytes(rp)
    if not data:
        util.write_text(sp, "")
        _write_record(root, 0, util.sha256_bytes(b""))
        return 0
    markers = list(MARKER_RE.finditer(data))
    head = data[: markers[0].start()] if markers else data
    bodies: List[bytes] = []
    for i, m in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(data)
        body = data[m.end():end]
        bodies.append(body)
    out = head.rstrip(b"\n") + b"\n"
    for i, body in enumerate(bodies, 1):
        body = re.sub(rb"^## #\d+ ", b"## #%d " % i, body, count=1).rstrip(b"\n") + b"\n"
        out += b"\n"
        marker = b"<!-- cc:entry n=%d prev=%s -->\n" % (i, util.sha256_bytes(out).encode())
        out = out + marker + body
    with open(rp, "wb") as fh:
        fh.write(out)
    with open(sp, "w", encoding="utf-8") as fh:
        fh.write("n=%d sha=%s at=%s resealed-by-user\n" % (len(bodies), util.sha256_bytes(out), util.now_iso()))
    _write_record(root, len(bodies), util.sha256_bytes(out))
    if os_lock:
        lock(root)
    return len(bodies)


def delete_entries(root: str, numbers: List[int], os_lock: bool = True) -> int:
    """Remove entries by number, then renumber and reseal. Only reachable through an approved request."""
    rp, _ = paths(root)
    unlock(root)
    data = _read_bytes(rp)
    markers = list(MARKER_RE.finditer(data))
    drop = set(int(n) for n in numbers)
    missing = drop - {int(m.group(1)) for m in markers}
    if missing:
        if os_lock:
            lock(root)
        raise ValueError("no such report(s): %s" % ", ".join("#%d" % n for n in sorted(missing)))
    out = data[: markers[0].start()] if markers else data
    for i, m in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(data)
        if int(m.group(1)) not in drop:
            out += data[m.start():end]
    with open(rp, "wb") as fh:
        fh.write(out)
    return reseal(root, os_lock)


def last_entry(root: str) -> Optional[str]:
    data = _read_bytes(paths(root)[0])
    markers = list(MARKER_RE.finditer(data))
    if not markers:
        return None
    return data[markers[-1].end():].decode("utf-8", "replace").strip()
