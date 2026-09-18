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


def _downtime_segments_from_cycles(rows, min_gap_s):
    """Mezery v nasich vlastnich cyklovych datech (zadny cyklus po dobu
    >= min_gap_s = stroj stal) - nezpochybnitelne presne, protoze vychazi
    primo z toho, kdy nas EUROMAP63 sber skutecne zaznamenal/nezaznamenal
    cyklus. Pouziva se jen pro stroje s vlastnim cyklovym sberem.
    """
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
    return segments


def _downtime_segments_from_histo_events(mac_refmac, since_dt, until_dt, min_gap_s):
    """Fallback zdroj prostoju pro stroje BEZ vlastniho EUROMAP63 sberu
    (vetsina z 20 lisu na hale) - odvozeno primo z Cyclades
    HISTOEVENEMENTS (kod 255 = bezi, jinak realny duvod prostoje).
    Na rozdil od _downtime_segments_from_cycles je to jen priblizne:
    HISTOEVENEMENTS je periodicky vzorkovany log (vzorky po ~5-45 min),
    ne log-na-zmenu, takze presny okamzik prechodu bezi<->stoji neznama -
    hranice useku jsou casem NEJBLIZSIHO vzorku, ne presnym prechodem.
    """
    if not (mac_refmac and pymssql and CYCLADES_DB_HOST):
        return []
    try:
        rows = _query_cyclades(
            "SUIVPRO",
            "SELECT h.HISEVE_DATEEVE, h.ARR_REFARRET, t.ARR_LIBARRET "
            "FROM HISTOEVENEMENTS h LEFT JOIN TYPES_ARRETS t ON t.ARR_REFARRET = h.ARR_REFARRET "
            "WHERE h.HISEVE_REFMAC=%s AND h.HISEVE_DATEEVE >= %s AND h.HISEVE_DATEEVE <= %s "
            "ORDER BY h.HISEVE_DATEEVE",
            (mac_refmac, _to_cyclades_naive(since_dt), _to_cyclades_naive(until_dt)),
        )
    except HTTPException:
        return []

    segments = []
    current = None
    for row in rows:
        ts = _cyclades_local(row["HISEVE_DATEEVE"])
        if row["ARR_REFARRET"] == 255:
            if current:
                current["end"] = ts
                segments.append(current)
                current = None
            continue
        if current and current["reason_code"] == row["ARR_REFARRET"]:
            continue  # dalsi vzorek stejneho prostoje, cekame na zmenu/konec
        if current:
            current["end"] = ts
            segments.append(current)
        current = {"start": ts, "end": None, "reason": row["ARR_LIBARRET"], "reason_code": row["ARR_REFARRET"]}
    if current:
        current["end"] = until_dt
        segments.append(current)

    for seg in segments:
        seg["duration_s"] = (seg["end"] - seg["start"]).total_seconds()
    return [s for s in segments if s["duration_s"] >= min_gap_s]


@app.get("/api/downtimes")
def downtimes(machine: str, since: str, until: str = None, min_gap_s: float = Query(90, ge=10)):
    since_dt = datetime.fromisoformat(since)
    until_dt = datetime.fromisoformat(until) if until else datetime.now(timezone.utc)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT time, cycle_count, cycle_time_s FROM cycles WHERE machine_code=%s AND time >= %s AND time <= %s "
            "ORDER BY cycle_count",
            (machine, since_dt, until_dt),
        )
        rows = cur.fetchall()

    mac_refmac = _mac_refmac_for_machine_code(machine)

    if rows:
        segments = _downtime_segments_from_cycles(rows, min_gap_s)
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
        source = "cycles"
    else:
        # Stroj bez vlastniho EUROMAP63 sberu (nebo bez dat v tomto okne) -
        # spocitej prostoje primo z Cyclades HISTOEVENEMENTS, aby graf
        # prostoju byl dostupny u kazdeho ze 20 lisu, ne jen instrumentovaneho.
        segments = _downtime_segments_from_histo_events(mac_refmac, since_dt, until_dt, min_gap_s)
        source = "histo_events"

    return {"since": since_dt, "until": until_dt, "min_gap_s": min_gap_s, "segments": segments, "source": source}


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
_worst_cavity_cache = {}  # machine_code -> {"data": {...}|None, "ts": float}


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


