"""replace_users_email_unique_with_partial_index

Revision ID: f4e08b4f226a
Revises: c52316c4b5e8
Create Date: 2026-03-17 20:12:25.569771

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4e08b4f226a'
down_revision: Union[str, Sequence[str], None] = 'c52316c4b5e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.create_index(
        "ix_users_email_active",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_users_email_active", table_name="users")
    op.create_unique_constraint("uq_users_email", "users", ["email"])
