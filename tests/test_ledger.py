import os
import sys

import pytest

from cockcage import ledger


@pytest.mark.skipif(sys.platform != "darwin", reason="chflags uappnd is macOS only")
def test_macos_append_only_flag(tmp_path):
    root = str(tmp_path)
    ledger.append(root, "T", "body", os_lock=True)
    rp, sp = ledger.paths(root)
    with pytest.raises(PermissionError):
        open(rp, "w").close()          # overwrite refused by the OS
    with pytest.raises(PermissionError):
        os.unlink(rp)                  # deletion refused by the OS
    ledger.append(root, "T2", "body2", os_lock=True)  # appending still works
    assert ledger.verify(root)[0]
    assert ledger.unlock(root)
    open(rp, "a").write("\nuser edit\n")
    ledger.reseal(root, os_lock=True)
    assert ledger.verify(root)[0]
    ledger.unlock(root)  # let pytest clean up tmp_path


def test_append_and_verify(tmp_path):
    root = str(tmp_path)
    assert ledger.verify(root) == (True, [])
    assert ledger.append(root, "First", "body one", os_lock=False) == 1
    assert ledger.append(root, "Second", "body two", os_lock=False) == 2
    ok, problems = ledger.verify(root)
    assert ok, problems
    assert ledger.entry_count(root) == 2
    assert "body two" in ledger.last_entry(root)


def test_edit_of_earlier_entry_detected(tmp_path):
    root = str(tmp_path)
    for i in range(3):
        ledger.append(root, "T%d" % i, "body %d" % i, os_lock=False)
    rp, _ = ledger.paths(root)
    text = open(rp).read().replace("body 0", "body zero")
    open(rp, "w").write(text)
    ok, problems = ledger.verify(root)
    assert not ok
    assert any("changed" in p for p in problems)


def test_truncation_of_last_entry_detected(tmp_path):
    root = str(tmp_path)
    for i in range(3):
        ledger.append(root, "T%d" % i, "body %d" % i, os_lock=False)
    rp, _ = ledger.paths(root)
    data = open(rp, "rb").read()
    cut = data.rfind(b"<!-- cc:entry n=3")
    open(rp, "wb").write(data[:cut])
    ok, problems = ledger.verify(root)
    assert not ok
    assert any("seal" in p for p in problems)


def test_emptied_or_deleted_ledger_detected(tmp_path):
    root = str(tmp_path)
    ledger.append(root, "T", "body", os_lock=False)
    rp, _ = ledger.paths(root)
    open(rp, "w").close()
    assert not ledger.verify(root)[0]
    import os
    os.unlink(rp)
    assert not ledger.verify(root)[0]


def test_user_reseal_after_deleting_middle_entry(tmp_path):
    root = str(tmp_path)
    for i in range(3):
        ledger.append(root, "T%d" % i, "body %d" % i, os_lock=False)
    rp, _ = ledger.paths(root)
    data = open(rp, "rb").read()
    start = data.find(b"<!-- cc:entry n=2")
    end = data.find(b"<!-- cc:entry n=3")
    open(rp, "wb").write(data[:start] + data[end:])
    assert not ledger.verify(root)[0]
    assert ledger.reseal(root, os_lock=False) == 2
    ok, problems = ledger.verify(root)
    assert ok, problems
    text = open(rp).read()
    assert "## #2 · " in text and "body 2" in text and "body 1" not in text
    # appending after a reseal keeps the chain valid
    ledger.append(root, "T3", "body 3", os_lock=False)
    assert ledger.verify(root)[0]
