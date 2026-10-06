"""Store private, user-triggered daily technical observations."""

import sqlalchemy as sa

from alembic import op

revision = "c015c2026f01"
down_revision = "bf15c2026e01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_portfolio_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("private_accounts.id"), nullable=False
        ),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("local_day", sa.String(10), nullable=False),
        sa.Column("observed_ms", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("account_id", "symbol", "local_day", name="uq_portfolio_owner_day"),
    )
    op.create_index(
        "ix_portfolio_owner_symbol_time",
        "private_portfolio_observations",
        ["account_id", "symbol", "observed_ms"],
    )


def downgrade():
    op.drop_index("ix_portfolio_owner_symbol_time", table_name="private_portfolio_observations")
    op.drop_table("private_portfolio_observations")
