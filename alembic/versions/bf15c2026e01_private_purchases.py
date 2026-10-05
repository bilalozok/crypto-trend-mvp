"""Add private accounts, sessions, login limits and exact-decimal purchase records."""

import sqlalchemy as sa

from alembic import op

revision = "bf15c2026e01"
down_revision = "ae15c2026d01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(192), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_ms", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "private_account_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("private_accounts.id"), nullable=False
        ),
        sa.Column("expires_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_private_account_sessions_account_id", "private_account_sessions", ["account_id"]
    )
    op.create_table(
        "private_login_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_ms", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_table(
        "private_purchases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("private_accounts.id"), nullable=False
        ),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("purchased_ms", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(4), nullable=False),
        sa.Column("unit_price", sa.String(64), nullable=False),
        sa.Column("quantity", sa.String(64), nullable=False),
        sa.Column("fee", sa.String(64), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("created_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_private_purchases_owner_time", "private_purchases", ["account_id", "purchased_ms"]
    )


def downgrade():
    op.drop_index("ix_private_purchases_owner_time", table_name="private_purchases")
    op.drop_table("private_purchases")
    op.drop_table("private_login_limits")
    op.drop_index("ix_private_account_sessions_account_id", table_name="private_account_sessions")
    op.drop_table("private_account_sessions")
    op.drop_table("private_accounts")
