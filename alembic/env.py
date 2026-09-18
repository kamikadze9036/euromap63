"""Alembic environment script.

============================================================================
 How this fits with postgres/init/*.sql (READ THIS FIRST)
============================================================================
This project has TWO schema-provisioning mechanisms that intentionally
coexist, each with a distinct job:

  - postgres/init/NN_*.sql (mounted as /docker-entrypoint-initdb.d in
    docker-compose.yml) is what bootstraps a brand-new, EMPTY Postgres data
    volume - Postgres only runs those scripts once, the very first time the
    container starts against an empty "pgdata" volume. This remains how a
    fresh dev/local database gets its schema. These files are NOT deleted or
    modified by the introduction of Alembic.

  - Alembic (this directory) is how schema changes are made and tracked
    FROM THIS POINT FORWARD, for a database that already exists - i.e.
    staging/production (spc-vm), or a local dev DB after its first init.
    The single baseline revision (see alembic/versions/) squashes
    postgres/init/01_schema.sql through 20_add_collector_heartbeat.sql (the
    full set as of this ticket) into one Alembic revision, so that running
    `alembic upgrade head` against a fresh, empty database produces the same
    schema as letting docker-entrypoint-initdb.d run all the numbered SQL
    files in order.

  Going forward: a fresh dev DB can be bootstrapped EITHER way (init-scripts
  on first container start, or init-scripts disabled + `alembic upgrade
  head`) - both should converge on the same schema as of the baseline. Any
  NEW schema change from now on should be a new Alembic revision, NOT
  another postgres/init/NN_*.sql file.

============================================================================
 CRITICAL - adopting Alembic on an already-existing database (spc-vm prod)
============================================================================
The production database on spc-vm already has the full schema (through
postgres/init/20_add_collector_heartbeat.sql, applied by hand via
`docker exec ... psql -f ...`, NOT via Alembic). The first time Alembic is
pointed at that database, someone MUST run:

    alembic stamp head

This marks the DB as already being at the baseline revision WITHOUT running
any SQL. Do NOT run `alembic upgrade head` against that database - it would
try to re-execute the baseline's CREATE TABLE / ALTER TABLE / INSERT
statements against a database that already has them, which is at best a
wasted no-op and at worst an error (not every statement in the baseline is
guaranteed idempotent - e.g. bare INSERTs without ON CONFLICT would fail
with a duplicate-key error).

Full details, and exactly which command to run in which situation, are in
docs/alembic_adoption.md - read that before touching spc-vm.
============================================================================

Below this point is a fairly standard Alembic env.py, with one
project-specific change: the DB connection string is read from the
DATABASE_URL environment variable (matching how api/main.py and
collector/collector.py both do `os.environ["DATABASE_URL"]` /
`os.environ.get(...)`), never hardcoded here or in alembic.ini.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object, gives access to values within alembic.ini.
config = context.config

# DATABASE_URL is required, same as api/main.py and collector/collector.py -
# fail loudly and immediately if it's missing rather than silently trying to
# connect to some default/local Postgres.
database_url = os.environ["DATABASE_URL"]
config.set_main_option("sqlalchemy.url", database_url)

# Interpret the config file for Python logging (the [loggers]/[handlers]/...
# sections in alembic.ini). This only affects Alembic CLI log output.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No SQLAlchemy ORM models/metadata in this project (api/ and collector/ use
# raw psycopg2, not the ORM) - so there is no `target_metadata` to compare
# against for autogenerate. `alembic revision --autogenerate` will not detect
# schema drift; migrations here are written by hand (raw SQL via
# op.execute(), or op.* DSL calls where that reads more cleanly - see
# alembic/versions/ for the established style). This is a deliberate choice,
# not an oversight.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL to stdout, no DB connection).

    Useful for generating a .sql script to review/hand off, e.g.:
        alembic upgrade head --sql > /tmp/preview.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connects to the DB via DATABASE_URL)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
