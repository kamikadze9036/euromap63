# Implementační backlog k MES_TARGET_ARCHITECTURE.md

## Účel a jak s tímto dokumentem pracovat

`MES_TARGET_ARCHITECTURE.md` popisuje cílovou architekturu a etapový plán na
úrovni strategie. Tento dokument ho rozpadá na tikety dost malé na to, aby je
šlo zadat jednomu běhu Claude (Sonnet/Opus) jako samostatný úkol — s jasným
rozsahem, závislostmi a akceptačními kritérii, aniž by agent musel domýšlet
byznys rozhodnutí.

Legenda:

- **[AGENT-S/M/L]** — čistě technický úkol, lze zadat coding agentovi bez
  dalšího lidského rozhodování. S = pár hodin/jedna session, M = 0,5–2 dny
  (možná víc sessions), L = víc dní, doporučeno dál rozdělit před zadáním.
- **[ČLOVĚK]** — vyžaduje rozhodnutí, schválení nebo workshop mimo kód;
  agent na tom nemá co dělat, dokud rozhodnutí nepadne.
- **[ČLOVĚK→AGENT]** — člověk rozhodne parametr/politiku, agent pak
  implementuje podle zadání.

Odkazy na soubory a řádky jsou k `main`/aktuálnímu stavu repa
(`C:\_my_\euromap63\euromap63-docker`) k 2026-09-18 — před zadáním tiketu si
agent musí stav ověřit znovu, kód se mezitím mohl posunout.

Fáze 3–5 jsou zatím jen orientační roadmapa. Detailní rozpad na tikety má
smysl až po splnění exit kritérií Fáze 0–1, protože přesná podoba Production
Execution modulu závisí na workshopu s výrobou.

---

## Fáze 0 — Produktové hranice a architektura

| # | Typ | Úkol | Závislosti |
|---|---|---|---|
| 0.1 | [ČLOVĚK] | Jmenovat product ownera za výrobu + technického vlastníka MES, vybrat pilotní stroj/produkt/zakázku. | – |
| 0.2 | [ČLOVĚK] | Workshop s výrobou, kvalitou, technologií, logistikou, údržbou, IT/OT — zmapovat tok zakázky a výjimky. | 0.1 |
| 0.3 | [AGENT-S] | Sepsat glosář identifikátorů (`machine_code`, `cycle_id`, `order_ref`, `label`, budoucí `operation_id`, `batch_id`) jako `docs/glossary.md`, vycházející z existujícího schématu (`postgres/init/01_schema.sql` a navazující migrace) a skutečně používaných polí v `collector.py`/`api/main.py`. | – |
| 0.4 | [AGENT-S] | Nakreslit *aktuální* (ne cílový) komponentový diagram do `docs/current_architecture.md`, ověřený proti reálnému `docker-compose.yml` (služby, porty, network_mode) — základ pro to, aby cílová architektura v `MES_TARGET_ARCHITECTURE.md` §9 nezastarala. | – |
| 0.5 | [AGENT-S] | Založit `docs/adr/` s šablonou ADR a prvním záznamem: rozhodnutí zachovat modulární monolit + Postgres/TimescaleDB (zdůvodnění převzít z §7 a §9.1 architektonického dokumentu). | – |
| 0.6 | [AGENT-M] | Draft ownership matice Cyclades × MES (`docs/ownership_matrix.md`) — agent umí předvyplnit sloupec "současný vlastník" z toho, co kód dnes skutečně čte/píše (Cyclades tabulky `MACHINE`, `OF`, `LIGOF` vs. lokální `machines`/`cycles`), ale sloupec "budoucí vlastník" a přechodová pravidla musí doplnit/schválit člověk. | 0.2 |
| 0.7 | [ČLOVĚK] | Schválit rozsah prvního MES release (které domény z §6 jdou do pilotu, které zůstávají v Cyclades). | 0.2, 0.6 |

**Exit:** shoda na tom, co vlastní první release MES vs. co zůstává v Cyclades (viz §11 Fáze 0 v hlavním dokumentu).

---

## Fáze 1 — Důvěryhodná datová základna

Toto je fáze s nejvíc konkrétní existující kódovou základnou — dá se začít
hned po Fázi 0, nezávisle na workshopech o Production Execution.

