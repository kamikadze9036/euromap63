# Cílová architektura a plán rozvoje Euromap63 na MES

## 1. Účel dokumentu

Tento dokument popisuje cestu, jak ze současné platformy Euromap63 vytvořit
výrobní informační systém (MES), aniž by bylo nutné jednorázově nahrazovat
stávající Cyclades/ERP. Doporučeným přístupem je vybudovat nový MES jako
vrstvu nad současnými systémy, nejprve pro sběr a řízení výroby a následně pro
další oblasti, jako jsou genealogie, kvalita, OEE a údržba.

Dokument obsahuje:

- zhodnocení současného řešení;
- rozhodnutí, které části technologického stacku zachovat;
- cílové rozdělení odpovědností mezi Cyclades a nový MES;
- návrh cílové architektury a doménového modelu;
- etapový implementační plán s výstupy a exit kritérii;
- provozní, bezpečnostní a organizační požadavky;
- hlavní rizika a orientační časový rozsah.

Nejde o finální detailní specifikaci. Konkrétní workflow, integrační kontrakty
a akceptační kritéria musí být doplněny společně s výrobou, kvalitou,
technologií, údržbou, logistikou a IT/OT.

## 2. Strategické rozhodnutí

Zvolenou cestou je **MES jako nová výrobní vrstva nad Cyclades**, nikoliv
okamžitá kompletní náhrada Cyclades.

Tento přístup umožňuje:

- zachovat funkční plánovací, ERP a historické procesy;
- nasazovat nové funkce po jednotlivých pracovištích;
- ověřit přínos na pilotním stroji před plošným rolloutem;
- snížit riziko souběžné změny technologie i výrobních procesů;
- přebírat jednotlivé odpovědnosti z Cyclades postupně;
- kdykoliv zastavit rollout bez přerušení stávající výroby.

Zásadní podmínkou je zabránit vzniku dvou rovnocenných zdrojů pravdy. Pro
každou entitu a proces musí být určen jediný vlastník dat a jasný směr
synchronizace.

## 3. Současný stav

Euromap63 dnes tvoří funkční vertikální cestu od vstřikovacího lisu až po
uživatelský dashboard:

```text
Vstřikovací lis
    │ EUROMAP 63 / SPI přes FTP
    ▼
FTP server
    │ REPORTS.DAT, JOB a REQ soubory
    ▼
Python collector
    │
    ▼
PostgreSQL + TimescaleDB
    │
    ├── FastAPI REST API
    └── WebSocket aktualizace
            │
            ▼
       Webový dashboard
```

Platforma již poskytuje:

- sběr cyklových parametrů stroje;
- ukládání časových řad do TimescaleDB;
- přehled aktivních lisů a detail stroje;
- živou aktualizaci cyklů;
- grafy procesních parametrů;
- dohledání podle zakázky, štítku a balení;
- obohacování dat ze systému Cyclades;
- zobrazení stavu stroje, důvodu prostoje a zmetkovitosti;
- základní podporu více strojů v číselníku.

Současné řešení je dobrý základ pro machine monitoring a částečnou
dohledatelnost. Zatím však neobsahuje procesní a transakční funkce potřebné
pro plnohodnotný MES.

## 4. Silné stránky současného řešení

### 4.1 Funkční OT integrace

Byla prakticky ověřena komunikace se starším strojem Krauss Maffei MC5 přes
EUROMAP 63/SPI. To je hodnotnější než čistě teoretický návrh, protože již
byly vyřešeny reálné odlišnosti FTP komunikace, formátu souborů, zalamování
dlouhých řádků a parametrů konkrétního stroje.

### 4.2 Vhodné uložení telemetrie

TimescaleDB je vhodný pro cyklová data, časové řady, agregace, kompresi a
retention politiku. JSONB dovoluje ukládat rozdílné sady parametrů bez
migrace tabulky při každé změně konfigurace stroje.

### 4.3 Existující integrační znalost Cyclades

Repozitář již obsahuje znalost tabulek a vazeb pro:

- aktivní výrobní zakázku;
- stroje a formy;
- stav stroje a důvody zastavení;
- štítky a jejich rozsahy;
- výrobní deklarace a balení;
- zmetkovitost produktů a kavit.

Tyto znalosti lze uzavřít do samostatného integračního adaptéru a využít při
postupném přechodu na nový MES.

### 4.4 Jednoduché nasazení

Docker Compose a frontend bez externích CDN jsou praktické pro vývoj,
laboratorní prostředí a první tovární pilot v omezené síti.

## 5. Kritické mezery, které je nutné odstranit

### 5.1 Čas vzniku cyklu

Cyklus dnes získává čas při vložení do PostgreSQL. Nejde nutně o skutečný čas
výroby. Po výpadku collectoru nebo databáze může být více historických cyklů
načteno najednou a jejich časová osa se stlačí do okamžiku importu.

To může zkreslit:

- výpočet prostojů;
- OEE a výkon stroje;
- přiřazení zakázky;
- časovou genealogii výrobku;
- SPC a vyhodnocení trendů;
- vyšetřování reklamací.

Cílový záznam musí rozlišovat minimálně:

- `occurred_at` – čas události na stroji nebo nejpřesnější rekonstruovaný čas;
- `received_at` – čas přijetí edge gateway;
- `persisted_at` – čas uložení do centrální databáze;
- kvalitu a zdroj časového údaje.

### 5.2 Přiřazení zakázky k backlogu

Collector dnes zjistí aktuální zakázku v okamžiku zpracování a přiřadí ji
celé právě načítané dávce. Po výpadku se proto mohou starší cykly přiřadit k
novější zakázce.

Přiřazení se musí provádět podle časově verzovaného intervalu skutečného
provádění operace, nikoliv pouze podle aktuálního stavu Cyclades.

### 5.3 Idempotence a checkpointy

Checkpoint založený jen na počtu přečtených řádků není dostatečný. Je nutné
řešit:

- nedokončený poslední řádek souboru;
- nahrazení nebo zkrácení souboru;
- rotaci a opětovné použití názvu;
- reset počítadla cyklů;
- restart collectoru mezi zápisem cyklu a checkpointu;
- obnovu databáze ze zálohy;
- opakované doručení stejného souboru;
- ruční replay archivních dat.

Každý cyklus musí mít stabilní identitu. Zápis má být idempotentní a duplicitní
doručení nesmí vytvořit druhý výrobní záznam.

### 5.4 Multi-machine podpora

Současný `REPORTS.JOB` je pevně sestaven pro konkrétní konfiguraci. Šablony
`GETID` a `GETINFO` se na stroj nahrají, ale jejich výsledky nejsou zpracovány
do automatické konfigurace collectoru.

Pro plošný rollout je potřeba:

- protokolový adapter podle výrobce a generace stroje;
- verzovaná konfigurace dostupných parametrů;
- profil stroje a schopností;
- překlad proprietárního parametru na kanonickou veličinu;
- normalizace jednotek;
- validace, zda stroj požadovaný parametr skutečně poskytuje;
- nezávislé nasazení a sledování každého edge collectoru.

### 5.5 Bezpečnost

Současné API nemá autentizaci ani role a CORS povoluje všechny zdroje.
PostgreSQL je ve výchozím Compose vystavený na hostitelském portu. FTP u
starších strojů přenáší přihlašovací údaje otevřeně.

Pro MES jsou nutné:

- SSO přes OIDC;
- role a oprávnění podle provozu a pracoviště;
- audit čtení citlivých údajů a všech zápisových operací;
- TLS na IT straně;
- reverzní proxy a centrální správa relací;
- správa secretů mimo repozitář a běžné environment soubory;
- síťové oddělení OT, edge a IT aplikační vrstvy;
- omezení databázových a FTP portů firewallem;
- pravidelné aktualizace a skenování image a závislostí.

FTP může zůstat kvůli omezení starších strojů, ale pouze na izolované OT
hraně. Nemá se používat jako interní komunikační mechanismus MES.

### 5.6 Provozní připravenost

