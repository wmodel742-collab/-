"""stage2 tickets and history

Revision ID: 0002_stage2_tickets
Revises: 0001_initial
Create Date: 2026-11-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_stage2_tickets"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "ticket_type",
            sa.Enum("one_time", "permanent", name="ticket_type", native_enum=False),
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
        sa.Column("teacher_name", sa.String(length=255), nullable=False),
        sa.Column("student_name", sa.String(length=255), nullable=False),
        sa.Column("student_external_id", sa.String(length=64), nullable=False),
        sa.Column("original_datetime", sa.String(length=128), nullable=True),
        sa.Column("new_datetime", sa.String(length=128), nullable=True),
        sa.Column("current_weekday", sa.Integer(), nullable=True),
        sa.Column("new_weekday", sa.Integer(), nullable=True),
        sa.Column("lesson_time", sa.String(length=32), nullable=True),
        sa.Column("schedule_start_date", sa.Date(), nullable=True),
        sa.Column("nearest_lesson_date", sa.Date(), nullable=True),
        sa.Column(
            "initiator",
            sa.Enum(
                "teacher",
                "student_parent",
                "school",
                name="ticket_initiator",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tickets_number", "tickets", ["number"], unique=True)
    op.create_index(
        "ix_tickets_student_external_id",
        "tickets",
        ["student_external_id"],
        unique=False,
    )

    op.create_table(
        "ticket_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "old_status",
            sa.Enum(
                "not_processed",
                "waiting_reply",
                "done",
                "rejected",
                name="ticket_status",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "new_status",
            sa.Enum(
                "not_processed",
                "waiting_reply",
                "done",
                "rejected",
                name="ticket_status",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_history_ticket_id",
        "ticket_history",
        ["ticket_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_history_ticket_id", table_name="ticket_history")
    op.drop_table("ticket_history")
    op.drop_index("ix_tickets_student_external_id", table_name="tickets")
    op.drop_index("ix_tickets_number", table_name="tickets")
    op.drop_table("tickets")
