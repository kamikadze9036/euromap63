#!/usr/bin/env python3
"""
Replay skript pro archivovany REPORTS.DAT (MES_IMPLEMENTATION_BACKLOG.md
tiket 1.6, viz MES_TARGET_ARCHITECTURE.md §5.3 "rucni replay archivnich
dat", "obnovu databaze ze zalohy", "opakovane doruceni stejneho souboru").

Ucel: disaster recovery, rekonciliace po incidentu, nebo znovu-postaveni
testovaci/staging DB ze skutecnych historickych dat. Toto je STANDALONE
operacni nastroj urceny k rucnimu spusteni clovekem/orchestrujici session,
NE neco, co by collector.py volal automaticky ve sve hlavni smycce.

Vstup je jeden konkretni ARCHIVOVANY soubor "REPORTS.DAT.<timestamp>" -
tedy soubor, ktery uz collector.py::maybe_rotate() prejmenoval a zaznamenal
do tabulky "reports_dat_archive" (tiket 1.5, viz
postgres/init/21_add_reports_dat_archive.sql). Tento skript NEUPRAVUJE ani
nepremistuje archivovany soubor - jen ho cte.

Bezpecnostni zasady (v tomto poradi, VZDY):

  1. Integritni kontrola JAKO PRVNI, pred jakymkoliv dotykem DB pro zapis:
     spocita se sha256 souboru (collector.py::_sha256_file - znovupouzito,
     ne prepsano) a porovna se s hodnotou zaznamenanou v
     "reports_dat_archive" pro dany "archive_path". Pri nesouhlasu skript
     bez --skip-checksum-verify VZDY odmitne pokracovat - to je cely smysl
     toho, ze tiket 1.5 checksum vubec zaznamenava. --skip-checksum-verify
     je urcen jen pro vzacne legitimni pripady (napr. replay souboru, ktery
     vznikl pred tiketem 1.5 a zadny zaznam v "reports_dat_archive" nikdy
     nemel).

  2. Vychozi rezim je VZDY dry run (jen vypise, co by se stalo - kolik
     radku bylo naparsovano, kolik by bylo genuinne novych a kolik uz
     zaznamenanych duplicit - bez jakehokoliv zapisu do DB). Skutecny zapis
     vyzaduje explicitni --apply. Duvod: tohle je destruktivne-podobna
     operace (zapis historickych dat do skutecne DB, kterou muze spoustet
     clovek proti produkci) - bezpecny vychozi stav je tu dulezitejsi nez
     pohodli.

  3. Insert prochazi PRESNE stejnym "cycle_identity" gatekeeperem jako
     collector.py::read_new_cycles() (INSERT INTO cycle_identity ...
     ON CONFLICT DO NOTHING RETURNING 1, INSERT do "cycles" jen pri
     uspechu) - opakovany replay stejneho archivu, nebo replay archivu do
     DB, ktera uz nektere z jeho cyklu ma (napr. castecna obnova do stejne
     DB, do ktere stroj porad zive sbira data), nesmi vytvorit duplikaty.

Co skript VYPLNI a jak (dulezite rozdily oproti zivemu sberu):

  - received_at: cas TOHOTO REPLAY BEHU (ne cas, kdy collector puvodne
    radek prijal - tahle informace je po archivaci nenavratne ztracena,
    collector.py drzi received_at jen v pameti behem jednoho pollu, ne v
    archivovanem souboru). Cestne oznaceno v reconciliation reportu.
  - occurred_at / occurred_at_source: pres collector.py::reconstruct_
    occurred_at() - stejna funkce jako pri zivem sberu, cely archiv se ale
    (na rozdil od zive smycky, ktera pracuje po malych davkach jednoho
    pollu) rekonstruuje jako JEDNA velka davka ukotvena na received_at
    tohoto behu. U velkych archivu proto nejstarsi cykly nesou extrapolaci
    o mnoho cyklu zpet - stejny predpoklad jako u ziveho sberu (kazdy
    cyklus nastal cycle_time_s pred nasledujicim), jen podstatne delsi
    retezec, protoze archivovany soubor uz nema hranice jednotlivych pollu.
    Neni to chyba tohoto skriptu, je to dusledek toho, ze archivace hranice
    pollu nezachovava.
  - order_ref: VZDY NULL. Tento skript nema zadne zive spojeni na Cyclades
    (get_active_order() v collector.py pracuje s AKTUALNIM stavem Cyclades
    v okamziku pollu) - hadat, ktera zakazka bezela v okamziku vzniku
    historickeho cyklu, by bylo nespolehlive a tento skript to zamerne
    nedela (viz tiket 1.7, casove verzovane prirazeni zakazky, ktery tohle
    resi jinak a je mimo rozsah tohoto tiketu).

Priklady pouziti:

  # Zjistit, jake archivy pro dany stroj DB zna:
  python scripts/replay_reports_dat.py --list-archives \\
      --machine-code KM-MC5-01

  # Suchy beh (nic nezapise, jen vypise reconciliation report):
  python scripts/replay_reports_dat.py \\
      --machine-code KM-MC5-01 \\
      --archive-path /ftpdata/REPORTS.DAT.20260910T120000

  # Skutecny zapis:
  python scripts/replay_reports_dat.py \\
      --machine-code KM-MC5-01 \\
      --archive-path /ftpdata/REPORTS.DAT.20260910T120000 \\
      --apply

  # Replay souboru, ktery predchazi tiketu 1.5 (nikdy nebyl zaznamenan
  # v reports_dat_archive) - pouzit jen pokud opravdu vite, co delate:
  python scripts/replay_reports_dat.py \\
      --machine-code KM-MC5-01 \\
      --archive-path /ftpdata/REPORTS.DAT.20260101T000000 \\
      --skip-checksum-verify --apply

DATABASE_URL se (stejne jako v collector.py a api/main.py) bere z
prostredi, pokud neni zadano --database-url.
"""
import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# collector.py na urovni modulu dela `DATABASE_URL = os.environ["DATABASE_URL"]`
# - to by bez nasledujiciho radku shodilo `import collector` jeste pred tim,
# nez tenhle skript stihne zpracovat --database-url. Tenhle skript ale
# collectorovo modulove DATABASE_URL/get_conn()/MACHINE_CODE vubec nepouziva
# (ty patri jen zive collector smycce) - vlastni pripojeni si otevira sam
# pres psycopg2.connect(args.database_url) nize. Zachovavame proto skutecnou
# hodnotu z prostredi PRED timhle "shim" pro pripadny fallback --database-url,
# stejny trik jako collector/tests/conftest.py pouziva pro testy.
_REAL_DATABASE_URL_ENV = os.environ.get("DATABASE_URL")
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")

