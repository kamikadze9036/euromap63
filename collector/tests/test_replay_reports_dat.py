"""Tests for scripts/replay_reports_dat.py (MES_IMPLEMENTATION_BACKLOG.md
ticket 1.6).

Environment note (see conftest.py / other tests in this directory): no
Docker and no working real psycopg2 in this sandbox, so these use the same
FakeDB/FakeCursor/FakeConn mocked-connection pattern established in
test_read_new_cycles_dedup.py / test_archive_rotation.py, driven by
pattern-matching the SQL text the script executes. Real-Postgres
verification of the actual cycle_identity/cycles INSERT semantics lives in
test_integration_real_db.py (skips cleanly here, runs for real against a
real DB elsewhere).

Covers the two behaviors explicitly called out by the ticket:
  1. checksum-mismatch-refuses-by-default (and the --skip-checksum-verify
     override, and the "no record at all" case).
  2. dry-run-vs-apply: a dry run must issue zero write statements
     (INSERT/UPDATE/DELETE) against the database, while --apply does.
"""
import os
import sys

import pytest

_COLLECTOR_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_COLLECTOR_TESTS_DIR))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import replay_reports_dat as replay  # noqa: E402


class FakeCursor:
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

        if sql_n.startswith("SELECT machine_code, sha256, line_count, size_bytes, archived_at"):
            (archive_path,) = params
            self._result = self.db.archive_records.get(archive_path)

        elif sql_n.startswith("SELECT cycle_count FROM cycle_identity"):
            machine_code, cycle_counts = params
            existing = {c for (m, c) in self.db.cycle_identity if m == machine_code and c in cycle_counts}
            self._rows = [(c,) for c in existing]

        elif sql_n.startswith("INSERT INTO cycle_identity"):
            machine_code, cycle_count = params
            key = (machine_code, cycle_count)
            if key in self.db.cycle_identity:
                self._result = None
            else:
                self.db.cycle_identity.add(key)
                self._result = (1,)

        elif sql_n.startswith("INSERT INTO cycles"):
            (machine_code, cycle_count, cycle_time_s, order_ref, params_json,
             received_at, occurred_at, occurred_at_source) = params
            self.db.cycles.append({
                "machine_code": machine_code,
                "cycle_count": cycle_count,
                "cycle_time_s": cycle_time_s,
                "order_ref": order_ref,
                "params_json": params_json,
                "received_at": received_at,
                "occurred_at": occurred_at,
                "occurred_at_source": occurred_at_source,
            })
            self._result = None

        elif sql_n.startswith("SELECT MIN(occurred_at), MAX(occurred_at) FROM cycles"):
            machine_code, cycle_counts = params
            matching = [
                c["occurred_at"] for c in self.db.cycles
                if c["machine_code"] == machine_code and c["cycle_count"] in cycle_counts
            ]
            self._result = (min(matching), max(matching)) if matching else (None, None)

        else:
            raise AssertionError(f"FakeCursor got unexpected SQL: {sql!r}")

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._rows


class FakeDB:
    def __init__(self):
        self.archive_records = {}  # archive_path -> (machine_code, sha256, line_count, size_bytes, archived_at)
        self.cycle_identity = set()  # {(machine_code, cycle_count)}
        self.cycles = []
        self.executed_sql = []

    def cursor(self):
        return FakeCursor(self)


class FakeConn:
    def __init__(self, db):
        self.db = db
        self.committed = 0

    def cursor(self):
        return self.db.cursor()

    def commit(self):
        self.committed += 1


def _write_archive(path, cycle_counts, cycle_time=12.5):
    header = "ActCntCyc,ActTimCyc\n"
    rows = "\n".join(f"{c},{cycle_time}" for c in cycle_counts)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + rows + "\n")


MACHINE_CODE = "KM-MC5-TEST"


def _archive_path(tmp_path):
    return str(tmp_path / "REPORTS.DAT.20260910T120000")


# --- checksum verification -------------------------------------------------

def test_checksum_mismatch_refuses_by_default(tmp_path):
    path = _archive_path(tmp_path)
    _write_archive(path, [1, 2, 3])
    db = FakeDB()
    db.archive_records[path] = (MACHINE_CODE, "0" * 64, 3, os.path.getsize(path), "2026-09-10T12:00:00Z")
    conn = FakeConn(db)

    with pytest.raises(replay.ChecksumVerificationError, match="NESOUHLASI"):
        replay.replay_archive(conn, MACHINE_CODE, path, apply=False, skip_checksum_verify=False)

    # Refusal must happen before any write - no cycle_identity/cycles touched.
    assert db.cycle_identity == set()
    assert db.cycles == []


