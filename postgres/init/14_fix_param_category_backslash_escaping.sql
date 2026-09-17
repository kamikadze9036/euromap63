-- ============================================================
--  Oprava 13_add_param_category.sql: retezce s parametry obsahujicimi
--  zpetne lomitko (\CycDataTmpZone\..., \CycVal.PdeValue apod.) byly
--  omylem zapsany se zdvojenymi lomitky. Postgres (standard_conforming_
--  strings=on, vychozi od 9.1) bere zpetne lomitko v retezcovem literalu
--  jako obycejny znak, ne escape - zdvojeni tedy vytvorilo retezec se
--  DVEMA lomitky, ktery nikdy neodpovidal skutecnemu param_name (s
--  jednim lomitkem), takze 5 z 8 kategorii melo cast nebo vsechny sve
--  radky bez kategorie (0-8 misto ocekavaneho poctu). Overeno v produkci
--  2026-09-17: "Teploty zasobniku materialu" a "Davkovani - hmotnost"
--  mely 0 radku, "Casovani cyklu" 8/11, "Teploty extruderu" 8/10,
--  "Skelne vlakno" 1/2.
-- ============================================================

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
