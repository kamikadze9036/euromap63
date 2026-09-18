-- ============================================================
--  Popisne udaje o stroji z Cyclades master dat (typ/tonaz, dilna,
--  sekce, cyclades nazev) - drzeny lokalne, aby se pri kazdem
--  zobrazeni detailu stroje nemusely tahat znovu z produkcni
--  Cyclades DB. Synchronizuje _sync_machine_info_to_db() v api/main.py,
--  jednou za MACHINE_INFO_SYNC_INTERVAL_SEC (staticke udaje, staci
--  obcas) - viz README.
-- ============================================================

ALTER TABLE machines ADD COLUMN IF NOT EXISTS cyclades_label TEXT;
ALTER TABLE machines ADD COLUMN IF NOT EXISTS type_label TEXT;
ALTER TABLE machines ADD COLUMN IF NOT EXISTS atelier TEXT;
ALTER TABLE machines ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE machines ADD COLUMN IF NOT EXISTS info_synced_at TIMESTAMPTZ;