Před produkčním využitím je potřeba doplnit:

- řízené databázové migrace;
- automatické unit, integrační a end-to-end testy;
- CI/CD;
- logy, metriky, tracing a alerting;
- health a readiness kontroly všech komponent;
- zálohování a pravidelně testovaný restore;
- retention a kompresi telemetrie;
- řízení verzí image;
- dokumentovaný disaster recovery postup;
- kapacitní a dlouhodobé soak testy.

SQL skripty v `docker-entrypoint-initdb.d` jsou vhodné pro vytvoření nové
databáze, ale nenahrazují migrace již existujícího prostředí.

### 5.7 Škálování API

Současná živá komunikace používá in-memory správu WebSocket spojení a počítá
s jediným API procesem. S rostoucím počtem uživatelů a instancí je potřeba
sdílený distribuční mechanismus událostí.

Rovněž je potřeba odstranit opakované otevírání databázových spojení a N+1
dotazy při sestavování přehledu haly.

## 6. Pokrytí oblastí MES

| Oblast | Současný stav | Cílový stav |
|---|---|---|
| Sběr cyklových dat | Funkční prototyp | Auditně spolehlivý edge sběr |
| Vizualizace strojů | Funkční | Role-based provozní dashboardy |
| Dohledání zakázky a štítku | Částečné | Úplná časová a materiálová genealogie |
| Prostoje | Odvozené z mezer | Události, důvody, potvrzení operátorem |
| OEE | Chybí | Availability, Performance, Quality |
| Řízení zakázek | Čtení z Cyclades | Dispatch a execution operací |
| Směny a operátoři | Chybí | Přihlášení, odpovědnost, kompetence |
| Materiálové šarže a WIP | Chybí | Spotřeba, transformace a genealogie |
| Kvalita a SPC | Částečná zmetkovitost | Kontrolní plány, měření, alarmy, neshody |
| Receptury a změnové řízení | Chybí | Verze, schválení a porovnání se strojem |
| Údržba a Andon | Chybí | Hlášení, eskalace a vazba na údržbu |
| Uživatelé, role a audit | Chybí | SSO, RBAC a neměnná auditní stopa |

## 7. Posouzení technologického stacku

Přibližně 70 % současného technologického směru lze zachovat. Není nutné
měnit Python za Java nebo .NET pouze proto, že se systém rozšíří na MES.
Hlavním omezením dnes není programovací jazyk, ale datová sémantika,
spolehlivost, doménový model a provozní připravenost.

| Vrstva | Rozhodnutí | Potřebná změna |
|---|---|---|
| Python | Zachovat | Typování, testy, struktura balíčků a standardy |
| FastAPI | Zachovat | Modulární API, dependency injection, pooling a autorizace |
| PostgreSQL | Zachovat | Transakční MES data a integrační outbox |
| TimescaleDB | Zachovat | Telemetrie, časové agregace, komprese a retention |
| Docker Compose | Zachovat pro dev/pilot | Produkční topologii řešit podle požadované dostupnosti |
| EUROMAP collector | Přepracovat | Edge spool, čas, idempotence, replay a health |
| FTP | Omezit na OT edge | Izolovaná VLAN, firewall a unikátní účty |
| Vanilla JavaScript | Postupně nahradit | TypeScript a komponentový frontend pro MES workflow |
| In-memory WebSocket | Nahradit | Outbox a sdílená distribuční vrstva |
| Přímé SQL do Cyclades | Uzavřít | Samostatný read-only integrační adapter |
| JSONB | Zachovat pro raw data | Normalizované veličiny pro limity a analytiku |

### 7.1 Doporučené konkrétní technologie

Technologie je vhodné definitivně vybrat v ADR, předběžný směr je:

- FastAPI a Pydantic pro API a integrační kontrakty;
- SQLAlchemy 2 nebo obdobná repository vrstva;
- Psycopg 3 s connection poolem;
- Alembic pro databázové migrace;
- PostgreSQL pro MES transakce;
- TimescaleDB pro telemetry;
- Postgres outbox pro spolehlivé integrační události;
- Redis Streams nebo NATS až při potřebě více API/worker instancí;
- TypeScript a React nebo Vue pro operátorské a administrativní workflow;
- OIDC napojené na firemní identity, například Entra ID nebo Keycloak;
- OpenTelemetry, Prometheus a Grafana pro observabilitu;
- centralizované logy, například Loki nebo ekvivalent firemního standardu;
- S3 kompatibilní objektové úložiště pro raw soubory a exporty, pokud je v
  infrastruktuře dostupné.

Kafka ani Kubernetes nejsou podmínkou prvního MES releasu. Mají být zavedeny
jen tehdy, pokud jejich provozní náklady ospravedlní skutečné požadavky na
objem, dostupnost nebo oddělené týmy.

## 8. Cílové vlastnictví dat

Výchozí rozdělení odpovědností:

| Oblast | Počáteční vlastník | Budoucí směr |
|---|---|---|
| Produkty a kusovníky | Cyclades/ERP | Synchronizovaná master data |
| Plánované zakázky | Cyclades/ERP | Cyclades zůstává plánovacím systémem |
| Stroje a formy | Cyclades + MES mapování | MES provozní digitální dvojče |
| Strojní telemetry | MES | MES je jediný vlastník |
| Provádění operace | Zpočátku Cyclades | Postupně MES |
| Směny a přihlášení operátorů | MES/IAM | MES s referencí na firemní identitu |
| Štítky a balení | Zpočátku Cyclades | Převzetí jen při jasném přínosu |
| Prostoje a OEE | MES | MES je jediný vlastník |
| Kvalita a SPC | MES | Výsledky předávat ERP/Cyclades podle potřeby |
| Materiálové šarže a genealogie | Postupný přechod | MES provozní genealogie, ERP účetní sklad |
| Účetní a skladové pohyby | ERP/Cyclades | MES pouze iniciuje nebo potvrzuje událost |

Při každé změně vlastnictví musí existovat přechodové období, reconciliation
report a možnost návratu k předchozímu procesu.

## 9. Cílová architektura

```text
┌──────────────────────────────────────────────────────────────┐
│ OT síť                                                       │
│                                                              │
│  Stroje ── EUROMAP 63 / OPC UA / další protokoly             │
│      │                                                       │
│      ▼                                                       │
│  Edge gateway                                                │
│  - protokolové adaptéry                                      │
│  - lokální durable spool                                     │
│  - raw archiv                                                │
│  - validace, idempotence a replay                            │
│  - heartbeat a diagnostika                                   │
└───────────────────────────┬──────────────────────────────────┘
                            │ zabezpečený přenos událostí
┌───────────────────────────▼──────────────────────────────────┐
│ IT / MES aplikační vrstva                                    │
│                                                              │
│  MES backend – modulární monolit                             │
│  - Assets & Capabilities                                     │
│  - Orders & Operations                                       │
│  - Production Execution                                      │
│  - Materials & Genealogy                                     │
│  - Downtime & OEE                                            │
│  - Quality & SPC                                             │
│  - Recipes & Changeovers                                     │
│  - Identity, Authorization & Audit                           │
│                                                              │
│       ┌──────────────────┐      ┌──────────────────────┐      │
│       │ PostgreSQL       │      │ TimescaleDB          │      │
│       │ MES transakce    │      │ telemetry            │      │
│       └──────────────────┘      └──────────────────────┘      │
│                                                              │
│  Integration adapter + outbox                                │
└───────────────────────────┬──────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
          Cyclades         ERP       Tisk / sklady / BI
```

### 9.1 Proč modulární monolit

Pro první fáze je vhodnější modulární monolit než síť mikroservis:

- jednodušší transakce mezi MES oblastmi;
- menší provozní režie;
- rychlejší změny doménového modelu;
- jednodušší lokální vývoj a testování;
- jasné hranice lze zachovat v modulech a později vybrané části oddělit.

Samostatnými procesy nebo službami mají od začátku být collectory, integrační
workery a případně výpočetně náročné reporty.

### 9.2 Event a integrační model

