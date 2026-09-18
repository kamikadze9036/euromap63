"""Tests for the cycle_identity dedup/idempotence gatekeeper added to
collector.read_new_cycles() by MES_IMPLEMENTATION_BACKLOG.md ticket 1.3
(see postgres/init/18_add_cycle_identity.sql for the schema/rationale).

Environment note (see final report / conftest.py): this sandbox has no
Docker and no working psycopg2-binary wheel for its Python (3.14), so a
real-Postgres integration test (real UNIQUE/ON CONFLICT behaviour, real
transactions) could not be run here. These tests instead use a small
in-memory fake standing in for psycopg2's connection/cursor, driven purely
by pattern-matching the SQL text read_new_cycles() executes. That's enough
to pin down the *flow* this ticket cares about:

  1. the cycle_identity INSERT ... ON CONFLICT DO NOTHING RETURNING 1
     happens before the cycles INSERT for each row;
  2. the cycles INSERT is skipped when the identity insert reports a
     conflict (fetchone() returns None) - simulating a replayed/duplicate
     REPORTS.DAT line;
  3. genuinely new rows still get an identity row, a cycles row, and are
     counted in the returned "inserted" total;
  4. the reports_lines_read checkpoint still advances to len(data_lines)
     regardless of how many rows were de-duplicated;
  5. a decreasing cycle_count (possible counter reset on the machine, see
     MES_TARGET_ARCHITECTURE.md §5.3) logs a warning but does not raise or
     otherwise break the insert flow.

This does NOT exercise real Postgres UNIQUE-constraint/transaction
semantics - only the Python-level control flow. Real-Postgres verification
of postgres/init/18_add_cycle_identity.sql (e.g. via docker-compose or
testcontainers) is still needed before this ships; ticket 1.3 is expected
to land together with 1.4, not deployed immediately, so this gap is known
and accepted for now.
"""
import logging

import pytest

import collector


class FakeDB:
    """Minimal in-memory stand-in for the three tables read_new_cycles()
    touches: collector_state, cycle_identity, cycles. Lets tests set up a
    starting state (e.g. cycle_identity rows already "recorded" from a
    previous poll) and inspect what ended up committed.
    """

    def __init__(self):
        self.collector_state = {}  # machine_code -> reports_lines_read
        self.cycle_identity = set()  # {(machine_code, cycle_count)}
        self.cycles = []  # list of dict rows "inserted" into cycles

    def cursor(self):
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @staticmethod
    def _norm(sql):
        return " ".join(sql.split())

    def execute(self, sql, params=()):
        sql_n = self._norm(sql)

        if sql_n.startswith("SELECT reports_lines_read FROM collector_state"):
            (machine_code,) = params
            self._result = (self.db.collector_state.get(machine_code, 0),)

        elif sql_n.startswith("SELECT MAX(cycle_count) FROM cycle_identity"):
            (machine_code,) = params
            counts = [c for (m, c) in self.db.cycle_identity if m == machine_code]
            self._result = (max(counts) if counts else None,)

        elif sql_n.startswith("INSERT INTO cycle_identity"):
            machine_code, cycle_count = params
            key = (machine_code, cycle_count)
            if key in self.db.cycle_identity:
                self._result = None  # ON CONFLICT DO NOTHING -> no RETURNING row
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

        elif sql_n.startswith("UPDATE collector_state SET reports_lines_read"):
            reports_lines_read, machine_code = params
            self.db.collector_state[machine_code] = reports_lines_read
            self._result = None

        else:
            raise AssertionError(f"FakeCursor got unexpected SQL: {sql!r}")

    def fetchone(self):
        return self._result


class FakeConn:
    def __init__(self, db):
        self.db = db
        self.committed = 0

    def cursor(self):
        return self.db.cursor()

    def commit(self):
        self.committed += 1


def _write_reports_dat(path, cycle_counts, cycle_time=12.5):
    header = "ActCntCyc,ActTimCyc\n"
    rows = "\n".join(f"{c},{cycle_time}" for c in cycle_counts)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + rows + "\n")


@pytest.fixture(autouse=True)
def _isolated_reports_dat(tmp_path, monkeypatch):
    """Point collector.REPORTS_DAT at a per-test tmp file instead of the
    real /ftpdata path, and pin MACHINE_CODE so tests are independent of
    whatever the environment happens to set.
    """
    reports_dat = tmp_path / "REPORTS.DAT"
    monkeypatch.setattr(collector, "REPORTS_DAT", str(reports_dat))
    monkeypatch.setattr(collector, "MACHINE_CODE", "KM-MC5-TEST")
    return reports_dat


