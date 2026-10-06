"""Private immutable candidate market scans, including the evaluated universe."""

import sqlalchemy as sa

from alembic import op

revision = "f315c2026i01"
down_revision = "e215c2026h01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_candidate_scans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("private_accounts.id"), nullable=False
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("created_ms", sa.BigInteger(), nullable=False),
        sa.Column("rule_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("account_id", "request_id", name="uq_candidate_scan_request"),
    )
    op.create_index(
        "ix_candidate_scan_owner_time", "private_candidate_scans", ["account_id", "created_ms"]
    )


def downgrade():
    op.drop_table("private_candidate_scans")
