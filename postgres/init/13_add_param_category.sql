-- ============================================================
--  Prida kategorii parametru (pro seskupeny checkbox picker na
--  detailu stroje - stejne 8 kategorii jako v referencnim artefaktu
--  "vyroba-live"). Jen kuratovana ~48 "chartovatelnych" (zivych,
--  numerickych) parametru dostava kategorii - zbytek (mrtve zony
--  nastroje, textova jmena materialu, referencni PDESpecial hodnoty)
--  zustava bez kategorie a dal se zobrazuje jen v surove tabulce
--  vsech parametru, ne v novem grafovem pickeru.
-- ============================================================

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