Interní moduly mají publikovat obchodní události, například:

- `machine.cycle_recorded`;
- `machine.state_changed`;
- `production_order.dispatched`;
- `operation.started`;
- `operation.paused`;
- `operation.completed`;
- `material.consumed`;
- `container.completed`;
- `downtime.started` a `downtime.ended`;
- `quality_result.recorded`;
- `nonconformance.opened`.

Událost se má uložit atomicky s doménovou transakcí do outbox tabulky.
Integrační worker ji následně doručí okolním systémům. Opakované doručení musí
být bezpečné.

## 10. Návrh klíčových domén

### 10.1 Organizace a zařízení

- site, area, line a work center;
- machine/asset a jeho schopnosti;
- forma, nástroj, kavita a pomocné zařízení;
- protokolový endpoint a collector instance;
- kalendář dostupnosti a směny.

### 10.2 Výrobní master data

- produkt a jeho revize;
- technologický postup a operace;
- kusovník nebo materiálové požadavky;
- receptura a procesní specifikace;
- předepsaná forma, stroj nebo skupina schopností;
- cílový cyklus, kavita a plánovaná zmetkovitost.

### 10.3 Production Execution

- výrobní zakázka;
- operace zakázky;
- dispatch do fronty pracoviště;
- execution/run konkrétní operace;
- start, pause, resume a complete;
- přihlášený operátor a směna;
- odvedené dobré kusy, zmetky a rework;
- changeover a potvrzení připravenosti.

### 10.4 Materiály a genealogie

- materiál a šarže;
- umístění a mezisklad;
- naložení šarže do stroje nebo násypky;
- časové intervaly spotřeby;
- vzniklý WIP, kus, kontejner a paleta;
- vztah parent/child mezi vstupy a výstupy;
- vazba na cykly a výrobní operaci.

### 10.5 Kvalita

- kontrolní plán;
- charakteristika a měřicí metoda;
- specifikační a regulační meze;
- měření a automatické procesní veličiny;
- SPC pravidla a alarm;
- neshoda, containment, blokace a uvolnění;
- rework nebo scrap disposition;
- elektronické schválení a audit.

### 10.6 Prostoje a OEE

- stavový interval stroje;
- plánovaný a neplánovaný prostoj;
- strom důvodů;
- automatický návrh důvodu a potvrzení operátorem;
- plánovaný výrobní čas;
- Availability, Performance a Quality;
- důvěryhodnost a původ každého vstupu do výpočtu.

## 11. Etapový implementační plán

### Fáze 0 – Produktové hranice a architektura

**Cíl:** určit přesný rozsah prvního releasu a vztah k Cyclades.

Aktivity:

1. Zmapovat skutečný tok zakázky od plánování přes výrobu až po balení.
2. Zmapovat výjimky: změna zakázky, přestavba, zmetek, rework, výpadek sítě,
   ruční odvádění a oprava chyby.
3. Popsat role operátora, mistra, kvality, technologa, plánovače, údržby a
   administrátora.
4. Vytvořit matici vlastnictví dat Cyclades × MES.
5. Definovat identifikátory stroje, formy, produktu, operace, zakázky,
   materiálové šarže, kontejneru a osoby.
6. Vybrat pilotní stroj, produkt a reprezentativní zakázku.
7. Sepsat integrační kontrakty a architektonická rozhodnutí.
8. Připravit backlog s obchodními akceptačními kritérii.

Výstupy:

- procesní mapy současného a cílového stavu;
- ownership matice;
- kontextový a komponentový diagram;
- glossary a identifikační pravidla;
- prioritizovaný backlog pilotu;
- měřitelné KPI pilotu.

Exit kritérium:

Všechny zúčastněné strany se shodnou, která data a workflow vlastní první
release MES a která nadále zůstávají v Cyclades.

### Fáze 1 – Důvěryhodná datová základna

**Cíl:** zajistit, že výpadek ani replay nezmění význam výrobních dat.

Aktivity:

1. Zavést skutečný nebo rekonstruovaný `occurred_at` a samostatný čas přijetí.
2. Navrhnout stabilní `cycle_id` a unikátní idempotency klíč.
3. Nahradit řádkový checkpoint robustním souborovým checkpointem.
4. Bezpečně zpracovávat pouze kompletní řádky.
5. Ukládat raw vstupní soubory s checksumem a metadaty.
6. Doplnit lokální durable spool při nedostupnosti centrálního systému.
7. Implementovat řízený replay a reconciliation report.
8. Přiřazovat zakázku podle časového intervalu execution.
9. Zavést heartbeat, lag, poslední přijatý cyklus a diagnostiku collectoru.
10. Zavést Alembic migrace a testovací databázi.
11. Přidat parserové, integrační a recovery testy.

Exit kritéria:

- restart stroje, collectoru nebo databáze neztratí ani nezdvojí cyklus;
- backlog se přiřadí ke správné zakázce;
- raw data lze znovu přehrát do prázdné databáze;
- systém automaticky odhalí přerušený sběr a upozorní obsluhu;
- testy pokrývají rotaci, částečný řádek, reset počítadla a obnovu ze zálohy.

### Fáze 2 – Platforma MES

**Cíl:** vytvořit bezpečný a provozovatelný základ pro zápisová workflow.

Aktivity:

1. Zavést modulární strukturu backendu.
2. Přidat OIDC/SSO, role a oprávnění podle provozu.
3. Implementovat neměnnou auditní stopu.
4. Verzovat API a integrační kontrakty.
5. Přidat connection pooling a odstranit N+1 dotazy.
6. Zavést outbox a integrační worker.
7. Přesunout přímé Cyclades dotazy do samostatného adaptéru.
8. Zavést centralizované logy, metriky, tracing a alerty.
9. Definovat backup, restore, retention a disaster recovery.
10. Zavést CI/CD, security scanning a řízené release.

Exit kritéria:

- každý zápis má uživatele, čas, důvod a audit;
- integrační událost se při výpadku neztratí;
- existuje testovaný restore prostředí;
- incident lze diagnostikovat z metrik, logů a trace bez přístupu do
  kontejneru.

### Fáze 3 – Production Execution MVP

**Cíl:** provést jednu pilotní zakázku v MES od dispatch po dokončení.

Funkce:

- import a synchronizace plánovaných zakázek z Cyclades;
- fronta zakázek pro pracoviště;
- zahájení, přerušení, obnovení a dokončení operace;
- přihlášení operátora a určení směny;
- přihlášení stroje, formy a pomocného zařízení;
- kontrola připravenosti a changeover checklist;
- automatické počítání cyklů;
- potvrzení dobrých kusů, zmetků a reworku;
- povinné důvody prostojů a zmetků;
- aktuální WIP a stav operace;
- operátorský terminál a obrazovka mistra;
- řízené předání výsledků zpět do Cyclades.

Exit kritéria:

- pilotní zakázku lze kompletně provést a dohledat bez pomocného Excelu;
- součet dobrých kusů, zmetků a WIP lze reconciliovat s Cyclades;
- při výpadku integrace lze ve výrobě bezpečně pokračovat podle definovaného
  offline režimu;
- odpovědnost za každou ruční změnu je dohledatelná.

### Fáze 4 – Genealogie a kvalita

**Cíl:** dohledat vstupní materiál, procesní podmínky, výsledek a balení.

Funkce:

- příjem a výběr materiálových šarží;
- skenování naložení materiálu do stroje nebo násypky;
- časové intervaly spotřeby šarže;
- vazba cyklus → výrobek → kontejner → štítek → zakázka → materiál;
- procesní specifikace podle produktu, formy a receptury;
- automatická kontrola limitů;
- SPC a pravidla trendů;
- kontrolní plány a ruční měření;
- neshody, blokace, containment, uvolnění a rework;
- elektronické pracovní instrukce;
- export traceability reportu pro reklamaci.

Exit kritéria:

- ze štítku lze dohledat všechny relevantní vstupní šarže a procesní cykly;
- ze vstupní šarže lze dohledat všechna dotčená balení;
- překročení kritického limitu vytvoří řízenou událost kvality;
- uvolnění blokovaného materiálu vyžaduje odpovídající oprávnění a audit.

