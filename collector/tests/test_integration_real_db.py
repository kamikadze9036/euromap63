"""Integracni testy tiketu 1.1-1.4 a 1.8 proti SKUTECNE Postgres/TimescaleDB
instanci - narozdil od test_reconstruct_occurred_at.py /
test_read_new_cycles_dedup.py / test_checkpoint_integrity.py, ktere behem
vyvoje bezely proti mockovanemu psycopg2 (sandbox bez pg_config/dockeru).

Vyzaduje DATABASE_URL ukazujici na skutecnou Postgres/TimescaleDB databazi
se schematem aplikovanym z postgres/init/01..19 (viz MES_IMPLEMENTATION_
BACKLOG.md tiket 1.10 - toto je prvni kus te integracni sady). Pokud
DATABASE_URL neni nastaveno nebo psycopg2 neni dostupny, cely modul se
preskoci - v CI/lokalnim vyvoji bez DB se tedy nerozbije zbytek sady.
"""
import hashlib
import os

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL or getattr(psycopg2, "_is_stub", False):
    # conftest.py's os.environ.setdefault() means DATABASE_URL is never
    # actually falsy, and its stub module (when no real driver is
    # installed) IS importable - so without the "_is_stub" check,
    # a sandbox with no psycopg2 install would hit NotImplementedError
    # from every fixture instead of cleanly skipping this whole module.
    pytest.skip(
        "DATABASE_URL neni nastaveno na skutecnou Postgres/TimescaleDB "
        "instanci (nebo psycopg2 tady neni realne nainstalovany) - "
        "nastav ho a spust proti DB se schematem z postgres/init/ "
        "pro spusteni techto integracnich testu.",
        allow_module_level=True,
    )

import collector as collector_module

MACHINE_CODE = "VERIFY-TEST-01"


def _write_reports_dat(tmp_dir, data_lines, header="ActCntCyc,ActTimCyc"):
    content = "\n".join([header] + data_lines) + "\n"
    (tmp_dir / "REPORTS.DAT").write_text(content, encoding="utf-8")


def _cleanup(conn, machine_code=MACHINE_CODE):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM cycles WHERE machine_code=%s", (machine_code,))
        cur.execute("DELETE FROM cycle_identity WHERE machine_code=%s", (machine_code,))
        cur.execute("DELETE FROM reports_dat_archive WHERE machine_code=%s", (machine_code,))
        # Ticket 1.7 (alembic/versions/0003_add_order_assignments.py) - must
        # go before the "machines" delete below (order_assignments.machine_code
        # is a FOREIGN KEY REFERENCES machines.machine_code).
        cur.execute("DELETE FROM order_assignments WHERE machine_code=%s", (machine_code,))
        cur.execute("DELETE FROM collector_state WHERE machine_code=%s", (machine_code,))
        cur.execute("DELETE FROM machines WHERE machine_code=%s", (machine_code,))
    conn.commit()


@pytest.fixture
def db_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    _cleanup(conn)
    yield conn
    _cleanup(conn)
    conn.close()


@pytest.fixture
def machine(db_conn, tmp_path, monkeypatch):
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO machines (machine_code, machine_name, ftp_root) "
            "VALUES (%s, %s, %s)",
            (MACHINE_CODE, MACHINE_CODE, str(tmp_path)),
        )
        cur.execute(
            "INSERT INTO collector_state (machine_code) VALUES (%s)",
            (MACHINE_CODE,),
        )
    db_conn.commit()

    monkeypatch.setattr(collector_module, "MACHINE_CODE", MACHINE_CODE)
    monkeypatch.setattr(collector_module, "FTP_ROOT", str(tmp_path))
    monkeypatch.setattr(collector_module, "REPORTS_DAT", str(tmp_path / "REPORTS.DAT"))
    monkeypatch.setattr(collector_module, "REPORTS_LOG", str(tmp_path / "REPORTS.LOG"))
    # Zadne Cyclades pripojeni v tomhle prostredi - get_active_order() se
    # sama vyhodnoti na None, kdyz CYCLADES_DB_HOST je prazdne (viz
    # collector.py), takze neni potreba nic dal mockovat.
    return tmp_path


