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
  CYCLADES_DB_HOST/USER/PASSWORD — pripojeni na tovarni Cyclades MES
  CYCLADES_MAC_REFMAC — oznaceni stroje v Cyclades (napr. P2700-01),
                        pro dohledani aktivni zakazky (OF) ke kazdemu
                        cyklu. Prazdne/chybejici = order_ref se neplni.
"""
import glob
import json
import logging
import os
import time
from datetime import datetime, timezone

import psycopg2

try:
    import pymssql
except ImportError:
    pymssql = None

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

CYCLADES_DB_HOST = os.environ.get("CYCLADES_DB_HOST", "")
CYCLADES_DB_USER = os.environ.get("CYCLADES_DB_USER", "")
CYCLADES_DB_PASSWORD = os.environ.get("CYCLADES_DB_PASSWORD", "")
CYCLADES_MAC_REFMAC = os.environ.get("CYCLADES_MAC_REFMAC", "")
CYCLADES_CACHE_TTL_SEC = 5

_cyclades_cache = {"order_ref": None, "checked_at": 0.0}

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


def get_active_order():
    """Vrati OF_REFOF (cislo zakazky) aktualne bezici na CYCLADES_MAC_REFMAC,
    nebo None. Vysledek se cachuje na CYCLADES_CACHE_TTL_SEC, aby se sdilena
    tovarni MES databaze nedotazovala pri kazdem pollu REPORTS.DAT. Pri chybe
    spojeni vraci posledni znamou hodnotu misto shozeni cele smycky.
    """
    if not (pymssql and CYCLADES_DB_HOST and CYCLADES_MAC_REFMAC):
        return None

    now = time.time()
    if now - _cyclades_cache["checked_at"] < CYCLADES_CACHE_TTL_SEC:
        return _cyclades_cache["order_ref"]

    try:
        conn = pymssql.connect(
            server=CYCLADES_DB_HOST,
            user=CYCLADES_DB_USER,
            password=CYCLADES_DB_PASSWORD,
            database="SUIVPRO",
            timeout=5,
            login_timeout=5,
        )
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT TOP 1 OF_REFOF FROM [OF] WHERE MAC_REFMAC=%s "
                "AND OF_DATEFINOF IS NULL ORDER BY OF_DATELANCER DESC",
                (CYCLADES_MAC_REFMAC,),
            )
            row = cur.fetchone()
            _cyclades_cache["order_ref"] = row[0] if row else None
        finally:
            conn.close()
    except Exception:
        log.warning("Cyclades dotaz na aktivni OF selhal, pouzivam posledni znamou hodnotu (%r).", _cyclades_cache["order_ref"])

    _cyclades_cache["checked_at"] = now
    return _cyclades_cache["order_ref"]


def is_data_line(line):
    """True if a REPORTS.DAT line starts with a number (a data row),
    as opposed to a parameter name (a header line or its continuation).
    """
    first_field = line.split(",", 1)[0].strip()
    try:
        float(first_field)
        return True
    except ValueError:
        return False


def merge_wrapped_lines(lines):
    """Machine wraps any physical line over ~1000 chars onto a
    continuation line starting with whitespace - a naive is_data_line()
    check can't tell such a continuation apart from a genuine new row
    (e.g. " 260,260" starts with a number too), so merge purely on the
    leading-whitespace signal before any row/column parsing happens.
    """
    merged = []
    for line in lines:
        if merged and line != line.lstrip():
            merged[-1] += line.lstrip()
        else:
            merged.append(line)
    return merged


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

    # Stroj lame dlouhe fyzicke radky (pozorovano puvodne u hlavicky nad
    # ~1000 znaku, po pridani 72 zon temperace formy i u datovych radku)
    # na vice fyzickych radku, pokracovani zacina mezerou. Sloucime vsechny
    # pokracovaci radky zpet na predchozi logicky radek driv, nez cokoliv
    # rozparsujeme - stejne pravidlo plati pro hlavicku i pro data.
    lines = merge_wrapped_lines(lines)

    header_line_count = 0
    header_parts = []
    for i, line in enumerate(lines):
        if i > 0 and is_data_line(line):
            break
        header_parts.append(line)
        header_line_count += 1
    header = split_csv_line("".join(header_parts))
    data_lines = lines[header_line_count:]
    if len(data_lines) <= last_count:
        return 0

    new_lines = data_lines[last_count:]
    order_ref = get_active_order()
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
                INSERT INTO cycles (machine_code, cycle_count, cycle_time_s, order_ref, params)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (MACHINE_CODE, cycle_count, cycle_time, order_ref, json.dumps(row)),
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
