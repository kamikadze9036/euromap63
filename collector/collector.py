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
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

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


def reconstruct_occurred_at(rows, received_at):
    """Best-effort rekonstrukce skutecneho casu vzniku kazdeho cyklu v ramci
    jedne davky (jeden poll REPORTS.DAT) - viz MES_TARGET_ARCHITECTURE.md
    §5.1 a MES_IMPLEMENTATION_BACKLOG.md tiket 1.1/1.2.

    Stroj v teto konfiguraci neposkytuje spolehlivy vlastni cas, takze se
    postupuje zpetne od "received_at" (cas, kdy collector davku precetl):
    nejnovejsi (posledni) cyklus v davce se ukotvi na received_at a kazdy
    predchozi cyklus se odvodi tak, ze od casu cyklu za nim odecte VLASTNI
    cycle_time_s ("kazdy cyklus nastal cycle_time_s sekund pred tim
    nasledujicim v poradi").

    Parametry:
      rows         - list dictu v chronologickem poradi (nejstarsi prvni,
                     presne jako v REPORTS.DAT), kazdy alespon s klicem
                     "cycle_time_s" (float nebo None).
      received_at  - datetime (tz-aware), cas prijeti/precteni cele davky.

    Vraci list dvojic (occurred_at, occurred_at_source) stejne delky a
    poradi jako "rows". Kdyz radku chybi cycle_time_s (jiz dnes osetreny
    warning pripad - viz vyse "Radek bez platneho ActTimCyc"), nelze pro
    nej rekonstrukci provest a pouzije se fallback occurred_at=received_at,
    occurred_at_source='received_at_fallback'.
    """
    n = len(rows)
    results = [None] * n
    for i in range(n - 1, -1, -1):
        cycle_time = rows[i].get("cycle_time_s")
        if i == n - 1:
            occurred_at = received_at
        elif cycle_time is not None:
            occurred_at = results[i + 1][0] - timedelta(seconds=cycle_time)
        else:
            occurred_at = received_at

        source = "reconstructed_from_cycle_time" if cycle_time is not None else "received_at_fallback"
        results[i] = (occurred_at, source)
    return results


