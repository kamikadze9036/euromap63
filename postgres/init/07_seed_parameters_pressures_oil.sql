-- ============================================================
--  Teplota oleje a tlaky hmoty - dalsi plocha "Act*" pole overena
--  jako spolehlivy vzor (stejny jako puvodnich 9 parametru).
-- ============================================================

INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', 'ActTmpOil',            'N', '°C',  'Teplota oleje'),
    ('KM-MC5-01', 'ActPrsXfrSpec[1]',     'N', 'bar', 'Přepínací tlak hmoty (dotlak)'),
    ('KM-MC5-01', 'ActPrsHldSpecMax[1]',  'N', 'bar', 'Max. tlak hmoty (za takt)'),
    ('KM-MC5-01', 'ActPrsMachSpecMax',    'N', 'bar', 'Max. tlak hmoty (celkový)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
