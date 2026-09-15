-- ============================================================
--  Nahrazuje "mrtve" cyklove hodnoty vstrikovaciho valce a nekterych
--  extruderovych zon (\CycDataTmpZone\CycVal.PdeValue, ActTmpBrlZn[1,N])
--  za primy zivy odecet (.TmpAct) - overeno, ze .../CycVal.PdeValue
--  zustavalo fixni na 240 po desitky cyklu, zatimco .TmpAct odpovida
--  hodnotam skutecne menicim se na displeji stroje (SPC/tolerance
--  monitoring pro tyto konkretni zony neni na stroji zapnuty).
-- ============================================================

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
