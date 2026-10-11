"""Variable spot purchase cost and linked purchase journal ID; preserve old budgets."""

import sqlalchemy as sa

from alembic import op

revision = "1110s2026m03"
down_revision = "1110s2026m02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "private_simulations",
        sa.Column("budget", sa.String(128), nullable=False, server_default="100"),
    )
    op.add_column("private_simulations", sa.Column("purchase_id", sa.String(36), nullable=True))


def downgrade():
    op.drop_column("private_simulations", "purchase_id")
    op.drop_column("private_simulations", "budget")
