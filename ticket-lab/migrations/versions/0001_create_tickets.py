"""Create the initial tickets table."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_tickets_status"),
    )


def downgrade():
    # Destructive: only use on a disposable database, never as a data backup.
    op.drop_table("tickets")
