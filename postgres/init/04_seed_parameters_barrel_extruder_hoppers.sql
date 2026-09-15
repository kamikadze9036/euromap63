-- ============================================================
--  Kompletni teploty vstrikovaciho valce a extruderu + vsechny 4
--  nasypky (davkovace) se zadanou i realnou hmotnosti.
--  Vytazeno z GETID.DAT dle obecneho vzoru: kazda "Act.../CycVal.
--  PdeValue" (mereno) ma parovou "Set..." nebo "...PDESpecial0.
--  PDESpecialBase" (zadana/referencni hodnota).
-- ============================================================

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
