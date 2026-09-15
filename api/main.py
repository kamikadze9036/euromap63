"""
Euromap63 API — FastAPI backend nad TimescaleDB, poskytuje cyklova data
a zakladni statistiky pro dashboard.
"""
import os

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

try:
    import pymssql
except ImportError:
    pymssql = None

DATABASE_URL = os.environ["DATABASE_URL"]

CYCLADES_DB_HOST = os.environ.get("CYCLADES_DB_HOST", "")
CYCLADES_DB_USER = os.environ.get("CYCLADES_DB_USER", "")
CYCLADES_DB_PASSWORD = os.environ.get("CYCLADES_DB_PASSWORD", "")

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
            "SELECT machine_code, machine_name, active, created_at "
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