def test_missing_archive_record_refuses_by_default(tmp_path):
    path = _archive_path(tmp_path)
    _write_archive(path, [1, 2, 3])
    db = FakeDB()  # no reports_dat_archive row at all for this path
    conn = FakeConn(db)

    with pytest.raises(replay.ChecksumVerificationError, match="Zadny zaznam"):
        replay.replay_archive(conn, MACHINE_CODE, path, apply=False, skip_checksum_verify=False)


def test_skip_checksum_verify_bypasses_missing_record(tmp_path):
    path = _archive_path(tmp_path)
    _write_archive(path, [1, 2])
    db = FakeDB()  # no record - would normally refuse
    conn = FakeConn(db)

    report = replay.replay_archive(conn, MACHINE_CODE, path, apply=False, skip_checksum_verify=True)

    assert report.checksum_verified is False
    assert report.new_count == 2


def test_matching_checksum_proceeds(tmp_path):
    path = _archive_path(tmp_path)
    _write_archive(path, [1, 2])
    db = FakeDB()
    real_sha256 = replay._sha256_file(path)
    db.archive_records[path] = (MACHINE_CODE, real_sha256, 2, os.path.getsize(path), "2026-09-10T12:00:00Z")
    conn = FakeConn(db)

    report = replay.replay_archive(conn, MACHINE_CODE, path, apply=False, skip_checksum_verify=False)

    assert report.checksum_verified is True
    assert report.new_count == 2


# --- dry-run vs apply --------------------------------------------------------

def _write_terms(db, path):
    real_sha256 = replay._sha256_file(path)
    db.archive_records[path] = (MACHINE_CODE, real_sha256, 3, os.path.getsize(path), "2026-09-10T12:00:00Z")


def test_dry_run_issues_zero_write_statements(tmp_path):
    path = _archive_path(tmp_path)
    _write_archive(path, [100, 101, 102])
    db = FakeDB()
    _write_terms(db, path)
    db.cycle_identity = {(MACHINE_CODE, 100)}  # one pre-existing duplicate
    conn = FakeConn(db)

    report = replay.replay_archive(conn, MACHINE_CODE, path, apply=False, skip_checksum_verify=False)

    assert report.applied is False
    assert report.new_count == 2
    assert report.duplicate_count == 1
    # No write statement of any kind was issued.
    write_stmts = [s for s in db.executed_sql if s.startswith(("INSERT", "UPDATE", "DELETE"))]
    assert write_stmts == []
    # Nothing actually landed in the fake DB either.
    assert db.cycles == []
    assert db.cycle_identity == {(MACHINE_CODE, 100)}
    assert conn.committed == 0


def test_apply_inserts_new_rows_and_skips_duplicates(tmp_path):
    path = _archive_path(tmp_path)
    _write_archive(path, [100, 101, 102])
    db = FakeDB()
    _write_terms(db, path)
    db.cycle_identity = {(MACHINE_CODE, 100)}
    conn = FakeConn(db)

    report = replay.replay_archive(conn, MACHINE_CODE, path, apply=True, skip_checksum_verify=False)

    assert report.applied is True
    assert report.new_count == 2
    assert report.duplicate_count == 1
    assert {c["cycle_count"] for c in db.cycles} == {101, 102}
    assert all(c["order_ref"] is None for c in db.cycles)
    assert db.cycle_identity == {(MACHINE_CODE, 100), (MACHINE_CODE, 101), (MACHINE_CODE, 102)}
    write_stmts = [s for s in db.executed_sql if s.startswith(("INSERT", "UPDATE", "DELETE"))]
    assert len(write_stmts) > 0
    assert conn.committed == 1


def test_apply_replaying_same_archive_twice_does_not_duplicate(tmp_path):
    """Core idempotency requirement: replaying the same archive twice (or
    into a DB that already has some/all of its cycles) must not create
    duplicate cycles rows."""
    path = _archive_path(tmp_path)
    _write_archive(path, [200, 201])
    db = FakeDB()
    _write_terms(db, path)
    conn = FakeConn(db)

    first = replay.replay_archive(conn, MACHINE_CODE, path, apply=True, skip_checksum_verify=False)
    assert first.new_count == 2
    assert len(db.cycles) == 2

    second = replay.replay_archive(conn, MACHINE_CODE, path, apply=True, skip_checksum_verify=False)
    assert second.new_count == 0
    assert second.duplicate_count == 2
    assert len(db.cycles) == 2  # still just the original two, no duplicates
