"""Tests for the checkpoint-integrity hardening added to
collector.read_new_cycles() / collector.maybe_rotate() by
MES_IMPLEMENTATION_BACKLOG.md ticket 1.4 (see
postgres/init/19_add_checkpoint_integrity.sql and MES_TARGET_ARCHITECTURE.md
§5.3 "Idempotence a checkpointy").

Reuses the FakeDB/FakeCursor/FakeConn mocked-DB pattern established in
test_read_new_cycles_dedup.py (see that module's docstring for why: no
Docker/real Postgres available in this sandbox, and psycopg2 doesn't build
against this Python 3.14). FakeDB.collector_state entries are dicts with
keys "reports_lines_read"/"last_line_hash"/"reports_dat_size_at_checkpoint"
to mirror the columns added by this ticket's migration.

Covers:
  1. Truncation detection - REPORTS.DAT smaller than the recorded
     reports_dat_size_at_checkpoint -> warning logged, treated as a fresh
     read from position 0 (not "0 new cycles forever").
  2. Hash-mismatch detection - file same/larger size but the content at the
     checkpointed line no longer matches last_line_hash -> warning logged,
     treated as a fresh read from position 0.
  3. Holdback - a malformed *last* line in the file does not advance
     reports_lines_read past it (retried next poll); once a newer line is
     appended after it, the old logic (warn + skip + advance) applies to it
     as usual because it is no longer the last line.
  4. Regression - a malformed line that is NOT the last line in the file
     keeps today's exact behaviour (skip + still advance the checkpoint
     past it) - ticket 1.4's holdback must not accidentally apply there.
  5. Normal path (no truncation/hash-mismatch/malformed lines) still sets
     last_line_hash/reports_dat_size_at_checkpoint correctly alongside
     reports_lines_read.
"""
import hashlib
import logging
import os

import pytest

import collector
from test_read_new_cycles_dedup import FakeConn, FakeDB


@pytest.fixture(autouse=True)
def _isolated_reports_dat(tmp_path, monkeypatch):
    """Same isolation fixture as test_read_new_cycles_dedup.py (autouse
    fixtures don't cross module boundaries in pytest, so it's duplicated
    here rather than shared) - point collector.REPORTS_DAT at a per-test
    tmp file and pin MACHINE_CODE.
    """
    reports_dat = tmp_path / "REPORTS.DAT"
    monkeypatch.setattr(collector, "REPORTS_DAT", str(reports_dat))
    monkeypatch.setattr(collector, "MACHINE_CODE", "KM-MC5-TEST")
    return reports_dat


def _sha256(line):
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


HEADER = "ActCntCyc,ActTimCyc\n"


def test_normal_path_records_hash_and_size_alongside_line_count():
    db = FakeDB()
    conn = FakeConn(db)
    content = HEADER + "100,12.5\n101,12.5\n102,12.5\n"
    _write(collector.REPORTS_DAT, content)

    inserted = collector.read_new_cycles(conn)

    assert inserted == 3
    state = db.collector_state["KM-MC5-TEST"]
    assert state["reports_lines_read"] == 3
    assert state["last_line_hash"] == _sha256("102,12.5")
    assert state["reports_dat_size_at_checkpoint"] == os.path.getsize(collector.REPORTS_DAT)


def test_truncation_smaller_than_checkpoint_is_treated_as_fresh_read(caplog):
    db = FakeDB()
    # Pretend a previous poll had already consumed 5 lines of a much bigger
    # file than what's on disk now (simulating the file having been
    # truncated/replaced outside maybe_rotate()'s own controlled path).
    db.collector_state["KM-MC5-TEST"] = {
        "reports_lines_read": 5,
        "last_line_hash": _sha256("999,12.5"),
        "reports_dat_size_at_checkpoint": 10_000,
    }
    conn = FakeConn(db)
    content = HEADER + "100,12.5\n101,12.5\n"
    _write(collector.REPORTS_DAT, content)
    assert os.path.getsize(collector.REPORTS_DAT) < 10_000

    with caplog.at_level(logging.WARNING, logger="euromap63-collector"):
        inserted = collector.read_new_cycles(conn)

    # Treated as fresh: both lines (re-)read from position 0, not "0 new
    # cycles forever" (the pre-1.4 bug).
    assert inserted == 2
    assert {c["cycle_count"] for c in db.cycles} == {100, 101}
    assert db.collector_state["KM-MC5-TEST"]["reports_lines_read"] == 2
    assert any("zkracen" in r.message for r in caplog.records)