def _cycles_count(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cycles WHERE machine_code=%s", (MACHINE_CODE,))
        return cur.fetchone()[0]


def _checkpoint(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT reports_lines_read, last_line_hash, reports_dat_size_at_checkpoint "
            "FROM collector_state WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
        return cur.fetchone()


def test_fresh_batch_inserts_rows_and_reconstructs_occurred_at(db_conn, machine):
    tmp_path = machine
    _write_reports_dat(tmp_path, ["1,10.0", "2,12.0", "3,15.0"])

    inserted = collector_module.read_new_cycles(db_conn)

    assert inserted == 3
    assert _cycles_count(db_conn) == 3

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT cycle_count, received_at, occurred_at, occurred_at_source "
            "FROM cycles WHERE machine_code=%s ORDER BY cycle_count",
            (MACHINE_CODE,),
        )
        rows = cur.fetchall()

    assert [r[0] for r in rows] == [1, 2, 3]
    # Vsechny tri sdileji stejny received_at (jedna davka, jeden poll).
    assert rows[0][1] == rows[1][1] == rows[2][1]
    # occurred_at rekonstruovano zpetne, monotonne rostouci, poslednI = received_at.
    assert rows[0][2] < rows[1][2] < rows[2][2]
    assert rows[2][2] == rows[2][1]
    assert all(r[3] == "reconstructed_from_cycle_time" for r in rows)

    reports_lines_read, last_line_hash, size_at_checkpoint = _checkpoint(db_conn)
    assert reports_lines_read == 3
    assert last_line_hash == hashlib.sha256(b"3,15.0").hexdigest()
    assert size_at_checkpoint == os.path.getsize(collector_module.REPORTS_DAT)


def test_replay_after_checkpoint_loss_does_not_duplicate_cycles(db_conn, machine):
    """Ticket 1.3: simuluje restart/replay se ztracenym checkpointem -
    cycle_identity musi zabranit duplicitnim radkum v cycles, i kdyz se
    stejna davka zpracuje znovu od zacatku."""
    tmp_path = machine
    _write_reports_dat(tmp_path, ["1,10.0", "2,12.0", "3,15.0"])
    assert collector_module.read_new_cycles(db_conn) == 3
    assert _cycles_count(db_conn) == 3

    # Simuluje ztraceny/resetnuty checkpoint (napr. obnova ze zalohy,
    # rucni zasah) - soubor je porad stejny, DB uz tyto cykly ale ma.
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE collector_state SET reports_lines_read=0, last_line_hash=NULL, "
            "reports_dat_size_at_checkpoint=NULL WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
    db_conn.commit()

    inserted_again = collector_module.read_new_cycles(db_conn)

    assert inserted_again == 0, "cycle_identity mel zabranit vsem 3 duplicitam"
    assert _cycles_count(db_conn) == 3, "cycles se nesmely zdvojit"

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cycle_identity WHERE machine_code=%s", (MACHINE_CODE,))
        assert cur.fetchone()[0] == 3


def test_overlapping_batch_only_new_cycles_land_in_db(db_conn, machine):
    tmp_path = machine
    _write_reports_dat(tmp_path, ["1,10.0", "2,12.0"])
    assert collector_module.read_new_cycles(db_conn) == 2

    # Pridany radek + checkpoint umyslne vraceny o jeden zpet, aby se
    # radek 2 zpracoval znovu spolu s genuinne novym radkem 3.
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE collector_state SET reports_lines_read=1 WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
    db_conn.commit()
    _write_reports_dat(tmp_path, ["1,10.0", "2,12.0", "3,9.0"])

    inserted = collector_module.read_new_cycles(db_conn)

    assert inserted == 1, "jen cyklus 3 je genuinne novy, cyklus 2 uz existuje"
    assert _cycles_count(db_conn) == 3


def test_truncated_file_outside_rotation_is_detected_and_recovers(db_conn, machine, caplog):
    tmp_path = machine
    _write_reports_dat(tmp_path, ["1,10.0", "2,12.0", "3,15.0", "4,11.0"])
    assert collector_module.read_new_cycles(db_conn) == 4

    # Neocekavane zkraceni MIMO maybe_rotate() - napr. rucni zasah.
    _write_reports_dat(tmp_path, ["1,10.0"])

    with caplog.at_level("WARNING"):
        inserted = collector_module.read_new_cycles(db_conn)

    assert any("zkracen" in r.message or "mensi nez posledni checkpoint" in r.message for r in caplog.records)
    # cycle_identity porad chrani proti duplicite cyklu 1, i kdyz se
    # checkpoint interne "resetnul" na cteni od zacatku.
    assert inserted == 0
    assert _cycles_count(db_conn) == 4, "puvodni 4 cykly zustavaji, zadna duplicita"


def test_hash_mismatch_at_checkpoint_line_is_detected(db_conn, machine, caplog):
    tmp_path = machine
    _write_reports_dat(tmp_path, ["1,10.0", "2,12.0"])
    assert collector_module.read_new_cycles(db_conn) == 2

    # Stejna delka radku na pozici checkpointu ("2,12.0" / "9,99.0" maji
    # oba 6 znaku), takze velikost souboru zustava stejna - truncation
    # size-check sam o sobe nezachyti nic, testujeme specificky hash-check.
    _write_reports_dat(tmp_path, ["1,10.0", "9,99.0"])
    assert os.path.getsize(collector_module.REPORTS_DAT) >= _checkpoint(db_conn)[2]

    with caplog.at_level("WARNING"):
        collector_module.read_new_cycles(db_conn)

    assert any("neodpovida ocekavanemu hashi" in r.message for r in caplog.records)


def _heartbeat(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT last_heartbeat_at FROM collector_state WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
        return cur.fetchone()[0]


def test_write_heartbeat_sets_last_heartbeat_at_against_real_db(db_conn, machine):
    """Ticket 1.8 (see postgres/init/20_add_collector_heartbeat.sql):
    write_heartbeat() must be a real, standalone DB write against the
    actual collector_state.last_heartbeat_at column, independent of
    read_new_cycles() ever having found any data."""
    assert _heartbeat(db_conn) is None

    collector_module.write_heartbeat(db_conn)

    first = _heartbeat(db_conn)
    assert first is not None

    # A second call (simulating the next main() loop iteration, e.g. with
    # no new REPORTS.DAT data at all) must move the timestamp forward -
    # this is the whole point of ticket 1.8: unlike last_poll_at, the
    # heartbeat advances every iteration regardless of whether any cycles
    # were ingested.
    import time as _time
    _time.sleep(0.01)
    collector_module.write_heartbeat(db_conn)
    second = _heartbeat(db_conn)
    assert second >= first


def test_malformed_last_line_is_held_back_then_resolves(db_conn, machine):
    tmp_path = machine
    _write_reports_dat(tmp_path, ["1,10.0", "2,12.0", "3,BAD,EXTRA"])

    inserted = collector_module.read_new_cycles(db_conn)

    assert inserted == 2, "posledni (nekompletni/poskozeny) radek se nezapocital"
    assert _cycles_count(db_conn) == 2
    reports_lines_read, _, _ = _checkpoint(db_conn)
    assert reports_lines_read == 2, "checkpoint se pridrzel pred poskozenym poslednim radkem"

    # Stroj "dopsal" dalsi platny cyklus - drive posledni (poskozeny)
    # radek uz neni posledni, takze se ma normalne preskocit a checkpoint
    # ma prejit az za nej.
    _write_reports_dat(tmp_path, ["1,10.0", "2,12.0", "3,BAD,EXTRA", "4,13.0"])

    inserted_next = collector_module.read_new_cycles(db_conn)

    assert inserted_next == 1, "jen radek 4 je platny, radek 3 zustava trvale poskozeny"
    assert _cycles_count(db_conn) == 3
    reports_lines_read, _, _ = _checkpoint(db_conn)
    assert reports_lines_read == 4


def _archive_rows(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT archive_path, sha256, line_count, size_bytes FROM reports_dat_archive "
            "WHERE machine_code=%s ORDER BY archived_at",
            (MACHINE_CODE,),
        )
        return cur.fetchall()


def test_maybe_rotate_records_archive_row_against_real_db(db_conn, machine, monkeypatch):
    """Tiket 1.5 (viz postgres/init/21_add_reports_dat_archive.sql): po
    rotaci REPORTS.DAT musi v reports_dat_archive pribyt radek se spravnym
    sha256/velikosti archivovaneho souboru a s poctem radku, ktere byly do
    "cycles" skutecne ingestovany PRED touto rotaci (ne s celkovym poctem
    radku ve souboru)."""
    tmp_path = machine
    # 4 radky v souboru, ale checkpoint drzime na 3 (simuluje napr. holdback
    # nedokonceneho posledniho radku) - archiv ma zaznamenat 3, ne 4.
    _write_reports_dat(tmp_path, ["1,10.0", "2,12.0", "3,15.0", "4,11.0"])
    assert collector_module.read_new_cycles(db_conn) == 4
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE collector_state SET reports_lines_read=3 WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
    db_conn.commit()

    # Vynutit rotaci bez skutecneho cekani/psani JOB souboru mimo tento test.
    monkeypatch.setattr(collector_module, "ROTATE_SIZE_MB", 0.0000001)
    monkeypatch.setattr(collector_module, "write_request", lambda job_name: None)
    monkeypatch.setattr(collector_module.time, "sleep", lambda secs: None)

    collector_module.maybe_rotate(db_conn)

    rows = _archive_rows(db_conn)
    assert len(rows) == 1
    archive_path, sha256_hex, line_count, size_bytes = rows[0]
    assert os.path.exists(archive_path)
    assert archive_path != str(tmp_path / "REPORTS.DAT")
    with open(archive_path, "rb") as f:
        content = f.read()
    assert sha256_hex == hashlib.sha256(content).hexdigest()
    assert size_bytes == len(content)
    assert line_count == 3

    reports_lines_read, last_line_hash, size_at_checkpoint = _checkpoint(db_conn)
    assert reports_lines_read == 0
    assert last_line_hash is None
    assert size_at_checkpoint is None


def test_read_new_cycles_resolves_order_ref_per_row_against_real_db(db_conn, machine, monkeypatch):
    """Ticket 1.7 end-to-end against real Postgres (alembic/versions/
    0003_add_order_assignments.py): a batch whose reconstructed occurred_at
    spans an order change must split order_ref per row accordingly -
    NOT stamp the whole batch with whatever get_active_order() would return
    right now (the bug MES_TARGET_ARCHITECTURE.md §5.2 describes)."""
    import datetime as datetime_module

    frozen_now = datetime_module.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime_module.timezone.utc)

    class _FrozenDateTime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    monkeypatch.setattr(collector_module, "datetime", _FrozenDateTime)

    tmp_path = machine
    # 3 rows, 10s apart: cycle 12 (last) -> occurred_at 12:00:00, cycle 11
    # -> 11:59:50, cycle 10 (first) -> 11:59:40 (see reconstruct_occurred_at).
    _write_reports_dat(tmp_path, ["10,10.0", "11,10.0", "12,10.0"])

    # Cutoff strictly between cycle 10's and cycle 11's occurred_at, so the
    # batch genuinely splits across the two intervals.
    cutoff = frozen_now - datetime_module.timedelta(seconds=15)
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO order_assignments (machine_code, order_ref, valid_from, valid_to) "
            "VALUES (%s, %s, %s, %s)",
            (MACHINE_CODE, "ORDER-BEFORE", frozen_now - datetime_module.timedelta(hours=1), cutoff),
        )
        cur.execute(
            "INSERT INTO order_assignments (machine_code, order_ref, valid_from, valid_to) "
            "VALUES (%s, %s, %s, %s)",
            (MACHINE_CODE, "ORDER-AFTER", cutoff, None),
        )
    db_conn.commit()

    inserted = collector_module.read_new_cycles(db_conn)
    assert inserted == 3

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT cycle_count, order_ref FROM cycles WHERE machine_code=%s ORDER BY cycle_count",
            (MACHINE_CODE,),
        )
        rows = dict(cur.fetchall())

    assert rows == {10: "ORDER-BEFORE", 11: "ORDER-AFTER", 12: "ORDER-AFTER"}


