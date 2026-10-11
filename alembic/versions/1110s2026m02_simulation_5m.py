"""Separate 5m execution prices; preserve existing 15m pending decisions."""

import sqlalchemy as sa

from alembic import op

revision = "1110s2026m02"
down_revision = "1110s2026m01"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("entry_interval", "exit_interval"):
        op.add_column(
            "private_simulations",
            sa.Column(name, sa.String(3), nullable=False, server_default="15m"),
        )
    op.create_table(
        "simulation_candles_5m",
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("open_time", sa.BigInteger(), primary_key=True),
        *[
            sa.Column(name, sa.Float(), nullable=False)
            for name in ("open", "high", "low", "close", "volume")
        ],
    )


def downgrade():
    op.drop_table("simulation_candles_5m")
    for name in ("exit_interval", "entry_interval"):
        op.drop_column("private_simulations", name)
