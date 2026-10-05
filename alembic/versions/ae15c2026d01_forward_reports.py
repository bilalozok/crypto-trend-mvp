"""Store fixed forward report snapshots without changing signal tables."""

import sqlalchemy as sa

from alembic import op

revision = "ae15c2026d01"
down_revision = "9d15c2026c01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "binance_forward_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_ms", sa.BigInteger(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("rule_hash", sa.String(64), nullable=False),
        sa.Column("signal_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_forward_reports_created", "binance_forward_reports", ["created_ms"])


def downgrade():
    op.drop_index("ix_forward_reports_created", table_name="binance_forward_reports")
    op.drop_table("binance_forward_reports")
