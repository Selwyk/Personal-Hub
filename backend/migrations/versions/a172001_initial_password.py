"""Require replacement of optional per-install bootstrap credentials."""
from alembic import op
import sqlalchemy as sa

revision = "a172001"
down_revision = "932c3fb1d4c2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(),
                                    server_default=sa.text("false"), nullable=False))


def downgrade():
    op.drop_column("users", "must_change_password")
