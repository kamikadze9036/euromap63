-- ============================================================
--  Přidání všech ostatních vstřikovacích lisů (Cyclades MACHINE,
--  ATEL_REFATEL='Injection') do "hala" dashboardu - zatím bez
--  EUROMAP63/OPC napojení, jen stav/OF/štítky z Cyclades (stejný
--  mechanismus, jaký už /api/machines/status pro KM-MC5-01 dělá).
--  Cyklová data (grafy, tabulka parametrů) přibydou postupně, jak
--  budou lisy jednotlivě zapojovány - viz README "Rozšíření na
--  další stroj". machine_code = přímo Cyclades MAC_REFMAC, protože
--  žádný vlastní interní kód (jako KM-MC5-01) tyhle stroje zatím
--  nemají.
--
--  Zdroj: SUIVPRO.dbo.MACHINE WHERE ATEL_REFATEL='Injection'
--  (ověřeno řádkovými daty 2026-09-17, 20 lisů celkem včetně
--  P2700-01/KM-MC5-01).
-- ============================================================

INSERT INTO machines (machine_code, machine_name, cyclades_mac_refmac, active) VALUES
  ('P1000-11', 'Presse 1000T',        'P1000-11', TRUE),
  ('P1100-03', 'Presse 1100 T',       'P1100-03', TRUE),
  ('P1100-04', 'Press ENGEL 1100 T',  'P1100-04', TRUE),
  ('P1800-04', 'Haitian 1800',        'P1800-04', TRUE),
  ('P220-002', 'Presse 220 T',        'P220-002', TRUE),
  ('P220-005', 'PRESSE 220T',         'P220-005', TRUE),
  ('P220-013', 'Presse 220 T Arburg', 'P220-013', TRUE),
  ('P2300-03', 'Presse Engel 2300T',  'P2300-03', TRUE),
  ('P2300-10', 'Presse 2300T',        'P2300-10', TRUE),
  ('P300-009', 'Presse 300 T',        'P300-009', TRUE),
  ('P400-012', 'Presse Engel 400T',   'P400-012', TRUE),
  ('P500-006', 'Presse 500 T',        'P500-006', TRUE),
  ('P600-002', 'Presse 600 T',        'P600-002', TRUE),
  ('P650-016', 'PRESSE 650T',         'P650-016', TRUE),
  ('P650-021', 'PRESSE DE 650T',      'P650-021', TRUE),
  ('P650-022', 'Engel DUO 4550/650',  'P650-022', TRUE),
  ('P700-001', 'Presse 700 T',        'P700-001', TRUE),
  ('P800-011', 'Presse 800T',         'P800-011', TRUE),
  ('P900-002', 'Presse 900T',         'P900-002', TRUE)
ON CONFLICT (machine_code) DO NOTHING;
