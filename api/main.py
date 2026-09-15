"""
Euromap63 API — FastAPI backend nad TimescaleDB, poskytuje cyklova data
a zakladni statistiky pro dashboard.
"""
import os

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DATABASE_URL = os.environ["DATABASE_URL"]

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
