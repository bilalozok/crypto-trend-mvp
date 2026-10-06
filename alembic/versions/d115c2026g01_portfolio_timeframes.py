"""Separate closed 4h/1d portfolio market data from frozen 15m analysis."""

import sqlalchemy as sa

from alembic import op

revision = "d115c2026g01"
down_revision = "c015c2026f01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "portfolio_market_candles",
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("interval", sa.String(3), primary_key=True),
        sa.Column("open_time", sa.BigInteger(), primary_key=True),
        *[
            sa.Column(key, sa.Float(), nullable=False)
            for key in ("open", "high", "low", "close", "volume")
        ],
        sa.CheckConstraint("interval IN ('4h', '1d')", name="ck_portfolio_interval"),
    )
    op.create_table(
        "portfolio_market_feeds",
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("interval", sa.String(3), primary_key=True),
        sa.Column("last_attempt_ms", sa.BigInteger(), nullable=False),
        sa.Column("last_success_ms", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.String(100), nullable=True),
        sa.CheckConstraint("interval IN ('4h', '1d')", name="ck_portfolio_feed_interval"),
    )


def downgrade():
    op.drop_table("portfolio_market_feeds")
    op.drop_table("portfolio_market_candles")