def test_new_batch_inserts_identity_and_cycle_rows_for_every_row():
    db = FakeDB()
    conn = FakeConn(db)
    _write_reports_dat(collector.REPORTS_DAT, [100, 101, 102])

    inserted = collector.read_new_cycles(conn)

    assert inserted == 3
    assert len(db.cycles) == 3
    assert db.cycle_identity == {
        ("KM-MC5-TEST", 100), ("KM-MC5-TEST", 101), ("KM-MC5-TEST", 102),
    }
    assert db.collector_state["KM-MC5-TEST"] == 3
    assert conn.committed == 1


def test_replayed_batch_is_fully_deduplicated_no_new_cycles_rows():
    """Simulates re-processing an archived REPORTS.DAT that has already
    been fully recorded (e.g. manual replay, or a collector restart that
    re-reads already-committed lines): cycle_identity already has all
    three (machine_code, cycle_count) pairs, so no new cycles rows should
    be inserted even though the checkpoint hadn't advanced.
    """
    db = FakeDB()
    db.cycle_identity = {
        ("KM-MC5-TEST", 100), ("KM-MC5-TEST", 101), ("KM-MC5-TEST", 102),
    }
    conn = FakeConn(db)
    _write_reports_dat(collector.REPORTS_DAT, [100, 101, 102])

    inserted = collector.read_new_cycles(conn)

    assert inserted == 0
    assert db.cycles == []
    # Checkpoint still advances - reports_lines_read is a read-position
    # optimization, not the dedup mechanism.
    assert db.collector_state["KM-MC5-TEST"] == 3


def test_overlapping_batch_only_inserts_genuinely_new_rows():
    """The core scenario from the ticket: a restart/replay re-reads a
    batch that partially overlaps what's already recorded. cycles row
    count should only grow by the genuinely-new rows.
    """
    db = FakeDB()
    db.cycle_identity = {("KM-MC5-TEST", 100), ("KM-MC5-TEST", 101)}
    conn = FakeConn(db)
    _write_reports_dat(collector.REPORTS_DAT, [100, 101, 102, 103])

    inserted = collector.read_new_cycles(conn)

    assert inserted == 2
    assert {c["cycle_count"] for c in db.cycles} == {102, 103}
    assert db.cycle_identity == {
        ("KM-MC5-TEST", 100), ("KM-MC5-TEST", 101),
        ("KM-MC5-TEST", 102), ("KM-MC5-TEST", 103),
    }


def test_identity_insert_happens_before_and_gates_the_cycles_insert():
    """Directly pins the required ordering: for a duplicate row, the
    cycle_identity INSERT must run (and report a conflict) and the
    cycles INSERT must never run at all for that row.
    """

    class RecordingCursor(FakeCursor):
        def execute(self, sql, params=()):
            self.db.executed_sql.append(self._norm(sql))
            super().execute(sql, params)

    class RecordingDB(FakeDB):
        def __init__(self):
            super().__init__()
            self.executed_sql = []

        def cursor(self):
            return RecordingCursor(self)

    db = RecordingDB()
    db.cycle_identity = {("KM-MC5-TEST", 100)}
    conn = FakeConn(db)
    _write_reports_dat(collector.REPORTS_DAT, [100])

    inserted = collector.read_new_cycles(conn)

    assert inserted == 0
    identity_inserts = [s for s in db.executed_sql if s.startswith("INSERT INTO cycle_identity")]
    cycles_inserts = [s for s in db.executed_sql if s.startswith("INSERT INTO cycles")]
    # cycle_identity insert attempted (and reported a conflict, see
    # test_replayed_batch_* above); the cycles insert must never have
    # been reached for the sole (duplicate) row in this batch.
    assert len(identity_inserts) == 1
    assert len(cycles_inserts) == 0


def test_decreasing_cycle_count_logs_warning_but_still_inserts(caplog):
    """Possible counter reset on the machine (MES_TARGET_ARCHITECTURE.md
    §5.3) - not solved by this ticket, but should be flagged with a
    warning rather than silently mishandled or crashing.
    """
    db = FakeDB()
    db.cycle_identity = {("KM-MC5-TEST", 500)}
    conn = FakeConn(db)
    _write_reports_dat(collector.REPORTS_DAT, [500, 5])  # 5 looks like a reset

    with caplog.at_level(logging.WARNING, logger="euromap63-collector"):
        inserted = collector.read_new_cycles(conn)

    # cycle_count=500 is a duplicate (already in cycle_identity) so only
    # cycle_count=5 (post-"reset") is genuinely new and gets inserted.
    assert inserted == 1
    assert {c["cycle_count"] for c in db.cycles} == {5}
    assert any("klesl" in r.message for r in caplog.records)


def test_no_warning_when_cycle_count_keeps_increasing(caplog):
    db = FakeDB()
    conn = FakeConn(db)
    _write_reports_dat(collector.REPORTS_DAT, [1, 2, 3])

    with caplog.at_level(logging.WARNING, logger="euromap63-collector"):
        inserted = collector.read_new_cycles(conn)

    assert inserted == 3
    assert not any("klesl" in r.message for r in caplog.records)