def read_new_cycles(conn):
    if not os.path.exists(REPORTS_DAT):
        return 0

    with conn.cursor() as cur:
        cur.execute(
            "SELECT reports_lines_read, last_line_hash, "
            "reports_dat_size_at_checkpoint FROM collector_state "
            "WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
        (last_count, last_line_hash, reports_dat_size_at_checkpoint) = cur.fetchone()

    # Integritni kontrola checkpointu (MES_TARGET_ARCHITECTURE.md §5.3,
    # MES_IMPLEMENTATION_BACKLOG.md tiket 1.4, viz
    # postgres/init/19_add_checkpoint_integrity.sql). maybe_rotate() svou
    # VLASTNI, zamernou rotaci uz resi spravne (explicitni reset na 0) -
    # tohle chyta jen NEOCEKAVANE zmenseni/nahrazeni souboru mimo tuto
    # cestu (rucni zasah, kvirk stroje, obnova ze zalohy...), kdy by
    # "len(data_lines) <= last_count" nize jinak tise a navzdy zastavilo
    # zpracovani.
    current_size = os.path.getsize(REPORTS_DAT)
    if reports_dat_size_at_checkpoint is not None and current_size < reports_dat_size_at_checkpoint:
        log.warning(
            "REPORTS.DAT je mensi nez posledni checkpoint (%d -> %d bajtu) "
            "mimo rizenou rotaci - soubor byl zrejme zkracen/nahrazen. "
            "Ignoruji stary checkpoint a ctu od zacatku souboru.",
            reports_dat_size_at_checkpoint, current_size,
        )
        last_count = 0

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

    if last_count > 0 and last_line_hash and last_count <= len(data_lines):
        actual_hash = hashlib.sha256(data_lines[last_count - 1].encode("utf-8")).hexdigest()
        if actual_hash != last_line_hash:
            log.warning(
                "Obsah REPORTS.DAT na pozici posledniho checkpointu (radek "
                "%d) neodpovida ocekavanemu hashi - soubor byl zrejme "
                "prepsan jinym obsahem mimo rizenou rotaci. Ignoruji stary "
                "checkpoint a ctu od zacatku souboru.",
                last_count,
            )
            last_count = 0

    if len(data_lines) <= last_count:
        return 0

    new_lines = data_lines[last_count:]
    order_ref = get_active_order()

    # Cas precteni cele davky - zachycen jednou za poll, ne per-radek (viz
    # MES_IMPLEMENTATION_BACKLOG.md tiket 1.1). Pouziva se jak jako
    # received_at, tak jako kotva pro zpetnou rekonstrukci occurred_at.
    received_at = datetime.now(timezone.utc)

    parsed_rows = []
    last_new_line_index = len(new_lines) - 1
    holdback_last_line = False
    for idx, line in enumerate(new_lines):
        values = split_csv_line(line)
        if len(values) != len(header):
            if idx == last_new_line_index:
                # Tenhle radek je AKTUALNE posledni v celem souboru - misto
                # rovnou "warn + preskocit + pocitat do checkpointu" (jako
                # nize u radku, ktere nejsou posledni) pridrzime checkpoint
                # jeden radek pred nim. Duvod (tiket 1.4, viz
                # MES_TARGET_ARCHITECTURE.md §5.3 "nedokonceny posledni
                # radek souboru"): stroj muze byt prave v procesu dopisovani
                # tohoto radku (POLL_INTERVAL_SEC je typicky jen 3s), takze
                # nesedici pocet sloupcu tu muze byt jen docasny stav, ne
                # trvale poskozena data. Pri pristim pollu bude tenhle radek
                # bud uz kompletni (a zpracuje se normalne), nebo uz nebude
                # poslednim radkem souboru (pribyl novejsi radek za nim) - v
                # tom pripade uz normalni "preskocit + pocitat do
                # checkpointu" vetev nize spravne plati, protoze skutecne
                # jde o trvale poskozena data.
                log.warning(
                    "Posledni radek REPORTS.DAT vypada nekompletni/poskozeny "
                    "(mozny soubezny zapis strojem) - pridrzuji checkpoint, "
                    "zkusim znovu pri pristim pollu: %r", line,
                )
                holdback_last_line = True
            else:
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

        parsed_rows.append({
            "cycle_count": cycle_count,
            "cycle_time_s": cycle_time,
            "params": row,
        })

    occurred = reconstruct_occurred_at(parsed_rows, received_at)

    inserted = 0
    with conn.cursor() as cur:
        # cycle_identity je zdroj pravdy pro "uz jsme tento cyklus
        # zaznamenali" (viz postgres/init/18_add_cycle_identity.sql a
        # MES_TARGET_ARCHITECTURE.md §5.3). reports_lines_read
        # (aktualizovano nize) zustava, ale uz jen jako rychla optimalizace
        # pozice cteni souboru - usetri opetovne cteni/parsovani celeho
        # REPORTS.DAT pri kazdem pollu. Neni to vic zdroj pravdy pro dedup:
        # replay archivu, restart collectoru mezi zapisem a checkpointem
        # apod. muze last_count/reports_lines_read obejit, ale
        # cycle_identity ne, protoze insert do ni je ve stejne transakci
        # jako insert do cycles.
        cur.execute(
            "SELECT MAX(cycle_count) FROM cycle_identity WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
        (last_seen_cycle_count,) = cur.fetchone()

        for parsed, (occurred_at, occurred_at_source) in zip(parsed_rows, occurred):
            cycle_count = parsed["cycle_count"]

            # Znama mezera (viz 18_add_cycle_identity.sql a
            # MES_TARGET_ARCHITECTURE.md §5.3 "reset pocitadla cyklu"):
            # ActCntCyc je normalne monotonne rostouci celozivotni citac
            # stroje. Pokud klesne, muze jit o reset citace na strani stroje
            # (servis/firmware) - cycle_identity.PRIMARY KEY (machine_code,
            # cycle_count) by pak novy cyklus se stejnou hodnotou jako pred
            # resetem chybne oznacil za duplicitu a tise ho zahodil. Tady jen
            # logujeme varovani jako casny signal; skutecne reseni (napr.
            # "epoch" sloupec) je mimo rozsah tohoto tiketu.
            if last_seen_cycle_count is not None and cycle_count < last_seen_cycle_count:
                log.warning(
                    "cycle_count pro %s klesl (%s -> %s) - mozny reset "
                    "pocitadla cyklu na stroji, viz MES_TARGET_ARCHITECTURE.md "
                    "§5.3.",
                    MACHINE_CODE, last_seen_cycle_count, cycle_count,
                )
            if last_seen_cycle_count is None or cycle_count > last_seen_cycle_count:
                last_seen_cycle_count = cycle_count

            # Gatekeeper: vloz identitu cyklu (machine_code, cycle_count) a
            # pokracuj s INSERTem do cycles jen kdyz tahle dvojice jeste
            # nebyla zaznamenana. ON CONFLICT DO NOTHING + RETURNING 1 vrati
            # radek jen pri skutecnem insertu, takze fetchone() je None
            # prave a jen pri konfliktu (duplicite). Ve stejne transakci
            # jako insert do cycles nize.
            cur.execute(
                """
                INSERT INTO cycle_identity (machine_code, cycle_count)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                RETURNING 1
                """,
                (MACHINE_CODE, cycle_count),
            )
            if cur.fetchone() is None:
                log.info(
                    "Preskakuji jiz zaznamenany cyklus (duplicitni doruceni): "
                    "machine_code=%s cycle_count=%s",
                    MACHINE_CODE, cycle_count,
                )
                continue

            cur.execute(
                """
                INSERT INTO cycles (
                    machine_code, cycle_count, cycle_time_s, order_ref, params,
                    received_at, occurred_at, occurred_at_source
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    MACHINE_CODE,
                    cycle_count,
                    parsed["cycle_time_s"],
                    order_ref,
                    json.dumps(parsed["params"]),
                    received_at,
                    occurred_at,
                    occurred_at_source,
                ),
            )
            inserted += 1

        # Normalne pokryvame cely soubor (len(data_lines)); pri holdbacku
        # posledniho (nekompletniho) radku ale checkpoint zamerne pridrzime
        # o jeden radek zpet, viz komentar u holdback_last_line vyse.
        lines_read = len(data_lines) - 1 if holdback_last_line else len(data_lines)
        new_last_line_hash = (
            hashlib.sha256(data_lines[lines_read - 1].encode("utf-8")).hexdigest()
            if lines_read > 0 else None
        )
        cur.execute(
            "UPDATE collector_state SET reports_lines_read=%s, last_poll_at=now(), "
            "last_line_hash=%s, reports_dat_size_at_checkpoint=%s "
            "WHERE machine_code=%s",
            (lines_read, new_last_line_hash, current_size, MACHINE_CODE),
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
        # last_line_hash/reports_dat_size_at_checkpoint se tykaji obsahu
        # PRED touto (zamernou) rotaci - musi jit na NULL spolu s
        # reports_lines_read, jinak by prvni read_new_cycles() na novem
        # (prazdnem) REPORTS.DAT porovnaval jeho velikost/hash proti
        # hodnotam ze stareho, uz prejmenovaneho souboru (viz tiket 1.4,
        # postgres/init/19_add_checkpoint_integrity.sql).
        cur.execute(
            "UPDATE collector_state SET reports_lines_read=0, last_line_hash=NULL, "
            "reports_dat_size_at_checkpoint=NULL WHERE machine_code=%s",
            (MACHINE_CODE,),
        )
    conn.commit()

    write_request("REPORTS.JOB")
    log.info("Rotace hotova, stara data v %s, REPORTS.JOB znovu spusten.", backup)


def write_heartbeat(conn):
    """Zapise collector_state.last_heartbeat_at=now() - VOLA SE JEDNOU ZA
    KAZDOU ITERACI hlavni smycky v main(), bez ohledu na to, jestli
    read_new_cycles() nasla nova data, skoncila nekterym ze svych tri
    early-returnu (chybejici/prazdny REPORTS.DAT, zadna nova data - viz
    komentar v postgres/init/20_add_collector_heartbeat.sql), nebo dokonce
    vyhodila osetrenou vyjimku (viz main() - vola se v obou vetvich
    except).

    MES_IMPLEMENTATION_BACKLOG.md tiket 1.8: collector_state.last_poll_at
    (aktualizovane uvnitr read_new_cycles()) NENI skutecny heartbeat, protoze
    ho ty tri early-returny nikdy nezasahnou - efektivne znamena "cas
    posledniho NOVE VLOZENEHO cyklu", ne "cas, kdy collector naposledy
    uspesne dobehl svou pollovaci smycku". Tahle funkce je zamerne
    samostatna a oddelena od read_new_cycles() (ktera resi jen ingest
    cyklu, ne diagnostiku zivosti procesu), s vlastni malou transakci a
    vlastnim try/except - pripadny vypadek DB pri zapisu heartbeatu nesmi
    nikdy shodit samotny sber dat v hlavni smycce main().

    lag_seconds se tu nepocita ani neuklada - viz api/main.py
    GET /api/collectors/health, kde se pocita az pri cteni z now() -
    last_heartbeat_at (aby nezastaravel, viz 20_add_collector_heartbeat.sql).
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE collector_state SET last_heartbeat_at=now() WHERE machine_code=%s",
                (MACHINE_CODE,),
            )
        conn.commit()
    except Exception:
        log.exception("Zapis heartbeatu selhal, pokracuji ve sberu dat.")
        try:
            conn.rollback()
        except Exception:
            pass


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
            # Heartbeat i po obnove spojeni - viz write_heartbeat()
            # docstring, ma se volat kazdou iteraci vcetne osetrenych
            # vyjimek, ne jen "stastnou cestu".
            write_heartbeat(conn)
            continue
        except Exception:
            log.exception("Neocekavana chyba v hlavni smycce.")
            try:
                conn.rollback()
            except Exception:
                pass

        write_heartbeat(conn)
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