def _duration_from_recent_events(latest_code, recent_rows, now_local):
    """Priblizne trvani aktualniho stavu: HISTOEVENEMENTS je periodicky
    vzorkovany log (ne log-na-zmenu), takze presny okamzik prechodu
    neznama - odhadneme ho jako cas NEJSTARSIHO vzorku v souvislem useku
    se stejnym kodem jako ten nejnovejsi (v ramci nekolika posledne
    natazenych radku). Muze mirne podhodnotit skutecne trvani, pokud byl
    stejny stav uz i pred natazenymi radky.
    """
    since = None
    for row in recent_rows:
        if row["ARR_REFARRET"] != latest_code:
            break
        since = row["HISEVE_DATEEVE"]
    if since is None:
        return None
    return (now_local - _cyclades_local(since)).total_seconds()


def _query_cyclades_machine_status(mac_refmac):
    """Zjisti, jestli stroj FYZICKY bezi/stoji podle SUIVPRO.dbo.HISTOEVENEMENTS
    (periodicky vzorkovany stavovy log stroje) - kod ARR_REFARRET=255 je
    sentinel "bez aktualniho prostoje" (bezi), jakykoliv jiny kod je realny
    duvod prostoje z TYPES_ARRETS. Zamerne NEpouzivame SUIVPRO.dbo.[OF]
    (existence otevrene zakazky) jako signal bezi/stoji - stroj muze bezet
    i bez prirazene zakazky (zkusebni kus, rucni rezim) a naopak stat i s
    otevrenou zakazkou (ceka na obsluhu/material). Overeno na datech
    2026-09-18: P220-005 mel po cely den kod 255 (bezi), presto ze [OF]
    pro nej nemel zadny otevreny radek - puvodni [OF]-only heuristika ho
    proto chybne hlasila jako "bez zakazky". [OF] tabulka se pouziva dal,
    ale jen pro order_ref/nastroj (co se prave vyrabi), ne pro bezi/stoji.
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
                "SELECT TOP 20 h.ARR_REFARRET, h.HISEVE_DATEEVE, t.ARR_LIBARRET "
                "FROM HISTOEVENEMENTS h LEFT JOIN TYPES_ARRETS t ON t.ARR_REFARRET = h.ARR_REFARRET "
                "WHERE h.HISEVE_REFMAC=%s ORDER BY h.HISEVE_DATEEVE DESC",
                (mac_refmac,),
            )
            events = cur.fetchall()

            # Aktualni zakazka = OF_REFOF z POSLEDNI smenove deklarace
            # (BILAN_SAISIE_EQUIPE), ne z [OF]/OF_DATEFINOF - viz duvod
            # v komentari u _batch_query_machine_status. [OF] se pouziva
            # jen jako doplnek pro nastroj (forma) k teto zakazce.
            cur.execute(
                "SELECT TOP 1 BILPSEQU_REFETIQUETTE, OF_REFOF FROM BILAN_SAISIE_EQUIPE "
                "WHERE BILPSEQU_REFMAC=%s ORDER BY BILPSEQU_DATESAISIE DESC",
                (mac_refmac,),
            )
            last_decl = cur.fetchone()
            order_ref = last_decl["OF_REFOF"] if last_decl else None

            tool_row = None
            if order_ref:
                cur.execute(
                    "SELECT TOP 1 o.OUT_REFOUT, u.OUT_LIBOUT, u.OUT_DATEMONTAGE "
                    "FROM [OF] o LEFT JOIN OUTIL u ON u.OUT_REFOUT = o.OUT_REFOUT "
                    "WHERE o.OF_REFOF=%s ORDER BY o.OF_DATELANCER DESC",
                    (order_ref,),
                )
                tool_row = cur.fetchone()
        finally:
            conn.close()
    except pymssql.Error:
        log.warning("Cyclades dotaz na stav stroje %s selhal.", mac_refmac)
        return {"state": "neznamo", "order_ref": None, "tool_ref": None, "tool_label": None, "tool_mounted_since": None}

    if not events:
        state, stop_reason, stop_cause, stop_duration_s = "neznamo", None, None, None
    else:
        latest = events[0]
        running = latest["ARR_REFARRET"] == 255
        state = "bezi" if running else "stoji"
        stop_reason = None if running else latest["ARR_LIBARRET"]
        stop_cause = None if running else latest["ARR_REFARRET"]
        stop_duration_s = None if running else _duration_from_recent_events(
            latest["ARR_REFARRET"], events, datetime.now(CYCLADES_TZ)
        )

    return {
        "state": state,
        "order_ref": order_ref,
        "tool_ref": tool_row["OUT_REFOUT"] if tool_row else None,
        "tool_label": tool_row["OUT_LIBOUT"] if tool_row else None,
        "tool_mounted_since": _cyclades_local(tool_row["OUT_DATEMONTAGE"]) if tool_row else None,
        "stop_cause": stop_cause,
        "stop_reason": stop_reason,
        "stop_duration_s": stop_duration_s,
    }


def get_machine_status(machine_code, mac_refmac):
    now = time.time()
    cached = _status_cache.get(machine_code)
    if cached and now - cached["ts"] < STATUS_CACHE_TTL_SEC:
        return cached["data"]
    data = _query_cyclades_machine_status(mac_refmac)
    _status_cache[machine_code] = {"data": data, "ts": now}
    return data


def _unknown_status():
    return {"state": "neznamo", "order_ref": None, "tool_ref": None, "tool_label": None, "tool_mounted_since": None}


CYCLADES_STATUS_REFRESH_INTERVAL_SEC = 5
# Pod timto oknem necinnosti dashboardu background loop Cyclades vubec
# nedotazuje - neni duvod zatezovat produkcni DB, kdyz se nikdo nediva.
CYCLADES_STATUS_IDLE_TIMEOUT_SEC = 60

_last_status_request_ts = 0.0


def _batch_query_machine_status(mac_refmacs):
    """Stav (bezi/stoji, z HISTOEVENEMENTS) + aktualni zakazka (z posledni
    smenove deklarace BILAN_SAISIE_EQUIPE) + nastroj k ni (z [OF]) +
    posledni deklarovany stitek pro VSECHNY predane stroje v jedne MSSQL
    konexi - pouziva background refresh loop, aby se produkcni Cyclades
    DB nezatezovala N-krat vic nez je nutne. Bezi/stoji se bere z
    HISTOEVENEMENTS (fyzicky stav stroje) a zakazka z BILAN_SAISIE_EQUIPE
    (posledni realna vyrobni aktivita) - [OF]/OF_DATEFINOF se ZAMERNE
    NEPOUZIVA pro zadne z toho, viz komentar u prvniho dotazu nize.
    """
    # CROSS APPLY + VALUES misto ROW_NUMBER() OVER (PARTITION BY ... WHERE
    # ... IN (...)) - ten puvodni tvar donutil SQL Server sortovat/rankovat
    # CELOU tabulku (HISTOEVENEMENTS ma historii za mesice/roky) pro vsech
    # 20 stroju najednou a spolehlive to prekracovalo 5s query timeout
    # (pymssql pak hlasi "DBPROCESS is dead"). CROSS APPLY dela pro kazdy
    # stroj z VALUES presne to same "TOP 1 ... WHERE mac=@x ORDER BY ... DESC",
    # co uz osvedcene rychle bezi v jednotlivych (single-machine) dotazech
    # nize - jen v jednom volani/spojeni pro vsechny stroje najednou.
    values_placeholders = ",".join(["(%s)"] * len(mac_refmacs))
    conn = pymssql.connect(
        server=CYCLADES_DB_HOST, user=CYCLADES_DB_USER, password=CYCLADES_DB_PASSWORD,
        database="SUIVPRO", timeout=15, login_timeout=5,
    )
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            "SELECT v.mac AS MAC_REFMAC, x.ARR_REFARRET, x.HISEVE_DATEEVE, t.ARR_LIBARRET "
            f"FROM (VALUES {values_placeholders}) AS v(mac) "
            "CROSS APPLY ("
            "  SELECT TOP 1 h.ARR_REFARRET, h.HISEVE_DATEEVE FROM HISTOEVENEMENTS h "
            "  WHERE h.HISEVE_REFMAC = v.mac ORDER BY h.HISEVE_DATEEVE DESC"
            ") x "
            "LEFT JOIN TYPES_ARRETS t ON t.ARR_REFARRET = x.ARR_REFARRET",
            tuple(mac_refmacs),
        )
        event_rows = cur.fetchall()

        # Aktualni zakazka (order_ref) se BERE Z POSLEDNI SMENOVE DEKLARACE
        # (BILAN_SAISIE_EQUIPE), ne z [OF]/OF_DATEFINOF - overeno na datech
        # 2026-09-18: OF_DATEFINOF u zakazky 0205881 na P220-005 byl
        # nastaveny uz 3 minuty po spusteni (2026-09-11), ale realne
        # smenove deklarace a LIGOF mnozstvi na ni bezely dal cely tyden
        # (naposledy dnes 07:24) - OF_DATEFINOF tedy NEZNAMENA "vyroba
        # skoncila", jen nejakou drivejsi administrativni udalost. [OF]
        # se pouziva uz jen jako doplnek pro nastroj (forma) k teto zakazce.
        cur.execute(
            "SELECT v.mac AS BILPSEQU_REFMAC, x.BILPSEQU_REFETIQUETTE, x.OF_REFOF "
            f"FROM (VALUES {values_placeholders}) AS v(mac) "
            "CROSS APPLY ("
            "  SELECT TOP 1 b.BILPSEQU_REFETIQUETTE, b.OF_REFOF FROM BILAN_SAISIE_EQUIPE b "
            "  WHERE b.BILPSEQU_REFMAC = v.mac ORDER BY b.BILPSEQU_DATESAISIE DESC"
            ") x",
            tuple(mac_refmacs),
        )
        last_label_rows = cur.fetchall()
        last_label_by_mac = {row["BILPSEQU_REFMAC"]: row for row in last_label_rows}
        order_ref_by_mac = {mac: row["OF_REFOF"] for mac, row in last_label_by_mac.items() if row["OF_REFOF"]}

        tool_by_mac = {}
        if order_ref_by_mac:
            pairs = list(order_ref_by_mac.items())
            pair_placeholders = ",".join(["(%s,%s)"] * len(pairs))
            pair_params = tuple(v for pair in pairs for v in pair)
            cur.execute(
                "SELECT v.mac AS MAC_REFMAC, x.OUT_REFOUT, u.OUT_LIBOUT, u.OUT_DATEMONTAGE "
                f"FROM (VALUES {pair_placeholders}) AS v(mac, order_ref) "
                "CROSS APPLY ("
                "  SELECT TOP 1 o.OUT_REFOUT FROM [OF] o "
                "  WHERE o.OF_REFOF = v.order_ref ORDER BY o.OF_DATELANCER DESC"
                ") x "
                "LEFT JOIN OUTIL u ON u.OUT_REFOUT = x.OUT_REFOUT",
                pair_params,
            )
            tool_by_mac = {row["MAC_REFMAC"]: row for row in cur.fetchall()}

        order_refs = sorted(set(order_ref_by_mac.values()))
        worst_cavity_by_order = {}
        if order_refs:
            order_placeholders = ",".join(["%s"] * len(order_refs))
            cur.execute(
                "SELECT OF_REFOF, PROD_REFPROD, PROD_LIBPROD, LIGOF_RANGPRO, LIGOF_QTEBONNE, "
                "LIGOF_QTEREBUT, LIGOF_TXTHEOREBUT FROM LIGOF "
                f"WHERE OF_REFOF IN ({order_placeholders})",
                tuple(order_refs),
            )
            cavities_by_order = {}
            for r in cur.fetchall():
                qty_good = float(r["LIGOF_QTEBONNE"] or 0)
                qty_reject = float(r["LIGOF_QTEREBUT"] or 0)
                qty_total = qty_good + qty_reject
                pct = (qty_reject / qty_total * 100) if qty_total > 0 else None
                cavities_by_order.setdefault(r["OF_REFOF"], []).append({
                    "product": r["PROD_REFPROD"],
                    "label": r["PROD_LIBPROD"],
                    "cavity_no": r["LIGOF_RANGPRO"],
                    "qty_good": qty_good,
                    "qty_reject": qty_reject,
                    "reject_pct": pct,
                    "target_pct": float(r["LIGOF_TXTHEOREBUT"]) if r["LIGOF_TXTHEOREBUT"] is not None else None,
                })
            for order_ref, cavities in cavities_by_order.items():
                with_pct = [c for c in cavities if c["reject_pct"] is not None]
                worst_cavity_by_order[order_ref] = max(with_pct, key=lambda c: c["reject_pct"]) if with_pct else None
    finally:
        conn.close()

    statuses = {}
    worst_cavities = {}
    for row in event_rows:
        mac = row["MAC_REFMAC"]
        order_ref = order_ref_by_mac.get(mac)
        tool_row = tool_by_mac.get(mac)
        running = row["ARR_REFARRET"] == 255
        statuses[mac] = {
            "state": "bezi" if running else "stoji",
            "order_ref": order_ref,
            "tool_ref": tool_row["OUT_REFOUT"] if tool_row else None,
            "tool_label": tool_row["OUT_LIBOUT"] if tool_row else None,
            "tool_mounted_since": _cyclades_local(tool_row["OUT_DATEMONTAGE"]) if tool_row else None,
            "stop_cause": None if running else row["ARR_REFARRET"],
            "stop_reason": None if running else row["ARR_LIBARRET"],
            # Presna doba trvani by vyzadovala dalsi dotaz (historii vzorku)
            # na kazdy stroj zvlast - v batch smycce (kazdych 5 s) se
            # nevyplati, staci pro ni jednotlivy on-demand dotaz na
            # machine.html (_query_cyclades_machine_status).
            "stop_duration_s": None,
        }
        worst_cavities[mac] = worst_cavity_by_order.get(order_ref)
    for mac in mac_refmacs:
        statuses.setdefault(mac, _unknown_status())
        worst_cavities.setdefault(mac, None)

    return statuses, last_label_by_mac, worst_cavities


def _batch_query_next_labels(last_labels):
    """Dalsi stitek v poradi (ETQGPAO.ETQ_ENCOURS) pro kazdy stroj z
    last_labels - jina Cyclades databaze (GPAO_PVL_SAP) nez status/labels
    vyse, takze samostatna konexe, ale opet jen JEDNA pro vsechny stroje
    (dotazy se jen strida na uz otevrenem spojeni, zadne nove connecty).
    """
    if not last_labels:
        return {}
    conn = pymssql.connect(
        server=CYCLADES_DB_HOST, user=CYCLADES_DB_USER, password=CYCLADES_DB_PASSWORD,
        database="GPAO_PVL_SAP", timeout=5, login_timeout=5,
    )
    next_labels = {}
    try:
        cur = conn.cursor(as_dict=True)
        for mac, row in last_labels.items():
            cur.execute(
                "SELECT TOP 1 ETQ_ENCOURS FROM ETQGPAO WHERE OF_REFOF=%s AND %s BETWEEN ETQ_DEBUT AND ETQ_FIN",
                (row["OF_REFOF"], row["BILPSEQU_REFETIQUETTE"]),
            )
            r = cur.fetchone()
            next_labels[mac] = r["ETQ_ENCOURS"] if r else None
    finally:
        conn.close()
    return next_labels


def _refresh_all_machine_status():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT machine_code, cyclades_mac_refmac FROM machines "
            "WHERE active AND cyclades_mac_refmac IS NOT NULL"
        )
        machines = cur.fetchall()
    if not machines:
        return
    mac_refmacs = [m["cyclades_mac_refmac"] for m in machines]
    code_by_mac = {m["cyclades_mac_refmac"]: m["machine_code"] for m in machines}

    try:
        statuses, last_labels, worst_cavities = _batch_query_machine_status(mac_refmacs)
    except pymssql.Error:
        log.warning("Batch dotaz na stav stroju do Cyclades selhal, cache zustava stara.")
        return

    try:
        next_labels = _batch_query_next_labels(last_labels)
    except pymssql.Error:
        log.warning("Batch dotaz na dalsi stitky do Cyclades selhal.")
        next_labels = {}

    now = time.time()
    for mac, code in code_by_mac.items():
        _status_cache[code] = {"data": statuses.get(mac, _unknown_status()), "ts": now}
        label_row = last_labels.get(mac)
        _label_cache[code] = {
            "data": {
                "last_label": label_row["BILPSEQU_REFETIQUETTE"] if label_row else None,
                "next_label": next_labels.get(mac),
            },
            "ts": now,
        }
        _worst_cavity_cache[code] = {"data": worst_cavities.get(mac), "ts": now}


async def _cyclades_status_refresh_loop():
    while True:
        try:
            idle = (time.time() - _last_status_request_ts) >= CYCLADES_STATUS_IDLE_TIMEOUT_SEC
            if pymssql and CYCLADES_DB_HOST and not idle:
                await asyncio.to_thread(_refresh_all_machine_status)
        except Exception:
            log.exception("Chyba v cyclades_status_refresh_loop.")
        await asyncio.sleep(CYCLADES_STATUS_REFRESH_INTERVAL_SEC)


@app.get("/api/machines/status")
def machines_status():
    """Cte VYHRADNE z cache naplnene _cyclades_status_refresh_loop -
    nikdy sama nevola Cyclades, takze odpoved je vzdy rychla i pro 20+
    stroju. Bezprostredne po startu (nez background loop poprve dobehne)
    vraci "neznamo"/prazdne stitky, dashboard se dotahne na dalsim pollu
    (frontend polluje kazdych 5 s).
    """
    global _last_status_request_ts
    _last_status_request_ts = time.time()

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT machine_code, machine_name, cyclades_mac_refmac FROM machines "
            "WHERE active ORDER BY machine_code"
        )
        machines = cur.fetchall()

    result = []
    for m in machines:
        status = (_status_cache.get(m["machine_code"]) or {}).get("data") or _unknown_status()
        labels = (_label_cache.get(m["machine_code"]) or {}).get("data") or {"last_label": None, "next_label": None}
        worst_cavity = (_worst_cavity_cache.get(m["machine_code"]) or {}).get("data")
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
            "worst_cavity_scrap": worst_cavity,
        })
    return result


# Popisne udaje o stroji (typ/tonaz, dilna, sekce) se meni jen vyjimecne -
# drzi se v lokalni Postgres tabulce `machines` (sloupce cyclades_label/
# type_label/atelier/section/info_synced_at, viz postgres/init/16_*.sql),
# synchronizovane na pozadi jednou za MACHINE_INFO_SYNC_INTERVAL_SEC. Endpoint
# nize cte jen lokalni DB, zadny primy dotaz do Cyclades v request path.
MACHINE_INFO_SYNC_INTERVAL_SEC = 6 * 3600


def _sync_machine_info_to_db():
    """Stahne popisne udaje o vsech strojich s namapovanym cyclades_mac_refmac
    JEDNIM batch dotazem (jedna konexe, IN (...)) a ulozi je do lokalni
    tabulky machines - misto opakovaneho tahani z produkcni Cyclades DB
    pri kazdem zobrazeni detailu stroje.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT machine_code, cyclades_mac_refmac FROM machines "
            "WHERE cyclades_mac_refmac IS NOT NULL"
        )
        machines = cur.fetchall()
    if not (machines and pymssql and CYCLADES_DB_HOST):
        return
    mac_refmacs = [m["cyclades_mac_refmac"] for m in machines]
    placeholders = ",".join(["%s"] * len(mac_refmacs))

    try:
        conn_ms = pymssql.connect(
            server=CYCLADES_DB_HOST, user=CYCLADES_DB_USER, password=CYCLADES_DB_PASSWORD,
            database="SUIVPRO", timeout=5, login_timeout=5,
        )
        try:
            cur_ms = conn_ms.cursor(as_dict=True)
            cur_ms.execute(
                "SELECT m.MAC_REFMAC, m.MAC_LIBMAC, m.ATEL_REFATEL, m.SEC_REFSEC, "
                "t.TYPESMAC_LIB0 AS type_label "
                "FROM MACHINE m "
                "LEFT JOIN TYPES_MACHINE t ON t.TYPESMAC_TYPE = m.MAC_TYPEMAC "
                f"WHERE m.MAC_REFMAC IN ({placeholders})",
                tuple(mac_refmacs),
            )
            info_by_mac = {r["MAC_REFMAC"]: r for r in cur_ms.fetchall()}
        finally:
            conn_ms.close()
    except pymssql.Error:
        log.warning("Batch sync popisnych udaju stroju z Cyclades selhal.")
        return

    now = datetime.now(timezone.utc)
    with get_conn() as conn, conn.cursor() as cur:
        for m in machines:
            info = info_by_mac.get(m["cyclades_mac_refmac"])
            cur.execute(
                "UPDATE machines SET cyclades_label=%s, type_label=%s, atelier=%s, section=%s, info_synced_at=%s "
                "WHERE machine_code=%s",
                (
                    info["MAC_LIBMAC"] if info else None,
                    info["type_label"] if info else None,
                    info["ATEL_REFATEL"] if info else None,
                    info["SEC_REFSEC"] if info else None,
                    now,
                    m["machine_code"],
                ),
            )
        conn.commit()


