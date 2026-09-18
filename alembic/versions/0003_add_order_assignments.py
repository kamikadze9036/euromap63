"""add order_assignments: time-versioned active-order tracking per machine

Revision ID: 0003_add_order_assignments
Revises: 0002_example_table_comment
Create Date: 2026-09-18

============================================================================
 WHY THIS MIGRATION EXISTS
============================================================================
MES_IMPLEMENTATION_BACKLOG.md ticket 1.7 / MES_TARGET_ARCHITECTURE.md §5.2:

  "Collector dnes zjisti aktualni zakazku v okamziku zpracovani a priradi ji
  cele prave nactene davce. Po vypadku se proto mohou starsi cykly priradit
  k novejsi zakazce."

Before this ticket, collector.py's read_new_cycles() called
get_active_order() ONCE per poll and stamped that single value onto every
row of the batch, regardless of each cycle's own occurred_at. After an
outage/catch-up batch, older cycles could get attributed to whatever order
happens to be running in Cyclades *right now*, not the order that was
actually running when they occurred.

This table lets collector.py record, with a timestamp, every time the
*observed* active order for a machine changes (see
collector.py's _sync_order_assignment()/get_active_order()), so that later
each cycle can be joined to the order that was active at that cycle's own
occurred_at, via:

    SELECT order_ref FROM order_assignments
    WHERE machine_code = %s AND valid_from <= %s
      AND (valid_to IS NULL OR valid_to > %s)

order_ref is nullable ON PURPOSE: a row with order_ref=NULL is a legitimate,
confirmed observation of "no active order" (machine idle / changeover) for
that interval - it is not "we don't know". "We don't know" is instead
represented by the ABSENCE of any covering row at all (e.g. cycles collected
before this ticket's tracking existed, or a genuine polling gap) - the
collector-side lookup treats that case as order_ref=NULL for the cycle too,
but for a different reason (see collector.py's _resolve_order_ref()).

Inherent limitation (documented, not solved here): "valid_from"/"valid_to"
are the times the COLLECTOR POLLED Cyclades and observed a change, not the
true moment the order changed in Cyclades - this is a consequence of polling
rather than subscribing to Cyclades change events.

Indexes:
  - ix_order_assignments_machine_valid_from: supports the overlap-lookup
    query above (machine_code + valid_from range scan).
  - ix_order_assignments_machine_open: a partial index on
    (machine_code) WHERE valid_to IS NULL - lets both
    _sync_order_assignment() (find the current open interval to close/adopt)
    and the "is there already an open interval" check run as a fast lookup
    instead of a sequential scan, since in steady state there are only ever
    a handful of open (or ever-any) rows per machine but the table grows
    forever.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_order_assignments"
down_revision: Union[str, None] = "0002_example_table_comment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_assignments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "machine_code",
            sa.Text(),
            sa.ForeignKey("machines.machine_code"),
            nullable=False,
        ),
        sa.Column("order_ref", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_order_assignments_machine_valid_from",
        "order_assignments",
        ["machine_code", "valid_from"],
    )
    op.create_index(
        "ix_order_assignments_machine_open",
        "order_assignments",
        ["machine_code"],
        postgresql_where=sa.text("valid_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_order_assignments_machine_open", table_name="order_assignments")
    op.drop_index("ix_order_assignments_machine_valid_from", table_name="order_assignments")
    op.drop_table("order_assignments")
