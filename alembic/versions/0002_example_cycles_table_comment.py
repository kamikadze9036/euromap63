"""example: document the from-now-on Alembic workflow with a harmless, no-op-safe change

Revision ID: 0002_example_table_comment
Revises: 0001_baseline
Create Date: 2026-09-18

============================================================================
 WHY THIS MIGRATION EXISTS
============================================================================
Ticket 1.9 (MES_IMPLEMENTATION_BACKLOG.md) asked for a second revision
demonstrating the intended future workflow: create revision -> write it with
op.* DSL calls (preferred over raw SQL for small, clean DDL changes - see
alembic/env.py and the baseline for when raw SQL is still the right choice)
-> test against a fresh DB -> apply with `alembic upgrade head`.

No real schema change was requested by anyone for this ticket, so rather
than invent one, this revision does the smallest genuinely safe, genuinely
real thing available: attaches a human-readable COMMENT ON TABLE/COLUMN to
a few `cycles` columns. Postgres comments are pure catalog metadata (stored
in pg_description) - they never touch table data, are always safe to (re-)
apply, and are visible to anyone doing \\d+ cycles in psql or looking at the
table in a DB client. This is a real, useful, low-value-but-nonzero change,
not a contrived placeholder.

This is what a NEW schema change should look like from now on: a new file
in alembic/versions/, NOT a new postgres/init/NN_*.sql file. See
docs/alembic_adoption.md for the full process and the production adoption
procedure.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_example_table_comment"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "COMMENT ON TABLE cycles IS "
        "'One row per press cycle (EUROMAP63 REPORTS.DAT line). "
        "cycle_count/cycle_time_s are typed for indexing; all other "
        "machine-specific parameters live in params (JSONB). See "
        "postgres/init/01_schema.sql and 17_add_cycle_timestamps.sql "
        "for the timestamp columns'' history.';"
    )
    op.execute(
        "COMMENT ON COLUMN cycles.occurred_at IS "
        "'Best-effort reconstructed event time - see occurred_at_source "
        "and postgres/init/17_add_cycle_timestamps.sql for why this is a "
        "reconstruction rather than a trustworthy machine-provided time.';"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN cycles.occurred_at IS NULL;")
    op.execute("COMMENT ON TABLE cycles IS NULL;")
