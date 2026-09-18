# Alembic adoption procedure (ticket 1.9)

This document exists for one reason: **the single highest-risk step in
adopting Alembic on this project is running the wrong command against the
production database on `spc-vm`.** Read this in full before running any
`alembic` command against a database that isn't a fresh throwaway one.

## The two mechanisms, and why both exist

- `postgres/init/NN_*.sql`, mounted as `/docker-entrypoint-initdb.d` in
  `docker-compose.yml`, bootstraps a **brand-new, empty** Postgres data
  volume. Postgres only ever runs these once, the first time a container
  starts against an empty `pgdata` volume. This is still how a fresh
  dev/local database gets its schema — these files are **not** deleted or
  changed by this ticket.
- **Alembic** (`alembic.ini` + `alembic/` at the repo root) is how schema
  changes are made and tracked **from now on**, for a database that already
  exists — staging/production, or a dev DB after its first init.

The single baseline revision `alembic/versions/0001_baseline_squash_postgres_init.py`
replays the exact SQL text of `postgres/init/01_schema.sql` through
`postgres/init/20_add_collector_heartbeat.sql`, in order, so that
`alembic upgrade head` against an empty database produces the same schema as
letting `docker-entrypoint-initdb.d` run today.

## Which database is at which state, as of this ticket

| Database | Current state | What it needs |
|---|---|---|
| Fresh dev/local (new `pgdata` volume) | Nothing yet | Either let `docker-entrypoint-initdb.d` run as today, **or** start empty and run `alembic upgrade head` — both should converge on the same schema (see "Verification" below — this has *not* been proven against a real DB yet) |
| An existing dev/local DB that already ran `postgres/init/*.sql` via `docker-entrypoint-initdb.d` | Has the full schema, not tracked by Alembic | `alembic stamp head` (never `upgrade`) |
| **`spc-vm` production** | Has the full schema through migration 20 (possibly 21 if ticket 1.5 landed one — check `postgres/init/` for a `21_*.sql` before doing anything), applied by hand via `docker exec ... psql -f postgres/init/NN_*.sql`. **Not** tracked by Alembic. | `alembic stamp head` (never `upgrade`) |

## THE rule

> **First-time adoption on a database that already has the schema:
> `alembic stamp head`. Never `alembic upgrade head`, until this has been
> verified and that database is confirmed to have nothing Alembic hasn't
> already accounted for.**

Why this matters: `alembic upgrade head` re-executes the baseline
revision's SQL. Most of the baseline is `IF NOT EXISTS`-guarded DDL, so a
lot of it would be a harmless no-op — but the seed-data `INSERT`s (parameter
catalogs, the machine list) are **not all idempotent under a plain
`upgrade`**: several of them use `ON CONFLICT ... DO UPDATE` and are safe to
re-run, but `stamp` is still correct and `upgrade` is still unnecessary and
riskier — there is no reason to re-run 20 files' worth of SQL against a
database that already has it, only downside (write load, lock contention on
a live table, a mistake in one of the `ON CONFLICT` clauses causing an
error and an aborted transaction on a production database that is currently
collecting real cycle data). `stamp` achieves the correct end state (DB
marked as being at revision `0001_baseline`) with zero risk, because it
**only writes to Alembic's own bookkeeping table** (`alembic_version`) and
touches nothing else.

## Exact procedure for spc-vm (to be run by the orchestrating session, not this one)

