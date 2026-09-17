"""
Euromap63 API — FastAPI backend nad TimescaleDB, poskytuje cyklova data
a zakladni statistiky pro dashboard, plus WebSocket push novych cyklu
a stav stroje (bezi/stoji) z Cyclades MES.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

CYCLADES_TZ = ZoneInfo("Europe/Prague")


def _cyclades_local(dt):
    """pymssql vraci naivni datetime v mistnim case Cyclades serveru
    (Europe/Prague). Naseho cycles.time je TIMESTAMPTZ (UTC) - bez
    pripojeni spravne timezone by porovnani bylo posunute o 1-2 hodiny
    (CET/CEST) a okno by nenaslo zadne cykly.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=CYCLADES_TZ)


def _to_cyclades_naive(dt):
    """Opak _cyclades_local - nas UTC-aware cas prevede na naivni
    Europe/Prague cas pro dotaz do Cyclades."""
    if dt is None:
        return None
    return dt.astimezone(CYCLADES_TZ).replace(tzinfo=None)

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

try:
    import pymssql
except ImportError:
    pymssql = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("euromap63-api")

DATABASE_URL = os.environ["DATABASE_URL"]

CYCLADES_DB_HOST = os.environ.get("CYCLADES_DB_HOST", "")
CYCLADES_DB_USER = os.environ.get("CYCLADES_DB_USER", "")
CYCLADES_DB_PASSWORD = os.environ.get("CYCLADES_DB_PASSWORD", "")
STATUS_CACHE_TTL_SEC = 5
CYCLE_POLL_INTERVAL_SEC = 1.5

app = FastAPI(title="Euromap63 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


@app.get("/api/health")
def health():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/machines")
def list_machines():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT machine_code, machine_name, cyclades_mac_refmac, active, created_at "
            "FROM machines ORDER BY machine_code"
        )
        return cur.fetchall()


@app.get("/api/parameters")
def list_parameters(machine: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT param_name, param_type, param_unit, param_label, param_category FROM machine_parameters "
            "WHERE machine_code=%s ORDER BY param_name",
            (machine,),
        )
        return cur.fetchall()


