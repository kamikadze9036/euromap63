# Euromap63 Platform

Sbírá cyklová data ze vstřikovacího lisu (Krauss Maffei MC5) přes protokol
EUROMAP 63/SPI a ukládá je do TimescaleDB, s API a dashboardem pro zobrazení
aktuálních hodnot a trendů.

## Architektura

```
stroj (EUROMAP63/SPI, FTP klient)
        │  FTP (port 21 + PASV rozsah)
        ▼
  ftp (pure-ftpd, network_mode: host)
        │  sdílený volume "ftpdata"
        ▼
  collector (Python) ──► postgres (TimescaleDB) ◄── api (FastAPI) ◄── frontend (nginx dashboard)
```

- **ftp** — FTP server, na který se stroj připojuje. Musí posílat **UNIX styl**
  výpisu adresáře (`ls -l`) — starší KM Euromap63/SPI interpretery MS-DOS styl
  neparsují (viz historie projektu v `../euromap63-km-mc5-setup.md`).
  `pure-ftpd` to dělá nativně, žádné dodatečné nastavení není potřeba.
- **postgres** — TimescaleDB, tabulka `cycles` (hypertable) se společnými
  sloupci (`cycle_count`, `cycle_time_s`) a `JSONB` pro všechny ostatní
  parametry — připraveno na víc strojů s různou HW konfigurací bez migrace
  schématu.
- **collector** — při startu nahraje JOB šablony a jednorázově "vyzbrojí"
  stroj (`ABORT` → `EXECUTE REPORTS.JOB`), pak průběžně čte nové řádky z
  `REPORTS.DAT` do DB. Umí i rotaci `REPORTS.DAT`, když přeroste
  `ROTATE_SIZE_MB`.
- **api** — FastAPI. REST: `/api/machines`, `/api/machines/status` (stav
  běží/stojí z Cyclades MES, cache 5 s), `/api/cycles`, `/api/cycles/latest`,
  `/api/cycles/by-order`, `/api/cycles/by-label`, `/api/stats`. WebSocket:
  `/ws/cycles?machine=...` — push nového cyklu ihned po vložení do DB (žádný
  HTTP polling z prohlížeče). Jeden uvicorn worker (nutné kvůli in-memory
  WebSocket pub/subu — nezvyšovat `--workers` bez přepsání na sdílený broker).
- **frontend** — statický dashboard (žádné externí závislosti/CDN — funguje
  i bez přístupu na internet), dvě stránky:
  - `index.html` — "hala": grid karet všech aktivních strojů (stav, aktuální
    cyklus/OF/forma), živé hodnoty přes WebSocket.
  - `machine.html?code=KM-MC5-01` — detail stroje: karty, graf trendu
    (klikatelný výběr libovolného parametru), tabulka všech parametrů.

## Dohledatelnost (Cyclades MES integrace)

- Ke každému cyklu se ukládá `order_ref` (aktuální OF z Cyclades `SUIVPRO.dbo.[OF]`).
- `GET /api/cycles/by-order?order_ref=...` — všechny parametry pro danou zakázku.
- `GET /api/cycles/by-label?label=205909005` — dohledá OF podle čísla štítku
  (`GPAO_PVL_SAP.dbo.ETQGPAO`, rozsahy `ETQ_DEBUT..ETQ_FIN`), pak stejné jako výše.
- Vyžaduje `~/cyclades-db.env` na `spc-vm` (o úroveň výš než tento projekt) a
  `CYCLADES_MAC_REFMAC` v `.env` (např. `P2700-01`). Bez nich funguje sběr dat
  dál, jen bez `order_ref`/stavu stroje.

## Spuštění

```
cp .env.example .env
# uprav VM_PUBLIC_IP, FTP_PASSWORD, POSTGRES_PASSWORD, CYCLADES_MAC_REFMAC
docker compose up -d --build
```

Dashboard (hala): `http://<VM_IP>:8092`
Detail stroje: `http://<VM_IP>:8092/machine.html?code=KM-MC5-01`
API: `http://<VM_IP>:8091/api/health`

## Nastavení na straně stroje (Euromap63/SPI obrazovka)

- Adresa TCP/IP (cíl FTP) = `VM_PUBLIC_IP` z `.env`
- Přihlášení = `FTP_USER` / `FTP_PASSWORD` z `.env`
- Session/station ID pole nechat **prázdné** (stroj pak hledá `.REQ` v
  kořeni FTP složky, stejně jako v původním PC/IIS řešení)

## Síťová podmínka

Stroj musí mít TCP/IP dosažitelnost na `VM_PUBLIC_IP:21` a na PASV rozsah
(`FTP_PASV_MIN`–`FTP_PASV_MAX`, výchozí `30000-30009`). Pokud je stroj na
jiném síťovém segmentu/VLAN než VM (typické u OT/IT segregace v továrních
sítích), je potřeba u správce sítě/firewallu povolit průchod těchto portů
mezi segmenty — jinak `LIST`/`RETR`/`DELE` z pohledu stroje nikdy neprojdou,
i když je celý stack správně nasazený.

## Rozšíření na další stroj

1. Přidat záznam do tabulky `machines` (nebo nechat collector, ať ho vytvoří
   sám při prvním startu s jiným `MACHINE_CODE`).
2. Spustit druhou instanci `collector` (jiný `MACHINE_CODE`, jiný `FTP_ROOT`
   volume) — buď v tomto compose souboru jako další službu, nebo úplně
   samostatný `euromap63-docker` projekt, pokud má stroj vlastní FTP server.
3. `machine_parameters` a `cycles.params` (JSONB) automaticky pojmou jiný
   set parametrů bez úprav schématu.
