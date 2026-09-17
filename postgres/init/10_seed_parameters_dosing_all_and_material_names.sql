-- ============================================================
--  Oprava/doplneni k 08_seed_parameters_dosing_percent.sql: predchozi
--  pruzkum GETID katalogu byl omylem proveden na useknutem souboru
--  (stazeno uprostred FTP prenosu, chybela cast za radkem 1259) a
--  ukazal jen davkovac 2 a "pseudo". Po znovu-stazeni kompletniho
--  GETID.DAT (2026-09-17) se ukazalo, ze stroj ma vsechny 4 gravimetricke
--  davkovace (1-4) elektronicky zapojene s % rizenim - presne jak rekl
--  uzivatel (4 davkovace, jeden na pozici 1 a jeden na pozici 4).
--
--  Pridava i "nazev materialu" pro kazdy davkovac (@080ETDosXName.Data,
--  textovy parametr) - pouzije se ke zjisteni, ktery davkovac aktualne
--  veze sklenene vlakno, a tim padem i to, ktereho davkovace MassPerc
--  je "procenta skelneho vlakna".
-- ============================================================

INSERT INTO machine_parameters (machine_code, param_name, param_type, param_unit, param_label) VALUES
    ('KM-MC5-01', '@080ETGraviDos1.MassPerc', 'N', '%', 'Podíl hmotnostního průtoku – dávkovač 1'),
    ('KM-MC5-01', '@080ETGraviDos3.MassPerc', 'N', '%', 'Podíl hmotnostního průtoku – dávkovač 3'),
    ('KM-MC5-01', '@080ETGraviDos4.MassPerc', 'N', '%', 'Podíl hmotnostního průtoku – dávkovač 4'),
    ('KM-MC5-01', '@080ETDos1Name.Data',      'A', '-', 'Název materiálu – dávkovač 1'),
    ('KM-MC5-01', '@080ETDos2Name.Data',      'A', '-', 'Název materiálu – dávkovač 2'),
    ('KM-MC5-01', '@080ETDos3Name.Data',      'A', '-', 'Název materiálu – dávkovač 3'),
    ('KM-MC5-01', '@080ETDos4Name.Data',      'A', '-', 'Název materiálu – dávkovač 4')
ON CONFLICT (machine_code, param_name) DO UPDATE
    SET param_type = EXCLUDED.param_type,
        param_unit = EXCLUDED.param_unit,
        param_label = EXCLUDED.param_label,
        updated_at = now();