| # | Typ | Úkol | Soubory | Závislosti |
|---|---|---|---|---|
| 1.1 | [AGENT-M] | Přidat `received_at` (čas přečtení řádku collectorem) a `persisted_at` (default `now()`) do tabulky `cycles`. Nová migrace `postgres/init/17_add_cycle_timestamps.sql`, úprava INSERTu v `collector.py::read_new_cycles`. | `postgres/init/`, `collector/collector.py` | – |
| 1.2 | [ČLOVĚK→AGENT] | `occurred_at`: nutno napřed zjistit, jestli EUROMAP63 GETINFO/GETID pro tento stroj vůbec nabízí spolehlivý časový/uptime údaj (v `templates/GETINFO.JOB`, `GETID.JOB`) — pokud ne, `occurred_at` bude jen *rekonstruovaný* (odvozený z `cycle_time_s` a pořadí), ne skutečný. Člověk/PO potvrdí, že rekonstrukce je akceptovatelná, pak agent implementuje. | `collector/` | 1.1 |
| 1.3 | [AGENT-M] | Idempotentní zápis: přidat `UNIQUE (machine_code, cycle_count, time)` na hypertable `cycles` (funguje, protože `time` je partitioning sloupec) a přepnout INSERT na `ON CONFLICT DO NOTHING`. Otestovat proti reálné rotaci REPORTS.DAT. | `postgres/init/`, `collector/collector.py:304-311` | – |
| 1.4 | [AGENT-M] | Robustnější checkpoint: rozšířit `collector_state` o `last_line_hash`/`reports_dat_size_at_checkpoint`; při `maybe_rotate`/startu detekovat zkrácení souboru (aktuální velikost < checkpoint) a zalogovat/ošetřit misto tichého pokračování. | `collector/collector.py:244-354` | – |
| 1.5 | [AGENT-S] | Archivovat REPORTS.DAT při rotaci s checksumem (SHA256) a metadaty (čas, počet řádků) vedle stávajícího `os.replace(REPORTS_DAT, backup)`. | `collector/collector.py:329-354` | – |
| 1.6 | [AGENT-M] | Replay skript (`scripts/replay_reports_dat.py`): přehraje archivovaný soubor do prázdné/testovací DB, na konci vypíše reconciliation report (počet cyklů, součty). | nový soubor | 1.3, 1.5 |
| 1.7 | [AGENT-L] | Časově verzované přiřazení zakázky: nová tabulka `order_assignments (machine_code, order_ref, valid_from, valid_to)` plněná při každé změně `get_active_order()` (ne jen cache), a JOIN cyklu k zakázce podle `occurred_at`/`received_at` místo aktuální live hodnoty. | `postgres/init/`, `collector/collector.py:176-213` | 1.1/1.2 |
| 1.8 | [AGENT-S] | Heartbeat/diagnostika: rozšířit `collector_state` o `last_heartbeat_at`, `lag_seconds`; nový endpoint `GET /api/collectors/health` v `api/main.py`. | `collector/collector.py`, `api/main.py` | – |
| 1.9 | [AGENT-L] | Zavést Alembic: `alembic init`, převést `postgres/init/01_schema.sql` … `16_*.sql` do baseline revize, od této chvíle nové schema změny jako Alembic migrace místo dalšího `NN_*.sql`. | `postgres/init/*`, nový `alembic/` | – |
| 1.10 | [AGENT-M] | Testy: unit testy pro `split_csv_line`, `merge_wrapped_lines`, `is_data_line` (fixture s víceřádkovým wrappingem a poškozeným řádkem — reálné případy už řešené v historii commitů), integrační test collectoru proti dočasné Postgres/Timescale instanci. | nový `tests/` | – |

**Exit** (z hlavního dokumentu, beze změny): restart nic neztratí/nezdvojí, backlog se přiřadí správně, raw data lze přehrát, výpadek sběru se automaticky pozná, testy pokrývají rotaci/částečný řádek/reset/obnovu.

---

## Fáze 2 — Platforma MES