@app.get("/api/cycles/latest")
def latest_cycle(machine: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT time, cycle_count, cycle_time_s, order_ref, params FROM cycles "
            "WHERE machine_code=%s ORDER BY cycle_count DESC LIMIT 1",
            (machine,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Zadna data pro tento stroj.")
    return row


@app.get("/api/cycles")
def list_cycles(machine: str, limit: int = Query(200, le=2000), since: str = None, until: str = None):
    """Bez since: chova se jako drive - poslednich `limit` cyklu.
    Se since (a volitelne until, jinak do "ted"): casove okno misto poctu
    cyklu - pro presety (15m/1h/6h/24h/3d/7d) a vlastni rozsah v dashboardu.
    """
    with get_conn() as conn, conn.cursor() as cur:
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
                until_dt = datetime.fromisoformat(until) if until else datetime.now(timezone.utc)
            except ValueError:
                raise HTTPException(status_code=400, detail="Neplatny format since/until (ocekavano ISO 8601).")
            cur.execute(
                "SELECT time, cycle_count, cycle_time_s, order_ref, params FROM cycles "
                "WHERE machine_code=%s AND time >= %s AND time <= %s "
                "ORDER BY cycle_count LIMIT %s",
                (machine, since_dt, until_dt, 20000),
            )
            return cur.fetchall()

        cur.execute(
            "SELECT time, cycle_count, cycle_time_s, order_ref, params FROM cycles "
            "WHERE machine_code=%s ORDER BY cycle_count DESC LIMIT %s",
            (machine, limit),
        )
        rows = cur.fetchall()
    rows.reverse()
    return rows


@app.get("/api/cycles/by-order")
def cycles_by_order(order_ref: str, machine: str = None, limit: int = Query(5000, le=50000)):
    """Dohledatelnost pri reklamaci: vsechny cyklove parametry patrici
    ke konkretnimu cislu zakazky (OF) - napric jednim nebo vsemi stroji.
    """
    with get_conn() as conn, conn.cursor() as cur:
        if machine:
            cur.execute(
                "SELECT time, machine_code, cycle_count, cycle_time_s, order_ref, params FROM cycles "
                "WHERE order_ref=%s AND machine_code=%s ORDER BY cycle_count LIMIT %s",
                (order_ref, machine, limit),
            )
        else:
            cur.execute(
                "SELECT time, machine_code, cycle_count, cycle_time_s, order_ref, params FROM cycles "
                "WHERE order_ref=%s ORDER BY machine_code, cycle_count LIMIT %s",
                (order_ref, limit),
            )
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Zadna data pro tuto zakazku.")
    return rows


def order_ref_for_label(label: int):
    """Dohleda cislo zakazky (OF_REFOF) v Cyclades podle cisla stitku -
    stitky se tisknou v rozsazich (ETQ_DEBUT..ETQ_FIN) patrici jedne
    zakazce, viz GPAO_PVL_SAP.dbo.ETQGPAO (stejna tabulka jako report
    Rpt_EtqSAP_Liste_etq v Cyclades).
    """
    if not (pymssql and CYCLADES_DB_HOST):
        raise HTTPException(status_code=503, detail="Pripojeni na Cyclades neni nakonfigurovano.")
    try:
        conn = pymssql.connect(
            server=CYCLADES_DB_HOST,
            user=CYCLADES_DB_USER,
            password=CYCLADES_DB_PASSWORD,
            database="GPAO_PVL_SAP",
            timeout=5,
            login_timeout=5,
        )
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT TOP 1 OF_REFOF FROM ETQGPAO "
                "WHERE %s BETWEEN ETQ_DEBUT AND ETQ_FIN "
                "ORDER BY ETQ_DEBUT DESC",
                (label,),
            )
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except pymssql.Error:
        raise HTTPException(status_code=502, detail="Dotaz na Cyclades selhal.")


@app.get("/api/cycles/by-label")
def cycles_by_label(label: int, machine: str = None, limit: int = Query(5000, le=50000)):
    """Dohledatelnost pri reklamaci podle cisla etikety: najde zakazku
    (OF) patrici tomuto stitku v Cycladech, pak vrati vsechny cyklove
    parametry ulozene pod timto order_ref (stejny mechanismus jako
    /api/cycles/by-order).
    """
    order_ref = order_ref_for_label(label)
    if not order_ref:
        raise HTTPException(status_code=404, detail="Stitek nenalezen v zadne zakazce.")
    return {
        "label": label,
        "order_ref": order_ref,
        "cycles": cycles_by_order(order_ref, machine, limit),
    }


def _query_cyclades(database, sql, params):
    if not (pymssql and CYCLADES_DB_HOST):
        raise HTTPException(status_code=503, detail="Pripojeni na Cyclades neni nakonfigurovano.")
    try:
        conn = pymssql.connect(
            server=CYCLADES_DB_HOST,
            user=CYCLADES_DB_USER,
            password=CYCLADES_DB_PASSWORD,
            database=database,
            timeout=5,
            login_timeout=5,
        )
        try:
            cur = conn.cursor(as_dict=True)
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.close()
    except pymssql.Error:
        raise HTTPException(status_code=502, detail="Dotaz na Cyclades selhal.")


def _machine_code_for_mac_refmac(mac_refmac):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT machine_code FROM machines WHERE cyclades_mac_refmac=%s", (mac_refmac,))
        row = cur.fetchone()
    return row["machine_code"] if row else None


def _mac_refmac_for_machine_code(machine_code):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT cyclades_mac_refmac FROM machines WHERE machine_code=%s", (machine_code,))
        row = cur.fetchone()
    return row["cyclades_mac_refmac"] if row else None


DOWNTIME_REASON_LOOKUP_PAD_MIN = 10  # jak daleko za konec mezery jeste hledat duvod v Cyclades
MAX_DOWNTIME_SEGMENTS_ENRICHED = 200  # limit dotazu na Cyclades za jedno volani


@app.get("/api/downtimes")
def downtimes(machine: str, since: str, until: str = None, min_gap_s: float = Query(90, ge=10)):
    """Casova osa prostoju odvozena z MEZER v nasich vlastnich cyklovych
    datech (zadny cyklus po dobu >= min_gap_s = stroj stal) - na rozdil
    od Cyclades HISTOEVENEMENTS (periodicky "sample" log s nejasnou
    presnou semantikou zacatku/konce) je tohle nezpochybnitelne presne,
    protoze vychazi primo z toho, kdy nas EUROMAP63 sber skutecne
    zaznamenal/nezaznamenal cyklus. Cyclades HISTOEVENEMENTS se pouzije
    jen jako doplnek pro CITELNY DUVOD té mezery (ARR_REFARRET/ARR_LIBARRET),
    ne pro urceni hranic mezery samotne.
    """
    since_dt = datetime.fromisoformat(since)
    until_dt = datetime.fromisoformat(until) if until else datetime.now(timezone.utc)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT time, cycle_count, cycle_time_s FROM cycles WHERE machine_code=%s AND time >= %s AND time <= %s "
            "ORDER BY cycle_count",
            (machine, since_dt, until_dt),
        )
        rows = cur.fetchall()

    segments = []
    for prev, cur_row in zip(rows, rows[1:]):
        gap = (cur_row["time"] - prev["time"]).total_seconds()
        if gap >= min_gap_s:
            # "time" je okamzik, kdy navazujici cyklus DOBEHL (zapsal se do
            # REPORTS.DAT) - stroj tedy uz beh nekolik sekund pred timhle
            # casem, presne cycle_time_s toho cyklu. Bez korekce by konec
            # prostoje byl posunuty o celou dobu trvani navazujiciho cyklu
            # (u dlouhych/atypickych cyklu klidne desitky-stovky sekund).
            resume_cycle_s = float(cur_row["cycle_time_s"] or 0)
            end_dt = cur_row["time"] - timedelta(seconds=resume_cycle_s)
            if end_dt <= prev["time"]:
                end_dt = cur_row["time"]
            segments.append({
                "start": prev["time"],
                "end": end_dt,
                "duration_s": (end_dt - prev["time"]).total_seconds(),
                "reason": None,
                "reason_code": None,
            })

    mac_refmac = _mac_refmac_for_machine_code(machine)
    if mac_refmac and pymssql and CYCLADES_DB_HOST:
        for seg in segments[:MAX_DOWNTIME_SEGMENTS_ENRICHED]:
            try:
                hist = _query_cyclades(
                    "SUIVPRO",
                    "SELECT TOP 1 h.ARR_REFARRET, t.ARR_LIBARRET FROM HISTOEVENEMENTS h "
                    "LEFT JOIN TYPES_ARRETS t ON t.ARR_REFARRET = h.ARR_REFARRET "
                    "WHERE h.HISEVE_REFMAC=%s AND h.ARR_REFARRET <> 255 "
                    "AND h.HISEVE_DATEEVE >= %s AND h.HISEVE_DATEEVE <= DATEADD(minute, %s, %s) "
                    "ORDER BY h.HISEVE_DATEEVE",
                    (
                        mac_refmac,
                        _to_cyclades_naive(seg["start"]),
                        DOWNTIME_REASON_LOOKUP_PAD_MIN,
                        _to_cyclades_naive(seg["end"]),
                    ),
                )
            except HTTPException:
                hist = None
            if hist:
                seg["reason_code"] = hist[0]["ARR_REFARRET"]
                seg["reason"] = hist[0]["ARR_LIBARRET"]

    return {"since": since_dt, "until": until_dt, "min_gap_s": min_gap_s, "segments": segments}


