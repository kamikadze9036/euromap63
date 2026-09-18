"""Tests for MES_IMPLEMENTATION_BACKLOG.md ticket 1.7 / MES_TARGET_
ARCHITECTURE.md §5.2: time-versioned order assignment.

Before this ticket, read_new_cycles() called get_active_order() once per
poll and stamped that single value onto every row of the batch, regardless
of each row's own occurred_at - so after an outage, a delayed catch-up batch
could get the order that's running *now* in Cyclades instead of the order
that was actually running when the older cycles occurred.

This module covers two distinct things, split into two classes of tests:

  1. collector._sync_order_assignment() / collector.get_active_order()
     (the WRITE side): detecting when the observed active order for a
     machine changes and recording that transition in order_assignments -
     closing the previous open interval and opening a new one - but only
     on an actual change, not on every call.

  2. collector.read_new_cycles() (the READ side): resolving each cycle
     row's own order_ref from order_assignments based on that row's
     occurred_at, instead of stamping one value across the whole batch.

Environment note (see test_read_new_cycles_dedup.py's docstring): this
sandbox has no Docker/working psycopg2, so both classes here use small
in-memory fakes standing in for psycopg2's connection/cursor, driven by
pattern-matching the SQL text collector.py executes - not real Postgres
UNIQUE/transaction semantics. See test_integration_real_db.py for the
real-DB counterpart of the read-side scenario.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest

import collector
from test_read_new_cycles_dedup import FakeConn, FakeDB, _write_reports_dat


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """collector._last_written_order_ref / collector._cyclades_cache are
    module-level mutable dicts (deliberately, so they persist across polls
    within one collector process) - reset them per test so tests don't leak
    state into each other, and pin MACHINE_CODE independent of whatever the
    environment happens to set.
    """
    monkeypatch.setattr(collector, "_last_written_order_ref", {"value": collector._UNOBSERVED})
    monkeypatch.setattr(collector, "_cyclades_cache", {"order_ref": None, "checked_at": 0.0})
    monkeypatch.setattr(collector, "MACHINE_CODE", "KM-MC5-TEST")


# ---------------------------------------------------------------------------
# Write side: collector._sync_order_assignment() / collector.get_active_order()
# ---------------------------------------------------------------------------

class _OAConn:
    """Minimal fake connection standing in for the local Postgres conn,
    modeling just the order_assignments table as a list of dict rows.
    """

    def __init__(self, existing_rows=()):
        self.rows = [dict(r) for r in existing_rows]
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return _OACursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class _OACursor:
    def __init__(self, conn):
        self.conn = conn
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

        if sql_n.startswith("SELECT order_ref FROM order_assignments"):
            (machine_code,) = params
            open_rows = [
                r for r in self.conn.rows
                if r["machine_code"] == machine_code and r["valid_to"] is None
            ]
            open_rows.sort(key=lambda r: r["valid_from"], reverse=True)
            self._result = (open_rows[0]["order_ref"],) if open_rows else None

        elif sql_n.startswith("UPDATE order_assignments SET valid_to=%s"):
            valid_to, machine_code = params
            for r in self.conn.rows:
                if r["machine_code"] == machine_code and r["valid_to"] is None:
                    r["valid_to"] = valid_to
            self._result = None

        elif sql_n.startswith("INSERT INTO order_assignments"):
            machine_code, order_ref, valid_from = params
            self.conn.rows.append({
                "machine_code": machine_code,
                "order_ref": order_ref,
                "valid_from": valid_from,
                "valid_to": None,
            })
            self._result = None

        else:
            raise AssertionError(f"_OACursor got unexpected SQL: {sql!r}")

    def fetchone(self):
        return self._result


class _FailingOACursor(_OACursor):
    def execute(self, sql, params=()):
        sql_n = self._norm(sql)
        if sql_n.startswith("UPDATE order_assignments") or sql_n.startswith("INSERT INTO order_assignments"):
            raise collector.psycopg2.OperationalError("simulated DB hiccup")
        super().execute(sql, params)


class _FailingOAConn(_OAConn):
    def cursor(self):
        return _FailingOACursor(self)


T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)


def test_first_observation_ever_opens_new_interval_nothing_to_close():
    conn = _OAConn()

    collector._sync_order_assignment(conn, "ORDER-A", T0)

    assert conn.rows == [
        {"machine_code": "KM-MC5-TEST", "order_ref": "ORDER-A", "valid_from": T0, "valid_to": None},
    ]
    assert conn.committed == 1
    assert collector._last_written_order_ref["value"] == "ORDER-A"


def test_no_change_between_calls_makes_no_further_writes():
    conn = _OAConn()

    collector._sync_order_assignment(conn, "ORDER-A", T0)
    collector._sync_order_assignment(conn, "ORDER-A", T1)

    assert len(conn.rows) == 1, "second call observed the same order_ref - must not write again"
    assert conn.committed == 1


def test_order_change_closes_old_interval_and_opens_new_one():
    conn = _OAConn()
    collector._sync_order_assignment(conn, "ORDER-A", T0)

    collector._sync_order_assignment(conn, "ORDER-B", T1)

    assert conn.rows == [
        {"machine_code": "KM-MC5-TEST", "order_ref": "ORDER-A", "valid_from": T0, "valid_to": T1},
        {"machine_code": "KM-MC5-TEST", "order_ref": "ORDER-B", "valid_from": T1, "valid_to": None},
    ]
    assert conn.committed == 2
    assert collector._last_written_order_ref["value"] == "ORDER-B"


def test_order_becoming_none_is_recorded_as_a_real_row_not_skipped():
    """order_ref=None ('machine idle, confirmed no active order') is a
    legitimate value to write, not treated as 'nothing to record'."""
    conn = _OAConn()
    collector._sync_order_assignment(conn, "ORDER-A", T0)

    collector._sync_order_assignment(conn, None, T1)

    assert conn.rows[-1] == {
        "machine_code": "KM-MC5-TEST", "order_ref": None, "valid_from": T1, "valid_to": None,
    }
    assert collector._last_written_order_ref["value"] is None


def test_process_restart_adopts_matching_open_interval_without_writing():
    """Simulates a collector process restart (module-level cache reset to
    _UNOBSERVED) where the DB already has an open interval matching what we
    just observed again - the real order never changed, so this must NOT
    create a spurious duplicate row.
    """
    conn = _OAConn(existing_rows=[
        {"machine_code": "KM-MC5-TEST", "order_ref": "ORDER-A", "valid_from": T0, "valid_to": None},
    ])
    assert collector._last_written_order_ref["value"] is collector._UNOBSERVED

    collector._sync_order_assignment(conn, "ORDER-A", T1)

    assert len(conn.rows) == 1, "must adopt the existing open interval, not add a new one"
    assert conn.committed == 0
    assert collector._last_written_order_ref["value"] == "ORDER-A"


def test_process_restart_with_genuinely_different_order_closes_and_opens():
    """Same restart scenario, but Cyclades' order actually did change while
    the collector was down - the existing open interval must still be
    closed and a new one opened, exactly as with the same-process case."""
    conn = _OAConn(existing_rows=[
        {"machine_code": "KM-MC5-TEST", "order_ref": "ORDER-A", "valid_from": T0, "valid_to": None},
    ])

    collector._sync_order_assignment(conn, "ORDER-B", T1)

    assert conn.rows == [
        {"machine_code": "KM-MC5-TEST", "order_ref": "ORDER-A", "valid_from": T0, "valid_to": T1},
        {"machine_code": "KM-MC5-TEST", "order_ref": "ORDER-B", "valid_from": T1, "valid_to": None},
    ]


def test_db_error_during_write_is_swallowed_and_state_not_advanced(caplog):
    """A DB hiccup while writing the transition must not crash the caller
    (same spirit as write_heartbeat(), ticket 1.8) - and must NOT mark the
    new value as written, so the next poll retries instead of silently
    losing the transition.
    """
    conn = _FailingOAConn()

    with caplog.at_level(logging.ERROR, logger="euromap63-collector"):
        collector._sync_order_assignment(conn, "ORDER-A", T0)  # must not raise

    assert conn.rows == []
    assert conn.committed == 0
    assert conn.rolled_back == 1
    assert collector._last_written_order_ref["value"] is collector._UNOBSERVED
    assert any("order_assignments" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# get_active_order() orchestration: wires Cyclades polling to
# _sync_order_assignment() through the real public entry point.
# ---------------------------------------------------------------------------

class _FakeCycladesDriver:
    """Stands in for the pymssql module: .connect() returns an object that
    is also its own cursor, replaying `responses` (one OF_REFOF value per
    execute()/fetchone() pair) in order, repeating the last one thereafter.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self._calls = 0
        self._current = None

    def connect(self, **kwargs):
        return self

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        idx = min(self._calls, len(self.responses) - 1)
        self._current = self.responses[idx]
        self._calls += 1

    def fetchone(self):
        return (self._current,) if self._current is not None else None

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _configure_cyclades(monkeypatch):
    monkeypatch.setattr(collector, "CYCLADES_DB_HOST", "cyclades-host")
    monkeypatch.setattr(collector, "CYCLADES_MAC_REFMAC", "P2700-01")
    # Force a live Cyclades fetch on every get_active_order() call in these
    # tests - the TTL cache itself is orthogonal to this ticket and already
    # covered by whatever tests existed for it before.
    monkeypatch.setattr(collector, "CYCLADES_CACHE_TTL_SEC", 0)