| # | Typ | Úkol | Soubory | Závislosti |
|---|---|---|---|---|
| 2.1 | [AGENT-M] | Než se `api/main.py` (dnes ~1150+ řádků, všechny endpointy v jednom souboru) rozdělí na routery podle domény — napřed přidat smoke testy na existující endpointy (aby refaktoring měl safety net). | `api/main.py`, nový `tests/` | – |
| 2.2 | [AGENT-L] | Rozdělit `api/main.py` na balíčky (`api/machines/`, `api/cycles/`, `api/downtimes/`, `api/cyclades_adapter/`) s FastAPI routery. Čistý refaktoring, bez změny chování. | `api/` | 2.1 |
| 2.3 | [AGENT-M] | Vyčlenit veškerý `pymssql`/Cyclades přístup (dnes rozházený v `collector.py` i `api/main.py` — `get_active_order`, `_sync_machine_info_to_db`, cavity-scrap dotaz) do jednoho modulu `cyclades_adapter` s jasným, testovatelným rozhraním (read-only). | `collector/collector.py`, `api/main.py` | 2.2 doporučeno souběžně |
| 2.4 | [AGENT-M] | Odstranit N+1 v `GET /api/machines/status` (`api/main.py:851-882`) — dnes 1 dotaz na seznam strojů + N dotazů na `latest_cycle`. Přepsat na jeden dotaz s `LATERAL JOIN`/`DISTINCT ON`. | `api/main.py:851-882` | – |
| 2.5 | [AGENT-M] | Connection pooling: nahradit ad-hoc `psycopg2.connect()`/`get_conn()` sdíleným poolem (`psycopg_pool` nebo SQLAlchemy engine). | `api/main.py`, `collector/collector.py` | 2.4 dřív, ať se pool navrhuje na už opravený query pattern |
| 2.6 | [ČLOVĚK] | Vybrat IdP (Entra ID vs. Keycloak) a potvrdit tenant/firemní identity zdroj. | – |
| 2.7 | [AGENT-L] | OIDC/SSO integrace + role/permissions (`Depends`) ve FastAPI — nejdřív jen na budoucí zápisové endpointy (Fáze 3), ne na dnešní read-only dashboard. | `api/main.py` | 2.6 |
| 2.8 | [AGENT-M] | Auditní tabulka `audit_log` (actor, akce, entita, čas, důvod) + decorator/middleware, zapojit hned jak vzniknou první zápisové endpointy (Fáze 3), ne dřív zbytečně. | nový | 2.7 |
| 2.9 | [AGENT-S] | Verzovat API (`/api/v1/...` prefix) před dalšími breaking změnami. | `api/main.py`, `frontend/*.js` volající API | 2.2 |
| 2.10 | [AGENT-M] | Outbox tabulka (`outbox_events`) + jednoduchý worker proces; první publikovaná událost `machine.cycle_recorded` napojená na insert v `collector.py`. | nový, `collector/collector.py` | 1.3 |
| 2.11 | [AGENT-S] | Strukturované (JSON) logy na API i collectoru, `/metrics` endpoint (Prometheus client). | `api/main.py`, `collector/collector.py` | – |
| 2.12 | [ČLOVĚK→AGENT] | Backup/retention politika: člověk rozhodne RTO/RPO a retenci telemetrie, agent naimplementuje `pg_dump`/TimescaleDB retention policy + cron a otestuje restore. | – | – |
| 2.13 | [ČLOVĚK] | Vyřešit, proč classifier blokuje `docker compose up -d`/`--build` na spc-vm (viz nasazení 2026-09-17, workaround přes `build`+`cp`+`restart`) — bez toho nejde postavit spolehlivé CD. | – | – |
| 2.14 | [ČLOVĚK] | Zvolit CI platformu (GitHub Actions apod.), pak [AGENT-M] napsat workflow (lint, testy, build image). | – | 1.10, 2.1 |

**Exit** (beze změny): každý zápis má audit, integrační událost se při výpadku neztratí, existuje otestovaný restore, incident lze diagnostikovat z metrik/logů/trace.

---

## Fáze 3 — Production Execution MVP (roadmapa, rozpad na tikety až po Fázi 0)

Tahle fáze zavádí nové doménové entity (operace, dispatch, přihlášení
operátora) — jejich přesný tvar závisí na workshopu (0.2) a schválení rozsahu
(0.7). Než ten proběhne, nemá smysl navrhovat schéma. Orientační startovací
tikety, jakmile je scope jasný:

- [AGENT-M] Datový model `production_orders`/`operations`/`operation_runs` (Postgres, ne hypertable).
- [AGENT-M] Import/sync plánovaných zakázek z Cyclades přes `cyclades_adapter` (2.3).
- [AGENT-L] Endpointy start/pause/resume/complete operace + zápis do `audit_log` (2.8) a `outbox_events` (2.10).
- [AGENT-M] Jednoduchý operátorský frontend (i vanilla JS lze pro MVP, viz §12 hlavního dokumentu — nemá smysl blokovat na TypeScript migraci).

---

## Fáze 4–5 — Genealogie/kvalita, OEE/Andon/rollout

Beze změny oproti `MES_TARGET_ARCHITECTURE.md` §11 — příliš vzdálené na to,
aby dnešní rozpad na tikety měl cenu; přesné workflow kvality a údržby musí
přijít z workshopu. Až se k tomu dojde, rozpad udělat stejným stylem jako
Fáze 1–2 (malé, na file/tabulku zaměřené tikety s explicitními závislostmi).

---

## Poznámka k odhadům

Odhady S/M/L jsou pro jednu agentní implementační session, ne kalendářní čas
lidského týmu — časový rámec v §18 hlavního dokumentu (týmy 2 backend + 1
frontend) nepočítá s tím, že implementaci vedou AI agenti; po dokončení Fáze
0 je vhodné odhad přepočítat na "počet agentních sessions" místo
"člověkoměsíce".