### Fáze 5 – OEE, Andon a širší rollout

**Cíl:** standardizovat řízení výkonu a rozšířit řešení na další pracoviště.

Funkce a aktivity:

- plánované kalendáře, směny a odstávky;
- OEE se zdokumentovaným původem vstupů;
- důvodové stromy a automatický návrh prostoje;
- Andon a eskalační pravidla;
- vazba na údržbu;
- changeover analýza;
- rollout dalších lisů a protokolů;
- kapacitní, zátěžové a dlouhodobé testy;
- high availability podle skutečné potřeby výroby;
- pravidelné disaster recovery cvičení;
- postupné přebírání dalších funkcí Cyclades.

## 12. Frontend a uživatelské aplikace

Současný vanilla JavaScript dashboard lze během Fází 1 a 2 zachovat. Nemá
smysl blokovat datovou spolehlivost kompletním redesignem.

Pro Production Execution však bude potřeba komponentový frontend s:

- TypeScriptem;
- jednotným design systémem;
- řízením formulářů a validací;
- lokalizací;
- automatickými UI testy;
- podporou scannerů a dotykových terminálů;
- režimem s omezeným připojením;
- oddělenými obrazovkami podle role.

Minimální aplikace budou:

- operátorský terminál;
- přehled haly a dashboard mistra;
- pracoviště kvality;
- konfigurace technologa;
- administrace master dat a integrací;
- traceability/reklamační vyhledávání.

## 13. Provozní topologie

### 13.1 Vývoj a test

Docker Compose zůstává vhodný pro lokální vývoj a integrační testy. Testovací
prostředí má obsahovat simulátor stroje a simulátor Cyclades kontraktů.

### 13.2 Pilot

Pilot může běžet na jednom průmyslovém nebo virtuálním hostu, pokud má:

- monitorovaný disk a UPS;
- automatické zálohy mimo hostitele;
- omezené síťové porty;
- restart a health management;
- vzdálené logy a metriky;
- zdokumentovaný manuální fallback výroby.

### 13.3 Produkce

Produkční topologie se má odvodit od požadovaného RTO a RPO. Kubernetes není
automatický požadavek. Pro jeden závod může být jednodušší provozovat dvě
aplikační instance, spravovaný nebo HA PostgreSQL cluster a samostatné edge
gateway. Důležitější než orchestrátor je testovaná obnova, monitoring a
jasná odpovědnost za pohotovost.

## 14. Testovací strategie

Minimální testovací vrstvy:

1. Unit testy parserů, převodů jednotek a doménových pravidel.
2. Property-based a fuzz testy vstupních souborů.
3. Integrační testy s PostgreSQL/TimescaleDB.
4. Contract testy vůči Cyclades adaptéru.
5. Testy idempotence a opakovaného doručení.
6. Recovery testy výpadku sítě, DB a collectoru.
7. End-to-end test pilotního výrobního scénáře.
8. Zátěžové a soak testy.
9. Test obnovy ze zálohy.
10. Uživatelské akceptační testy přímo s výrobou.

Pro každý incident, který způsobil ztrátu nebo nesprávnou interpretaci dat,
má vzniknout regresní test.

## 15. Bezpečnost a audit

MES bude obsahovat výrobní know-how, osobní identifikátory operátorů a data
důležitá pro kvalitu. Bezpečnost proto není samostatná finální fáze.

Minimální požadavky:

- least-privilege oprávnění;
- oddělené účty pro stroje, collectory, API a integrace;
- rotace přihlašovacích údajů;
- read-only přístup k Cyclades, dokud není schválen zápisový kontrakt;
- ochrana proti změně nebo smazání auditních záznamů;
- audit změny receptury, zakázky, množství, důvodu prostoje a rozhodnutí
  kvality;
- sledování neúspěšných přihlášení a podezřelých exportů;
- zálohy šifrované a oddělené od produkčního hostu;
- dokumentované řešení zranitelností a aktualizací.

