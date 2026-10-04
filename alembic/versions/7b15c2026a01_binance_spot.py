"""Add isolated Binance Spot 15m tables.

Revision ID: 7b15c2026a01
Revises: 4125d212c4aa
"""

import sqlalchemy as sa

from alembic import op

revision = "7b15c2026a01"
down_revision = "4125d212c4aa"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "binance_spot_symbols",
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("base_asset", sa.String(64), nullable=False),
        sa.Column("quote_volume_24h", sa.Float(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("catalog_updated_ms", sa.BigInteger(), nullable=False),
        sa.Column("last_attempt_ms", sa.BigInteger()),
        sa.Column("last_success_ms", sa.BigInteger()),
        sa.Column("last_error", sa.String(100)),
    )
    op.create_table(
        "binance_spot_candles",
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("open_time", sa.BigInteger(), primary_key=True),
        sa.Column("interval", sa.String(3), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.CheckConstraint("interval = '15m'", name="ck_binance_spot_15m"),
    )


def downgrade():
    op.drop_table("binance_spot_candles")
    op.drop_table("binance_spot_symbols")