async def _machine_info_sync_loop():
    await asyncio.sleep(10)  # nech API doraznout, nez poprve sahne na Cyclades
    while True:
        try:
            if pymssql and CYCLADES_DB_HOST:
                await asyncio.to_thread(_sync_machine_info_to_db)
        except Exception:
            log.exception("Chyba v machine_info_sync_loop.")
        await asyncio.sleep(MACHINE_INFO_SYNC_INTERVAL_SEC)


@app.get("/api/machines/info")
def machine_info(machine: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT machine_code, machine_name, cyclades_mac_refmac, cyclades_label, "
            "type_label, atelier, section FROM machines WHERE machine_code=%s",
            (machine,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Neznamy stroj.")
    return row


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
    asyncio.create_task(_cyclades_status_refresh_loop())
    asyncio.create_task(_machine_info_sync_loop())


_cavity_scrap_cache = {}  # (machine_code, order_ref) -> {"data": [...], "ts": float}
CAVITY_SCRAP_CACHE_TTL_SEC = 15


def _query_cavity_scrap(order_ref):
    """Zmetkovitost po kavitach/produktech pro aktualni zakazku (OF).
    Kazda kavita vicekavitove formy ma v Cyclades vlastni PROD_REFPROD
    (viz komentar u /api/cycles/by-package - stitky bezi ve vice
    paralelnich radach pod jednou zakazkou, jedna rada na kavitu/produkt).

    Zdroj je LIGOF (radek OF x produkt) - Cyclades sam na nem drzi zive,
    kumulativni mnozstvi za CELOU zakazku (LIGOF_QTEBONNE/LIGOF_QTEREBUT),
    prubezne aktualizovane pri kazde smenove deklaraci. To je presnejsi a
    jednodussi nez pocitat z BILAN_SAISIE_EQUIPE (tam by bylo nutne brat jen
    nejnovejsi radek na produkt v ramci aktualni smeny, cimz by se ztratila
    historie z predchozich smen te same zakazky). LIGOF_TXTHEOREBUT je
    Cycladi vlastni cilova/teoreticka zmetkovitost pro srovnani.
    Neni vazano na machine_code/mac_refmac - OF_REFOF uz je dost specificky.
    """
    if not (pymssql and CYCLADES_DB_HOST and order_ref):
        return []
    rows = _query_cyclades(
        "SUIVPRO",
        "SELECT PROD_REFPROD, PROD_LIBPROD, LIGOF_RANGPRO, LIGOF_QTEBONNE, LIGOF_QTEREBUT, LIGOF_TXTHEOREBUT "
        "FROM LIGOF WHERE OF_REFOF=%s ORDER BY LIGOF_RANGPRO",
        (order_ref,),
    )
    cavities = []
    for r in rows:
        qty_good = float(r["LIGOF_QTEBONNE"] or 0)
        qty_reject = float(r["LIGOF_QTEREBUT"] or 0)
        qty_total = qty_good + qty_reject
        pct = (qty_reject / qty_total * 100) if qty_total > 0 else None
        cavities.append({
            "product": r["PROD_REFPROD"],
            "label": r["PROD_LIBPROD"],
            "cavity_no": r["LIGOF_RANGPRO"],
            "qty_good": qty_good,
            "qty_reject": qty_reject,
            "reject_pct": pct,
            "target_pct": float(r["LIGOF_TXTHEOREBUT"]) if r["LIGOF_TXTHEOREBUT"] is not None else None,
        })
    cavities.sort(key=lambda c: (c["reject_pct"] is None, -(c["reject_pct"] or 0)))
    return cavities


@app.get("/api/machines/cavity-scrap")
def machine_cavity_scrap(machine: str):
    """Zmetkovitost nejhorsi kavity aktualni zakazky na danem stroji -
    viz _query_cavity_scrap. Bez Cyclades pripojeni nebo bez aktivni
    zakazky vraci prazdny seznam (ne chybu), aby detail stroje bezel
    dal i pro stroje zatim bez teto integrace.
    """
    mac_refmac = _mac_refmac_for_machine_code(machine)
    status = get_machine_status(machine, mac_refmac)
    order_ref = status.get("order_ref")

    now = time.time()
    cache_key = (machine, order_ref)
    cached = _cavity_scrap_cache.get(cache_key)
    if cached and now - cached["ts"] < CAVITY_SCRAP_CACHE_TTL_SEC:
        cavities = cached["data"]
    else:
        cavities = _query_cavity_scrap(order_ref)
        if len(_cavity_scrap_cache) > 500:
            _cavity_scrap_cache.clear()
        _cavity_scrap_cache[cache_key] = {"data": cavities, "ts": now}

    worst = cavities[0] if cavities and cavities[0]["reject_pct"] is not None else None
    return {"order_ref": order_ref, "cavities": cavities, "worst": worst}


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
