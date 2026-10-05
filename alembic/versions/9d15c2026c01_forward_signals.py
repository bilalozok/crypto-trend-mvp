"""Add immutable observed signal snapshots and later hypothetical outcomes."""

import sqlalchemy as sa

from alembic import op

revision = "9d15c2026c01"
down_revision = "8c15c2026b01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "binance_forward_signals",
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("rule_hash", sa.String(64), primary_key=True),
        sa.Column("signal_close_ms", sa.BigInteger(), primary_key=True),
        sa.Column("observed_ms", sa.BigInteger(), nullable=False),
        sa.Column("entry_ms", sa.BigInteger(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("outcomes", sa.JSON(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_forward_pending", "binance_forward_signals", ["symbol", "complete"])


def downgrade():
    op.drop_index("ix_forward_pending", table_name="binance_forward_signals")
    op.drop_table("binance_forward_signals")
