"""Tests for collector.write_heartbeat(), added by MES_IMPLEMENTATION_
BACKLOG.md ticket 1.8 (see postgres/init/20_add_collector_heartbeat.sql for
the schema/rationale).

Ticket 1.8's core finding: collector_state.last_poll_at (updated only
inside read_new_cycles()) is NOT a real heartbeat, because read_new_cycles()
has three early-return points (missing/empty REPORTS.DAT, no new data - the
overwhelmingly common case since machine cycle time >> POLL_INTERVAL_SEC)
that all return before ever reaching its own "UPDATE ... last_poll_at=now()"
statement. write_heartbeat() is a small, separate function called once per
main() loop iteration regardless of what read_new_cycles() did, so it must
be tested independently of read_new_cycles().

Reuses the FakeDB/FakeCursor/FakeConn mocked-DB pattern established in
test_read_new_cycles_dedup.py (see that module's docstring for why: no
Docker/real Postgres available in this sandbox). FakeCursor doesn't know
about the heartbeat UPDATE statement yet, so this module extends it with a
tiny dedicated fake rather than teaching the shared one about a statement
only this ticket cares about.

Covers:
  1. write_heartbeat() issues the expected UPDATE for the configured
     MACHINE_CODE and commits.
  2. A DB error during the UPDATE (or the commit) is caught, logged, rolled
     back, and does NOT propagate - the whole point of ticket 1.8's
     "must never crash the main loop" requirement.
"""
import logging

import pytest

import collector


class _HeartbeatCursor:
    def __init__(self, calls, fail=False):
        self.calls = calls
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        if self.fail:
            raise collector.psycopg2.OperationalError("simulated DB hiccup")
        self.calls.append((" ".join(sql.split()), params))


class _HeartbeatConn:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return _HeartbeatCursor(self.calls, fail=self.fail)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


@pytest.fixture(autouse=True)
def _pin_machine_code(monkeypatch):
    monkeypatch.setattr(collector, "MACHINE_CODE", "KM-MC5-TEST")


def test_write_heartbeat_issues_expected_update_and_commits():
    conn = _HeartbeatConn()

    collector.write_heartbeat(conn)

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert sql.startswith("UPDATE collector_state SET last_heartbeat_at=now()")
    assert "WHERE machine_code=%s" in sql
    assert params == ("KM-MC5-TEST",)
    assert conn.committed == 1
    assert conn.rolled_back == 0


def test_write_heartbeat_swallows_db_error_and_rolls_back(caplog):
    conn = _HeartbeatConn(fail=True)

    with caplog.at_level(logging.ERROR, logger="euromap63-collector"):
        collector.write_heartbeat(conn)  # must not raise

    assert conn.committed == 0
    assert conn.rolled_back == 1
    assert any("heartbeat" in r.message.lower() for r in caplog.records)


def test_write_heartbeat_swallows_error_even_when_rollback_also_fails(caplog):
    """Belt-and-braces: even if the connection is so broken that rollback()
    itself raises, write_heartbeat() still must not propagate - main()'s
    loop must survive.
    """
    conn = _HeartbeatConn(fail=True)

    def _broken_rollback():
        raise collector.psycopg2.OperationalError("connection already dead")

    conn.rollback = _broken_rollback

    with caplog.at_level(logging.ERROR, logger="euromap63-collector"):
        collector.write_heartbeat(conn)  # must not raise

    assert conn.committed == 0
