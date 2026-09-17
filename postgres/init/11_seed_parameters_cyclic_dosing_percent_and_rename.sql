-- ============================================================
--  1) Uzivatel potvrdil, ze "davkovac 8 (pseudo)" je skelne vlakno
--     (soucet vsech 5 kanalu = 68+0+0+2+30 = presne 100 %, 30 % sedi na
--     typickou GF30 recepturu, dava smysl u LFT linky, kde se sklo
--     netahne pres gravimetricky podavac, ale pres roving) - prejmenovat
--     label, ať je to na dashboardu na prvni pohled jasne.
--
--  2) Existuje i strukturovana cyklicka varianta stejne %-hodnoty
--     (@080CycGraviMassFlowPercN\CycVal.PdeValue + PDESpecial0 reference)
--     - stejny vzor jako u CycGraviWeightNet. Plocha ETGraviDosN.MassPerc,
--     kterou uz sbirame, nema zvlast "Set"/"Act" varinatu (na rozdil od
--     RovMonit), takze jde pravdepodobne o zivou (aktualni) hodnotu v
--     okamziku zapisu reportu - pridavame cyklickou variantu vedle pro
--     porovnani/overeni, jestli se lisi (stejna metoda, jakou jsme uz
--     jednou pouzili u teplotnich zon Act vs Cyc).
--
--  Zadne min/max/avg pole za cely cyklus stroj pro tento parametr
--  nenabizi - prohledan cely GETID katalog, jediny podobny "peak"
--  parametr na celem stroji je @010CycMaxPowLC (spickovy odber), zvlastni
--  ucelovy tag, ne obecny mechanismus dostupny pro kazdy parametr.
-- ============================================================

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
