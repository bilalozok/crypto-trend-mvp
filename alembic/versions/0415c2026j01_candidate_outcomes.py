"""Immutable completed hypothetical outcomes for saved candidate scans."""

import sqlalchemy as sa

from alembic import op

revision = "0415c2026j01"
down_revision = "f315c2026i01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_candidate_outcomes",
        sa.Column(
            "scan_id",
            sa.String(36),
            sa.ForeignKey("private_candidate_scans.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("symbol", sa.String(64), primary_key=True),
        sa.Column("horizon_bars", sa.Integer(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("private_candidate_outcomes")
