"""stage3 mass tickets and subtasks

Revision ID: 0003_stage3_mass
Revises: 0002_stage2_tickets
Create Date: 2026-11-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_stage3_mass"
down_revision: Union[str, None] = "0002_stage2_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite: Enum хранится как VARCHAR — значение mass допускается без ALTER ENUM.
    with op.batch_alter_table("tickets") as batch:
        batch.alter_column(
            "student_name",
            existing_type=sa.String(length=255),
            nullable=True,
        )
        batch.alter_column(
            "student_external_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch.add_column(sa.Column("reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("absence_period", sa.String(length=255), nullable=True))

    op.create_table(
        "ticket_subtasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("student_name", sa.String(length=255), nullable=False),
        sa.Column("student_external_id", sa.String(length=64), nullable=False),
        sa.Column("lesson_datetime", sa.String(length=255), nullable=False),
        sa.Column("desired_new_datetime", sa.String(length=255), nullable=True),
        sa.Column(
            "required_action",
            sa.Enum(
                "propose_transfer",
                "agree_cancel",
                "temp_replacement",
                "clarify_parent",
                name="mass_subtask_action",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "not_processed",
                "waiting_reply",
                "done",
                "rejected",
                name="ticket_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_subtasks_ticket_id",
        "ticket_subtasks",
        ["ticket_id"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_subtasks_student_external_id",
        "ticket_subtasks",
        ["student_external_id"],
        unique=False,
    )

    with op.batch_alter_table("ticket_history") as batch:
        batch.add_column(sa.Column("subtask_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_ticket_history_subtask_id",
            "ticket_subtasks",
            ["subtask_id"],
            ["id"],
        )
        batch.create_index("ix_ticket_history_subtask_id", ["subtask_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("ticket_history") as batch:
        batch.drop_index("ix_ticket_history_subtask_id")
        batch.drop_constraint("fk_ticket_history_subtask_id", type_="foreignkey")
        batch.drop_column("subtask_id")

    op.drop_index(
        "ix_ticket_subtasks_student_external_id", table_name="ticket_subtasks"
    )
    op.drop_index("ix_ticket_subtasks_ticket_id", table_name="ticket_subtasks")
    op.drop_table("ticket_subtasks")

    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("absence_period")
        batch.drop_column("reason")
        batch.alter_column(
            "student_external_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch.alter_column(
            "student_name",
            existing_type=sa.String(length=255),
            nullable=False,
        )