_COLLECTOR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "collector"
)
if _COLLECTOR_DIR not in sys.path:
    sys.path.insert(0, _COLLECTOR_DIR)

import psycopg2  # noqa: E402

from collector import (  # noqa: E402
    _sha256_file,
    is_data_line,
    merge_wrapped_lines,
    reconstruct_occurred_at,
    split_csv_line,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("replay-reports-dat")


class ChecksumVerificationError(Exception):
    """Integritni kontrola archivu selhala (chybejici nebo neshodujici se
    zaznam v reports_dat_archive) a --skip-checksum-verify nebyl zadan."""


@dataclass
class ReplayReport:
    archive_path: str
    machine_code: str
    applied: bool
    checksum_verified: bool
    total_data_lines: int
    parsed_ok: int
    malformed_skipped: int
    new_count: int
    duplicate_count: int
    # Rozsah occurred_at cyklu z tohoto archivu, ktere jsou (po --apply)
    # skutecne v cilove DB, NEBO (v dry run rezimu) uz tam byly PRED timto
    # behem (podmnozina "duplicate_count").
    existing_occurred_at_min: Optional[datetime] = None
    existing_occurred_at_max: Optional[datetime] = None
    # Jen v dry run rezimu: rozsah occurred_at, ktery by dostaly genuinne
    # nove radky, KDYBY se s --apply skutecne zapsaly (vypocteno touto
    # session, nic se nezapisuje) - viz modul docstring o extrapolaci u
    # velkych archivu.
    preview_new_occurred_at_min: Optional[datetime] = None
    preview_new_occurred_at_max: Optional[datetime] = None


def get_conn(database_url):
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    return conn


def list_archives(conn, machine_code):
    """--list-archives: vypise vse, co reports_dat_archive zna pro dany
    stroj, aby si clovek mohl vybrat archiv bez znalosti presneho
    nazvu/timestampu souboru."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT archive_path, archived_at, line_count, size_bytes, sha256 "
            "FROM reports_dat_archive WHERE machine_code=%s "
            "ORDER BY archived_at DESC",
            (machine_code,),
        )
        rows = cur.fetchall()

    if not rows:
        print(f"Zadny archiv pro machine_code={machine_code!r} nenalezen v reports_dat_archive.")
        return

    print(f"Archivy pro machine_code={machine_code!r} ({len(rows)}):")
    for archive_path, archived_at, line_count, size_bytes, sha256_hex in rows:
        print(
            f"  {archive_path}\n"
            f"      archived_at={archived_at}  line_count={line_count}  "
            f"size_bytes={size_bytes}  sha256={sha256_hex}"
        )


def _lookup_archive_record(conn, archive_path):
    """Najde radek v reports_dat_archive pro dany archive_path. Zkousi
    presnou shodu a pote (kdyby uzivatel zadal relativni/jinak zapsanou
    cestu nez tu, kterou collector.py ulozil) os.path.abspath() variantu.
    Vraci (machine_code, sha256, line_count, size_bytes, archived_at) nebo
    None.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT machine_code, sha256, line_count, size_bytes, archived_at "
            "FROM reports_dat_archive WHERE archive_path=%s",
            (archive_path,),
        )
        row = cur.fetchone()
    if row is not None:
        return row

    abspath = os.path.abspath(archive_path)
    if abspath == archive_path:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT machine_code, sha256, line_count, size_bytes, archived_at "
            "FROM reports_dat_archive WHERE archive_path=%s",
            (abspath,),
        )
        return cur.fetchone()


