"""create items table"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("items", sa.Column("id", sa.Integer, primary_key=True), sa.Column("name", sa.String(120)))


def downgrade() -> None:
    op.drop_table("items")
