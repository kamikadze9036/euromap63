-- ============================================================
--  Archiv REPORTS.DAT pri rotaci - checksum + metadata (MES_TARGET_
--  ARCHITECTURE.md §5.3 "rotaci a opetovne pouziti nazvu" / "rucni replay
--  archivnich dat", MES_IMPLEMENTATION_BACKLOG.md tiket 1.5).
--
--  collector.py::maybe_rotate() uz dnes pri prekroceni ROTATE_SIZE_MB
--  prejmenuje REPORTS.DAT na "REPORTS.DAT.<timestamp>" ZE STEJNEHO adresare
--  (FTP_ROOT) - historicka data se tedy nemazou, jen prejmenovavaji. Chybi
--  ale zpusob, jak:
--
--   1. overit integritu archivovaneho souboru pred tim, nez mu budoucí
--      replay/reconciliation nastroj (tiket 1.6, zatim nepostaveny) zacne
--      duverovat (soubor na FTP sdilene slozce muze byt zvenku upraven,
--      poskozen na disku, atd.);
--   2. zjistit, CO archiv obsahuje (kolik radku/cyklu bylo z nej jiz
--      skutecne zpracovano do "cycles"), aniz by bylo nutne cely (az
--      ROTATE_SIZE_MB velky) soubor znovu parsovat jen kvuli tomuto zjisteni.
--
--  Reseni: samostatna DB tabulka (ne sidecar .json soubor vedle archivu).
--  Duvod, proc DB a ne soubor: kazdy jiny kus stavu, ktery tenhle collector
--  potrebuje prezit napric restarty (checkpointy v collector_state, identita
--  cyklu v cycle_identity, heartbeat), uz je dnes v DB, ne v souborech vedle
--  REPORTS.DAT - DB tabulka je s tim konzistentni a navic umoznuje budoucimu
--  replay nastroji (tiket 1.6) jednoduchy dotaz "vsechny archivy pro stroj X
--  od data Y" bez prochazeni FTP_ROOT. Cena (DB nemusi byt v okamziku
--  rotace dostupna) je prijatelna, protoze bookkeeping v collector.py je
--  zamerne oddeleny do vlastniho try/except, ktery pri selhani jen zaloguje
--  a nechá samotnou (uz dnes funkcni) rotaci doběhnout - viz komentar u
--  collector.py::maybe_rotate.
--
--  Jde o obycejnou (ne hypertable) tabulku - jeden radek na kazdou rotaci,
--  objem je zanedbatelny (rotace nastava az po ROTATE_SIZE_MB, tj. radove
--  jednotky az desitky radku rocne na stroj), zadna potreba TimescaleDB
--  partitionovani.
--
--  Sloupce:
--   - archive_path: absolutni cesta k prejmenovanemu souboru (framework
--     stale v FTP_ROOT, viz collector.py komentar - tenhle tiket soubor
--     nikam nepresouva).
--   - sha256: hex sha256 CELEHO obsahu archivovaneho souboru (pocitano
--     streamovane po blocich, ne nacitanim celeho souboru do pameti - viz
--     collector.py::_sha256_file), pro budouci overeni integrity pred
--     replayem.
--   - line_count: pocet DATOVYCH radku, ktere z tohoto souboru collector uz
--     skutecne stihl zpracovat do "cycles" pred rotaci (posledni znama
--     hodnota collector_state.reports_lines_read pred jejim vynulovanim
--     touto rotaci) - NE celkovy pocet radku fyzicky v souboru. Tyhle dve
--     hodnoty se typicky lisi jen o "holdback" (max. 1 nedokonceny posledni
--     radek, viz tiket 1.4) - ale line_count tu zaznamenava to dulezitejsi
--     pro rekonciliaci: kolik z obsahu archivu uz je bezpecne v DB, tedy co
--     by pripadny replay mel (nebo nemel) znovu zpracovavat.
--   - size_bytes: os.path.getsize() archivovaneho souboru v okamziku
--     archivace (rychla sanity-check hodnota bez nutnosti soubor otevirat).
-- ============================================================

CREATE TABLE IF NOT EXISTS reports_dat_archive (
    id              BIGSERIAL PRIMARY KEY,
    machine_code    TEXT NOT NULL REFERENCES machines(machine_code),
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    archive_path    TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    line_count      INTEGER NOT NULL,
    size_bytes      BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_dat_archive_machine_time
    ON reports_dat_archive (machine_code, archived_at DESC);
