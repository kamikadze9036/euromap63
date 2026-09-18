"""baseline: squash postgres/init/01_schema.sql .. 20_add_collector_heartbeat.sql

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-18

============================================================================
 WHAT THIS IS
============================================================================
This is a single baseline revision representing the FULL schema produced by
running postgres/init/01_schema.sql through postgres/init/20_add_collector_
heartbeat.sql, in order, against an empty database - which is exactly what
docker-entrypoint-initdb.d does today for a brand-new dev/local Postgres
container (see docker-compose.yml, `postgres` service, and
alembic/env.py for the fuller explanation of why both mechanisms exist).

Each block below is the verbatim SQL text of one postgres/init/NN_*.sql
file (comments included, unchanged), executed in the same order those files
are numbered, via `connection.exec_driver_sql()` rather than
`op.execute()`. That specific choice matters: exec_driver_sql() sends the
string straight to the DBAPI with no bind-parameter parsing, so the literal
backslashes (EUROMAP63 parameter names like
'@080Ext1TmpGear1\\CycDataTmpZone\\CycVal.PdeValue') and literal '%'
characters (unit strings like '%') in this SQL cannot be misinterpreted as
SQLAlchemy/psycopg2 bind placeholders - they are just bytes going straight
to Postgres, exactly as `psql -f` would send them.

This project has no SQLAlchemy ORM models (api/ and collector/ use raw
psycopg2), so there's no `metadata.create_all()`/autogenerate option here -
raw SQL is the correct, not merely convenient, choice for this baseline.
Where a *future* migration is a small, self-contained DDL change, using
Alembic's op.* DSL (op.add_column, op.create_index, etc.) is preferred for
readability - see 0002_add_cycles_table_comment_example.py. This baseline is
a one-time squash of already-existing SQL and is deliberately literal.

============================================================================
 DO NOT RUN `alembic upgrade head` AGAINST spc-vm PRODUCTION
============================================================================
spc-vm's database already has this exact schema (applied by hand via
`docker exec ... psql -f postgres/init/NN_*.sql`). Adopting Alembic there
means running `alembic stamp head` - NOT `alembic upgrade head`. See
docs/alembic_adoption.md for the full procedure. This is the single
highest-risk part of ticket 1.9 - read that file before touching spc-vm.

============================================================================
 VERIFICATION STATUS
============================================================================
This migration's equivalence to running postgres/init/01..20 in order on a
fresh database has NOT been verified against a real Postgres/TimescaleDB
instance in the sandbox this revision was written in (no Docker / working
psycopg2 available there). It has only been checked for Python/SQL syntax
validity. Before this is trusted for real use (including before running
`alembic stamp head` against spc-vm), spin up a throwaway TimescaleDB
container, run `alembic upgrade head` against it, and diff
`pg_dump --schema-only` (and machine_parameters/machines row counts) against
a second throwaway database bootstrapped the old way (docker-entrypoint-
initdb.d running postgres/init/*.sql). Tickets 1.1-1.4 and 1.8 already had
this kind of real-DB verification done for them elsewhere in this project's
history - this baseline still needs the same treatment.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# postgres/init/01_schema.sql
# ---------------------------------------------------------------------------
SQL_01_SCHEMA = r"""
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
"""

# ---------------------------------------------------------------------------
# postgres/init/02_seed_parameters.sql
# ---------------------------------------------------------------------------
SQL_02_SEED_PARAMETERS = r"""
INSERT INTO machines (machine_code, machine_name, ftp_root)
VALUES ('KM-MC5-01', 'Krauss Maffei MC5', '/ftpdata')
ON CONFLICT (machine_code) DO NOTHING;

INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', 'ActCntCyc',                 'N', '-',  'Počítadlo cyklů'),
    ('KM-MC5-01', 'ActTimCyc',                 'N', 's',  'Doba cyklu'),
    ('KM-MC5-01', 'ActTimFill[1]',             'N', 's',  'Doba vstřiku'),
    ('KM-MC5-01', 'ActTimPlst[1]',             'N', 's',  'Doba přepouštění (plastikace)'),
    ('KM-MC5-01', 'ActStrCsh[1]',              'N', 'mm', 'Polštář'),
    ('KM-MC5-01', '@010ModeCycle.CoolTimAct',  'N', 's',  'Doba chlazení'),
    ('KM-MC5-01', 'ActTimXfr[1]',              'N', 's',  'Doba dotlaku'),
    ('KM-MC5-01', 'ActStrXfr[1]',              'N', 'mm', 'Přepínací dráha dotlaku'),
    ('KM-MC5-01', 'ActStrPlst[1]',             'N', 'mm', 'Přepouštěcí zdvih (plastikace)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
"""

# ---------------------------------------------------------------------------
# postgres/init/03_seed_parameters_hardcopy.sql
# ---------------------------------------------------------------------------
SQL_03_SEED_PARAMETERS_HARDCOPY = r"""
INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', 'ActTmpBrlZn[1,3]',                                       'N', '°C',   'Zóna válce 3'),
    ('KM-MC5-01', 'ActTmpBrlZn[1,5]',                                       'N', '°C',   'Zóna válce 5'),
    ('KM-MC5-01', '@080Inj1MatResT1\CycDataTmpZone\CycVal.PdeValue',        'N', '°C',   'Zóna zásobníku materiálu 1'),
    ('KM-MC5-01', '@080Inj1MatResT2\CycDataTmpZone\CycVal.PdeValue',        'N', '°C',   'Zóna zásobníku materiálu 2'),
    ('KM-MC5-01', '@080Inj1MatResT3\CycDataTmpZone\CycVal.PdeValue',        'N', '°C',   'Zóna zásobníku materiálu 3'),
    ('KM-MC5-01', '@080Inj1MatResT7\CycDataTmpZone\CycVal.PdeValue',        'N', '°C',   'Zóna zásobníku materiálu 7'),
    ('KM-MC5-01', '@080Inj1MatResT10\CycDataTmpZone\CycVal.PdeValue',       'N', '°C',   'Zóna zásobníku materiálu 10'),
    ('KM-MC5-01', '@080CycGraviWeightNet1\CycVal.PdeValue',                 'N', 'kg',   'Čistá hmotnost dávkovače 1'),
    ('KM-MC5-01', '@080CycGraviWeightNet2\CycVal.PdeValue',                 'N', 'kg',   'Čistá hmotnost dávkovače 2'),
    ('KM-MC5-01', '@080CycGraviFeedFactor2\CycVal.PdeValue',                'N', 'kg/h', 'Aktuální dávkovací faktor 2'),
    ('KM-MC5-01', '@010CycDataMld\CycCfbTim\CycVal.PdeValue',               'N', 's',    'Doba náběhu uzavírací síly'),
    ('KM-MC5-01', '@010CycDataMld\CycClpOpnTim\CycVal.PdeValue',            'N', 's',    'Doba otvírání nástroje'),
    ('KM-MC5-01', '@010CycDataOth\CycBreakTim\CycVal.PdeValue',             'N', 's',    'Doba pauzy')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
"""

# ---------------------------------------------------------------------------
# postgres/init/04_seed_parameters_barrel_extruder_hoppers.sql
# ---------------------------------------------------------------------------
SQL_04_SEED_PARAMETERS_BARREL_EXTRUDER_HOPPERS = r"""
INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    -- Vstrikovaci valec - mereno (zony 3 a 5 uz existuji z drivejsiho seedu)
    ('KM-MC5-01', 'ActTmpBrlZn[1,1]',  'N', '°C', 'Zóna vstřikovacího válce 1 (měřeno)'),
    ('KM-MC5-01', 'ActTmpBrlZn[1,2]',  'N', '°C', 'Zóna vstřikovacího válce 2 (měřeno)'),
    ('KM-MC5-01', 'ActTmpBrlZn[1,4]',  'N', '°C', 'Zóna vstřikovacího válce 4 (měřeno)'),
    ('KM-MC5-01', 'ActTmpBrlZn[1,11]', 'N', '°C', 'Zóna vstřikovacího válce 11 (měřeno)'),
    ('KM-MC5-01', 'ActTmpBrlZn[1,12]', 'N', '°C', 'Zóna vstřikovacího válce 12 (měřeno)'),
    -- Vstrikovaci valec - zadano (vsech 7 zon)
    ('KM-MC5-01', 'SetTmpBrlZn[1,1]',  'N', '°C', 'Zóna vstřikovacího válce 1 (zadáno)'),
    ('KM-MC5-01', 'SetTmpBrlZn[1,2]',  'N', '°C', 'Zóna vstřikovacího válce 2 (zadáno)'),
    ('KM-MC5-01', 'SetTmpBrlZn[1,3]',  'N', '°C', 'Zóna vstřikovacího válce 3 (zadáno)'),
    ('KM-MC5-01', 'SetTmpBrlZn[1,4]',  'N', '°C', 'Zóna vstřikovacího válce 4 (zadáno)'),
    ('KM-MC5-01', 'SetTmpBrlZn[1,5]',  'N', '°C', 'Zóna vstřikovacího válce 5 (zadáno)'),
    ('KM-MC5-01', 'SetTmpBrlZn[1,11]', 'N', '°C', 'Zóna vstřikovacího válce 11 (zadáno)'),
    ('KM-MC5-01', 'SetTmpBrlZn[1,12]', 'N', '°C', 'Zóna vstřikovacího válce 12 (zadáno)'),

    -- Extruder - mereno + zadano (10 zon)
    ('KM-MC5-01', '@080Ext1TmpGear1\CycDataTmpZone\CycVal.PdeValue', 'N', '°C', 'Převodovka extrudéru (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpGear1.TmpSet',                          'N', '°C', 'Převodovka extrudéru (zadáno)'),
    ('KM-MC5-01', '@080Ext1TmpBush1\CycDataTmpZone\CycVal.PdeValue', 'N', '°C', 'Vtahovací zóna extrudéru (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBush1.TmpSet',                          'N', '°C', 'Vtahovací zóna extrudéru (zadáno)'),
    ('KM-MC5-01', '@080Ext1TmpBar1\CycDataTmpZone\CycVal.PdeValue',  'N', '°C', 'Válcová zóna extrudéru 1 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar1.TmpSet',                           'N', '°C', 'Válcová zóna extrudéru 1 (zadáno)'),
    ('KM-MC5-01', '@080Ext1TmpBar2\CycDataTmpZone\CycVal.PdeValue',  'N', '°C', 'Válcová zóna extrudéru 2 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar2.TmpSet',                           'N', '°C', 'Válcová zóna extrudéru 2 (zadáno)'),
    ('KM-MC5-01', '@080Ext1TmpBar3\CycDataTmpZone\CycVal.PdeValue',  'N', '°C', 'Válcová zóna extrudéru 3 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar3.TmpSet',                           'N', '°C', 'Válcová zóna extrudéru 3 (zadáno)'),
    ('KM-MC5-01', '@080Ext1TmpBar4\CycDataTmpZone\CycVal.PdeValue',  'N', '°C', 'Válcová zóna extrudéru 4 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar4.TmpSet',                           'N', '°C', 'Válcová zóna extrudéru 4 (zadáno)'),
    ('KM-MC5-01', '@080Ext1TmpBar5\CycDataTmpZone\CycVal.PdeValue',  'N', '°C', 'Válcová zóna extrudéru 5 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar5.TmpSet',                           'N', '°C', 'Válcová zóna extrudéru 5 (zadáno)'),
    ('KM-MC5-01', '@080Ext1TmpBar6\CycDataTmpZone\CycVal.PdeValue',  'N', '°C', 'Válcová zóna extrudéru 6 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar6.TmpSet',                           'N', '°C', 'Válcová zóna extrudéru 6 (zadáno)'),
    ('KM-MC5-01', '@080Ext1TmpBar7\CycDataTmpZone\CycVal.PdeValue',  'N', '°C', 'Válcová zóna extrudéru 7 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar7.TmpSet',                           'N', '°C', 'Válcová zóna extrudéru 7 (zadáno)'),
    ('KM-MC5-01', '@080Ext1TmpMelt1\CycDataTmpZone\CycVal.PdeValue', 'N', '°C', 'Teplota hmoty v extrudéru (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpMelt1.TmpSet',                          'N', '°C', 'Teplota hmoty v extrudéru (zadáno)'),

    -- Nasypky/davkovace 1-4 - realna hmotnost (3 a 4 nove, 1 a 2 uz existuji)
    ('KM-MC5-01', '@080CycGraviWeightNet3\CycVal.PdeValue', 'N', 'kg', 'Čistá hmotnost dávkovače 3 (měřeno)'),
    ('KM-MC5-01', '@080CycGraviWeightNet4\CycVal.PdeValue', 'N', 'kg', 'Čistá hmotnost dávkovače 4 (měřeno)'),
    -- Nasypky/davkovace 1-4 - zadana (referencni) hmotnost
    ('KM-MC5-01', '@080CycGraviWeightNet1\PDESpecial0.PDESpecialBase', 'N', 'kg', 'Čistá hmotnost dávkovače 1 (zadáno)'),
    ('KM-MC5-01', '@080CycGraviWeightNet2\PDESpecial0.PDESpecialBase', 'N', 'kg', 'Čistá hmotnost dávkovače 2 (zadáno)'),
    ('KM-MC5-01', '@080CycGraviWeightNet3\PDESpecial0.PDESpecialBase', 'N', 'kg', 'Čistá hmotnost dávkovače 3 (zadáno)'),
    ('KM-MC5-01', '@080CycGraviWeightNet4\PDESpecial0.PDESpecialBase', 'N', 'kg', 'Čistá hmotnost dávkovače 4 (zadáno)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();

-- Doplneni "(mereno)" do labelu jiz existujicich zon 3 a 5, aby byl
-- konzistentni s novymi (predtim mely jen holy popis bez "(mereno)").
UPDATE machine_parameters SET param_label = 'Zóna vstřikovacího válce 3 (měřeno)'
    WHERE machine_code = 'KM-MC5-01' AND param_name = 'ActTmpBrlZn[1,3]';
UPDATE machine_parameters SET param_label = 'Zóna vstřikovacího válce 5 (měřeno)'
    WHERE machine_code = 'KM-MC5-01' AND param_name = 'ActTmpBrlZn[1,5]';
UPDATE machine_parameters SET param_label = 'Čistá hmotnost dávkovače 1 (měřeno)'
    WHERE machine_code = 'KM-MC5-01' AND param_name = '@080CycGraviWeightNet1\CycVal.PdeValue';
UPDATE machine_parameters SET param_label = 'Čistá hmotnost dávkovače 2 (měřeno)'
    WHERE machine_code = 'KM-MC5-01' AND param_name = '@080CycGraviWeightNet2\CycVal.PdeValue';
"""

# ---------------------------------------------------------------------------
# postgres/init/05_seed_parameters_tmpact_fix.sql
# ---------------------------------------------------------------------------
SQL_05_SEED_PARAMETERS_TMPACT_FIX = r"""
INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', '@020Inj1T1.TmpAct',  'N', '°C', 'Zóna vstřikovacího válce 1 (měřeno)'),
    ('KM-MC5-01', '@020Inj1T2.TmpAct',  'N', '°C', 'Zóna vstřikovacího válce 2 (měřeno)'),
    ('KM-MC5-01', '@020Inj1T3.TmpAct',  'N', '°C', 'Zóna vstřikovacího válce 3 (měřeno)'),
    ('KM-MC5-01', '@020Inj1T4.TmpAct',  'N', '°C', 'Zóna vstřikovacího válce 4 (měřeno)'),
    ('KM-MC5-01', '@020Inj1T5.TmpAct',  'N', '°C', 'Zóna vstřikovacího válce 5 (měřeno)'),
    ('KM-MC5-01', '@020Inj1T11.TmpAct', 'N', '°C', 'Zóna vstřikovacího válce 11 (měřeno)'),
    ('KM-MC5-01', '@020Inj1T12.TmpAct', 'N', '°C', 'Zóna vstřikovacího válce 12 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar1.TmpAct', 'N', '°C', 'Válcová zóna extrudéru 1 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar2.TmpAct', 'N', '°C', 'Válcová zóna extrudéru 2 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar3.TmpAct', 'N', '°C', 'Válcová zóna extrudéru 3 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar4.TmpAct', 'N', '°C', 'Válcová zóna extrudéru 4 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar5.TmpAct', 'N', '°C', 'Válcová zóna extrudéru 5 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar6.TmpAct', 'N', '°C', 'Válcová zóna extrudéru 6 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpBar7.TmpAct', 'N', '°C', 'Válcová zóna extrudéru 7 (měřeno)'),
    ('KM-MC5-01', '@080Ext1TmpMelt1.TmpAct', 'N', '°C', 'Teplota hmoty v extrudéru (měřeno)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
"""

# ---------------------------------------------------------------------------
# postgres/init/06_add_order_ref.sql
# ---------------------------------------------------------------------------
SQL_06_ADD_ORDER_REF = r"""
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS order_ref TEXT;
CREATE INDEX IF NOT EXISTS idx_cycles_order_ref ON cycles (order_ref);

ALTER TABLE machines ADD COLUMN IF NOT EXISTS cyclades_mac_refmac TEXT;
UPDATE machines SET cyclades_mac_refmac = 'P2700-01' WHERE machine_code = 'KM-MC5-01';
"""

# ---------------------------------------------------------------------------
# postgres/init/07_seed_parameters_pressures_oil.sql
# ---------------------------------------------------------------------------
SQL_07_SEED_PARAMETERS_PRESSURES_OIL = r"""
INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', 'ActTmpOil',            'N', '°C',  'Teplota oleje'),
    ('KM-MC5-01', 'ActPrsXfrSpec[1]',     'N', 'bar', 'Přepínací tlak hmoty (dotlak)'),
    ('KM-MC5-01', 'ActPrsHldSpecMax[1]',  'N', 'bar', 'Max. tlak hmoty (za takt)'),
    ('KM-MC5-01', 'ActPrsMachSpecMax',    'N', 'bar', 'Max. tlak hmoty (celkový)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
"""

# ---------------------------------------------------------------------------
# postgres/init/08_seed_parameters_dosing_percent.sql
# ---------------------------------------------------------------------------
SQL_08_SEED_PARAMETERS_DOSING_PERCENT = r"""
INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', '@080ETGraviDos2.MassPerc',       'N', '%', 'Podíl hmotnostního průtoku – dávkovač 2'),
    ('KM-MC5-01', '@080ETGraviPseudoDos.MassPerc',  'N', '%', 'Podíl hmotnostního průtoku – dávkovač 8 (pseudo)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
"""

# ---------------------------------------------------------------------------
# postgres/init/09_seed_parameters_glass_fiber_roving.sql
# ---------------------------------------------------------------------------
SQL_09_SEED_PARAMETERS_GLASS_FIBER_ROVING = r"""
INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', '@080CycRovMonit\CycVal.PdeValue',          'N', 'poč.', 'Počet pramenů skl. vlákna (hodnota cyklu)'),
    ('KM-MC5-01', '@080CycRovMonit\PDESpecial0.PDESpecialBase','N', 'poč.', 'Počet pramenů skl. vlákna (referenční)'),
    ('KM-MC5-01', '@080ETRovMonit.SetNumber',                  'N', 'poč.', 'Počet pramenů skl. vlákna (nastaveno)'),
    ('KM-MC5-01', '@080ETRovMonit.MinNumber',                  'N', 'poč.', 'Počet pramenů skl. vlákna (min.)'),
    ('KM-MC5-01', '@080ETRovMonit.MaxNumber',                  'N', 'poč.', 'Počet pramenů skl. vlákna (max.)'),
    ('KM-MC5-01', '@080ETRovMonit.ActNumber',                  'N', 'poč.', 'Počet pramenů skl. vlákna (živá hodnota)'),
    ('KM-MC5-01', '@080ETRovCtrl1.RovTEX',                     'N', 'g/km', 'Jemnost vlákna (TEX)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
"""

# ---------------------------------------------------------------------------
# postgres/init/10_seed_parameters_dosing_all_and_material_names.sql
# ---------------------------------------------------------------------------
SQL_10_SEED_PARAMETERS_DOSING_ALL_AND_MATERIAL_NAMES = r"""
INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', '@080ETGraviDos1.MassPerc', 'N', '%', 'Podíl hmotnostního průtoku – dávkovač 1'),
    ('KM-MC5-01', '@080ETGraviDos3.MassPerc', 'N', '%', 'Podíl hmotnostního průtoku – dávkovač 3'),
    ('KM-MC5-01', '@080ETGraviDos4.MassPerc', 'N', '%', 'Podíl hmotnostního průtoku – dávkovač 4'),
    ('KM-MC5-01', '@080ETDos1Name.Data',      'A', '-', 'Název materiálu – dávkovač 1'),
    ('KM-MC5-01', '@080ETDos2Name.Data',      'A', '-', 'Název materiálu – dávkovač 2'),
    ('KM-MC5-01', '@080ETDos3Name.Data',      'A', '-', 'Název materiálu – dávkovač 3'),
    ('KM-MC5-01', '@080ETDos4Name.Data',      'A', '-', 'Název materiálu – dávkovač 4')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
"""

# ---------------------------------------------------------------------------
# postgres/init/11_seed_parameters_cyclic_dosing_percent_and_rename.sql
# ---------------------------------------------------------------------------
SQL_11_SEED_PARAMETERS_CYCLIC_DOSING_PERCENT_AND_RENAME = r"""
UPDATE machine_parameters
   SET param_label = 'Podíl skelného vlákna (dávkovač 8, pseudo)',
       updated_at = now()
 WHERE machine_code = 'KM-MC5-01'
   AND param_name = '@080ETGraviPseudoDos.MassPerc';

INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', '@080CycGraviMassFlowPerc1\CycVal.PdeValue',           'N', '%', 'Podíl hmot. průtoku – dávkovač 1 (hodnota cyklu)'),
    ('KM-MC5-01', '@080CycGraviMassFlowPerc2\CycVal.PdeValue',           'N', '%', 'Podíl hmot. průtoku – dávkovač 2 (hodnota cyklu)'),
    ('KM-MC5-01', '@080CycGraviMassFlowPerc3\CycVal.PdeValue',           'N', '%', 'Podíl hmot. průtoku – dávkovač 3 (hodnota cyklu)'),
    ('KM-MC5-01', '@080CycGraviMassFlowPerc4\CycVal.PdeValue',           'N', '%', 'Podíl hmot. průtoku – dávkovač 4 (hodnota cyklu)'),
    ('KM-MC5-01', '@080CycGraviMassFlowPerc8\CycVal.PdeValue',           'N', '%', 'Podíl skelného vlákna (hodnota cyklu)'),
    ('KM-MC5-01', '@080CycGraviMassFlowPerc1\PDESpecial0.PDESpecialBase','N', '%', 'Podíl hmot. průtoku – dávkovač 1 (referenční)'),
    ('KM-MC5-01', '@080CycGraviMassFlowPerc2\PDESpecial0.PDESpecialBase','N', '%', 'Podíl hmot. průtoku – dávkovač 2 (referenční)'),
    ('KM-MC5-01', '@080CycGraviMassFlowPerc3\PDESpecial0.PDESpecialBase','N', '%', 'Podíl hmot. průtoku – dávkovač 3 (referenční)'),
    ('KM-MC5-01', '@080CycGraviMassFlowPerc4\PDESpecial0.PDESpecialBase','N', '%', 'Podíl hmot. průtoku – dávkovač 4 (referenční)'),
    ('KM-MC5-01', '@080CycGraviMassFlowPerc8\PDESpecial0.PDESpecialBase','N', '%', 'Podíl skelného vlákna (referenční)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
"""

# ---------------------------------------------------------------------------
# postgres/init/12_seed_parameters_mold_heating_zones.sql
# ---------------------------------------------------------------------------
SQL_12_SEED_PARAMETERS_MOLD_HEATING_ZONES = r"""
INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', '@021MldHtg1T1.TmpSet', 'N', '°C', 'Zóna nástroje 1 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T1.TmpAct', 'N', '°C', 'Zóna nástroje 1 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T2.TmpSet', 'N', '°C', 'Zóna nástroje 2 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T2.TmpAct', 'N', '°C', 'Zóna nástroje 2 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T3.TmpSet', 'N', '°C', 'Zóna nástroje 3 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T3.TmpAct', 'N', '°C', 'Zóna nástroje 3 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T4.TmpSet', 'N', '°C', 'Zóna nástroje 4 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T4.TmpAct', 'N', '°C', 'Zóna nástroje 4 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T5.TmpSet', 'N', '°C', 'Zóna nástroje 5 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T5.TmpAct', 'N', '°C', 'Zóna nástroje 5 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T6.TmpSet', 'N', '°C', 'Zóna nástroje 6 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T6.TmpAct', 'N', '°C', 'Zóna nástroje 6 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T7.TmpSet', 'N', '°C', 'Zóna nástroje 7 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T7.TmpAct', 'N', '°C', 'Zóna nástroje 7 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T8.TmpSet', 'N', '°C', 'Zóna nástroje 8 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T8.TmpAct', 'N', '°C', 'Zóna nástroje 8 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T9.TmpSet', 'N', '°C', 'Zóna nástroje 9 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T9.TmpAct', 'N', '°C', 'Zóna nástroje 9 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T10.TmpSet', 'N', '°C', 'Zóna nástroje 10 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T10.TmpAct', 'N', '°C', 'Zóna nástroje 10 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T11.TmpSet', 'N', '°C', 'Zóna nástroje 11 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T11.TmpAct', 'N', '°C', 'Zóna nástroje 11 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T12.TmpSet', 'N', '°C', 'Zóna nástroje 12 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T12.TmpAct', 'N', '°C', 'Zóna nástroje 12 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T13.TmpSet', 'N', '°C', 'Zóna nástroje 13 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T13.TmpAct', 'N', '°C', 'Zóna nástroje 13 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T14.TmpSet', 'N', '°C', 'Zóna nástroje 14 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T14.TmpAct', 'N', '°C', 'Zóna nástroje 14 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T15.TmpSet', 'N', '°C', 'Zóna nástroje 15 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T15.TmpAct', 'N', '°C', 'Zóna nástroje 15 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T16.TmpSet', 'N', '°C', 'Zóna nástroje 16 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T16.TmpAct', 'N', '°C', 'Zóna nástroje 16 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T17.TmpSet', 'N', '°C', 'Zóna nástroje 17 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T17.TmpAct', 'N', '°C', 'Zóna nástroje 17 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T18.TmpSet', 'N', '°C', 'Zóna nástroje 18 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T18.TmpAct', 'N', '°C', 'Zóna nástroje 18 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T19.TmpSet', 'N', '°C', 'Zóna nástroje 19 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T19.TmpAct', 'N', '°C', 'Zóna nástroje 19 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T20.TmpSet', 'N', '°C', 'Zóna nástroje 20 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T20.TmpAct', 'N', '°C', 'Zóna nástroje 20 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T21.TmpSet', 'N', '°C', 'Zóna nástroje 21 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T21.TmpAct', 'N', '°C', 'Zóna nástroje 21 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T22.TmpSet', 'N', '°C', 'Zóna nástroje 22 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T22.TmpAct', 'N', '°C', 'Zóna nástroje 22 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T23.TmpSet', 'N', '°C', 'Zóna nástroje 23 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T23.TmpAct', 'N', '°C', 'Zóna nástroje 23 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T24.TmpSet', 'N', '°C', 'Zóna nástroje 24 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T24.TmpAct', 'N', '°C', 'Zóna nástroje 24 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T25.TmpSet', 'N', '°C', 'Zóna nástroje 25 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T25.TmpAct', 'N', '°C', 'Zóna nástroje 25 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T26.TmpSet', 'N', '°C', 'Zóna nástroje 26 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T26.TmpAct', 'N', '°C', 'Zóna nástroje 26 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T27.TmpSet', 'N', '°C', 'Zóna nástroje 27 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T27.TmpAct', 'N', '°C', 'Zóna nástroje 27 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T28.TmpSet', 'N', '°C', 'Zóna nástroje 28 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T28.TmpAct', 'N', '°C', 'Zóna nástroje 28 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T29.TmpSet', 'N', '°C', 'Zóna nástroje 29 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T29.TmpAct', 'N', '°C', 'Zóna nástroje 29 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T30.TmpSet', 'N', '°C', 'Zóna nástroje 30 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T30.TmpAct', 'N', '°C', 'Zóna nástroje 30 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T31.TmpSet', 'N', '°C', 'Zóna nástroje 31 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T31.TmpAct', 'N', '°C', 'Zóna nástroje 31 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T32.TmpSet', 'N', '°C', 'Zóna nástroje 32 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T32.TmpAct', 'N', '°C', 'Zóna nástroje 32 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T33.TmpSet', 'N', '°C', 'Zóna nástroje 33 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T33.TmpAct', 'N', '°C', 'Zóna nástroje 33 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T34.TmpSet', 'N', '°C', 'Zóna nástroje 34 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T34.TmpAct', 'N', '°C', 'Zóna nástroje 34 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T35.TmpSet', 'N', '°C', 'Zóna nástroje 35 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T35.TmpAct', 'N', '°C', 'Zóna nástroje 35 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T36.TmpSet', 'N', '°C', 'Zóna nástroje 36 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T36.TmpAct', 'N', '°C', 'Zóna nástroje 36 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T37.TmpSet', 'N', '°C', 'Zóna nástroje 37 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T37.TmpAct', 'N', '°C', 'Zóna nástroje 37 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T38.TmpSet', 'N', '°C', 'Zóna nástroje 38 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T38.TmpAct', 'N', '°C', 'Zóna nástroje 38 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T39.TmpSet', 'N', '°C', 'Zóna nástroje 39 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T39.TmpAct', 'N', '°C', 'Zóna nástroje 39 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T40.TmpSet', 'N', '°C', 'Zóna nástroje 40 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T40.TmpAct', 'N', '°C', 'Zóna nástroje 40 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T41.TmpSet', 'N', '°C', 'Zóna nástroje 41 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T41.TmpAct', 'N', '°C', 'Zóna nástroje 41 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T42.TmpSet', 'N', '°C', 'Zóna nástroje 42 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T42.TmpAct', 'N', '°C', 'Zóna nástroje 42 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T43.TmpSet', 'N', '°C', 'Zóna nástroje 43 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T43.TmpAct', 'N', '°C', 'Zóna nástroje 43 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T44.TmpSet', 'N', '°C', 'Zóna nástroje 44 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T44.TmpAct', 'N', '°C', 'Zóna nástroje 44 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T45.TmpSet', 'N', '°C', 'Zóna nástroje 45 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T45.TmpAct', 'N', '°C', 'Zóna nástroje 45 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T46.TmpSet', 'N', '°C', 'Zóna nástroje 46 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T46.TmpAct', 'N', '°C', 'Zóna nástroje 46 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T47.TmpSet', 'N', '°C', 'Zóna nástroje 47 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T47.TmpAct', 'N', '°C', 'Zóna nástroje 47 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T48.TmpSet', 'N', '°C', 'Zóna nástroje 48 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T48.TmpAct', 'N', '°C', 'Zóna nástroje 48 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T49.TmpSet', 'N', '°C', 'Zóna nástroje 49 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T49.TmpAct', 'N', '°C', 'Zóna nástroje 49 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T50.TmpSet', 'N', '°C', 'Zóna nástroje 50 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T50.TmpAct', 'N', '°C', 'Zóna nástroje 50 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T51.TmpSet', 'N', '°C', 'Zóna nástroje 51 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T51.TmpAct', 'N', '°C', 'Zóna nástroje 51 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T52.TmpSet', 'N', '°C', 'Zóna nástroje 52 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T52.TmpAct', 'N', '°C', 'Zóna nástroje 52 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T53.TmpSet', 'N', '°C', 'Zóna nástroje 53 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T53.TmpAct', 'N', '°C', 'Zóna nástroje 53 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T54.TmpSet', 'N', '°C', 'Zóna nástroje 54 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T54.TmpAct', 'N', '°C', 'Zóna nástroje 54 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T55.TmpSet', 'N', '°C', 'Zóna nástroje 55 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T55.TmpAct', 'N', '°C', 'Zóna nástroje 55 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T56.TmpSet', 'N', '°C', 'Zóna nástroje 56 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T56.TmpAct', 'N', '°C', 'Zóna nástroje 56 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T57.TmpSet', 'N', '°C', 'Zóna nástroje 57 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T57.TmpAct', 'N', '°C', 'Zóna nástroje 57 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T58.TmpSet', 'N', '°C', 'Zóna nástroje 58 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T58.TmpAct', 'N', '°C', 'Zóna nástroje 58 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T59.TmpSet', 'N', '°C', 'Zóna nástroje 59 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T59.TmpAct', 'N', '°C', 'Zóna nástroje 59 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T60.TmpSet', 'N', '°C', 'Zóna nástroje 60 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T60.TmpAct', 'N', '°C', 'Zóna nástroje 60 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T61.TmpSet', 'N', '°C', 'Zóna nástroje 61 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T61.TmpAct', 'N', '°C', 'Zóna nástroje 61 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T62.TmpSet', 'N', '°C', 'Zóna nástroje 62 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T62.TmpAct', 'N', '°C', 'Zóna nástroje 62 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T63.TmpSet', 'N', '°C', 'Zóna nástroje 63 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T63.TmpAct', 'N', '°C', 'Zóna nástroje 63 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T64.TmpSet', 'N', '°C', 'Zóna nástroje 64 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T64.TmpAct', 'N', '°C', 'Zóna nástroje 64 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T65.TmpSet', 'N', '°C', 'Zóna nástroje 65 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T65.TmpAct', 'N', '°C', 'Zóna nástroje 65 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T66.TmpSet', 'N', '°C', 'Zóna nástroje 66 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T66.TmpAct', 'N', '°C', 'Zóna nástroje 66 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T67.TmpSet', 'N', '°C', 'Zóna nástroje 67 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T67.TmpAct', 'N', '°C', 'Zóna nástroje 67 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T68.TmpSet', 'N', '°C', 'Zóna nástroje 68 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T68.TmpAct', 'N', '°C', 'Zóna nástroje 68 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T69.TmpSet', 'N', '°C', 'Zóna nástroje 69 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T69.TmpAct', 'N', '°C', 'Zóna nástroje 69 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T70.TmpSet', 'N', '°C', 'Zóna nástroje 70 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T70.TmpAct', 'N', '°C', 'Zóna nástroje 70 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T71.TmpSet', 'N', '°C', 'Zóna nástroje 71 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T71.TmpAct', 'N', '°C', 'Zóna nástroje 71 (měřeno)'),
    ('KM-MC5-01', '@021MldHtg1T72.TmpSet', 'N', '°C', 'Zóna nástroje 72 (zadáno)'),
    ('KM-MC5-01', '@021MldHtg1T72.TmpAct', 'N', '°C', 'Zóna nástroje 72 (měřeno)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
"""

# ---------------------------------------------------------------------------
# postgres/init/13_add_param_category.sql
# (superseded in part by 14_fix_param_category_backslash_escaping.sql, which
#  runs right after it below - both are replayed, in order, exactly as they
#  were originally applied, so the end result matches production bit-for-bit)
# ---------------------------------------------------------------------------
SQL_13_ADD_PARAM_CATEGORY = r"""
ALTER TABLE machine_parameters ADD COLUMN IF NOT EXISTS param_category TEXT;

UPDATE machine_parameters SET param_category = 'Časování cyklu'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('ActTimCyc', 'ActTimFill[1]', 'ActTimPlst[1]', 'ActStrCsh[1]', '@010ModeCycle.CoolTimAct', 'ActTimXfr[1]', 'ActStrXfr[1]', 'ActStrPlst[1]', '@010CycDataMld\CycCfbTim\CycVal.PdeValue', '@010CycDataMld\CycClpOpnTim\CycVal.PdeValue', '@010CycDataOth\CycBreakTim\CycVal.PdeValue');

UPDATE machine_parameters SET param_category = 'Teploty vstřikovacího válce'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@020Inj1T1.TmpAct', '@020Inj1T2.TmpAct', '@020Inj1T3.TmpAct', '@020Inj1T4.TmpAct', '@020Inj1T5.TmpAct', '@020Inj1T11.TmpAct', '@020Inj1T12.TmpAct');

UPDATE machine_parameters SET param_category = 'Teploty extrudéru'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@080Ext1TmpGear1\CycDataTmpZone\CycVal.PdeValue', '@080Ext1TmpBush1\CycDataTmpZone\CycVal.PdeValue', '@080Ext1TmpBar1.TmpAct', '@080Ext1TmpBar2.TmpAct', '@080Ext1TmpBar3.TmpAct', '@080Ext1TmpBar4.TmpAct', '@080Ext1TmpBar5.TmpAct', '@080Ext1TmpBar6.TmpAct', '@080Ext1TmpBar7.TmpAct', '@080Ext1TmpMelt1.TmpSet');

UPDATE machine_parameters SET param_category = 'Teploty zásobníku materiálu'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@080Inj1MatResT1\CycDataTmpZone\CycVal.PdeValue', '@080Inj1MatResT2\CycDataTmpZone\CycVal.PdeValue', '@080Inj1MatResT3\CycDataTmpZone\CycVal.PdeValue', '@080Inj1MatResT7\CycDataTmpZone\CycVal.PdeValue', '@080Inj1MatResT10\CycDataTmpZone\CycVal.PdeValue');

UPDATE machine_parameters SET param_category = 'Tlaky a olej'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('ActTmpOil', 'ActPrsXfrSpec[1]', 'ActPrsHldSpecMax[1]', 'ActPrsMachSpecMax');

UPDATE machine_parameters SET param_category = 'Dávkování — hmotnost'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@080CycGraviWeightNet1\CycVal.PdeValue', '@080CycGraviWeightNet2\CycVal.PdeValue', '@080CycGraviWeightNet3\CycVal.PdeValue', '@080CycGraviWeightNet4\CycVal.PdeValue');

UPDATE machine_parameters SET param_category = 'Dávkování — složení (%)'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@080ETGraviDos1.MassPerc', '@080ETGraviDos2.MassPerc', '@080ETGraviDos3.MassPerc', '@080ETGraviDos4.MassPerc', '@080ETGraviPseudoDos.MassPerc');

UPDATE machine_parameters SET param_category = 'Skelné vlákno'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@080CycRovMonit\CycVal.PdeValue', '@080ETRovMonit.ActNumber');
"""

# ---------------------------------------------------------------------------
# postgres/init/14_fix_param_category_backslash_escaping.sql
# ---------------------------------------------------------------------------
SQL_14_FIX_PARAM_CATEGORY_BACKSLASH_ESCAPING = r"""
UPDATE machine_parameters SET param_category = 'Časování cyklu'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('ActTimCyc', 'ActTimFill[1]', 'ActTimPlst[1]', 'ActStrCsh[1]', '@010ModeCycle.CoolTimAct', 'ActTimXfr[1]', 'ActStrXfr[1]', 'ActStrPlst[1]', '@010CycDataMld\CycCfbTim\CycVal.PdeValue', '@010CycDataMld\CycClpOpnTim\CycVal.PdeValue', '@010CycDataOth\CycBreakTim\CycVal.PdeValue');

UPDATE machine_parameters SET param_category = 'Teploty extrudéru'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@080Ext1TmpGear1\CycDataTmpZone\CycVal.PdeValue', '@080Ext1TmpBush1\CycDataTmpZone\CycVal.PdeValue');

UPDATE machine_parameters SET param_category = 'Teploty zásobníku materiálu'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@080Inj1MatResT1\CycDataTmpZone\CycVal.PdeValue', '@080Inj1MatResT2\CycDataTmpZone\CycVal.PdeValue', '@080Inj1MatResT3\CycDataTmpZone\CycVal.PdeValue', '@080Inj1MatResT7\CycDataTmpZone\CycVal.PdeValue', '@080Inj1MatResT10\CycDataTmpZone\CycVal.PdeValue');

UPDATE machine_parameters SET param_category = 'Dávkování — hmotnost'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@080CycGraviWeightNet1\CycVal.PdeValue', '@080CycGraviWeightNet2\CycVal.PdeValue', '@080CycGraviWeightNet3\CycVal.PdeValue', '@080CycGraviWeightNet4\CycVal.PdeValue');

UPDATE machine_parameters SET param_category = 'Skelné vlákno'
    WHERE machine_code = 'KM-MC5-01' AND param_name IN ('@080CycRovMonit\CycVal.PdeValue');
"""

# ---------------------------------------------------------------------------
# postgres/init/15_seed_all_presses.sql
# ---------------------------------------------------------------------------
SQL_15_SEED_ALL_PRESSES = r"""
INSERT INTO machines (machine_code, machine_name, cyclades_mac_refmac, active) VALUES
  ('P1000-11', 'Presse 1000T',        'P1000-11', TRUE),
  ('P1100-03', 'Presse 1100 T',       'P1100-03', TRUE),
  ('P1100-04', 'Press ENGEL 1100 T',  'P1100-04', TRUE),
  ('P1800-04', 'Haitian 1800',        'P1800-04', TRUE),
  ('P220-002', 'Presse 220 T',        'P220-002', TRUE),
  ('P220-005', 'PRESSE 220T',         'P220-005', TRUE),
  ('P220-013', 'Presse 220 T Arburg', 'P220-013', TRUE),
  ('P2300-03', 'Presse Engel 2300T',  'P2300-03', TRUE),
  ('P2300-10', 'Presse 2300T',        'P2300-10', TRUE),
  ('P300-009', 'Presse 300 T',        'P300-009', TRUE),
  ('P400-012', 'Presse Engel 400T',   'P400-012', TRUE),
  ('P500-006', 'Presse 500 T',        'P500-006', TRUE),
  ('P600-002', 'Presse 600 T',        'P600-002', TRUE),
  ('P650-016', 'PRESSE 650T',         'P650-016', TRUE),
  ('P650-021', 'PRESSE DE 650T',      'P650-021', TRUE),
  ('P650-022', 'Engel DUO 4550/650',  'P650-022', TRUE),
  ('P700-001', 'Presse 700 T',        'P700-001', TRUE),
  ('P800-011', 'Presse 800T',         'P800-011', TRUE),
  ('P900-002', 'Presse 900T',         'P900-002', TRUE)
ON CONFLICT (machine_code) DO NOTHING;
"""

# ---------------------------------------------------------------------------
# postgres/init/16_add_machine_info_columns.sql
# ---------------------------------------------------------------------------
SQL_16_ADD_MACHINE_INFO_COLUMNS = r"""
ALTER TABLE machines ADD COLUMN IF NOT EXISTS cyclades_label TEXT;
ALTER TABLE machines ADD COLUMN IF NOT EXISTS type_label TEXT;
ALTER TABLE machines ADD COLUMN IF NOT EXISTS atelier TEXT;
ALTER TABLE machines ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE machines ADD COLUMN IF NOT EXISTS info_synced_at TIMESTAMPTZ;
"""

# ---------------------------------------------------------------------------
# postgres/init/17_add_cycle_timestamps.sql
# ---------------------------------------------------------------------------
SQL_17_ADD_CYCLE_TIMESTAMPS = r"""
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ;
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS persisted_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ;
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS occurred_at_source TEXT;

CREATE INDEX IF NOT EXISTS idx_cycles_occurred_at ON cycles (machine_code, occurred_at DESC);
"""

# ---------------------------------------------------------------------------
# postgres/init/18_add_cycle_identity.sql
# ---------------------------------------------------------------------------
SQL_18_ADD_CYCLE_IDENTITY = r"""
CREATE TABLE IF NOT EXISTS cycle_identity (
    machine_code  TEXT NOT NULL,
    cycle_count   INTEGER NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (machine_code, cycle_count)
);
"""

# ---------------------------------------------------------------------------
# postgres/init/19_add_checkpoint_integrity.sql
# ---------------------------------------------------------------------------
SQL_19_ADD_CHECKPOINT_INTEGRITY = r"""
ALTER TABLE collector_state
    ADD COLUMN IF NOT EXISTS last_line_hash TEXT;

ALTER TABLE collector_state
    ADD COLUMN IF NOT EXISTS reports_dat_size_at_checkpoint BIGINT;
"""

# ---------------------------------------------------------------------------
# postgres/init/20_add_collector_heartbeat.sql
# ---------------------------------------------------------------------------
SQL_20_ADD_COLLECTOR_HEARTBEAT = r"""
ALTER TABLE collector_state
    ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;
"""

# Ordered exactly as postgres/init/ numbers them - this order matters (later
# blocks assume earlier ones already ran, e.g. 04/05/13/14 update rows
# inserted by 02/03).
_ALL_BLOCKS_IN_ORDER = (
    SQL_01_SCHEMA,
    SQL_02_SEED_PARAMETERS,
    SQL_03_SEED_PARAMETERS_HARDCOPY,
    SQL_04_SEED_PARAMETERS_BARREL_EXTRUDER_HOPPERS,
    SQL_05_SEED_PARAMETERS_TMPACT_FIX,
    SQL_06_ADD_ORDER_REF,
    SQL_07_SEED_PARAMETERS_PRESSURES_OIL,
    SQL_08_SEED_PARAMETERS_DOSING_PERCENT,
    SQL_09_SEED_PARAMETERS_GLASS_FIBER_ROVING,
    SQL_10_SEED_PARAMETERS_DOSING_ALL_AND_MATERIAL_NAMES,
    SQL_11_SEED_PARAMETERS_CYCLIC_DOSING_PERCENT_AND_RENAME,
    SQL_12_SEED_PARAMETERS_MOLD_HEATING_ZONES,
    SQL_13_ADD_PARAM_CATEGORY,
    SQL_14_FIX_PARAM_CATEGORY_BACKSLASH_ESCAPING,
    SQL_15_SEED_ALL_PRESSES,
    SQL_16_ADD_MACHINE_INFO_COLUMNS,
    SQL_17_ADD_CYCLE_TIMESTAMPS,
    SQL_18_ADD_CYCLE_IDENTITY,
    SQL_19_ADD_CHECKPOINT_INTEGRITY,
    SQL_20_ADD_COLLECTOR_HEARTBEAT,
)


def upgrade() -> None:
    connection = op.get_bind()
    for sql_block in _ALL_BLOCKS_IN_ORDER:
        # exec_driver_sql (not op.execute/sa.text) - sends the string
        # straight to the DBAPI with no bind-parameter parsing, so literal
        # ':' or '%' characters anywhere in this SQL (there are none in
        # practice here, but the point still stands) can't be misread as
        # placeholders. psycopg2 supports multiple ';'-separated statements
        # in a single exec_driver_sql call, so each block runs as one unit,
        # same as `psql -f postgres/init/NN_*.sql` would.
        connection.exec_driver_sql(sql_block)


def downgrade() -> None:
    """Drop everything this baseline created.

    This is provided for completeness/local testing (e.g. spinning up a
    throwaway DB, upgrading, downgrading, upgrading again to sanity-check
    the migration). It is a full, destructive teardown of the schema and
    all data in it - it is NOT part of the intended production workflow and
    must never be run against spc-vm.
    """
    connection = op.get_bind()
    connection.exec_driver_sql("DROP TABLE IF EXISTS cycle_identity;")
    connection.exec_driver_sql("DROP TABLE IF EXISTS collector_state;")
    connection.exec_driver_sql("DROP TABLE IF EXISTS cycles;")
    connection.exec_driver_sql("DROP TABLE IF EXISTS machine_parameters;")
    connection.exec_driver_sql("DROP TABLE IF EXISTS machines;")
    connection.exec_driver_sql("DROP EXTENSION IF EXISTS timescaledb;")