def test_get_active_order_writes_order_assignment_transition_against_real_db(db_conn, machine, monkeypatch):
    """Ticket 1.7: get_active_order()/_sync_order_assignment() must persist
    a real row in order_assignments exactly when the observed Cyclades
    order changes, and must NOT write again while it hasn't (see
    collector.py's _sync_order_assignment for why: comparing against the
    last value THIS PROCESS wrote, not re-querying on every call)."""

    class _FakeCycladesDriver:
        """Stands in for the pymssql module - see collector/tests/
        test_order_assignments.py's identical fake for the unit-test
        version of this same scenario."""

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

    monkeypatch.setattr(collector_module, "pymssql", _FakeCycladesDriver(["ORD-A", "ORD-A", "ORD-B"]))
    monkeypatch.setattr(collector_module, "CYCLADES_DB_HOST", "cyclades-host")
    monkeypatch.setattr(collector_module, "CYCLADES_MAC_REFMAC", "P-01")
    monkeypatch.setattr(collector_module, "CYCLADES_CACHE_TTL_SEC", 0)
    monkeypatch.setattr(collector_module, "_last_written_order_ref", {"value": collector_module._UNOBSERVED})
    monkeypatch.setattr(collector_module, "_cyclades_cache", {"order_ref": None, "checked_at": 0.0})

    assert collector_module.get_active_order(db_conn) == "ORD-A"
    assert collector_module.get_active_order(db_conn) == "ORD-A"
    assert collector_module.get_active_order(db_conn) == "ORD-B"

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT order_ref, valid_to FROM order_assignments "
            "WHERE machine_code=%s ORDER BY valid_from",
            (MACHINE_CODE,),
        )
        rows = cur.fetchall()

    assert len(rows) == 2, "only the genuine ORD-A -> ORD-B transition should have written a new row"
    assert rows[0][0] == "ORD-A" and rows[0][1] is not None  # closed
    assert rows[1][0] == "ORD-B" and rows[1][1] is None      # still open
    assert size_at_checkpoint is None
