-- ============================================================
--  Zadany (%) podil hmotnostniho prutoku davkovacu - ETGraviDos2 a
--  ETGraviPseudoDos jsou jedine dva davkovaci kanaly, ktere ma tenhle
--  konkretni stroj elektronicky zapojene s % rizenim (overeno cerstvym
--  GETID vypisem 2026-09-17 - CycGraviWeightNet1-4 existuji jako
--  hmotnostni cidla na vice pozicich, ale procentualni podil ma jen
--  davkovac 2 a "pseudo" davkovac).
-- ============================================================

INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', '@080ETGraviDos2.MassPerc',       'N', '%', 'Podíl hmotnostního průtoku – dávkovač 2'),
    ('KM-MC5-01', '@080ETGraviPseudoDos.MassPerc',  'N', '%', 'Podíl hmotnostního průtoku – dávkovač 8 (pseudo)')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
