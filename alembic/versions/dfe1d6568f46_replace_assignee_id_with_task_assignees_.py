"""Replace assignee_id with task_assignees junction table

Revision ID: dfe1d6568f46
Revises: cf8fbcff6e3a
Create Date: 2026-03-16 21:56:55.892861

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dfe1d6568f46'
down_revision: Union[str, Sequence[str], None] = 'cf8fbcff6e3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f("ix_tasks_assignee_id"), table_name="tasks")
    op.drop_constraint("tasks_assignee_id_fkey", "tasks", type_="foreignkey")
    op.drop_column("tasks", "assignee_id")

    op.create_table(
        "task_assignees",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("assigned_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("task_id", "user_id"),
    )
    op.create_index(
        op.f("ix_task_assignees_user_id"), "task_assignees", ["user_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_task_assignees_user_id"), table_name="task_assignees")
    op.drop_table("task_assignees")

    # assignee_id restored as nullable — original data is not recoverable
    op.add_column("tasks", sa.Column("assignee_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "tasks_assignee_id_fkey",
        "tasks",
        "users",
        ["assignee_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_tasks_assignee_id"), "tasks", ["assignee_id"], unique=False)
