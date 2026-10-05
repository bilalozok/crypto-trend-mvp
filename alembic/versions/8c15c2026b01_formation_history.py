"""Add observed formation states and transitions, preserving candle tables."""

import sqlalchemy as sa

from alembic import op

revision = "8c15c2026b01"
down_revision = "7b15c2026a01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "binance_formation_states",
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("pattern", sa.String(64), primary_key=True),
        sa.Column("candle_close_ms", sa.BigInteger(), nullable=False),
        sa.Column("signature", sa.JSON(), nullable=False),
    )
    op.create_table(
        "binance_formation_events",
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("pattern", sa.String(64), primary_key=True),
        sa.Column("candle_close_ms", sa.BigInteger(), primary_key=True),
        sa.Column("observed_ms", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("previous", sa.JSON()),
        sa.Column("current", sa.JSON(), nullable=False),
        sa.Column("method_version", sa.String(64), nullable=False),
    )


def downgrade():
    op.drop_table("binance_formation_events")
    op.drop_table("binance_formation_states")
