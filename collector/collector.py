#!/usr/bin/env python3
"""
Euromap63 Collector — sleduje sdilenou FTP slozku (na kterou se pripojuje
vstrikovaci lis pres protokol EUROMAP 63/SPI) a prubezne uklada cyklova
data z REPORTS.DAT do TimescaleDB.

Pri prvnim startu:
  1. Nahraje JOB sablony (ABORT/GETINFO/GETID/REPORTS.JOB) do FTP korene.
  2. Posle SESSxxxx.REQ s ABORT.JOB (vycisti pripadne bezici stare ulohy),
     pak SESSxxxx.REQ s EXECUTE REPORTS.JOB (spusti produkcni sber dat).
  Tohle se deje jen jednou (viz collector_state.job_armed_at) - restart
  kontejneru uz stroj znovu nevyzbrojuje.

Hlavni smycka (kazdych POLL_INTERVAL_SEC):
  - precte nove radky z REPORTS.DAT (od posledniho zpracovaneho radku,
    stav se drzi v DB, ne v souboru - prezije restart kontejneru),
    naparsuje CSV header a hodnoty, ulozi do tabulky cycles.
  - pokud REPORTS.DAT prekroci ROTATE_SIZE_MB, provede rotaci: ABORT,
    prejmenovani stareho souboru (rename na Linuxu funguje i na otevrenem
    souboru, na rozdil od Windows/NTFS), znovu EXECUTE REPORTS.JOB.

Promenne prostredi (viz docker-compose.yml):
  DATABASE_URL       — postgresql://user:pass@host:port/dbname
  MACHINE_CODE       — identifikator stroje v DB (napr. KM-MC5-01)
  FTP_ROOT           — cesta ke sdilenemu FTP korenu (/ftpdata)
  POLL_INTERVAL_SEC  — jak casto kontrolovat REPORTS.DAT
  ROTATE_SIZE_MB     — velikost, pri ktere se REPORTS.DAT rotuje
"""
import glob
import json
import logging
import os
import time
from datetime import datetime, timezone

import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("euromap63-collector")

DATABASE_URL = os.environ["DATABASE_URL"]
MACHINE_CODE = os.environ.get("MACHINE_CODE", "KM-MC5-01")
FTP_ROOT = os.environ.get("FTP_ROOT", "/ftpdata")
POLL_INTERVAL_SEC = float(os.environ.get("POLL_INTERVAL_SEC", "3"))
ROTATE_SIZE_MB = float(os.environ.get("ROTATE_SIZE_MB", "50"))
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

REPORTS_DAT = os.path.join(FTP_ROOT, "REPORTS.DAT")
REPORTS_LOG = os.path.join(FTP_ROOT, "REPORTS.LOG")


def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def ensure_machine(conn):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO machines (machine_code, machine_name, ftp_root) "
            "VALUES (%s, %s, %s) ON CONFLICT (machine_code) DO NOTHING",
            (MACHINE_CODE, MACHINE_CODE, FTP_ROOT),
        )
        cur.execute(
            "INSERT INTO collector_state (machine_code) VALUES (%s) "
            "ON CONFLICT DO NOTHING",
            (MACHINE_CODE,),
        )
    conn.commit()


def used_session_numbers():
    used = set()
    for pattern in ("SESS*.REQ", "SESS*.RSP"):
        for f in glob.glob(os.path.join(FTP_ROOT, pattern)):
            digits = os.path.basename(f)[4:8]
            if digits.isdigit():
                used.add(int(digits))
    return used


def write_request(job_name):
    used = used_session_numbers()
    num = 0
    while num in used:
        num += 1
    fname = f"SESS{num:04d}.REQ"
    path = os.path.join(FTP_ROOT, fname)
    with open(path, "w") as f:
        f.write(f"00000001 EXECUTE {job_name};\n")
    log.info("Zapsan pozadavek %s -> EXECUTE %s", fname, job_name)
    return fname


def deploy_job_templates():
    for job_file in ("ABORT.JOB", "GETINFO.JOB", "GETID.JOB", "REPORTS.JOB"):
        src = os.path.join(TEMPLATES_DIR, job_file)
        dst = os.path.join(FTP_ROOT, job_file)
        with open(src, "rb") as s, open(dst, "wb") as d:
            d.write(s.read())
    log.info("JOB sablony nahrany do %s", FTP_ROOT)