def test_get_active_order_records_transition_only_on_actual_change(monkeypatch):
    monkeypatch.setattr(collector, "pymssql", _FakeCycladesDriver(["ORDER-A", "ORDER-A", "ORDER-B"]))
    conn = _OAConn()

    assert collector.get_active_order(conn) == "ORDER-A"
    assert collector.get_active_order(conn) == "ORDER-A"
    assert collector.get_active_order(conn) == "ORDER-B"

    assert len(conn.rows) == 2, "only the genuine A->B transition should have written a new row"
    assert conn.rows[0]["order_ref"] == "ORDER-A" and conn.rows[0]["valid_to"] is not None
    assert conn.rows[1]["order_ref"] == "ORDER-B" and conn.rows[1]["valid_to"] is None


def test_get_active_order_returns_none_when_cyclades_not_configured(monkeypatch):
    monkeypatch.setattr(collector, "CYCLADES_DB_HOST", "")
    conn = _OAConn()

    assert collector.get_active_order(conn) is None
    assert conn.rows == [], "no DB write should happen when Cyclades integration is disabled"


# ---------------------------------------------------------------------------
# Read side: read_new_cycles() resolves each row's own order_ref from
# order_assignments based on that row's occurred_at.
# ---------------------------------------------------------------------------

class _FrozenDateTime(datetime):
    """Pins collector.datetime.now(timezone.utc) to a fixed instant so the
    reconstructed occurred_at values in a test batch are fully predictable
    (see reconstruct_occurred_at(): it anchors the batch's last row on
    received_at = datetime.now(timezone.utc) and walks backward from there).
    """
    _frozen_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen_now