def test_hash_mismatch_at_checkpoint_line_is_treated_as_fresh_read(caplog):
    db = FakeDB()
    # File is same-or-larger size than recorded, so the pure size check
    # would not catch this - but the content at the checkpointed line
    # (index last_count - 1 == 1, i.e. the 2nd data line) no longer matches
    # what was hashed at the time of the last checkpoint. This simulates
    # the file having been rewritten with different content at/after that
    # position (not just truncated).
    db.collector_state["KM-MC5-TEST"] = {
        "reports_lines_read": 2,
        "last_line_hash": _sha256("101,99.9"),  # does NOT match line below
        "reports_dat_size_at_checkpoint": 10,
    }
    conn = FakeConn(db)
    content = HEADER + "100,12.5\n101,12.5\n102,12.5\n"
    _write(collector.REPORTS_DAT, content)
    assert os.path.getsize(collector.REPORTS_DAT) >= 10

    with caplog.at_level(logging.WARNING, logger="euromap63-collector"):
        inserted = collector.read_new_cycles(conn)

    assert inserted == 3
    assert {c["cycle_count"] for c in db.cycles} == {100, 101, 102}
    assert db.collector_state["KM-MC5-TEST"]["reports_lines_read"] == 3
    assert any("hashi" in r.message for r in caplog.records)


def test_matching_hash_at_checkpoint_line_is_not_flagged():
    """Sanity companion to the mismatch test above: when last_line_hash
    genuinely matches data_lines[last_count - 1], no fresh-read reset
    should happen and only the genuinely new lines get processed.
    """
    db = FakeDB()
    db.collector_state["KM-MC5-TEST"] = {
        "reports_lines_read": 2,
        "last_line_hash": _sha256("101,12.5"),  # matches line below
        "reports_dat_size_at_checkpoint": 10,
    }
    conn = FakeConn(db)
    content = HEADER + "100,12.5\n101,12.5\n102,12.5\n"
    _write(collector.REPORTS_DAT, content)

    inserted = collector.read_new_cycles(conn)

    assert inserted == 1
    assert {c["cycle_count"] for c in db.cycles} == {102}
    assert db.collector_state["KM-MC5-TEST"]["reports_lines_read"] == 3


def test_malformed_last_line_is_held_back_and_not_counted_in_checkpoint(caplog):
    db = FakeDB()
    conn = FakeConn(db)
    # Third line has only one column - looks like the machine was still
    # mid-write on it at the moment of this poll.
    content = HEADER + "100,12.5\n101,12.5\n102\n"
    _write(collector.REPORTS_DAT, content)

    with caplog.at_level(logging.WARNING, logger="euromap63-collector"):
        inserted = collector.read_new_cycles(conn)

    assert inserted == 2
    assert {c["cycle_count"] for c in db.cycles} == {100, 101}
    state = db.collector_state["KM-MC5-TEST"]
    # Checkpoint held back one line short of the file's current line count
    # (3 data lines on disk, but only 2 counted as consumed).
    assert state["reports_lines_read"] == 2
    assert state["last_line_hash"] == _sha256("101,12.5")
    assert any("nekompletni" in r.message for r in caplog.records)

    # Next poll: the machine finishes writing the line and appends a new
    # one after it. The old malformed line is no longer last, so it now
    # gets the ordinary warn+skip+advance treatment, and the new line after
    # it is also picked up.
    content2 = HEADER + "100,12.5\n101,12.5\n102\n103,12.5\n"
    _write(collector.REPORTS_DAT, content2)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="euromap63-collector"):
        inserted2 = collector.read_new_cycles(conn)

    assert inserted2 == 1
    assert {c["cycle_count"] for c in db.cycles} == {100, 101, 103}
    assert db.collector_state["KM-MC5-TEST"]["reports_lines_read"] == 4
    assert any("Preskakuji poskozeny radek" in r.message for r in caplog.records)


def test_malformed_line_not_last_still_advances_checkpoint_as_before(caplog):
    """Regression guard: holdback must only apply to the line that is
    currently last in the whole file. A malformed line anywhere else keeps
    the pre-1.4 behaviour exactly - skip it, but still count it toward the
    checkpoint advance.
    """
    db = FakeDB()
    conn = FakeConn(db)
    content = HEADER + "100,12.5\n101\n102,12.5\n"
    _write(collector.REPORTS_DAT, content)

    with caplog.at_level(logging.WARNING, logger="euromap63-collector"):
        inserted = collector.read_new_cycles(conn)

    assert inserted == 2
    assert {c["cycle_count"] for c in db.cycles} == {100, 102}
    # All 3 data lines counted, including the malformed (but non-last) one.
    assert db.collector_state["KM-MC5-TEST"]["reports_lines_read"] == 3
    assert any("Preskakuji poskozeny radek" in r.message for r in caplog.records)
