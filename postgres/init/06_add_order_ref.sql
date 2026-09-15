-- ============================================================
--  Dohledatelnost dle cisla zakazky (OF) - Cyclades MES.
--  Collector plni order_ref aktualni bezici zakazkou v dobe cyklu
--  (viz get_active_order() v collector.py). Umoznuje pri reklamaci
--  dohledat vsechny cyklove parametry patrici ke konkretnimu OF.
-- ============================================================

ALTER TABLE cycles ADD COLUMN IF NOT EXISTS order_ref TEXT;
CREATE INDEX IF NOT EXISTS idx_cycles_order_ref ON cycles (order_ref);

ALTER TABLE machines ADD COLUMN IF NOT EXISTS cyclades_mac_refmac TEXT;
UPDATE machines SET cyclades_mac_refmac = 'P2700-01' WHERE machine_code = 'KM-MC5-01';