1. Rebuild/redeploy the `api` image so it has `alembic` and `SQLAlchemy`
   installed (they're now in `api/requirements.txt`):
   ```
   docker compose build api
   docker compose up -d api
   ```
2. Confirm which `postgres/init/NN_*.sql` files actually exist on spc-vm and
   have actually been applied there (ticket 1.5, running in parallel, may
   have added a `21_*.sql` after this baseline was written — if so, that
   file is **not** part of `0001_baseline` and needs its own follow-up
   Alembic revision before spc-vm can be stamped at a revision that matches
   its real state).
3. Run, from a location that has this repo checked out and can reach the
   `postgres` service on the same Docker network as `api` (the `api`
   container already has the right `DATABASE_URL` wired up via
   `docker-compose.yml`, so reusing that service definition is the easiest
   way to get the network/credentials right without retyping them):
   ```
   docker compose run --rm \
     -v "$(pwd)/alembic:/app/alembic:ro" \
     -v "$(pwd)/alembic.ini:/app/alembic.ini:ro" \
     api alembic stamp head
   ```
   (`docker compose run` starts a fresh, one-off container from the
   already-built `api` image — it does not touch the running `api`
   container or restart it. The two `-v` mounts are needed only because the
   `api` image itself doesn't `COPY` `alembic.ini`/`alembic/` in — see
   "Docker/compose" below for why that was a deliberate choice.)
4. Verify: `docker compose run --rm -v ... api alembic current` should now
   print `0001_baseline (head)` (add `0002_example_table_comment` too if
   that revision has also been applied by this point).
5. From this point on, any new schema change is a new Alembic revision
   (see "Workflow for a new migration" below), applied on spc-vm with
   `alembic upgrade head` using the same `docker compose run` pattern —
   **not** another `postgres/init/NN_*.sql` file.

## Workflow for a new migration (from now on)

1. `alembic revision -m "short description"` (run locally, or via the same
   `docker compose run --rm -v ... api alembic revision -m "..."` pattern —
   either way, the new file lands in `alembic/versions/` since that's a
   bind-mounted/checked-out path, not something baked into an image).
2. Write `upgrade()`/`downgrade()` using `op.*` calls (`op.add_column`,
   `op.create_index`, etc.) where that reads cleanly; fall back to
   `op.execute()` (or `connection.exec_driver_sql()` if the SQL contains
   literal `:` or `%` characters that could be misread as bind parameters —
   see the comment in `0001_baseline_squash_postgres_init.py` for why that
   distinction matters) for anything `op.*` can't express well. Consistency
   and readability matter more than always picking one style.
3. Test against a fresh, empty database: `alembic upgrade head`, confirm it
   applies cleanly, spot-check the resulting schema.
4. Apply it:
   - Fresh dev/local DB: `alembic upgrade head` (or just let
     `docker-entrypoint-initdb.d` handle a *brand-new* DB the old way if you
     also add a matching `postgres/init/NN_*.sql` — but for anything beyond
     this ticket's baseline, prefer Alembic-only and treat init-scripts as
     frozen at the baseline; don't grow that directory further).
   - Already-Alembic-tracked environments (staging, spc-vm once stamped):
     `alembic upgrade head` via the `docker compose run` pattern above.

## Docker/compose: how migrations actually get run

`api/Dockerfile` intentionally does **not** `COPY` `alembic.ini`/`alembic/`
into the image, and `docker-compose.yml`'s `api` service is unchanged by
this ticket. This was a deliberate choice, not an oversight: this ticket
was written without a working Docker/Postgres in its sandbox, so any change
to the `api` image's build context or `Dockerfile` couldn't be verified
here — and `api` is a live, currently-collecting-real-data production
service. Bind-mounting `alembic.ini`/`alembic/` at invocation time (via
`docker compose run -v ...`, shown above) achieves the same result — the
`api` image just needs `alembic`/`SQLAlchemy` installed (which
`api/requirements.txt` now ensures) — with **zero** changes to the image or
compose service definitions, so there's nothing here that needed
Docker-based verification that wasn't available.

**Possible future improvement (not implemented here, consider later):** if
`docker compose exec api alembic upgrade head` (no bind mounts) is wanted
for convenience, `api/Dockerfile` would need to also `COPY` `alembic.ini`
and `alembic/`, which requires changing `docker-compose.yml`'s `api.build`
from `./api` to `{context: ., dockerfile: api/Dockerfile}` and adjusting the
`Dockerfile`'s `COPY` paths accordingly. That's a real (if small) change to
a live production service's build definition and should be made and
verified by someone with actual Docker access, not guessed at here.

**Auto-running migrations on container startup was explicitly not done**
(and is out of scope for this ticket) — that's a bigger, debatable design
decision (unlike `docker-entrypoint-initdb.d`'s "runs once on an empty
volume" model, auto-migrating on every boot has real failure modes:
concurrent starts racing on the same migration, a bad migration silently
blocking every future container start, etc.) and shouldn't be decided
unilaterally inside this ticket.

## Verification status (read this before trusting any of the above)

This baseline migration's equivalence to running `postgres/init/01_schema.sql`
through `20_add_collector_heartbeat.sql` in order has **not** been verified
against a real Postgres/TimescaleDB instance — the sandbox this was written
in has no Docker and no working `psycopg2`/`alembic`/`SQLAlchemy` install
(confirmed: `import sqlalchemy` fails; `import alembic` silently resolves to
this project's own `alembic/` directory as a namespace package, not the
real library — there is no real Alembic in this sandbox to run anything
against). What *was* checked here:

- `alembic/env.py` and every file under `alembic/versions/` parse as valid
  Python (`ast.parse`).
- `alembic.ini` parses as valid, well-formed `ConfigParser` syntax, and its
  `%%`-escaped `file_template` resolves to the expected literal `%(rev)s_%(slug)s`.
- Every `postgres/init/*.sql` file was read in full and its statements were
  transcribed into `0001_baseline_squash_postgres_init.py` verbatim (via
  `r"""..."""` raw strings, executed with `connection.exec_driver_sql()` —
  chosen specifically because it sends SQL straight to the DBAPI with no
  bind-parameter parsing, so this project's parameter names containing
  literal backslashes and unit strings containing literal `%` can't be
  misinterpreted).

What this means in practice: **before running `alembic stamp head` against
spc-vm, or trusting this baseline for any real use**, spin up a throwaway
TimescaleDB container, run `alembic upgrade head` against it, and diff
`pg_dump --schema-only` (plus `machine_parameters`/`machines` row counts)
against a second throwaway database bootstrapped the old way
(`docker-entrypoint-initdb.d` running `postgres/init/*.sql` in order). This
is exactly the kind of real-DB verification already done elsewhere in this
project's history for tickets 1.1–1.4 and 1.8 — this baseline still needs
the same treatment before it's trusted.
