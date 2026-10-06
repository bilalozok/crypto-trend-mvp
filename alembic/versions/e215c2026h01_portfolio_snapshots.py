"""Keep timestamped portfolio observations separately from daily first observations."""

import sqlalchemy as sa

from alembic import op

revision = "e215c2026h01"
down_revision = "d115c2026g01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_portfolio_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("private_accounts.id"), nullable=False
        ),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("observed_ms", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("comparison", sa.JSON(), nullable=False),
        sa.UniqueConstraint("account_id", "request_id", name="uq_portfolio_snapshot_request"),
    )
    op.create_index(
        "ix_portfolio_snapshot_time",
        "private_portfolio_snapshots",
        ["account_id", "symbol", "observed_ms"],
    )


def downgrade():
    op.drop_table("private_portfolio_snapshots")