def verify_checksum(conn, archive_path, skip_checksum_verify=False):
    """Integritni kontrola JAKO PRVNI (viz modul docstring bod 1). Vraci
    True, pokud kontrola probehla a souhlasila; False, pokud byla
    (explicitne) preskocena. Vyhodi ChecksumVerificationError, kdyz
    kontrola selhala a preskoceni nebylo pozadano.
    """
    if skip_checksum_verify:
        log.warning(
            "Integritni kontrola checksum PRESKOCENA (--skip-checksum-verify) pro %s.",
            archive_path,
        )
        return False

    record = _lookup_archive_record(conn, archive_path)
    if record is None:
        raise ChecksumVerificationError(
            f"Zadny zaznam v reports_dat_archive pro archive_path={archive_path!r} - "
            "nelze overit integritu, odmitam pokracovat. Pokud jde o soubor "
            "predchazejici tiketu 1.5 (nikdy nezaznamenany), pouzijte vedome "
            "--skip-checksum-verify."
        )

    recorded_machine_code, recorded_sha256, _line_count, _size_bytes, _archived_at = record
    actual_sha256 = _sha256_file(archive_path)
    if actual_sha256 != recorded_sha256:
        raise ChecksumVerificationError(
            f"Checksum souboru {archive_path!r} NESOUHLASI se zaznamem v "
            f"reports_dat_archive (recorded={recorded_sha256}, actual={actual_sha256}). "
            "Soubor mohl byt po archivaci zmenen/poskozen - odmitam pokracovat. "
            "Pouzijte --skip-checksum-verify jen pokud jste si jisti, ze je to "
            "v poradku."
        )

    log.info("Checksum souboru %s souhlasi se zaznamem v reports_dat_archive.", archive_path)
    return True


