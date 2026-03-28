"""add_username_to_users

Revision ID: 14189401f524
Revises: 5bbbcf1b4ae7
Create Date: 2026-03-28 12:57:27.071958

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "14189401f524"
down_revision: Union[str, Sequence[str], None] = "5bbbcf1b4ae7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=30), nullable=True))

    # Backfill: slugify name (lowercase, non-alphanumeric → underscore) + _{id} suffix.
    # The id suffix guarantees uniqueness across all existing rows without a loop.
    op.execute(
        """
        UPDATE users
        SET username = lower(regexp_replace(name, '[^a-z0-9_-]', '_', 'gi'))
                       || '_' || id
        """
    )

    op.alter_column("users", "username", nullable=False)

    op.create_index(
        "ix_users_username_active",
        "users",
        ["username"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_users_username_active",
        table_name="users",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_column("users", "username")