@pytest.fixture(autouse=True)
def _isolated_reports_dat(tmp_path, monkeypatch):
    reports_dat = tmp_path / "REPORTS.DAT"
    monkeypatch.setattr(collector, "REPORTS_DAT", str(reports_dat))
    return reports_dat


def test_batch_spanning_an_order_change_splits_order_ref_by_occurred_at(monkeypatch):
    """The core scenario from the ticket: a catch-up batch (e.g. after an
    outage) contains cycles whose reconstructed occurred_at spans an order
    change - each row must get the order that was actually active AT ITS
    OWN occurred_at, not whatever get_active_order() would return right now
    for the whole batch.
    """
    monkeypatch.setattr(collector, "datetime", _FrozenDateTime)
    # No live Cyclades polling needed for this test - only the read-side
    # per-row lookup against a pre-populated order_assignments table.
    monkeypatch.setattr(collector, "CYCLADES_DB_HOST", "")

    received_at = _FrozenDateTime._frozen_now
    # 3 rows, 10s apart, reconstructed backward from received_at:
    #   cycle 12 (last)  -> occurred_at = 12:00:00 (== received_at)
    #   cycle 11         -> occurred_at = 11:59:50
    #   cycle 10 (first) -> occurred_at = 11:59:40
    _write_reports_dat(collector.REPORTS_DAT, [10, 11, 12], cycle_time=10.0)

    # Cutoff (11:59:45) sits strictly between cycle 10's occurred_at
    # (11:59:40) and cycle 11's occurred_at (11:59:50), so this genuinely
    # splits the batch across the two intervals.
    cutoff = received_at - timedelta(seconds=15)
    db = FakeDB()
    db.order_assignments = [
        {
            "machine_code": "KM-MC5-TEST", "order_ref": "ORDER-BEFORE",
            "valid_from": received_at - timedelta(hours=1),
            "valid_to": cutoff,
        },
        {
            "machine_code": "KM-MC5-TEST", "order_ref": "ORDER-AFTER",
            "valid_from": cutoff,
            "valid_to": None,
        },
    ]
    conn = FakeConn(db)

    inserted = collector.read_new_cycles(conn)

    assert inserted == 3
    by_cycle = {c["cycle_count"]: c["order_ref"] for c in db.cycles}
    assert by_cycle == {
        10: "ORDER-BEFORE",  # occurred_at 11:59:40, before cutoff
        11: "ORDER-AFTER",   # occurred_at 11:59:50, at/after cutoff
        12: "ORDER-AFTER",   # occurred_at 12:00:00 (== received_at)
    }


def test_cycle_with_no_covering_interval_gets_null_order_ref_not_an_error(monkeypatch):
    """No order_assignments row covers this batch's occurred_at at all
    (e.g. cycles collected before this ticket's tracking existed, or a
    genuine gap) - order_ref must end up NULL, not raise."""
    monkeypatch.setattr(collector, "datetime", _FrozenDateTime)
    monkeypatch.setattr(collector, "CYCLADES_DB_HOST", "")
    _write_reports_dat(collector.REPORTS_DAT, [1, 2], cycle_time=5.0)

    db = FakeDB()  # order_assignments left empty
    conn = FakeConn(db)

    inserted = collector.read_new_cycles(conn)

    assert inserted == 2
    assert all(c["order_ref"] is None for c in db.cycles)