def parse_archive_file(path):
    """Rozparsuje archivovany REPORTS.DAT.<timestamp> na (header, data_lines)
    - hlavicka+datove radky maji stejnou strukturu jako zivy REPORTS.DAT,
    vcetne lamani dlouhych radku na pokracovaci radky (merge_wrapped_lines)
    a rozlisovani hlavicky od dat (is_data_line) - viz collector.py, ktereho
    tyhle funkce znovupouzivame, aby se parsovaci logika nikdy nerozjela do
    dvou nezavislych implementaci (viz historie oprav wrap/header parsovani
    v collector.py).

    Na rozdil od collector.py::read_new_cycles() tu NENI zadny "holdback"
    posledniho radku - to reseni je specificke pro ZIVY, prave dopisovany
    soubor (stroj muze byt uprostred zapisu posledniho radku). Archivovany
    soubor uz je uzavreny (rotace ho prejmenovala az PO ABORT.JOB), takze
    jeho posledni radek uz je definitivni - poskozeny posledni radek archivu
    je proste trvale poskozeny radek, ne docasny stav.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    if not lines:
        return [], []

    lines = merge_wrapped_lines(lines)

    header_line_count = 0
    header_parts = []
    for i, line in enumerate(lines):
        if i > 0 and is_data_line(line):
            break
        header_parts.append(line)
        header_line_count += 1
    header = split_csv_line("".join(header_parts))
    data_lines = lines[header_line_count:]
    return header, data_lines


def parse_data_lines(header, data_lines):
    """Rozparsuje kazdy datovy radek na {"cycle_count", "cycle_time_s",
    "params"} - stejna extrakce jako uvnitr collector.py::read_new_cycles()
    (ActCntCyc/ActTimCyc). Neni to samostatna, znovupouzitelna funkce v
    collector.py, takhle par radku duplikovat je bezpecnejsi nez kvuli
    tomuto skriptu refaktorovat zivou collector.py cestu (viz zadani
    tiketu - collector.py se nema menit).

    Vraci (parsed_rows, malformed_skipped_count).
    """
    parsed_rows = []
    malformed_skipped = 0
    for line in data_lines:
        values = split_csv_line(line)
        if len(values) != len(header):
            log.warning("Preskakuji poskozeny radek archivu (pocet sloupcu nesedi): %r", line)
            malformed_skipped += 1
            continue

        row = dict(zip(header, values))
        try:
            cycle_count = int(float(row.get("ActCntCyc", "nan")))
        except ValueError:
            log.warning("Radek bez platneho ActCntCyc, preskakuji: %r", line)
            malformed_skipped += 1
            continue

        try:
            cycle_time = float(row["ActTimCyc"]) if row.get("ActTimCyc") else None
        except ValueError:
            cycle_time = None

        parsed_rows.append({
            "cycle_count": cycle_count,
            "cycle_time_s": cycle_time,
            "params": row,
        })
    return parsed_rows, malformed_skipped


def _existing_cycle_counts(conn, machine_code, cycle_counts):
    """Ktere z techto cycle_count uz cycle_identity zna pro dany stroj -
    cisty SELECT, zadny zapis (pouziva jak dry-run klasifikace, tak
    reconciliation report po --apply)."""
    if not cycle_counts:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cycle_count FROM cycle_identity WHERE machine_code=%s "
            "AND cycle_count = ANY(%s)",
            (machine_code, list(cycle_counts)),
        )
        return {row[0] for row in cur.fetchall()}


def _occurred_at_range(conn, machine_code, cycle_counts):
    if not cycle_counts:
        return (None, None)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(occurred_at), MAX(occurred_at) FROM cycles "
            "WHERE machine_code=%s AND cycle_count = ANY(%s)",
            (machine_code, list(cycle_counts)),
        )
        return cur.fetchone()


def _apply_inserts(conn, machine_code, parsed_rows, occurred, received_at):
    """Skutecny zapis (--apply): PRESNE stejny cycle_identity gatekeeper
    vzor jako collector.py::read_new_cycles() - INSERT INTO cycle_identity
    ... ON CONFLICT DO NOTHING RETURNING 1 pred kazdym insertem do cycles,
    order_ref vzdy NULL (viz modul docstring proc)."""
    new_count = 0
    duplicate_count = 0
    with conn.cursor() as cur:
        for parsed, (occurred_at, occurred_at_source) in zip(parsed_rows, occurred):
            cur.execute(
                """
                INSERT INTO cycle_identity (machine_code, cycle_count)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                RETURNING 1
                """,
                (machine_code, parsed["cycle_count"]),
            )
            if cur.fetchone() is None:
                duplicate_count += 1
                continue

            cur.execute(
                """
                INSERT INTO cycles (
                    machine_code, cycle_count, cycle_time_s, order_ref, params,
                    received_at, occurred_at, occurred_at_source
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    machine_code,
                    parsed["cycle_count"],
                    parsed["cycle_time_s"],
                    None,  # order_ref - viz modul docstring, tento skript nehada zakazku
                    json.dumps(parsed["params"]),
                    received_at,
                    occurred_at,
                    occurred_at_source,
                ),
            )
            new_count += 1
    conn.commit()
    return new_count, duplicate_count


