-- ============================================================
--  Dalsi parametry pro KM-MC5-01, vybrane z hardcopy obrazovky
--  "Vyber parametru" na stroji (2026-09-15, slozka MC5/BMP/P0032_00.jpg)
--  a dohledane v GETID.DAT podle shodujiciho se ceskeho popisku.
-- ============================================================

INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', 'ActTmpBrlZn[1,3]',                                       'N', '°C',   'Zóna válce 3'),
    ('KM-MC5-01', 'ActTmpBrlZn[1,5]',                                       'N', '°C',   'Zóna válce 5'),
    ('KM-MC5-01', '@080Inj1MatResT1\CycDataTmpZone\CycVal.PdeValue',        'N', '°C',   'Zóna zásobníku materiálu 1'),
    ('KM-MC5-01', '@080Inj1MatResT2\CycDataTmpZone\CycVal.PdeValue',        'N', '°C',   'Zóna zásobníku materiálu 2'),
    ('KM-MC5-01', '@080Inj1MatResT3\CycDataTmpZone\CycVal.PdeValue',        'N', '°C',   'Zóna zásobníku materiálu 3'),
    ('KM-MC5-01', '@080Inj1MatResT7\CycDataTmpZone\CycVal.PdeValue',        'N', '°C',   'Zóna zásobníku materiálu 7'),
    ('KM-MC5-01', '@080Inj1MatResT10\CycDataTmpZone\CycVal.PdeValue',       'N', '°C',   'Zóna zásobníku materiálu 10'),
    ('KM-MC5-01', '@080CycGraviWeightNet1\CycVal.PdeValue',                 'N', 'kg',   'Čistá hmotnost dávkovače 1'),
    ('KM-MC5-01', '@080CycGraviWeightNet2\CycVal.PdeValue',                 'N', 'kg',   'Čistá hmotnost dávkovače 2'),
    ('KM-MC5-01', '@080CycGraviFeedFactor2\CycVal.PdeValue',                'N', 'kg/h', 'Aktuální dávkovací faktor 2'),
    ('KM-MC5-01', '@010CycDataMld\CycCfbTim\CycVal.PdeValue',               'N', 's',    'Doba náběhu uzavírací síly'),
    ('KM-MC5-01', '@010CycDataMld\CycClpOpnTim\CycVal.PdeValue',            'N', 's',    'Doba otvírání nástroje'),
    ('KM-MC5-01', '@010CycDataOth\CycBreakTim\CycVal.PdeValue',             'N', 's',    'Doba pauzy')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
