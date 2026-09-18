"""Unit tests for collector.reconstruct_occurred_at() - the best-effort
occurred_at reconstruction described in MES_TARGET_ARCHITECTURE.md §5.1 and
MES_IMPLEMENTATION_BACKLOG.md ticket 1.1/1.2.

Pure function, no DB/FTP involved (see conftest.py for why `import collector`
works without a real psycopg2 install or a live DATABASE_URL).
"""
from datetime import datetime, timedelta, timezone

from collector import reconstruct_occurred_at


def _dt(seconds_offset=0):
    return datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds_offset)


def test_empty_batch_returns_empty_list():
    assert reconstruct_occurred_at([], _dt()) == []


def test_single_row_anchors_to_received_at():
    received_at = _dt()
    rows = [{"cycle_time_s": 12.5}]

    result = reconstruct_occurred_at(rows, received_at)

    assert result == [(received_at, "reconstructed_from_cycle_time")]


def test_walks_backwards_using_each_rows_own_cycle_time():
    received_at = _dt()
    # Chronological order as read from REPORTS.DAT: oldest first, newest last.
    rows = [
        {"cycle_time_s": 10.0},  # oldest cycle in the batch
        {"cycle_time_s": 12.0},
        {"cycle_time_s": 15.0},  # newest cycle -> anchored to received_at
    ]

    result = reconstruct_occurred_at(rows, received_at)
    occurred_ats = [r[0] for r in result]
    sources = [r[1] for r in result]

    assert occurred_ats[2] == received_at
    assert occurred_ats[1] == received_at - timedelta(seconds=12.0)
    assert occurred_ats[0] == occurred_ats[1] - timedelta(seconds=10.0)
    assert sources == ["reconstructed_from_cycle_time"] * 3
    # Reconstructed timeline must be monotonically increasing.
    assert occurred_ats[0] < occurred_ats[1] < occurred_ats[2]


def test_missing_cycle_time_falls_back_to_received_at_for_that_row():
    received_at = _dt()
    rows = [
        {"cycle_time_s": 10.0},
        {"cycle_time_s": None},   # ActTimCyc missing/unparseable for this cycle
        {"cycle_time_s": 15.0},
    ]

    result = reconstruct_occurred_at(rows, received_at)

    assert result[2] == (received_at, "reconstructed_from_cycle_time")
    assert result[1] == (received_at, "received_at_fallback")
    # Row before the gap still chains normally off whatever occurred_at the
    # next row ended up with (documented judgment call - see report).
    assert result[0] == (received_at - timedelta(seconds=10.0), "reconstructed_from_cycle_time")


def test_anchor_row_missing_cycle_time_is_still_labeled_fallback():
    received_at = _dt()
    rows = [{"cycle_time_s": None}]

    result = reconstruct_occurred_at(rows, received_at)

    assert result == [(received_at, "received_at_fallback")]


def test_all_rows_missing_cycle_time_all_fall_back_to_received_at():
    received_at = _dt()
    rows = [{"cycle_time_s": None}, {"cycle_time_s": None}, {"cycle_time_s": None}]

    result = reconstruct_occurred_at(rows, received_at)

    assert result == [
        (received_at, "received_at_fallback"),
        (received_at, "received_at_fallback"),
        (received_at, "received_at_fallback"),
    ]
