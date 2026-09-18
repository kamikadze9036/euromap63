-- ============================================================
--  Robustnejsi checkpoint pro cteni REPORTS.DAT (MES_TARGET_ARCHITECTURE.md
--  §5.3 "Idempotence a checkpointy", MES_IMPLEMENTATION_BACKLOG.md
--  tiket 1.4).
--
--  collector_state.reports_lines_read (prosty pocet radku) ma dve slabiny
--  popsane v §5.3:
--
--  1. "nahrazeni nebo zkraceni souboru" - pokud REPORTS.DAT nekdo zkrati
--     nebo nahradi mimo rizenou rotaci v maybe_rotate() (rucni zasah,
--     kvirk stroje, obnova ze zalohy, chyba disku...), novy soubor muze
--     byt kratsi nez posledni checkpoint. collector.py::read_new_cycles
--     dnes na to nema zadnou obranu - "len(data_lines) <= last_count"
--     jednoduse vrati 0 navzdy, ticha ztrata dat bez logu.
--
--  2. "nedokonceny posledni radek souboru" - pokud stroj v okamziku pollu
--     jeste dopisuje posledni radek, muze byt docasne nekompletni. Bez
--     rozliseni "posledni radek prave ted" vs. "stary poskozeny radek
--     nekde uprostred" se checkpoint posune i pres nedokonceny radek a
--     dana davka dat je natrvalo ztracena (viz collector.py komentar u
--     "holdback" logiky v read_new_cycles).
--
--  Reseni: pridat ke collector_state dve pomocna pole, ktera collector.py
--  zapisuje spolu s reports_lines_read pri kazde uspesne aktualizaci
--  checkpointu, a pri dalsim ctenim je pouzije k detekci, ze soubor pod
--  checkpointem "uz neni to, co byval":
--
--   - last_line_hash: sha256 (hex) posledniho radku pokryteho aktualnim
--     reports_lines_read (tj. data_lines[reports_lines_read - 1] v
--     okamziku zapisu checkpointu). NULL, pokud jeste nic precteno.
--   - reports_dat_size_at_checkpoint: os.path.getsize(REPORTS.DAT) ve
--     stejnem okamziku.
--
--  collector.py pri dalsim pollu porovna aktualni velikost/hash se
--  zaznamenanymi hodnotami - pri nesouladu (mensi soubor, nebo stejny/
--  vetsi soubor s jinym obsahem na pozici checkpointu) zaloguje warning
--  a chova se, jako by soubor byl novy (cte od zacatku data_lines), misto
--  aby tise pokracoval z (uz neplatneho) last_count. Viz
--  collector.py::read_new_cycles.
--
--  maybe_rotate() pri sve VLASTNI, zamerne rotaci uz dnes explicitne
--  nuluje reports_lines_read - tenhle tiket k tomu pridava i nulovani
--  techto dvou noveho sloupcu, aby se prvni cteni po rotaci neporovnavalo
--  s hodnotami z predchoziho (jiz prejmenovaneho) souboru.
-- ============================================================

ALTER TABLE collector_state
    ADD COLUMN IF NOT EXISTS last_line_hash TEXT;

ALTER TABLE collector_state
    ADD COLUMN IF NOT EXISTS reports_dat_size_at_checkpoint BIGINT;
