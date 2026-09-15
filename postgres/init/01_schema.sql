-- ============================================================
--  Euromap63 Platform — databázové schéma
--  TimescaleDB (PostgreSQL rozšíření pro časové řady)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ────────────────────────────────────────────────────────────
--  Stroje — číselník (pripraveno na budouci rozsireni o dalsi lisy)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS machines (
    machine_code    TEXT PRIMARY KEY,        -- napr. 'KM-MC5-01'
    machine_name    TEXT,
    ftp_root        TEXT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ────────────────────────────────────────────────────────────
--  Parametry, ktere dany stroj podporuje (z GETID.DAT) - informativni,
--  ruzne stroje maji ruzne HW konfigurace a tedy ruzne parametry.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS machine_parameters (
    machine_code    TEXT NOT NULL REFERENCES machines(machine_code),
    param_name      TEXT NOT NULL,
    param_type      TEXT,                    -- 'A' (alfa) / 'N' (numericky) dle GETID.DAT
    param_unit      TEXT,
    param_label     TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (machine_code, param_name)
);

-- ────────────────────────────────────────────────────────────
--  Cyklova data - jeden radek = jeden takt stroje.
--  cycle_count/cycle_time_s jsou spolecne vsem strojum (pro rychle
--  dotazy/indexy), vsechny ostatni parametry (ruzne stroj od stroje)
--  jdou do JSONB - zadna migrace schematu pri pridani dalsiho stroje.
-- ────────────────────────────────────────────────────────────
-- Zadny PRIMARY KEY na (machine_code, cycle_count) - TimescaleDB vyzaduje,
-- aby kazdy UNIQUE/PK obsahoval partitioning sloupec ("time"), a cyklum se
-- casova znacka prirazuje az pri vlozeni (DEFAULT now()), takze presna
-- deduplikace stejne resi collector (atomicka transakce + pocet uz
-- zpracovanych radku REPORTS.DAT, viz collector_state).
CREATE TABLE IF NOT EXISTS cycles (
    time            TIMESTAMPTZ NOT NULL DEFAULT now(),
    machine_code    TEXT NOT NULL REFERENCES machines(machine_code),
    cycle_count     BIGINT NOT NULL,
    cycle_time_s    DOUBLE PRECISION,
    params          JSONB NOT NULL
);

SELECT create_hypertable('cycles', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_cycles_machine_time ON cycles (machine_code, time DESC);
CREATE INDEX IF NOT EXISTS idx_cycles_machine_cycle ON cycles (machine_code, cycle_count);

-- ────────────────────────────────────────────────────────────
--  Stav collectoru - kolik radku z REPORTS.DAT uz bylo zpracovano,
--  jestli uz byl stroji poslan ABORT+EXECUTE REPORTS.JOB (aby se
--  po restartu kontejneru neposilal porad dokola).
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS collector_state (
    machine_code        TEXT PRIMARY KEY REFERENCES machines(machine_code),
    reports_lines_read  BIGINT NOT NULL DEFAULT 0,
    job_armed_at        TIMESTAMPTZ,
    last_poll_at        TIMESTAMPTZ
);
