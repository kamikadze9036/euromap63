-- ============================================================
--  Monitoring poctu pramenu (rovingu) sklen?neho vlakna - nalezeno
--  v GETID katalogu 2026-09-17 (blok @080CycRovMonit / @080ETRovMonit /
--  @080ETRovCtrl1), odpovida HMI obrazovce "Pocet rovingu" (hardcopy
--  P0032). Cyklicka hodnota + PDESpecial referencni pro pripad, ze by
--  se u nekterych stroju chovala jako "zamrzla" (viz jiz reseny pripad
--  teplotnich zon), plus staticke min/max/nastaveno a TEX cislo vlakna.
-- ============================================================

INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', '@080CycRovMonit\CycVal.PdeValue',          'N', 'poč.', 'Počet pramenů skl. vlákna (hodnota cyklu)'),
    ('KM-MC5-01', '@080CycRovMonit\PDESpecial0.PDESpecialBase','N', 'poč.', 'Počet pramenů skl. vlákna (referenční)'),
    ('KM-MC5-01', '@080ETRovMonit.SetNumber',                  'N', 'poč.', 'Počet pramenů skl. vlákna (nastaveno)'),
    ('KM-MC5-01', '@080ETRovMonit.MinNumber',                  'N', 'poč.', 'Počet pramenů skl. vlákna (min.)'),
    ('KM-MC5-01', '@080ETRovMonit.MaxNumber',                  'N', 'poč.', 'Počet pramenů skl. vlákna (max.)'),
    ('KM-MC5-01', '@080ETRovMonit.ActNumber',                  'N', 'poč.', 'Počet pramenů skl. vlákna (živá hodnota)'),
    ('KM-MC5-01', '@080ETRovCtrl1.RovTEX',                     'N', 'g/km', 'Jemnost vlákna (TEX)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