def arm_reports_job(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT job_armed_at FROM collector_state WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
        row = cur.fetchone()
    if row and row[0] is not None:
        log.info("REPORTS.JOB uz byl drive vyzbrojen (%s), preskakuji.", row[0])
        return

    deploy_job_templates()
    write_request("ABORT.JOB")
    time.sleep(2)
    write_request("REPORTS.JOB")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE collector_state SET job_armed_at=now() WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
    conn.commit()
    log.info("REPORTS.JOB vyzbrojen (ABORT + EXECUTE odeslany, ceka se na zpracovani strojem).")


def split_csv_line(line):
    """Split a REPORTS.DAT CSV line on commas, except commas inside [...].

    Some EUROMAP parameter names use multi-index array syntax like
    "ActTmpBrlZn[1,3]" - a plain str.split(",") breaks that single field
    into two, desyncing the header from every data row.
    """
    fields = []
    depth = 0
    current = []
    for ch in line:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def read_new_cycles(conn):
    if not os.path.exists(REPORTS_DAT):
        return 0

    with conn.cursor() as cur:
        cur.execute(
            "SELECT reports_lines_read FROM collector_state WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
        (last_count,) = cur.fetchone()

    with open(REPORTS_DAT, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    if not lines:
        return 0

    header = split_csv_line(lines[0])
    data_lines = lines[1:]
    if len(data_lines) <= last_count:
        return 0

    new_lines = data_lines[last_count:]
    inserted = 0
    with conn.cursor() as cur:
        for line in new_lines:
            values = split_csv_line(line)
            if len(values) != len(header):
                log.warning("Preskakuji poskozeny radek REPORTS.DAT: %r", line)
                continue
            row = dict(zip(header, values))
            try:
                cycle_count = int(float(row.get("ActCntCyc", "nan")))
            except ValueError:
                log.warning("Radek bez platneho ActCntCyc, preskakuji: %r", line)
                continue
            try:
                cycle_time = float(row["ActTimCyc"]) if row.get("ActTimCyc") else None
            except ValueError:
                cycle_time = None

            # Zadny ON CONFLICT - tabulka cycles nema unique constraint na
            # (machine_code, cycle_count), protoze TimescaleDB vyzaduje, aby
            # unique/PK vzdy obsahoval partitioning sloupec "time". Dedup
            # reseni je atomicka transakce + reports_lines_read nize.
            cur.execute(
                """
                INSERT INTO cycles (machine_code, cycle_count, cycle_time_s, params)
                VALUES (%s, %s, %s, %s)
                """,
                (MACHINE_CODE, cycle_count, cycle_time, json.dumps(row)),
            )
            inserted += 1

        cur.execute(
            "UPDATE collector_state SET reports_lines_read=%s, last_poll_at=now() "
            "WHERE machine_code=%s",
            (len(data_lines), MACHINE_CODE),
        )
    conn.commit()

    if inserted:
        log.info(
            "Vlozeno %d novych cyklu (celkem v REPORTS.DAT: %d).",
            inserted,
            len(data_lines),
        )
    return inserted


def maybe_rotate(conn):
    if not os.path.exists(REPORTS_DAT):
        return
    size_mb = os.path.getsize(REPORTS_DAT) / (1024 * 1024)
    if size_mb < ROTATE_SIZE_MB:
        return

    log.warning("REPORTS.DAT dosahl %.1f MB, provadim rotaci.", size_mb)
    write_request("ABORT.JOB")
    time.sleep(2)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup = f"{REPORTS_DAT}.{stamp}"
    os.replace(REPORTS_DAT, backup)
    if os.path.exists(REPORTS_LOG):
        os.remove(REPORTS_LOG)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE collector_state SET reports_lines_read=0 WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
    conn.commit()

    write_request("REPORTS.JOB")
    log.info("Rotace hotova, stara data v %s, REPORTS.JOB znovu spusten.", backup)


def main():
    log.info(
        "Start collectoru pro %s, sleduji %s (interval %.1fs, rotace nad %.0f MB).",
        MACHINE_CODE, FTP_ROOT, POLL_INTERVAL_SEC, ROTATE_SIZE_MB,
    )
    conn = get_conn()
    ensure_machine(conn)
    arm_reports_job(conn)

    while True:
        try:
            read_new_cycles(conn)
            maybe_rotate(conn)
        except psycopg2.Error:
            log.exception("Chyba DB spojeni, obnovuji spojeni.")
            try:
                conn.close()
            except Exception:
                pass
            time.sleep(POLL_INTERVAL_SEC)
            conn = get_conn()
            continue
        except Exception:
            log.exception("Neocekavana chyba v hlavni smycce.")
            try:
                conn.rollback()
            except Exception:
                pass
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
