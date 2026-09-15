-- ============================================================
--  Popisky a jednotky parametru pro KM-MC5-01, vytazeno z GETID.DAT
--  (co presne stroj podle EUROMAP 63 nabizi). Pouziva se pro citelne
--  popisky v dashboardu (viz api/main.py /api/parameters).
-- ============================================================

INSERT INTO machines (machine_code, machine_name, ftp_root)
VALUES ('KM-MC5-01', 'Krauss Maffei MC5', '/ftpdata')
ON CONFLICT (machine_code) DO NOTHING;

INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', 'ActCntCyc',                 'N', '-',  'Počítadlo cyklů'),
    ('KM-MC5-01', 'ActTimCyc',                 'N', 's',  'Doba cyklu'),
    ('KM-MC5-01', 'ActTimFill[1]',             'N', 's',  'Doba vstřiku'),
    ('KM-MC5-01', 'ActTimPlst[1]',             'N', 's',  'Doba přepouštění (plastikace)'),
    ('KM-MC5-01', 'ActStrCsh[1]',              'N', 'mm', 'Polštář'),
    ('KM-MC5-01', '@010ModeCycle.CoolTimAct',  'N', 's',  'Doba chlazení'),
    ('KM-MC5-01', 'ActTimXfr[1]',              'N', 's',  'Doba dotlaku'),
    ('KM-MC5-01', 'ActStrXfr[1]',              'N', 'mm', 'Přepínací dráha dotlaku'),
    ('KM-MC5-01', 'ActStrPlst[1]',             'N', 'mm', 'Přepouštěcí zdvih (plastikace)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
