-- ============================================================
--  Duveryhodnejsi casova osa cyklu (MES_TARGET_ARCHITECTURE.md §5.1,
--  MES_IMPLEMENTATION_BACKLOG.md tiket 1.1 + zuzeny 1.2).
--
--  Dosud mel cyklus jen sloupec "time" (DEFAULT now() pri INSERTu) -
--  tvaril se jako cas udalosti, ale ve skutecnosti to byl cas, kdy
--  collector radek zapsal do DB. Po vypadku collectoru/DB se tak vic
--  historickych cyklu nacetlo v jednom pollu a jejich casova osa se
--  stlacila do jednoho okamziku (rozbiji vypocet prostoju, OEE,
--  genealogii a SPC trendy).
--
--  Sloupec "time" (partitioning sloupec hypertabulky) se nemeni - to
--  je tiket 1.3 (UNIQUE constraint/ON CONFLICT musi obsahovat
--  partitioning sloupec, reseno spolu s idempotenci). Misto toho
--  pridavame samostatne, poctive pojmenovane sloupce:
--
--   - received_at         - cas, kdy collector radek precetl/naparsoval
--                            z REPORTS.DAT (jeden cas na cely poll/davku,
--                            ne per-radek - viz collector.py::read_new_cycles).
--   - persisted_at        - cas ulozeni do DB (DEFAULT now(), stejne
--                            chovani jako drivejsi implicitni "time",
--                            jen poctive pojmenovane).
--   - occurred_at         - nejlepsi odhad skutecneho casu udalosti na
--                            stroji. Stroj v teto konfiguraci neposkytuje
--                            spolehlivy vlastni cas (GETINFO/GETID - viz
--                            tiket 1.2, vyzaduje samostatne HW overeni),
--                            takze jde o REKONSTRUKCI: cyklus se odvozuje
--                            zpetne od received_at pomoci cycle_time_s
--                            (ActTimCyc) kazdeho cyklu v ramci davky.
--   - occurred_at_source  - odkud occurred_at pochazi, napr.
--                            'reconstructed_from_cycle_time' nebo
--                            'received_at_fallback' (kdyz cycle_time_s
--                            chybi) - downstream konzumenti (OEE, SPC,
--                            genealogie) tak vidi, ze nejde o presny
--                            strojovy cas.
-- ============================================================

ALTER TABLE cycles ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ;
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS persisted_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ;
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS occurred_at_source TEXT;

CREATE INDEX IF NOT EXISTS idx_cycles_occurred_at ON cycles (machine_code, occurred_at DESC);