## 16. KPI úspěchu pilotu

Technická KPI:

- žádná ztráta ani duplicita cyklu v definovaných recovery scénářích;
- známý a monitorovaný ingest lag;
- úplnost cyklových dat nad dohodnutou hranicí;
- úspěšný restore v rámci dohodnutého RTO/RPO;
- dohledatelný původ každé ruční změny;
- reconciliation zakázky mezi MES a Cyclades bez nevysvětleného rozdílu.

Provozní KPI:

- zkrácení času dohledání údajů pro reklamaci;
- snížení ručního přepisování dat;
- zvýšení podílu prostojů s potvrzeným důvodem;
- zkrácení času reakce na procesní odchylku;
- snížení počtu pomocných Excelů a lokálních evidencí;
- přijetí systému operátory bez zpomalení taktu.

## 17. Hlavní rizika

| Riziko | Dopad | Mitigace |
|---|---|---|
| Dvojí zdroj pravdy | Rozdílné stavy MES a Cyclades | Ownership matice, outbox, reconciliation |
| Chybný čas cyklu | Nesprávná genealogie a OEE | `occurred_at`, synchronizace času, quality flag |
| Nestabilní starší protokol | Výpadky sběru | Edge spool, raw archiv, replay a monitoring |
| Příliš široký první release | Dlouhá implementace bez přínosu | Jeden pilotní tok a tvrdý MVP scope |
| Přepis funkčního frontendu příliš brzy | Zdržení datové vrstvy | Migrovat UI až s MES workflow |
| Přehnaná mikroservisní architektura | Provozní složitost | Modulární monolit a měřené důvody pro dělení |
| Závislost na znalosti Cyclades DB | Křehké integrace | Adapter, contract testy, dokumentace a read-only účet |
| Nízké přijetí operátory | Obcházení systému | Společný návrh, ergonomie, scanner-first workflow |
| Nekvalitní master data | Chybné dispatch a traceability | Validace, datové stewardship a reconciliation |

## 18. Orientační časový a kapacitní rámec

Pro tým přibližně:

- dva backend/integration vývojáři;
- jeden frontend vývojář;
- částečná kapacita OT/DevOps specialisty;
- product owner se znalostí výroby;
- pravidelná dostupnost klíčových uživatelů;

lze orientačně očekávat:

| Milník | Orientační rozsah |
|---|---|
| Fáze 0 – scope a architektura | 2–4 týdny |
| Fáze 1 – spolehlivá datová základna | 6–10 týdnů |
| Fáze 2 – MES platforma | částečně paralelně, 6–10 týdnů |
| První Production Execution MVP | přibližně 4–6 měsíců od startu |
| MES pro jednu výrobní oblast | přibližně 9–15 měsíců |

Odhad se musí zpřesnit po Fázi 0. Největší vliv mají kvalita master dat,
dostupnost integračních rozhraní, množství výjimek ve výrobním procesu a
požadovaná validační úroveň.

## 19. Nejbližší konkrétní kroky

1. Jmenovat product ownera za výrobu a technického vlastníka MES.
2. Vybrat pilotní stroj, produkt a zakázkový scénář.
3. Uspořádat workflow workshop s výrobou, kvalitou, technologií, logistikou,
   údržbou a IT/OT.
4. Dokončit ownership matici Cyclades × MES.
5. Definovat `occurred_at`, `cycle_id`, idempotency klíč a pravidla replaye.
6. Převést SQL změny na řízené migrace.
7. Vytvořit automatický testovací dataset a simulátor REPORTS.DAT.
8. Zavést CI a základní observabilitu collectoru.
9. Navrhnout modulární strukturu MES backendu a první doménové tabulky.
10. Rozdělit Fázi 1 na implementovatelné issues s akceptačními kritérii.

Prioritou není přidat co nejrychleji další dashboardy. Prioritou je vytvořit
důvěryhodnou výrobní časovou osu a bezpečný integrační základ, na kterém lze
postavit OEE, genealogii a řízení výroby bez pozdějšího přepisování.