@app.get("/api/cycles/by-package")
def cycles_by_package(label: str, limit: int = Query(5000, le=50000)):
    """Presna dohledatelnost 'ktere cykly jsou v tomhle konkretnim baleni':
    najde deklaraci stitku v BILAN_SAISIE_EQUIPE (cas, stroj, karton,
    deklarovane mnozstvi), pak predchozi stitek ve STEJNE cislene rade
    (label-1) jako zacatek casoveho okna (stitky bezi ve vice paralelnich
    radach pod jednou zakazkou - typicky vicedutinova forma, kazda
    dutina/produkt ma svou radu, proto nelze brat jen "posledni deklaraci
    na stroji"). Vrati presne cykly z nasi DB spadajici do tohoto okna.
    """
    rows = _query_cyclades(
        "SUIVPRO",
        "SELECT BILPSEQU_DATESAISIE, BILPSEQU_REFCARTON, BILPSEQU_QTEBONNESAISIE, "
        "BILPSEQU_CODEOP, OF_REFOF, BILPSEQU_REFMAC "
        "FROM BILAN_SAISIE_EQUIPE WHERE BILPSEQU_REFETIQUETTE=%s",
        (label,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Stitek nenalezen v zadne deklaraci vyroby.")
    decl = rows[0]

    machine_code = _machine_code_for_mac_refmac(decl["BILPSEQU_REFMAC"])
    if not machine_code:
        raise HTTPException(status_code=404, detail="Stroj z Cyclades neni namapovan na zadny nas machine_code.")

    declared_at = _cyclades_local(decl["BILPSEQU_DATESAISIE"])

    prev_label = str(int(label) - 1)
    prev_rows = _query_cyclades(
        "SUIVPRO",
        "SELECT TOP 1 BILPSEQU_DATESAISIE FROM BILAN_SAISIE_EQUIPE "
        "WHERE BILPSEQU_REFETIQUETTE=%s AND BILPSEQU_REFMAC=%s",
        (prev_label, decl["BILPSEQU_REFMAC"]),
    )
    if prev_rows:
        start_time = _cyclades_local(prev_rows[0]["BILPSEQU_DATESAISIE"])
    else:
        of_rows = _query_cyclades(
            "SUIVPRO",
            "SELECT OF_DATELANCER FROM [OF] WHERE OF_REFOF=%s AND MAC_REFMAC=%s",
            (decl["OF_REFOF"], decl["BILPSEQU_REFMAC"]),
        )
        start_time = _cyclades_local(of_rows[0]["OF_DATELANCER"]) if of_rows else None

    with get_conn() as conn, conn.cursor() as cur:
        if start_time:
            cur.execute(
                "SELECT time, machine_code, cycle_count, cycle_time_s, order_ref, params FROM cycles "
                "WHERE machine_code=%s AND time > %s AND time <= %s "
                "ORDER BY cycle_count LIMIT %s",
                (machine_code, start_time, declared_at, limit),
            )
        else:
            cur.execute(
                "SELECT time, machine_code, cycle_count, cycle_time_s, order_ref, params FROM cycles "
                "WHERE machine_code=%s AND time <= %s "
                "ORDER BY cycle_count DESC LIMIT %s",
                (machine_code, declared_at, limit),
            )
        cycles = cur.fetchall()
        if not start_time:
            cycles.reverse()

    return {
        "label": label,
        "order_ref": decl["OF_REFOF"],
        "machine_code": machine_code,
        "carton_ref": decl["BILPSEQU_REFCARTON"],
        "declared_qty": decl["BILPSEQU_QTEBONNESAISIE"],
        "declared_at": declared_at,
        "operator_code": decl["BILPSEQU_CODEOP"],
        "window_start": start_time,
        "cycles_found": len(cycles),
        "cycles": cycles,
    }


_status_cache = {}  # machine_code -> {"data": {...}, "ts": float}
_label_cache = {}  # machine_code -> {"data": {...}, "ts": float}


def _query_label_progress(mac_refmac):
    """Posledni deklarovany stitek (BILAN_SAISIE_EQUIPE, skutecne dokoncena
    a naskenovana krabice) a dalsi stitek v poradi ve STEJNE cislene rade
    (ETQGPAO.ETQ_ENCOURS pro rozsah obsahujici posledni stitek). Stitky bezi
    ve vice paralelnich radach pod jednou zakazkou (vicedutinova forma) -
    ETQ_ENCOURS je autoritativni "dalsi na rade" hodnota, kterou Cyclades
    uz sam ukazuje operatorovi, takze se nepocita jako last+1 (stroj muze
    mit stitky predtistene o par kusu dopredu).
    """
    if not (pymssql and CYCLADES_DB_HOST and mac_refmac):
        return {"last_label": None, "next_label": None}

    last_rows = _query_cyclades(
        "SUIVPRO",
        "SELECT TOP 1 BILPSEQU_REFETIQUETTE, OF_REFOF FROM BILAN_SAISIE_EQUIPE "
        "WHERE BILPSEQU_REFMAC=%s ORDER BY BILPSEQU_DATESAISIE DESC",
        (mac_refmac,),
    )
    if not last_rows:
        return {"last_label": None, "next_label": None}

    last_label = last_rows[0]["BILPSEQU_REFETIQUETTE"]
    of_refof = last_rows[0]["OF_REFOF"]

    next_label = None
    try:
        range_rows = _query_cyclades(
            "GPAO_PVL_SAP",
            "SELECT TOP 1 ETQ_ENCOURS FROM ETQGPAO "
            "WHERE OF_REFOF=%s AND %s BETWEEN ETQ_DEBUT AND ETQ_FIN",
            (of_refof, last_label),
        )
        if range_rows:
            next_label = range_rows[0]["ETQ_ENCOURS"]
    except HTTPException:
        pass

    return {"last_label": last_label, "next_label": next_label}


def get_label_progress(machine_code, mac_refmac):
    now = time.time()
    cached = _label_cache.get(machine_code)
    if cached and now - cached["ts"] < STATUS_CACHE_TTL_SEC:
        return cached["data"]
    data = _query_label_progress(mac_refmac)
    _label_cache[machine_code] = {"data": data, "ts": now}
    return data


def _query_cyclades_machine_status(mac_refmac):
    """Zjisti, jestli stroj bezi/stoji podle SUIVPRO.dbo.[OF] (base tabulka,
    ne view - live rolling okno otevrenych zakazek). Zivy stav "jede forma":
    radek s OF_DATEFINOF IS NULL = zakazka jeste bezi na stroji.
    OF_CAUSEARRETCOURANT = -2 a OF_DUREARRETCOURANT = 0 -> stroj aktualne jede
    (-2 neni v TYPES_ARRETS - je to sentinel "bez prostoje", ne skutecny duvod).
    Jinak stoji - duvod se dohleda v TYPES_ARRETS (ARR_REFARRET -> ARR_LIBARRET).
    """
    if not (pymssql and CYCLADES_DB_HOST and mac_refmac):
        return {"state": "neznamo", "order_ref": None, "tool_ref": None, "tool_label": None, "tool_mounted_since": None}
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
            cur = conn.cursor(as_dict=True)
            cur.execute(
                "SELECT TOP 1 o.OF_REFOF, o.OUT_REFOUT, o.OF_CAUSEARRETCOURANT, "
                "o.OF_DUREARRETCOURANT, t.ARR_LIBARRET, u.OUT_LIBOUT, u.OUT_DATEMONTAGE "
                "FROM [OF] o "
                "LEFT JOIN TYPES_ARRETS t ON t.ARR_REFARRET = o.OF_CAUSEARRETCOURANT "
                "LEFT JOIN OUTIL u ON u.OUT_REFOUT = o.OUT_REFOUT "
                "WHERE o.MAC_REFMAC=%s AND o.OF_DATEFINOF IS NULL "
                "ORDER BY o.OF_DATELANCER DESC",
                (mac_refmac,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except pymssql.Error:
        log.warning("Cyclades dotaz na stav stroje %s selhal.", mac_refmac)
        return {"state": "neznamo", "order_ref": None, "tool_ref": None, "tool_label": None, "tool_mounted_since": None}

    if not row:
        return {"state": "bez_zakazky", "order_ref": None, "tool_ref": None, "tool_label": None, "tool_mounted_since": None}

    running = row["OF_CAUSEARRETCOURANT"] == -2 and (row["OF_DUREARRETCOURANT"] or 0) == 0
    return {
        "state": "bezi" if running else "stoji",
        "order_ref": row["OF_REFOF"],
        "tool_ref": row["OUT_REFOUT"],
        "tool_label": row["OUT_LIBOUT"],
        "tool_mounted_since": _cyclades_local(row["OUT_DATEMONTAGE"]),
        "stop_cause": row["OF_CAUSEARRETCOURANT"],
        "stop_reason": None if running else row["ARR_LIBARRET"],
        "stop_duration_s": row["OF_DUREARRETCOURANT"],
    }


def get_machine_status(machine_code, mac_refmac):
    now = time.time()
    cached = _status_cache.get(machine_code)
    if cached and now - cached["ts"] < STATUS_CACHE_TTL_SEC:
        return cached["data"]
    data = _query_cyclades_machine_status(mac_refmac)
    _status_cache[machine_code] = {"data": data, "ts": now}
    return data


@app.get("/api/machines/status")
def machines_status():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT machine_code, machine_name, cyclades_mac_refmac FROM machines "
            "WHERE active ORDER BY machine_code"
        )
        machines = cur.fetchall()

    result = []
    for m in machines:
        status = get_machine_status(m["machine_code"], m["cyclades_mac_refmac"])
        labels = get_label_progress(m["machine_code"], m["cyclades_mac_refmac"])
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT time, cycle_count, cycle_time_s FROM cycles "
                "WHERE machine_code=%s ORDER BY cycle_count DESC LIMIT 1",
                (m["machine_code"],),
            )
            latest = cur.fetchone()
        result.append({
            "machine_code": m["machine_code"],
            "machine_name": m["machine_name"],
            "cyclades_mac_refmac": m["cyclades_mac_refmac"],
            **status,
            **labels,
            "latest_cycle": latest,
        })
    return result


_machine_info_cache = {}  # mac_refmac -> {"data": {...}, "ts": float}
MACHINE_INFO_CACHE_TTL_SEC = 300  # staticke udaje, staci obcas


def _query_cyclades_machine_info(mac_refmac):
    """Popisne udaje o stroji z Cyclades master dat (SUIVPRO.dbo.MACHINE) -
    typ/tonaz, dilna, sekce. Na rozdil od stavu (bezi/stoji) se toto meni
    jen vyjimecne, proto delsi cache.
    """
    if not (pymssql and CYCLADES_DB_HOST and mac_refmac):
        return None
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
            cur = conn.cursor(as_dict=True)
            cur.execute(
                "SELECT m.MAC_REFMAC, m.MAC_LIBMAC, m.ATEL_REFATEL, m.SEC_REFSEC, "
                "t.TYPESMAC_LIB0 AS type_label "
                "FROM MACHINE m "
                "LEFT JOIN TYPES_MACHINE t ON t.TYPESMAC_TYPE = m.MAC_TYPEMAC "
                "WHERE m.MAC_REFMAC=%s",
                (mac_refmac,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except pymssql.Error:
        log.warning("Cyclades dotaz na info o stroji %s selhal.", mac_refmac)
        return None
    return row


def get_machine_info(mac_refmac):
    now = time.time()
    cached = _machine_info_cache.get(mac_refmac)
    if cached and now - cached["ts"] < MACHINE_INFO_CACHE_TTL_SEC:
        return cached["data"]
    data = _query_cyclades_machine_info(mac_refmac)
    _machine_info_cache[mac_refmac] = {"data": data, "ts": now}
    return data


@app.get("/api/machines/info")
def machine_info(machine: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT machine_code, machine_name, cyclades_mac_refmac FROM machines WHERE machine_code=%s",
            (machine,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Neznamy stroj.")
    info = get_machine_info(row["cyclades_mac_refmac"]) if row["cyclades_mac_refmac"] else None
    return {
        "machine_code": row["machine_code"],
        "machine_name": row["machine_name"],
        "cyclades_mac_refmac": row["cyclades_mac_refmac"],
        "cyclades_label": info["MAC_LIBMAC"] if info else None,
        "type_label": info["type_label"] if info else None,
        "atelier": info["ATEL_REFATEL"] if info else None,
        "section": info["SEC_REFSEC"] if info else None,
    }


# ────────────────────────────────────────────────────────────
#  WebSocket - push novych cyklu bez HTTP pollingu z prohlizece.
#  Jeden API proces = jednoduchy in-memory pub/sub staci (zadny Redis
#  navic). Pozadi bezici uloha sleduje DB kazdych CYCLE_POLL_INTERVAL_SEC
#  a posle novy cyklus jen kdyz je aspon jeden posluchac daneho stroje.
# ────────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active = {}  # machine_code -> set[WebSocket]

    async def connect(self, machine, ws):
        await ws.accept()
        self.active.setdefault(machine, set()).add(ws)

    def disconnect(self, machine, ws):
        self.active.get(machine, set()).discard(ws)

    def has_listeners(self, machine):
        return bool(self.active.get(machine))

    async def broadcast(self, machine, message):
        dead = []
        for ws in self.active.get(machine, set()):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active[machine].discard(ws)


manager = ConnectionManager()
_last_broadcast_cycle = {}


def _jsonable(row):
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


@app.websocket("/ws/cycles")
async def ws_cycles(websocket: WebSocket, machine: str):
    await manager.connect(machine, websocket)
    try:
        while True:
            # klient nic neposila, jen drzime spojeni otevrene
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(machine, websocket)


async def _poll_and_broadcast_loop():
    while True:
        try:
            machines = list(manager.active.keys())
            for machine in machines:
                if not manager.has_listeners(machine):
                    continue
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        "SELECT time, cycle_count, cycle_time_s, order_ref, params FROM cycles "
                        "WHERE machine_code=%s ORDER BY cycle_count DESC LIMIT 1",
                        (machine,),
                    )
                    latest = cur.fetchone()
                if latest and _last_broadcast_cycle.get(machine) != latest["cycle_count"]:
                    _last_broadcast_cycle[machine] = latest["cycle_count"]
                    await manager.broadcast(machine, {"type": "cycle", "data": _jsonable(latest)})
        except Exception:
            log.exception("Chyba v poll_and_broadcast smycce.")
        await asyncio.sleep(CYCLE_POLL_INTERVAL_SEC)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(_poll_and_broadcast_loop())


@app.get("/api/stats")
def stats(machine: str, window: int = Query(100, le=5000)):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                count(*)                    AS n,
                avg(cycle_time_s)           AS avg_cycle_time,
                min(cycle_time_s)           AS min_cycle_time,
                max(cycle_time_s)           AS max_cycle_time,
                stddev_samp(cycle_time_s)   AS stddev_cycle_time
            FROM (
                SELECT cycle_time_s FROM cycles
                WHERE machine_code=%s
                ORDER BY cycle_count DESC
                LIMIT %s
            ) t
            """,
            (machine, window),
        )
        return cur.fetchone()
