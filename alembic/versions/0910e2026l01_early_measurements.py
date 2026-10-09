"""Prospective early formation measurements, separate from confirmed signals."""

import sqlalchemy as sa

from alembic import op

revision = "0910e2026l01"
down_revision = "1515c2026k01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "early_formation_measurements",
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("rule_hash", sa.String(64), primary_key=True),
        sa.Column("pattern", sa.String(64), primary_key=True),
        sa.Column("close_ms", sa.BigInteger(), primary_key=True),
        sa.Column("observed_ms", sa.BigInteger(), nullable=False),
        sa.Column("entry_ms", sa.BigInteger(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("outcomes", sa.JSON(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_early_measure_pending", "early_formation_measurements", ["symbol", "complete"]
    )


def downgrade():
    op.drop_index("ix_early_measure_pending", table_name="early_formation_measurements")
    op.drop_table("early_formation_measurements")
