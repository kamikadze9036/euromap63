-- ============================================================
--  Idempotentni zapis cyklu (MES_TARGET_ARCHITECTURE.md §5.3
--  "Idempotence a checkpointy", MES_IMPLEMENTATION_BACKLOG.md
--  tiket 1.3 - opraveny rozsah, viz komentar nize).
--
--  Puvodni zneni tiketu 1.3 navrhovalo
--  "UNIQUE (machine_code, cycle_count, time)" primo na "cycles" +
--  "ON CONFLICT DO NOTHING". To nefunguje: "time" je DEFAULT now()
--  nastavovane az pri INSERTu (nedeterministicka hodnota casu zapisu,
--  ne udalosti), takze unique constraint, ktery ji obsahuje, by
--  odchytil jen duplicitu zapsanou ve stejnou mikrosekundu - ne
--  skutecne scenare, ktere reseni potrebuje: replay archivovaneho
--  REPORTS.DAT do DB, ktera uz tyto cykly ma, restart collectoru,
--  ktery znovu precte par jiz zapsanych radku, nebo jine opakovane
--  zpracovani stejne dvojice (machine_code, cycle_count) v jiny cas.
--
--  TimescaleDB navic vyzaduje, aby kazdy UNIQUE index/constraint na
--  hypertabulce obsahoval partitioning sloupec ("time" u "cycles") -
--  "UNIQUE (machine_code, cycle_count)" primo na "cycles" tedy vubec
--  nejde vytvorit.
--
--  Reseni: samostatna, obycejna (ne hypertable) tabulka drzici
--  skutecnou identitu cyklu. "cycles" zustava append-only hypertable
--  beze zmeny; "cycle_identity" je gatekeeper - collector.py pred
--  kazdym INSERTem do "cycles" nejdriv zkusi
--  "INSERT INTO cycle_identity ... ON CONFLICT DO NOTHING RETURNING 1"
--  ve stejne transakci, a radek do "cycles" vlozi jen kdyz se vratil
--  radek (tj. tato dvojice (machine_code, cycle_count) jeste
--  nebyla zaznamenana). Viz collector.py::read_new_cycles.
--
--  Znama mezera (zamerne neresena timto tiketem): "cycle_count"
--  (ActCntCyc) je normalne monotonne rostouci celozivotni citac
--  stroje, ale §5.3 uvadi "reset pocitadla cyklu" jako realnou
--  udalost (napr. po servisnim zasahu/firmware resetu na stroji).
--  Pokud k tomu dojde, PRIMARY KEY (machine_code, cycle_count) by
--  novy cyklus po resetu se stejnou hodnotou citace jako pred resetem
--  chybne vyhodnotil jako duplicitu a tise ho zahodil. Reseni (napr.
--  pridani "epoch" sloupce, ktery se pri detekci resetu inkrementuje)
--  je mimo rozsah tohoto tiketu - collector.py jen loguje warning,
--  kdyz cycle_count pro dany stroj klesne oproti minule videne hodnote.
-- ============================================================

CREATE TABLE IF NOT EXISTS cycle_identity (
    machine_code  TEXT NOT NULL,
    cycle_count   INTEGER NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (machine_code, cycle_count)
);
