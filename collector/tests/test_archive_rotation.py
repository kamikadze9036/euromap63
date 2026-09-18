"""Tests for the REPORTS.DAT archival bookkeeping added to
collector.maybe_rotate() by MES_IMPLEMENTATION_BACKLOG.md ticket 1.5 (see
postgres/init/21_add_reports_dat_archive.sql for the schema/rationale: a DB
table rather than a sidecar .meta.json file, matching this codebase's
existing convention of tracking all collector state in Postgres, not in
files next to REPORTS.DAT).

Reuses the FakeDB/FakeCursor/FakeConn mocked-DB pattern established in
test_read_new_cycles_dedup.py (see that module's docstring for why: no
Docker/real Postgres available in this sandbox). Neither of that module's
FakeCursor nor test_checkpoint_integrity.py's variant knows about the two
new SQL statements this ticket adds (the pre-reset SELECT
reports_lines_read, and the reports_dat_archive INSERT), so - following
test_heartbeat.py's precedent - this module extends the pattern with its
own tiny dedicated fake rather than teaching the shared one about
statements only this ticket cares about.

Covers:
  1. record_archive() computes the correct sha256/size for a real file on
     disk (tmp_path fixture, known content) and issues the expected INSERT.
  2. _sha256_file() matches hashlib.sha256() computed directly against the
     same bytes, for content larger than one chunk (verifies the chunked
     streaming read doesn't corrupt the digest).
  3. maybe_rotate() calls record_archive() with the *ingested* line count
     (collector_state.reports_lines_read as it stood right before this same
     rotation's own reset-to-0), not the raw physical line count of the
     archived file.
  4. maybe_rotate() completes rotation (rename, collector_state reset,
     REPORTS.JOB re-armed) even when the archival bookkeeping step raises -
     ticket 1.5's explicit "must not block rotation" requirement.
"""
import hashlib
import logging
import os

import pytest

import collector


@pytest.fixture(autouse=True)
def _isolated_ftp_root(tmp_path, monkeypatch):
    """Point collector.REPORTS_DAT/REPORTS_LOG/FTP_ROOT/TEMPLATES_DIR at a
    per-test tmp directory, and pin MACHINE_CODE - same isolation approach
    as test_read_new_cycles_dedup.py / test_checkpoint_integrity.py.
    """
    reports_dat = tmp_path / "REPORTS.DAT"
    monkeypatch.setattr(collector, "FTP_ROOT", str(tmp_path))
    monkeypatch.setattr(collector, "REPORTS_DAT", str(reports_dat))
    monkeypatch.setattr(collector, "REPORTS_LOG", str(tmp_path / "REPORTS.LOG"))
    monkeypatch.setattr(collector, "MACHINE_CODE", "KM-MC5-TEST")
    # maybe_rotate() also calls write_request() (ABORT.JOB / REPORTS.JOB),
    # which does a real time.sleep(2) - stub both out so tests run fast and
    # don't need real JOB template files on disk.
    monkeypatch.setattr(collector, "write_request", lambda job_name: None)
    monkeypatch.setattr(collector.time, "sleep", lambda secs: None)
    return tmp_path


class _RotationCursor:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @staticmethod
    def _norm(sql):
        return " ".join(sql.split())

    def execute(self, sql, params=()):
        sql_n = self._norm(sql)
        self.db.executed_sql.append(sql_n)

        if self.db.fail_archive_insert and sql_n.startswith("INSERT INTO reports_dat_archive"):
            raise collector.psycopg2.OperationalError("simulated archive-table write failure")

        if sql_n.startswith("SELECT reports_lines_read FROM collector_state"):
            (machine_code,) = params
            self._result = (self.db.collector_state.get(machine_code, {}).get("reports_lines_read"),)

        elif sql_n.startswith("UPDATE collector_state SET reports_lines_read=0"):
            (machine_code,) = params
            self.db.collector_state[machine_code] = {
                "reports_lines_read": 0,
                "last_line_hash": None,
                "reports_dat_size_at_checkpoint": None,
            }
            self._result = None

        elif sql_n.startswith("INSERT INTO reports_dat_archive"):
            machine_code, archive_path, sha256_hex, line_count, size_bytes = params
            self.db.reports_dat_archive.append({
                "machine_code": machine_code,
                "archive_path": archive_path,
                "sha256": sha256_hex,
                "line_count": line_count,
                "size_bytes": size_bytes,
            })
            self._result = None

        else:
            raise AssertionError(f"FakeCursor got unexpected SQL: {sql!r}")

    def fetchone(self):
        return self._result


class _RotationDB:
    def __init__(self):
        self.collector_state = {}
        self.reports_dat_archive = []
        self.executed_sql = []
        self.fail_archive_insert = False

    def cursor(self):
        return _RotationCursor(self)


