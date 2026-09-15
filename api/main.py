"""
Euromap63 API — FastAPI backend nad TimescaleDB, poskytuje cyklova data
a zakladni statistiky pro dashboard, plus WebSocket push novych cyklu
a stav stroje (bezi/stoji) z Cyclades MES.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, date
from decimal import Decimal

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
            "SELECT param_name, param_type, param_unit, param_label FROM machine_parameters "
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
def list_cycles(machine: str, limit: int = Query(200, le=2000)):
    with get_conn() as conn, conn.cursor() as cur:
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


_status_cache = {}  # machine_code -> {"data": {...}, "ts": float}


def _query_cyclades_machine_status(mac_refmac):
    """Zjisti, jestli stroj bezi/stoji podle SUIVPRO.dbo.[OF] (base tabulka,
    ne view - live rolling okno otevrenych zakazek). Zivy stav "jede forma":
    radek s OF_DATEFINOF IS NULL = zakazka jeste bezi na stroji.
    OF_CAUSEARRETCOURANT = -2 a OF_DUREARRETCOURANT = 0 -> stroj aktualne jede,
    jinak stoji (duvod/kod prostoje neni dale rozlisovan na poruchu/serizovani -
    to by vyzadovalo mapovani pres TYPES_ARRETS, zatim neoverene).
    """
    if not (pymssql and CYCLADES_DB_HOST and mac_refmac):
        return {"state": "neznamo", "order_ref": None, "tool_ref": None}
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
                "SELECT TOP 1 OF_REFOF, OUT_REFOUT, OF_CAUSEARRETCOURANT, OF_DUREARRETCOURANT "
                "FROM [OF] WHERE MAC_REFMAC=%s AND OF_DATEFINOF IS NULL "
                "ORDER BY OF_DATELANCER DESC",
                (mac_refmac,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except pymssql.Error:
        log.warning("Cyclades dotaz na stav stroje %s selhal.", mac_refmac)
        return {"state": "neznamo", "order_ref": None, "tool_ref": None}

    if not row:
        return {"state": "bez_zakazky", "order_ref": None, "tool_ref": None}

    running = row["OF_CAUSEARRETCOURANT"] == -2 and (row["OF_DUREARRETCOURANT"] or 0) == 0
    return {
        "state": "bezi" if running else "stoji",
        "order_ref": row["OF_REFOF"],
        "tool_ref": row["OUT_REFOUT"],
        "stop_cause": row["OF_CAUSEARRETCOURANT"],
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
            **status,
            "latest_cycle": latest,
        })
    return result


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