def _classify_dry_run(conn, machine_code, parsed_rows):
    """Dry run varianta _apply_inserts(): stejna gatekeeper logika (vcetne
    duplicit UVNITR sameho archivu), ale BEZ jedineho zapisu do DB - jen
    cteni cycle_identity a mnozinova operace v pameti."""
    all_counts = [row["cycle_count"] for row in parsed_rows]
    seen = _existing_cycle_counts(conn, machine_code, all_counts)
    new_count = 0
    duplicate_count = 0
    new_cycle_counts = []
    for cycle_count in all_counts:
        if cycle_count in seen:
            duplicate_count += 1
        else:
            new_count += 1
            new_cycle_counts.append(cycle_count)
            seen.add(cycle_count)
    return new_count, duplicate_count, new_cycle_counts


def replay_archive(conn, machine_code, archive_path, apply=False, skip_checksum_verify=False):
    """Jadro skriptu - volatelne primo z testu (viz collector/tests/
    test_replay_reports_dat.py a test_integration_real_db.py), ne jen pres
    CLI/subprocess."""
    checksum_verified = verify_checksum(conn, archive_path, skip_checksum_verify=skip_checksum_verify)

    header, data_lines = parse_archive_file(archive_path)
    parsed_rows, malformed_skipped = parse_data_lines(header, data_lines)

    received_at = datetime.now(timezone.utc)
    occurred = reconstruct_occurred_at(parsed_rows, received_at)

    if apply:
        new_count, duplicate_count = _apply_inserts(conn, machine_code, parsed_rows, occurred, received_at)
        all_counts = [row["cycle_count"] for row in parsed_rows]
        occurred_min, occurred_max = _occurred_at_range(conn, machine_code, all_counts)
        return ReplayReport(
            archive_path=archive_path,
            machine_code=machine_code,
            applied=True,
            checksum_verified=checksum_verified,
            total_data_lines=len(data_lines),
            parsed_ok=len(parsed_rows),
            malformed_skipped=malformed_skipped,
            new_count=new_count,
            duplicate_count=duplicate_count,
            existing_occurred_at_min=occurred_min,
            existing_occurred_at_max=occurred_max,
        )

    new_count, duplicate_count, new_cycle_counts = _classify_dry_run(conn, machine_code, parsed_rows)
    existing_counts = [row["cycle_count"] for row in parsed_rows if row["cycle_count"] not in new_cycle_counts]
    existing_min, existing_max = _occurred_at_range(conn, machine_code, existing_counts)

    preview_min = preview_max = None
    if new_cycle_counts:
        new_set = set(new_cycle_counts)
        preview_occurred_ats = [
            occurred_at
            for parsed, (occurred_at, _source) in zip(parsed_rows, occurred)
            if parsed["cycle_count"] in new_set
        ]
        preview_min = min(preview_occurred_ats)
        preview_max = max(preview_occurred_ats)

    return ReplayReport(
        archive_path=archive_path,
        machine_code=machine_code,
        applied=False,
        checksum_verified=checksum_verified,
        total_data_lines=len(data_lines),
        parsed_ok=len(parsed_rows),
        malformed_skipped=malformed_skipped,
        new_count=new_count,
        duplicate_count=duplicate_count,
        existing_occurred_at_min=existing_min,
        existing_occurred_at_max=existing_max,
        preview_new_occurred_at_min=preview_min,
        preview_new_occurred_at_max=preview_max,
    )


