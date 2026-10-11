"""Private 100 USDT paper positions and immutable decision contexts."""

import sqlalchemy as sa

from alembic import op

revision = "1110s2026m01"
down_revision = "0910e2026l01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_simulations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("private_accounts.id"), nullable=False
        ),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("created_ms", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("entry_ms", sa.BigInteger(), nullable=False),
        sa.Column("exit_ms", sa.BigInteger(), nullable=True),
        sa.Column("entry_price", sa.String(128), nullable=True),
        sa.Column("exit_price", sa.String(128), nullable=True),
        sa.Column("quantity", sa.String(128), nullable=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("entry_context", sa.JSON(), nullable=True),
        sa.Column("sale_context", sa.JSON(), nullable=True),
        sa.Column("exit_context", sa.JSON(), nullable=True),
        sa.Column("observations", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_simulation_owner_time", "private_simulations", ["account_id", "created_ms", "id"]
    )


def downgrade():
    op.drop_index("ix_simulation_owner_time", table_name="private_simulations")
    op.drop_table("private_simulations")
