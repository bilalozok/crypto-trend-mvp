"""Versioned retrospective daily top gainers and pre-event indicator studies."""

import sqlalchemy as sa

from alembic import op

revision = "1210s2026n01"
down_revision = "1110s2026m03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "real_gainer_studies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("computed_ms", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_real_gainer_day_version", "real_gainer_studies", ["day", "version", "computed_ms"]
    )


def downgrade():
    op.drop_index("ix_real_gainer_day_version", table_name="real_gainer_studies")
    op.drop_table("real_gainer_studies")