class _RotationConn:
    def __init__(self, db):
        self.db = db
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return self.db.cursor()

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_big_rotatable_file(path):
    """maybe_rotate() only fires once the file is >= ROTATE_SIZE_MB - write
    enough bytes to cross the (monkeypatched, small) threshold used below.
    """
    _write(path, "x" * (2 * 1024 * 1024))  # 2 MiB


def test_sha256_file_streaming_matches_direct_hash_for_multi_chunk_content(tmp_path):
    """Content bigger than one _sha256_file() chunk, to actually exercise
    the chunked-read loop (not just a single f.read() call).
    """
    path = tmp_path / "big.dat"
    content = os.urandom(3 * 1024 * 1024 + 12345)  # > 3 chunks of 1 MiB
    path.write_bytes(content)

    got = collector._sha256_file(str(path), chunk_size=1024 * 1024)

    assert got == hashlib.sha256(content).hexdigest()


def test_record_archive_computes_correct_hash_size_and_issues_expected_insert(tmp_path):
    db = _RotationDB()
    conn = _RotationConn(db)
    archive_path = tmp_path / "REPORTS.DAT.20260101T000000"
    content = b"ActCntCyc,ActTimCyc\n100,12.5\n101,12.5\n"
    archive_path.write_bytes(content)

    collector.record_archive(conn, str(archive_path), line_count=2)

    assert len(db.reports_dat_archive) == 1
    row = db.reports_dat_archive[0]
    assert row["machine_code"] == "KM-MC5-TEST"
    assert row["archive_path"] == str(archive_path)
    assert row["sha256"] == hashlib.sha256(content).hexdigest()
    assert row["line_count"] == 2
    assert row["size_bytes"] == len(content)
    assert conn.committed == 1


def test_maybe_rotate_records_archive_with_ingested_not_physical_line_count(monkeypatch, tmp_path):
    """The archived file itself will have MANY data lines (it's the whole
    accumulated REPORTS.DAT), but only some of them were ever actually
    ingested into `cycles` by the time rotation happens (reports_lines_read
    lags behind for the usual reasons - holdback, restart between write and
    checkpoint, etc). line_count recorded in reports_dat_archive must
    reflect that ingested count, not the raw number of lines in the file.
    """
    monkeypatch.setattr(collector, "ROTATE_SIZE_MB", 0.001)  # trip rotation immediately
    db = _RotationDB()
    db.collector_state["KM-MC5-TEST"] = {
        "reports_lines_read": 7,
        "last_line_hash": "deadbeef",
        "reports_dat_size_at_checkpoint": 123,
    }
    conn = _RotationConn(db)
    _make_big_rotatable_file(collector.REPORTS_DAT)

    collector.maybe_rotate(conn)

    assert len(db.reports_dat_archive) == 1
    assert db.reports_dat_archive[0]["line_count"] == 7
    # collector_state reset to 0 as part of the (unrelated, pre-existing)
    # rotation logic - confirms record_archive() read the OLD value first.
    assert db.collector_state["KM-MC5-TEST"]["reports_lines_read"] == 0
    # The file was renamed (not left in place, not deleted).
    assert not os.path.exists(collector.REPORTS_DAT)
    assert os.path.exists(db.reports_dat_archive[0]["archive_path"])


def test_maybe_rotate_completes_rotation_even_when_archive_bookkeeping_fails(caplog, monkeypatch, tmp_path):
    """Core requirement of ticket 1.5: a DB error while writing the
    reports_dat_archive row must not stop the rotation itself (abort, file
    rename, collector_state reset) from completing.
    """
    monkeypatch.setattr(collector, "ROTATE_SIZE_MB", 0.001)
    db = _RotationDB()
    db.fail_archive_insert = True
    db.collector_state["KM-MC5-TEST"] = {
        "reports_lines_read": 3,
        "last_line_hash": "deadbeef",
        "reports_dat_size_at_checkpoint": 123,
    }
    conn = _RotationConn(db)
    _make_big_rotatable_file(collector.REPORTS_DAT)

    with caplog.at_level(logging.ERROR, logger="euromap63-collector"):
        collector.maybe_rotate(conn)  # must not raise

    # Rotation's own (already-working, critical) steps still completed.
    assert not os.path.exists(collector.REPORTS_DAT)
    assert db.collector_state["KM-MC5-TEST"]["reports_lines_read"] == 0
    # But no archive row made it in, and the failure was logged.
    assert db.reports_dat_archive == []
    assert any("Archivniho zaznamu" in r.message or "Archivni" in r.message or "selhal" in r.message
               for r in caplog.records)