def format_report(report: ReplayReport) -> str:
    mode = "APPLY (zapsano do DB)" if report.applied else "DRY RUN (nic nezapsano)"
    checksum_line = (
        "checksum: OVEREN, souhlasi"
        if report.checksum_verified
        else "checksum: PRESKOCEN (--skip-checksum-verify)"
    )
    lines = [
        "=== Replay REPORTS.DAT - reconciliation report ===",
        f"archiv:          {report.archive_path}",
        f"machine_code:    {report.machine_code}",
        f"rezim:           {mode}",
        checksum_line,
        f"datovych radku v archivu:      {report.total_data_lines}",
        f"uspesne naparsovano:           {report.parsed_ok}",
        f"preskoceno (poskozene radky):  {report.malformed_skipped}",
    ]
    if report.applied:
        lines.append(f"nove vlozene cykly:            {report.new_count}")
        lines.append(f"jiz zaznamenane duplicity:     {report.duplicate_count}")
        lines.append(
            "occurred_at rozsah v cilove DB pro tento archiv: "
            f"{report.existing_occurred_at_min} .. {report.existing_occurred_at_max}"
        )
    else:
        lines.append(f"genuinne nove (byly by vlozeny s --apply): {report.new_count}")
        lines.append(f"jiz zaznamenane duplicity:                 {report.duplicate_count}")
        lines.append(
            "occurred_at rozsah JIZ V DB (duplicitni podmnozina): "
            f"{report.existing_occurred_at_min} .. {report.existing_occurred_at_max}"
        )
        lines.append(
            "occurred_at PREDBEZNY odhad pro nove radky (nezapsano, jen "
            f"vypocet teto session): {report.preview_new_occurred_at_min} .. "
            f"{report.preview_new_occurred_at_max}"
        )
        lines.append("Pro skutecny zapis spustte znovu s --apply.")
    return "\n".join(lines)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Prehraje archivovany REPORTS.DAT.<timestamp> (tiket 1.5) do cilove "
            "databaze pres stejny cycle_identity gatekeeper jako zivy collector. "
            "Vychozi rezim je dry run - pro skutecny zapis pridejte --apply."
        ),
    )
    parser.add_argument(
        "--machine-code",
        required=True,
        help="Identifikator stroje v DB (napr. KM-MC5-01), stejny jako collector.py MACHINE_CODE.",
    )
    parser.add_argument(
        "--archive-path",
        help=(
            "Cesta k archivovanemu souboru REPORTS.DAT.<timestamp>, presne jak je "
            "zaznamenana v reports_dat_archive.archive_path. Povinne, pokud "
            "neni zadano --list-archives."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=_REAL_DATABASE_URL_ENV,
        help="Cilova DATABASE_URL. Vychozi: promenna prostredi DATABASE_URL.",
    )
    parser.add_argument(
        "--list-archives",
        action="store_true",
        help="Jen vypsat dostupne archivy pro --machine-code z reports_dat_archive a skoncit.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Skutecne zapsat do DB. Bez tohoto prepinace jde vzdy jen o dry run.",
    )
    parser.add_argument(
        "--skip-checksum-verify",
        action="store_true",
        help=(
            "Preskocit overeni sha256 proti reports_dat_archive (vychozi je "
            "kontrolovat a pri nesouhlasu odmitnout). Pouzivat jen pro soubory "
            "predchazejici tiketu 1.5, ktere nikdy nemely zaznam checksum."
        ),
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.database_url:
        parser.error("--database-url neni zadano a DATABASE_URL neni nastaveno v prostredi.")
    if not args.list_archives and not args.archive_path:
        parser.error("--archive-path je povinne, pokud neni zadano --list-archives.")

    conn = get_conn(args.database_url)
    try:
        if args.list_archives:
            list_archives(conn, args.machine_code)
            return 0

        try:
            report = replay_archive(
                conn,
                machine_code=args.machine_code,
                archive_path=args.archive_path,
                apply=args.apply,
                skip_checksum_verify=args.skip_checksum_verify,
            )
        except ChecksumVerificationError as exc:
            print(f"CHYBA: {exc}", file=sys.stderr)
            return 1

        print(format_report(report))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
